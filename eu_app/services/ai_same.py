"""Gemini check: do two end-user records describe the SAME entity? (SAME / DIFFERENT)"""
from __future__ import annotations

import asyncio
import csv
import os
from pathlib import Path
from typing import Callable, Dict, List, Optional

from pydantic import BaseModel, Field

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

try:
    from google import genai
    from google.genai import types
    from google.genai.errors import APIError
except ImportError as _e:  # pragma: no cover
    genai = types = None
    APIError = Exception
    _import_error = _e
else:
    _import_error = None

MODEL_NAME = "gemini-2.5-flash"
BATCH_SIZE = 50
MAX_CONCURRENT = 15
CACHE_FIELDS = ["key", "result", "confidence", "reason"]

SYSTEM = (
    "You compare two healthcare end-user records and decide if they point to the SAME entity.\n"
    "Inputs per record: name, street address, city, state.\n"
    "- SAME: same organization/site — spelling variants, abbreviations, alternate or DBA names, "
    "typos, or the same facility described differently, owned by, operated by, part of same network.\n"
    "- DIFFERENT: distinct organizations or clearly distinct facilities. Sharing an address alone is NOT proof "
    "of SAME; the names must also plausibly describe the same entity.\n"
    "Use your healthcare-industry knowledge. Return confidence 0-100 and a 1-2 line reason."
)


class AISameUnavailable(RuntimeError):
    pass


class SameDecision(BaseModel):
    result: str = Field(description="Strictly 'SAME' or 'DIFFERENT'.")
    reason: str = Field(description="1-2 lines of evidence.")
    confidence: int = Field(description="0-100")


def ensure_available() -> None:
    if _import_error is not None:
        raise AISameUnavailable(f"google-genai / pydantic / python-dotenv missing: {_import_error}")
    if not (os.getenv("GEMINI_API_KEY") or "").strip():
        raise AISameUnavailable("GEMINI_API_KEY is missing (environment or .env file).")


async def _call(client, sem, it: dict) -> dict:
    prompt = (
        "ENTITY 1:\n"
        f"Name: {it['n1']}\nAddress: {it['a1']}\nCity: {it['c1']}\nState: {it['s1']}\n\n"
        "ENTITY 2:\n"
        f"Name: {it['n2']}\nAddress: {it['a2']}\nCity: {it['c2']}\nState: {it['s2']}\n\n"
        "Are these the same entity?"
    )
    async with sem:
        for attempt in range(4):
            try:
                resp = await client.aio.models.generate_content(
                    model=MODEL_NAME,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM,
                        response_mime_type="application/json",
                        response_schema=SameDecision,
                        temperature=0.0,
                    ),
                )
                d = SameDecision.model_validate_json(resp.text)
                res = d.result.strip().upper()
                if res not in ("SAME", "DIFFERENT"):
                    res = "ERROR"
                return {"result": res, "confidence": int(d.confidence), "reason": d.reason}
            except APIError as e:
                await asyncio.sleep((2 ** attempt) + 1 if getattr(e, "code", None) == 429 else 1)
            except Exception:
                await asyncio.sleep(1)
    return {"result": "ERROR", "confidence": 0, "reason": "API/parse failure after retries"}


async def _run(items, log, progress, api_key, batch_size, conc, on_batch_done):
    key = (api_key or os.getenv("GEMINI_API_KEY") or "").strip()
    client = genai.Client(api_key=key)
    sem = asyncio.Semaphore(conc)
    out: Dict[str, dict] = {}
    total = len(items)
    for s in range(0, total, batch_size):
        batch = items[s:s + batch_size]
        log(f"AI checking pairs {s + 1}-{s + len(batch)} of {total}…")
        res = await asyncio.gather(*[_call(client, sem, it) for it in batch])
        chunk = {it["key"]: r for it, r in zip(batch, res)}
        out.update(chunk)
        progress(min(s + len(batch), total) / total)
        if on_batch_done:
            try:
                on_batch_done(chunk)
            except Exception as exc:
                log(f"Warning: checkpoint failed: {exc}")
    return out


def classify_pairs(items: List[dict], log=None, progress=None, api_key: Optional[str] = None,
                   batch_size: int = BATCH_SIZE, max_concurrency: int = MAX_CONCURRENT,
                   on_batch_done: Optional[Callable[[Dict[str, dict]], None]] = None) -> Dict[str, dict]:
    ensure_available()
    return asyncio.run(_run(items, log or (lambda m: None), progress or (lambda p: None),
                            api_key, batch_size, max_concurrency, on_batch_done))


# ---- checkpoint cache (only SAME / DIFFERENT are stored; ERROR gets retried next run)
def load_cache(path: Path) -> Dict[str, dict]:
    if not path.is_file():
        return {}
    try:
        with open(path, newline="", encoding="utf-8") as f:
            return {r["key"]: {"result": r["result"], "confidence": int(r["confidence"] or 0), "reason": r["reason"]}
                    for r in csv.DictReader(f) if r.get("key")}
    except Exception:
        return {}


def append_cache(path: Path, results: Dict[str, dict]) -> None:
    rows = [(k, v["result"], v["confidence"], v["reason"]) for k, v in results.items()
            if v["result"] in ("SAME", "DIFFERENT")]
    if not rows:
        return
    new = not path.is_file()
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(CACHE_FIELDS)
        w.writerows(rows)
        f.flush()
        os.fsync(f.fileno())
