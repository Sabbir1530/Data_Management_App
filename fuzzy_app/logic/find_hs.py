"""
logic/find_hs.py
AI Health System Affiliation checker using Gemini API.
"""
import os
import json
import asyncio
import logging
import re
import pandas as pd
from pydantic import BaseModel, Field

logger = logging.getLogger("find_hs")

class AffiliationLookup(BaseModel):
    is_under_target_system: bool = Field(description="True if this entity genuinely belongs to, is owned by, or is integrated/affiliated with the target health system. False otherwise.")
    health_system_name: str | None = Field(default=None, description="ALWAYS return the overarching Parent Health System Name. IF input is a Doctor/Physician name, append their practice/clinic name in brackets. ONLY IF Relation is 'Wrong Address': return any active business/org name found at that address.")
    Relation: str = Field(description="The exact relationship derived STRICTLY from keywords in the search text. Select the most accurate standard category.")

async def _call_gemini_find_hs(client, semaphore, name, addr, city, state, zip_val, target_name, target_aliases, model):
    from google.genai import types
    from google.genai.errors import APIError

    prompt = f"""
Analyze the following healthcare facility or provider record, perform a Google Search to determine its true parent health system, specific clinic/practice name (if a doctor name is provided), and exact relationship based ON STRICT SEARCH KEYWORDS, then return a JSON object.

FACILITY / PROVIDER DETAILS:
Name: {name}
Address: {addr}
Location: {city}, {state} {zip_val}

REQUIRED OUTPUT FORMAT:
Return ONLY a valid JSON object without markdown formatting or code blocks:
{ 
  "is_under_target_system": true/false,
  "health_system_name": "Parent Health System Name" | "Parent Health System Name (Clinic Name)" | "Multiple" | null,
  "Relation": "Exact Relationship"
} 
"""

    system_instruction = f"""You are an expert healthcare data intelligence analyst mapping corporate affiliations. 

        1. PARENT SYSTEM & CLINIC NAME RULE:
        - Identify the TRUE ultimate parent system for `health_system_name` (e.g., "{target_name}", "Mercy", "SSM Health").
        - {target_name} divisions/partners ({', '.join(target_aliases)}) MUST use "{target_name}" as ultimate parent.
        - CRITICAL: If the entity belongs to {target_name} or ANY of the divisions listed above, you MUST set `is_under_target_system` to true.
        - For individual doctors/practitioners ({name}): format as `Parent Health System (Clinic Name)` (e.g., "{target_name} (Medical Group)"). If no clinic is found, output just the parent system.

        2. INDEPENDENT & GOVERNMENT ENTITIES (CRITICAL WARNING):
        - DO NOT assume an entity belongs to {target_name} just because they are in the same state/city.
        - County Health Departments, State Departments of Health, Public Clinics, and VA Medical Centers are INDEPENDENT GOVERNMENT ENTITIES. They are NEVER owned or operated by private health systems like {target_name}.
        - If the entity is a public health department, government clinic, or competitor, YOU MUST set `is_under_target_system` to false and `Relation` to "None / Independent".

        3. RELATION KEYWORD MATCHING (STRICT PRIORITIES):
        - "affiliated" / "partnership" -> "Affiliated / Partner"
        - "acquired" / "subsidiary" / "wholly owned" -> "Child Org / Subsidiary"
        - "owned and operated" / "hospital location" -> "Owned and Operated"
        - "joint venture" / "JV" -> "Joint Venture"
        - "managed" / "management agreement" -> "Managed"
        - "member" / "network practice" -> "Part of Network"
        - Independent / Unrelated / Competitor / Government -> "None / Independent"

        4. WRONG ADDRESS RULE:
        - Use "Wrong Address" ONLY IF the physical address ({addr}, {city}, {state} {zip_val}) belongs to a completely different business.
        - DO NOT use "Wrong Address" for minor discrepancies (like missing suite numbers or street abbreviations). If it's the correct clinic, keep it true.
        - If it is actually a Wrong Address:
            * `is_under_target_system` = false
            * `Relation` = "Wrong Address"
            * `health_system_name` = Actual business name at address, "Multiple" (if several), or null (if none).

        Base all decisions on real-time search terminology."""

    async with semaphore:
        for attempt in range(4):
            try:
                response = await client.aio.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        tools=[types.Tool(google_search=types.GoogleSearch())],
                        temperature=0.0
                    ),
                )
                
                text = response.text.strip()
                
                start_idx = text.find('{')
                end_idx = text.rfind('}')
                
                if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                    text = text[start_idx:end_idx+1]
                elif start_idx == -1 and end_idx != -1:
                    
                    text = "{" + text[:end_idx+1]
                    start_idx = text.find('{')
                    text = text[start_idx:end_idx+2]

                try:
                    result = AffiliationLookup.model_validate_json(text)
                except Exception as parse_e:
                    
                    import re
                    match = re.search(r'\{.*\}', response.text, re.DOTALL)
                    if match:
                        result = AffiliationLookup.model_validate_json(match.group(0))
                    else:
                        raise parse_e
                
                keywords = [kw.strip().lower() for kw in target_aliases] + [target_name.lower()]
                if not result.is_under_target_system and result.health_system_name and result.Relation not in ["None / Independent", "None / Competitor", "Wrong Address"]:
                    if any(kw in result.health_system_name.lower() for kw in keywords):
                        result.is_under_target_system = True

                if result.is_under_target_system and result.Relation != "Wrong Address":
                    if not result.health_system_name or not result.health_system_name.startswith(target_name):
                        if result.health_system_name and "(" in result.health_system_name:
                            clinic_part = result.health_system_name[result.health_system_name.find("("):]
                            result.health_system_name = f"{target_name} {clinic_part}"
                        else:
                            result.health_system_name = target_name
                        
                return result
            except APIError as e:
                if getattr(e, "code", None) == 429:
                    await asyncio.sleep((2 ** attempt) + 1)
                else:
                    logger.error(f"Gemini API Exception: {e}")
                    await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                await asyncio.sleep(1)
                
        return AffiliationLookup(is_under_target_system=False, health_system_name="ERROR", Relation="None / Independent")

async def _process_batch_hs(client, semaphore, batch_df, name_col, addr_col, city_col, state_col, zip_col, target_name, target_aliases, model):
    tasks = [
        _call_gemini_find_hs(
            client, semaphore,
            row.get(name_col, ""), row.get(addr_col, ""), row.get(city_col, ""),
            row.get(state_col, ""), row.get(zip_col, ""),
            target_name, target_aliases, model
        )
        for _, row in batch_df.iterrows()
    ]
    return await asyncio.gather(*tasks)

def run_find_hs(df, api_key, target_system_name, target_system_aliases, name_col, addr_col, city_col, state_col, zip_col, id_col=None, output_dir=".", batch_size=50, max_concurrent=15, model="gemini-2.5-flash", progress_cb=None):
    from google import genai
    
    df = df.copy()
    if not id_col or id_col not in df.columns:
        df["Unique_ID"] = df.index.astype(str)
        id_col = "Unique_ID"
    else:
        df[id_col] = df[id_col].astype(str)
        
    sanitized_name = re.sub(r'[^a-zA-Z0-9]', '', target_system_name)
    bool_col_name = f'is_under_{sanitized_name}'
    target_cols = [bool_col_name, 'health_system_name', 'Relation']
    
    for col in target_cols:
        if col not in df.columns:
            df[col] = None

    os.makedirs(output_dir, exist_ok=True)
    cache_csv = os.path.join(output_dir, f"{sanitized_name.lower()}_cache.csv")
    output_excel = os.path.join(output_dir, f"{sanitized_name}_Affiliations_Output.xlsx")
    
    if os.path.exists(cache_csv):
        try:
            cache_df = pd.read_csv(cache_csv)
            cache_df = cache_df.dropna(subset=[bool_col_name]).drop_duplicates(subset=[id_col])
            cache_df = cache_df.set_index(id_col)
            for col in target_cols:
                if col in cache_df.columns:
                    df[col] = df[col].fillna(df[id_col].map(cache_df[col]))
        except Exception as e:
            logger.error(f"Cache load failed: {e}")

    unprocessed = df[df[bool_col_name].isna()]
    unproc_indices = unprocessed.index.tolist()
    total = len(unproc_indices)

    if total > 0:
        client = genai.Client(api_key=api_key)
        
        async def main_loop():
            semaphore = asyncio.Semaphore(max_concurrent)
            processed_so_far = 0
            
            for i in range(0, total, batch_size):
                batch_idx = unproc_indices[i:i+batch_size]
                batch_df = df.loc[batch_idx]
                
                results = await _process_batch_hs(
                    client, semaphore, batch_df,
                    name_col, addr_col, city_col, state_col, zip_col,
                    target_system_name, target_system_aliases, model
                )
                
                df.loc[batch_idx, bool_col_name] = [r.is_under_target_system for r in results]
                df.loc[batch_idx, 'health_system_name'] = [r.health_system_name for r in results]
                df.loc[batch_idx, 'Relation'] = [r.Relation for r in results]
                
                processed_so_far += len(batch_idx)
                if progress_cb:
                    progress_cb(processed_so_far, total)
                    
                cache_to_save = df[df[bool_col_name].notna()][[id_col] + target_cols]
                cache_to_save.to_csv(cache_csv, index=False)
                
        asyncio.run(main_loop())
    
    from shared.ui.ui_common import save_dataframe
    output_excel = save_dataframe(df, output_excel)
    return df, output_excel
