"""AI-assisted entity matching (SAME / AFFILIATED / DIFFERENT).

Adapted from the standalone CMPNY_UPD_Matching_AI.py script so it can be
called directly from the desktop app:
  - takes a pandas DataFrame in, returns a pandas DataFrame out
  - reports progress/log lines through callbacks instead of print()/logging
  - has no dependency on fixed input/output file paths or a cache CSV

Requires the same dependencies as the original script:
    pip install google-genai pydantic python-dotenv
and a GEMINI_API_KEY available in the environment or a local .env file.
"""
from __future__ import annotations

import asyncio
import os
from typing import Callable, Optional

import pandas as pd
from shared.models.ai import EntityMatchDecision

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

try:
    from google import genai
    from google.genai import types
    from google.genai.errors import APIError
except ImportError as _imp_err:  
    genai = None
    types = None
    APIError = Exception
    _import_error = _imp_err
else:
    _import_error = None

BATCH_SIZE = 50
MAX_CONCURRENT_REQUESTS = 15
MODEL_NAME = "gemini-2.5-flash"

RESULT_COLUMNS = [
    "result", "result_reason", "confidence", "entity_1_parent", "entity_2_parent",
]

system_instruction = (
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

class AIMatchingUnavailable(RuntimeError):
    """Raised when the google-genai stack or an API key is not available."""

def ensure_available() -> None:
    if _import_error is not None:
        raise AIMatchingUnavailable(
            "AI matching dependencies are missing (google-genai / pydantic / python-dotenv). "
            f"Import error: {_import_error}"
        )
    if not (os.getenv("GEMINI_API_KEY") or "").strip():
        raise AIMatchingUnavailable(
            "GEMINI_API_KEY is missing. Set it in your environment or in a .env file next to the app."
        )

def _make_client(api_key: Optional[str] = None):
    key = (api_key or os.getenv("GEMINI_API_KEY") or "").strip()
    if not key:
        raise AIMatchingUnavailable(
            "GEMINI_API_KEY is missing. Set it in your environment or in a .env file next to the app."
        )
    return genai.Client(api_key=key)

async def _call_gemini(client, semaphore, bp_name, bp_addr, dm_name, dm_addr) -> EntityMatchDecision:
    prompt = f"""
Compare the following two healthcare organizational entities and determine their relationship category.

ENTITY 1 (UPD):
Name: {bp_name}
Address: {bp_addr}

ENTITY 2 (CMPNY):
Name: {dm_name}
Address: {dm_addr}
"""
    async with semaphore:
        for attempt in range(4):
            try:
                response = await client.aio.models.generate_content(
                    model=MODEL_NAME,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        response_mime_type="application/json",
                        response_schema=EntityMatchDecision,
                        temperature=0.0,
                    ),
                )
                return EntityMatchDecision.model_validate_json(response.text)
            except APIError as e:
                if getattr(e, "code", None) == 429:
                    await asyncio.sleep((2 ** attempt) + 1)
                else:
                    await asyncio.sleep(1)
            except Exception:
                await asyncio.sleep(1)

        return EntityMatchDecision(
            result="ERROR",
            reason="Failed to process due to persistent API or parsing timeouts.",
            confidence=0,
            entity_1_parent=None,
            entity_2_parent=None,
        )

async def _process_batch(client, semaphore, batch_df: pd.DataFrame):
    tasks = [
        _call_gemini(
            client, semaphore,
            row.get("UPD_NM_1", ""), row.get("UPD_ADDR_1", ""),
            row.get("CMPNY_NM", ""), row.get("CMPNY_ADDR_1", ""),
        )
        for _, row in batch_df.iterrows()
    ]
    return await asyncio.gather(*tasks)

async def _classify_async(
    df: pd.DataFrame,
    log: Callable[[str], None],
    progress: Callable[[float], None],
    api_key: Optional[str],
    batch_size: int,
    max_concurrency: int,
    on_batch_done: Optional[Callable[[pd.DataFrame], None]] = None,
) -> pd.DataFrame:
    out = df.copy().reset_index(drop=True)
    for col in RESULT_COLUMNS:
        if col not in out.columns:
            out[col] = None

    total = len(out)
    if total == 0:
        return out

    client = _make_client(api_key)
    semaphore = asyncio.Semaphore(max_concurrency)

    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        batch = out.iloc[start:end]
        log(f"AI classifying rows {start + 1}-{end} of {total}…")
        try:
            results = await _process_batch(client, semaphore, batch)
        except Exception as exc:
            log(f"Batch {start + 1}-{end} failed outright: {exc}. Marking as ERROR.")
            results = [
                EntityMatchDecision(result="ERROR", reason=str(exc), confidence=0)
                for _ in range(len(batch))
            ]
        idx = batch.index
        out.loc[idx, "result"] = [r.result for r in results]
        out.loc[idx, "result_reason"] = [r.reason for r in results]
        out.loc[idx, "confidence"] = [r.confidence for r in results]
        out.loc[idx, "entity_1_parent"] = [r.entity_1_parent for r in results]
        out.loc[idx, "entity_2_parent"] = [r.entity_2_parent for r in results]
        progress(end / total)

        if on_batch_done is not None:
            try:
                on_batch_done(out.loc[idx])
            except Exception as exc:
                log(f"Warning: checkpoint save failed for rows {start + 1}-{end}: {exc}")

    same = int((out["result"] == "SAME").sum())
    affiliated = int((out["result"] == "AFFILIATED").sum())
    different = int((out["result"] == "DIFFERENT").sum())
    errors = int((out["result"] == "ERROR").sum())
    log(f"AI classification done — SAME={same}, AFFILIATED={affiliated}, DIFFERENT={different}, ERROR={errors}.")
    return out

def classify_candidates(
    df: pd.DataFrame,
    log: Optional[Callable[[str], None]] = None,
    progress: Optional[Callable[[float], None]] = None,
    api_key: Optional[str] = None,
    batch_size: int = BATCH_SIZE,
    max_concurrency: int = MAX_CONCURRENT_REQUESTS,
    on_batch_done: Optional[Callable[[pd.DataFrame], None]] = None,
) -> pd.DataFrame:
    """Classify each row of `df` (needs UPD_NM_1, UPD_ADDR_1, CMPNY_NM, CMPNY_ADDR_1) as
    SAME / AFFILIATED / DIFFERENT / ERROR using Gemini. Adds result, result_reason,
    confidence, entity_1_parent, entity_2_parent columns. Safe to call from a background
    thread (spins up its own asyncio event loop).

    If `on_batch_done` is given, it's called with each finished batch's slice of the
    output DataFrame right after that batch is classified — the caller can use this to
    persist a checkpoint (see services.match_cache) so a crash mid-run only loses at
    most one in-flight batch instead of the whole run.
    """
    log = log or (lambda m: None)
    progress = progress or (lambda p: None)
    if _import_error is not None:
        raise AIMatchingUnavailable(
            "AI matching dependencies are missing (google-genai / pydantic / python-dotenv). "
            f"Import error: {_import_error}"
        )
    return asyncio.run(
        _classify_async(df, log, progress, api_key, batch_size, max_concurrency, on_batch_done)
    )
