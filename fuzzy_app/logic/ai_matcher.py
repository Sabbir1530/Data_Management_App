"""
logic/ai_matcher.py
Gemini-powered SAME / AFFILIATED / DIFFERENT entity matching, adapted
from the user's original CMPNY_UPD_Matching_AI.py. Designed to be called
from a background thread (it is blocking + runs its own asyncio loop
internally) with a progress callback for the GUI to poll.
"""
import os
import html
import json
import time
import asyncio
import logging
from datetime import datetime
from typing import Optional

import pandas as pd
from pydantic import BaseModel, Field

logger = logging.getLogger("ai_matcher")

TARGET_COLUMNS = ["result", "relation", "entity_1_parent", "entity_2_parent",
                   "confidence", "result_reason"]


class EntityMatchDecision(BaseModel):
    result: str = Field(description="One of: 'SAME', 'AFFILIATED', 'DIFFERENT'.")
    relation: str = Field(description="One of: 'SELF','OWNED','OPERATED','MANAGED',"
                                       "'AFFILIATED','JOINT_VENTURE','NONE'.")
    entity_1_parent: Optional[str] = Field(default=None)
    entity_2_parent: Optional[str] = Field(default=None)
    confidence: int = Field(default=0)
    reason: str = Field(default="")


SYSTEM_INSTRUCTION = (
    "Your task is to determine if two healthcare entities refer to the SAME organization, are AFFILIATED, or are DIFFERENT organizations.\n\n"

    "CRITICAL MATCHING RULES:\n"
    "1. NAME MATCHING & NORMALIZATION:\n"
    "   - Normalize names by expanding healthcare abbreviations (e.g., Hosp -> Hospital, Med Ctr -> Medical Center), removing punctuation, and ignoring case.\n"
    "   - Treat names as SAME only if they are strong phonetic/linguistic variants, explicit DBAs, or clear aliases. Do NOT match if core brand names are distinct.\n\n"

    "2. ADDRESS & LOCATION:\n"
    "   - ADDRESS IS NOT DEFINITIVE. Unrelated clinics and doctors frequently share buildings or suites. Shared address alone DOES NOT prove a relationship.\n\n"

    "3. INDIVIDUAL PROVIDERS VS CORPORATIONS:\n"
    "   - If Entity 1 is a solo practitioner (e.g., 'Dr. John Smith') and Entity 2 is a health system or hospital, default to DIFFERENT unless official data proves Entity 1 is a DBA of Entity 2.\n\n"

    "4. GOVERNMENT & PUBLIC ENTITIES:\n"
    "   - County Health Departments, State Health Divisions, VA Medical Centers, and Public Health Clinics are INDEPENDENT GOVERNMENT ENTITIES. They are NEVER owned by or affiliated with private health systems (e.g., Johns Hopkins, Mercy). Mark as DIFFERENT.\n\n"

    "5. PARENT ORGANIZATION IDENTIFICATION:\n"
    "   - Identify the parent health network/system (e.g., HCA Healthcare, Ascension, Providence) for BOTH entities independently.\n"
    "   - If an entity is standalone or independent, return null. DO NOT guess or hallucinate parent entities.\n\n"

    "CLASSIFICATION DEFINITIONS:\n"
    "- SAME: Identical legal entity, DBA, former name, or a minor formatting variant of the exact same clinic/branch.\n"
    "- AFFILIATED: Distinct operating entities that belong to the SAME overarching health system/parent network.\n"
    "- DIFFERENT: Unrelated organizations, distinct brands, or private vs public entities. and If an entity is small, obscure, or lacks a verifiable connection, do not force an affiliation—if there's no clear 'owned or operated' link, just say they are different.\n\n"

    "STRICT OUTPUT REQUIREMENT:\n"
    "Return ONLY a valid raw JSON object without markdown formatting, preamble, or code blocks (no ```json).\n\n"

    "JSON STRUCTURE:\n"
    "{\n"
    "  \"result\": \"SAME\" | \"AFFILIATED\" | \"DIFFERENT\",\n"
    "  \"relation\": \"SELF\" | \"OWNED\" | \"OPERATED\" | \"MANAGED\" | \"AFFILIATED\" | \"JOINT_VENTURE\" | \"NONE\",\n"
    "  \"entity_1_parent\": \"String or null\",\n"
    "  \"entity_2_parent\": \"String or null\",\n"
    "  \"confidence\": 0-100,\n"
    "  \"reason\": \"Concise explanation citing name match, address context, and parent network comparison.\"\n"
    "}"
)


def _build_prompt(name1, addr1, city1, name2, addr2, city2):
    return f"""Compare the following two healthcare organizational entities and determine
their relationship category.

ENTITY 1:
Name: {name1}
Address: {addr1}
City: {city1}

ENTITY 2:
Name: {name2}
Address: {addr2}
City: {city2}
"""


async def _call_gemini(client, semaphore, name1, addr1, city1, name2, addr2, city2, model, use_grounding=False):
    from google.genai import types
    from google.genai.errors import APIError

    prompt = _build_prompt(name1, addr1, city1, name2, addr2, city2)

    sys_instruction = SYSTEM_INSTRUCTION
    tools = None

    if use_grounding:
        sys_instruction = (
            "You are an expert healthcare entity matching assistant with access to Google Search.\n"
            "Your task is to search Google to verify if the two healthcare entities below refer to the SAME organization, are AFFILIATED, or are DIFFERENT organizations.\n\n"
            "CRITICAL RULES:\n"
            "- GOOGLE SEARCH FIRST: Actively search online for both entities. Trust the search results over basic name similarity. If the search results confirm they are the same legal entity, aliases, or DBAs, classify them as SAME.\n"
            "- DBAs & ACQUISITIONS: Healthcare organizations often have completely different names (e.g., after an acquisition or as a DBA). If evidence shows one is a DBA, former name, or acquired by the other and operating as the same entity, they are SAME.\n"
            "- ADDRESS CORROBORATION: While sharing an address isn't definitive proof, if search results link the two names to the same practice at the same address, consider them SAME.\n"
            "- INDEPENDENT & GOVERNMENT ENTITIES: County Health Departments, State Departments of Health, Public Clinics, and VA Medical Centers are INDEPENDENT GOVERNMENT ENTITIES. They are NEVER owned, operated by, or affiliated with private health systems/hospitals (e.g. Johns Hopkins, Mercy, etc.). If one is a government/public health department and the other is a private health system/hospital, they are definitively DIFFERENT and relation is NONE, regardless of shared geography.\n"
            "- RELATIONSHIP CATEGORIZATION: Specify the relation in the 'relation' field as strictly one of: 'SELF', 'OWNED', 'OPERATED', 'MANAGED', 'AFFILIATED', 'JOINT_VENTURE', or 'NONE'. Use 'SELF' if both names match/are aliases.\n"
            "- PARENT ORGANIZATION IDENTIFICATION: Search and identify the parent organization or overarching health network for BOTH entities independently. Return null if standalone or unverified.\n\n"
            "DEFINITIONS FOR CLASSIFICATION:\n"
            "- SAME = Identical legal/operating entity, owned by, operated by, part of same networ, DBA, former name, or confirmed alias based on search results.\n"
            "- AFFILIATED = Part of the same health system or network, but distinct legal or operating entities.\n"
            "- DIFFERENT = Unrelated organizations, or entities with entirely distinct core names and ownership without any search evidence linking them.\n\n"
            "CRITICAL OUTPUT FORMATTING:\n"
            "Output ONLY valid JSON.\n"
            "JSON structure must be exact:\n"
            "{\n"
            '  "result": "SAME" | "AFFILIATED" | "DIFFERENT",\n'
            '  "relation": "SELF" | "OWNED" | "OPERATED" | "MANAGED" | "AFFILIATED" | "JOINT_VENTURE" | "NONE",\n'
            '  "entity_1_parent": "String or null",\n'
            '  "entity_2_parent": "String or null",\n'
            '  "confidence": 0-100,\n'
            '  "reason": "Concise reason citing real search results or evidence"\n'
            "}"
        )
        tools = [types.Tool(google_search=types.GoogleSearch())]

    async with semaphore:
        for attempt in range(4):
            try:
                config_kwargs = {
                    "system_instruction": sys_instruction,
                    "temperature": 0.0
                }
                if tools is not None:
                    config_kwargs["tools"] = tools

                response = await client.aio.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(**config_kwargs),
                )
                raw_text = response.text.strip()

                start_idx = raw_text.find('{')
                end_idx = raw_text.rfind('}')
                if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                    raw_text = raw_text[start_idx:end_idx+1]
                elif start_idx == -1 and end_idx != -1:
                    raw_text = "{" + raw_text[:end_idx+1]
                    start_idx = raw_text.find('{')
                    raw_text = raw_text[start_idx:end_idx+2]

                try:
                    data = json.loads(raw_text)
                except json.JSONDecodeError as parse_e:
                    import re
                    match = re.search(r'\{.*\}', response.text, re.DOTALL)
                    if match:
                        data = json.loads(match.group(0))
                    else:
                        raise parse_e

                return EntityMatchDecision(**data)

            except APIError as e:
                if getattr(e, "code", None) == 429:
                    wait_time = (2 ** attempt) + 1
                    logger.warning(f"Rate limited. Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"Gemini API error: {e}")
                    await asyncio.sleep(1)
            except json.JSONDecodeError as e:
                logger.error(f"JSON parse failed: {e}")
                await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"Unexpected error: {e}")
                await asyncio.sleep(1)

        return EntityMatchDecision(
            result="ERROR", relation="NONE", entity_1_parent=None,
            entity_2_parent=None, confidence=0,
            reason="Failed to process due to persistent API or parsing timeouts.",
        )


async def _process_batch(client, semaphore, batch_df, name1_col, addr1_col, city1_col,
                          name2_col, addr2_col, city2_col, model, use_grounding=False):
    tasks = [
        _call_gemini(
            client, semaphore,
            row.get(name1_col, ""), row.get(addr1_col, ""), row.get(city1_col, "") if city1_col else "",
            row.get(name2_col, ""), row.get(addr2_col, ""), row.get(city2_col, "") if city2_col else "",
            model, use_grounding
        )
        for _, row in batch_df.iterrows()
    ]
    return await asyncio.gather(*tasks)


def run_ai_matching(
    df,
    api_key,
    name1_col,
    addr1_col,
    name2_col,
    addr2_col,
    city1_col=None,
    city2_col=None,
    id_col=None,
    output_dir=".",
    batch_size=50,
    max_concurrent=15,
    model="gemini-2.5-flash",
    use_grounding=False,
    output_filename="gpo_ai_matching_results.xlsx",
    progress_cb=None,
    stop_flag=None,
):
    """Blocking call meant to run in a background thread.
    Returns (result_df, output_excel_path, html_report_path, stats_dict).
    """
    from google import genai

    df = df.copy()
    if not id_col or id_col not in df.columns:
        df["Unique_ID"] = df.index.astype(str)
        id_col = "Unique_ID"
    else:
        df[id_col] = df[id_col].astype(str)

    for col in TARGET_COLUMNS:
        if col not in df.columns:
            df[col] = pd.Series(dtype=object)
        else:
            df[col] = df[col].astype(object)
            df[col] = df[col].apply(lambda x: str(x).strip() if pd.notna(x) and x is not None else None)
            df[col] = df[col].replace({"nan": None, "NaN": None, "None": None, "<NA>": None, "": None})

    # Explicitly enforce object dtype to avoid string/float dtype conflicts when inserting mixed values
    for col in TARGET_COLUMNS:
        df[col] = df[col].astype(object)

    os.makedirs(output_dir, exist_ok=True)
    cache_csv = os.path.join(output_dir, "ai_cache_results.csv")
    if os.path.exists(cache_csv):
        try:
            cache_df = pd.read_csv(cache_csv)
            cache_df.columns = cache_df.columns.str.strip()
            cache_df = cache_df.dropna(subset=["result"]).drop_duplicates(subset=[id_col])
            cache_df = cache_df.set_index(id_col)
            for col in TARGET_COLUMNS:
                if col in cache_df.columns:
                    df[col] = df[col].fillna(df[id_col].map(cache_df[col]))
        except Exception as e:
            logger.error(f"Cache load failed: {e}")

    unprocessed = df[df["result"].isna()].index.tolist()
    total = len(unprocessed)

    client = genai.Client(api_key=api_key)
    semaphore = asyncio.Semaphore(max_concurrent)

    async def run_all():
        for i in range(0, total, batch_size):
            if stop_flag is not None and stop_flag.is_set():
                if progress_cb:
                    progress_cb(i, total, 0.0, stopped=True)
                break

            batch_idx = unprocessed[i:i + batch_size]
            batch_df = df.loc[batch_idx]
            start = time.time()
            try:
                results = await _process_batch(
                    client, semaphore, batch_df,
                    name1_col, addr1_col, city1_col,
                    name2_col, addr2_col, city2_col,
                    model, use_grounding
                )
                df.loc[batch_idx, "result"] = [r.result for r in results]
                df.loc[batch_idx, "relation"] = [r.relation for r in results]
                df.loc[batch_idx, "entity_1_parent"] = [r.entity_1_parent for r in results]
                df.loc[batch_idx, "entity_2_parent"] = [r.entity_2_parent for r in results]
                df.loc[batch_idx, "confidence"] = [r.confidence for r in results]
                df.loc[batch_idx, "result_reason"] = [r.reason for r in results]
            except Exception as e:
                logger.error(f"Batch failed: {e}")
                df.loc[batch_idx, "result"] = "ERROR"
                df.loc[batch_idx, "relation"] = "NONE"
                df.loc[batch_idx, "confidence"] = 0
                df.loc[batch_idx, "result_reason"] = f"Batch failed: {e}"

            elapsed = time.time() - start
            done = min(i + batch_size, total)
            if progress_cb:
                progress_cb(done, total, elapsed)

            try:
                df[df["result"].notna()][[id_col] + TARGET_COLUMNS].to_csv(cache_csv, index=False)
            except Exception as e:
                logger.error(f"Cache save failed: {e}")

    if total > 0:
        asyncio.run(run_all())

    non_target = [c for c in df.columns if c not in TARGET_COLUMNS]
    df = df[non_target + TARGET_COLUMNS]

    output_excel = os.path.join(output_dir, output_filename)
    from shared.ui.ui_common import save_dataframe
    output_excel = save_dataframe(df, output_excel)

    stats = {
        "total": len(df),
        "same": int((df["result"] == "SAME").sum()),
        "affiliated": int((df["result"] == "AFFILIATED").sum()),
        "different": int((df["result"] == "DIFFERENT").sum()),
        "error": int((df["result"] == "ERROR").sum()),
    }

    html_path = os.path.join(output_dir, output_filename.replace(".xlsx", "_report.html"))
    try:
        report = _generate_html_report(df, output_excel, name1_col, addr1_col, city1_col,
                                        name2_col, addr2_col, city2_col, stats)
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(report)
    except Exception as e:
        logger.error(f"HTML report generation failed: {e}")
        html_path = None

    return df, output_excel, html_path, stats


# ---------------------------------------------------------------------------
# HTML report (same visual style as the original script)
# ---------------------------------------------------------------------------

def _generate_html_report(df, output_file, name1_col, addr1_col, city1_col, name2_col, addr2_col, city2_col, stats):
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Entity Matching Report</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box;font-family:Segoe UI,Arial,sans-serif;}}
body{{background:#f8f9fb;padding:40px;min-height:100vh;}}
.header{{margin-bottom:35px;background:white;padding:32px 28px;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,.06);border-left:6px solid #047857;}}
.header h1{{color:#1a202c;font-size:36px;font-weight:700;letter-spacing:-.5px;margin-bottom:20px;}}
.header-meta{{display:grid;grid-template-columns:repeat(3,1fr);gap:24px;}}
.header-item{{display:flex;flex-direction:column;}}
.header-label{{font-size:12px;font-weight:600;color:#6b7280;text-transform:uppercase;letter-spacing:.3px;margin-bottom:6px;}}
.header-value{{font-size:16px;font-weight:600;color:#1a202c;}}
.summary{{display:flex;gap:20px;margin-bottom:35px;}}
.stat{{flex:1;background:white;padding:28px;border-radius:12px;box-shadow:0 2px 8px rgba(0,0,0,.06);}}
.stat h2{{font-size:40px;margin-bottom:8px;font-weight:700;letter-spacing:-.5px;}}
.stat p{{font-weight:500;font-size:14px;letter-spacing:.3px;}}
.same-stat{{border-left:5px solid #047857;}}.same-stat h2{{color:#047857;}}.same-stat p{{color:#5a6b6f;}}
.aff-stat{{border-left:5px solid #b45309;}}.aff-stat h2{{color:#b45309;}}.aff-stat p{{color:#5a6b6f;}}
.diff-stat{{border-left:5px solid #b91c1c;}}.diff-stat h2{{color:#b91c1c;}}.diff-stat p{{color:#5a6b6f;}}
.card{{background:white;border-radius:12px;margin-bottom:20px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.06);}}
.card.same{{border-left:5px solid #047857;}}.card.affiliated{{border-left:5px solid #b45309;}}.card.different{{border-left:5px solid #b91c1c;}}.card.error{{border-left:5px solid #4b5563;}}
.top{{display:flex;align-items:center;justify-content:space-between;padding:28px;background:white;}}
.entity{{width:40%;}}.entity h3{{font-size:20px;color:#111827;margin-bottom:8px;}}.entity p{{font-size:13px;color:#6b7280;line-height:1.5;}}
.parent-info{{font-size:12px;font-weight:bold;color:#4b5563;margin-top:6px;}}
.middle{{width:20%;text-align:center;}}
.badge{{display:inline-block;padding:8px 18px;border-radius:6px;color:white;font-weight:600;letter-spacing:.3px;font-size:12px;text-transform:uppercase;}}
.sub-badge{{display:block;margin-top:6px;font-size:11px;font-weight:700;color:#374151;text-transform:uppercase;}}
.same-badge{{background:#047857;}}.affiliated-badge{{background:#b45309;}}.different-badge{{background:#b91c1c;}}.error-badge{{background:#4b5563;}}
.reason{{background:#f9fafb;border-top:1px solid #e5e7eb;padding:22px 28px;}}
.reason-title{{font-weight:600;color:#1a202c;margin-bottom:10px;font-size:13px;text-transform:uppercase;letter-spacing:.4px;}}
.reason-text{{color:#5a6b6f;line-height:1.6;font-size:14px;}}
.category-section{{margin-bottom:50px;}}
.category-header{{display:flex;align-items:center;gap:12px;margin-bottom:25px;padding-bottom:15px;border-bottom:2px solid #e5e7eb;}}
.category-header h2{{font-size:22px;font-weight:700;color:#1a202c;margin:0;}}
.category-badge{{display:inline-block;padding:6px 14px;border-radius:6px;color:white;font-weight:600;font-size:11px;text-transform:uppercase;}}
.category-same .category-badge{{background:#047857;}}.category-affiliated .category-badge{{background:#b45309;}}.category-different .category-badge{{background:#b91c1c;}}
.count-badge{{background:#6b7280;color:white;padding:4px 10px;border-radius:20px;font-size:12px;font-weight:600;margin-left:auto;}}
</style>
</head>
<body>
<div class="header">
    <h1>Entity Matching Report</h1>
    <div class="header-meta">
        <div class="header-item"><div class="header-label">File Name</div><div class="header-value">{html.escape(os.path.basename(str(output_file)))}</div></div>
        <div class="header-item"><div class="header-label">Total Rows</div><div class="header-value">{stats['total']}</div></div>
        <div class="header-item"><div class="header-label">Generated</div><div class="header-value">{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</div></div>
    </div>
</div>
<div class="summary">
    <div class="stat same-stat"><h2>{stats['same']}</h2><p>SAME</p></div>
    <div class="stat aff-stat"><h2>{stats['affiliated']}</h2><p>AFFILIATED</p></div>
    <div class="stat diff-stat"><h2>{stats['different']}</h2><p>DIFFERENT</p></div>
</div>
"""

    def render_section(category, label, title, rows):
        section = f"""<div class="category-section category-{category}">
<div class="category-header">
    <span class="category-badge">{label}</span>
    <h2>{title}</h2>
    <span class="count-badge">{len(rows)}</span>
</div>
"""
        for _, row in rows.iterrows():
            n1 = html.escape(str(row.get(name1_col, "") or ""))
            n2 = html.escape(str(row.get(name2_col, "") or ""))
            a1 = html.escape(str(row.get(addr1_col, "") or ""))
            a2 = html.escape(str(row.get(addr2_col, "") or ""))
            c1 = html.escape(str(row.get(city1_col, "") or "")) if city1_col else ""
            c2 = html.escape(str(row.get(city2_col, "") or "")) if city2_col else ""

            addr_str1 = f"{a1}<br>{c1}" if c1 else a1
            addr_str2 = f"{a2}<br>{c2}" if c2 else a2

            relation = html.escape(str(row.get("relation", "") or "NONE"))
            reason = html.escape(str(row.get("result_reason", "") or "") or "No reason provided")
            confidence = html.escape(str(row.get("confidence", "") or "N/A"))
            p1 = row.get("entity_1_parent", None)
            p2 = row.get("entity_2_parent", None)
            p1_str = (f'<div class="parent-info">Parent: {html.escape(str(p1))}</div>'
                      if pd.notna(p1) and p1 else '<div class="parent-info">Parent: None Found</div>')
            p2_str = (f'<div class="parent-info">Parent: {html.escape(str(p2))}</div>'
                      if pd.notna(p2) and p2 else '<div class="parent-info">Parent: None Found</div>')

            section += f"""<div class="card {category}">
    <div class="top">
        <div class="entity"><h3>{n1}</h3><p>{addr_str1}</p>{p1_str}</div>
        <div class="middle">
            <span class="badge {category}-badge">{label}</span>
            <span class="sub-badge">Relation: {relation}</span>
        </div>
        <div class="entity" style="text-align:right;"><h3>{n2}</h3><p>{addr_str2}</p>{p2_str}</div>
    </div>
    <div class="reason"><div class="reason-title">Reason (Confidence: {confidence}%)</div><div class="reason-text">{reason}</div></div>
</div>
"""
        section += "</div>\n"
        return section

    if stats["same"] > 0:
        html_content += render_section("same", "SAME", "Matching Entities", df[df["result"] == "SAME"])
    if stats["affiliated"] > 0:
        html_content += render_section("affiliated", "AFFILIATED", "Affiliated Entities", df[df["result"] == "AFFILIATED"])
    if stats["different"] > 0:
        html_content += render_section("different", "DIFFERENT", "Different Entities", df[df["result"] == "DIFFERENT"])

    html_content += "</body>\n</html>"
    return html_content
