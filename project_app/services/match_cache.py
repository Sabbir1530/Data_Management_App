"""Local checkpoint cache for the Auto-Match (AI) workflow.

If the app is closed, crashes, or the run is interrupted partway through,
Auto-Match can resume without re-sending already-classified pairs to the
AI. Each row's classification is appended to a small CSV file the moment
it comes back from Gemini.

The cache is scoped per (server, database, schema). Caches and outputs are
routed to the active workspace directory.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
from shared.services.workspace import cache_dir as ws_cache_dir, output_dir as ws_output_dir, get_active_workspace_name

CACHE_COLUMNS = [
    "uni_id", "_source_task", "REC_ID", "CMPNY_ID",
    "result", "result_reason", "confidence", "entity_1_parent", "entity_2_parent",
]


def cache_dir() -> Path:
    """Return the active workspace cache directory (or override via env)."""
    env = os.environ.get("MDM_AUTOMATCH_CACHE_DIR")
    if env:
        base = Path(env)
        base.mkdir(parents=True, exist_ok=True)
        return base
    return ws_cache_dir()


def output_dir() -> Path:
    """Return the active workspace output directory."""
    return ws_output_dir()


def _cache_key(server: str, database: str, schema: str) -> str:
    raw = f"{server}|{database}|{schema}".strip().lower()
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def cache_path(server: str, database: str, schema: str) -> Path:
    """Return absolute path to cache file inside active workspace."""
    key = _cache_key(server, database, schema)
    return cache_dir() / f"automatch_{key}.csv"


def final_results_path(task_group: str = "4-7") -> Path:
    """Return path for default final result file. task_group is '4-7' or '2'."""
    return output_dir() / f"nad_ai_{task_group}.xlsx"


def make_uni_id(task_name: str, rec_id, cmpny_id) -> str:
    return f"{task_name}::{rec_id}::{cmpny_id}"


def load_cache(path: Path) -> Dict[str, dict]:
    """Return {uni_id: {result fields...}}. Empty dict if no/corrupt cache file."""
    if not path.is_file():
        return {}
    try:
        df = pd.read_csv(path, dtype=str)
    except Exception:
        return {}
    if "uni_id" not in df.columns:
        return {}
    df = df.dropna(subset=["uni_id"]).drop_duplicates(subset=["uni_id"], keep="last")
    return {row["uni_id"]: row.to_dict() for _, row in df.iterrows()}


def append_cache(path: Path, records: List[dict]) -> None:
    """Safely append newly classified rows to the cache file (mkdir + flush/fsync)."""
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    
    df = pd.DataFrame(records)
    
    # Ensure uni_id and essential key fields are populated
    for _, row in df.iterrows():
        if "uni_id" not in row or pd.isna(row["uni_id"]):
            row["uni_id"] = make_uni_id(
                str(row.get("_source_task", "")),
                str(row.get("REC_ID", "")),
                str(row.get("CMPNY_ID", ""))
            )

    for col in CACHE_COLUMNS:
        if col not in df.columns:
            df[col] = None
    df = df[CACHE_COLUMNS]

    write_header = not path.is_file()
    
    # Append with explicit flush/fsync for crash protection
    with open(path, mode="a", encoding="utf-8", newline="") as f:
        df.to_csv(f, header=write_header, index=False)
        f.flush()
        os.fsync(f.fileno())


def cache_status(server: str, database: str, schema: str) -> dict:
    """Returns details about active workspace and cache file for display in UI."""
    cpath = cache_path(server, database, schema)
    records = load_cache(cpath)
    return {
        "workspace": get_active_workspace_name(),
        "cache_path": cpath,
        "row_count": len(records),
        "output_dir": output_dir(),
    }


def clear_cache(path: Path) -> bool:
    if path.is_file():
        path.unlink()
        return True
    return False