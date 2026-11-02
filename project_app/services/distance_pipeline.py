import os
import time
import re
import sqlite3
import asyncio
import logging
import threading
from pathlib import Path
import duckdb
import pyodbc
import pandas as pd
import numpy as np
from rapidfuzz import fuzz
from geopy.geocoders import ArcGIS
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
from google import genai
from google.genai import types
from google.genai.errors import APIError
from dotenv import load_dotenv

from shared.models.ai import EntityMatchDecision
from shared.services.workspace import global_cache_dir, cache_dir, output_dir

load_dotenv()

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Initialize Gemini Client
api_key = os.getenv("GEMINI_API_KEY")
if api_key:
    client = genai.Client(api_key=api_key)
else:
    client = None

# Global configuration defaults
DB_SERVER = os.getenv("DB_SERVER", "")
DB_NAME = os.getenv("DB_NAME", "")
DB_USER = os.getenv("DB_USER", "")
DB_PASS = os.getenv("DB_PASS", "")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
GLOBAL_HSP_CACHE_CSV = str(global_cache_dir() / "company_master_hsp.csv")
GLOBAL_NON_HSP_CACHE_CSV = str(global_cache_dir() / "company_master_non_hsp.csv")

BATCH_SIZE = 50
MAX_CONCURRENT_REQUESTS = 15

def _get_conn_str():
    preferred = [
        "ODBC Driver 18 for SQL Server",
        "ODBC Driver 17 for SQL Server",
        "ODBC Driver 13 for SQL Server",
        "SQL Server Native Client 11.0",
        "SQL Server",
    ]
    try:
        available = set(pyodbc.drivers())
    except Exception:
        available = set()

    for driver in preferred:
        if driver in available:
            d = driver
            break
    else:
        d = list(available)[0] if available else "SQL Server"

    modern = d.startswith("ODBC Driver")
    trust = "TrustServerCertificate=yes;" if modern else ""
    return (
        f"DRIVER={{{d}}};"
        f"SERVER={DB_SERVER};"
        f"DATABASE={DB_NAME};"
        f"UID={DB_USER};"
        f"PWD={DB_PASS};"
        f"{trust}"
    )

CONN_STR = _get_conn_str()

HEALTHCARE_SYNONYMS = {}
try:
    import dict as custom_dict
    HEALTHCARE_SYNONYMS.update(getattr(custom_dict, "HEALTHCARE_SYNONYMS", {}))
except ImportError:
    pass

NUMBER_WORDS = {
    "FIRST": "1ST", "SECOND": "2ND", "THIRD": "3RD", "FOURTH": "4TH", "FIFTH": "5TH",
    "SIXTH": "6TH", "SEVENTH": "7TH", "EIGHTH": "8TH", "NINTH": "9TH", "TENTH": "10TH",
    "ONE": "1", "TWO": "2", "THREE": "3", "FOUR": "4", "FIVE": "5",
    "SIX": "6", "SEVEN": "7", "EIGHT": "8", "NINE": "9", "TEN": "10"
}

geolocator = ArcGIS(user_agent="medical_master_geocoder_step1")

# Global cancellation flag
CANCEL_EVENT = threading.Event()

# -------------------------------------------------------------------------
# 1. Local Caching (SQLite for Geocode & AI Engine)
# -------------------------------------------------------------------------
def init_run_databases(run_dir):
    os.makedirs(run_dir, exist_ok=True)
    c_dir = cache_dir()
    c_dir.mkdir(parents=True, exist_ok=True)

    # Init AI Cache DB
    ai_db_path = os.path.join(c_dir, "ai_matching_cache.db")
    conn = sqlite3.connect(ai_db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ai_cache (
            rec_id TEXT,
            cmpny_id TEXT,
            result TEXT,
            reason TEXT,
            confidence INTEGER,
            entity_1_parent TEXT,
            entity_2_parent TEXT,
            PRIMARY KEY (rec_id, cmpny_id)
        )
    """)
    conn.commit()
    conn.close()

    # Init Geocode Cache DB
    geo_db_path = os.path.join(c_dir, "geocode_cache.db")
    conn = sqlite3.connect(geo_db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS geocode_cache (
            full_address TEXT PRIMARY KEY,
            latitude REAL,
            longitude REAL
        )
    """)
    conn.commit()
    conn.close()

def load_geocode_cache(run_dir):
    c_dir = cache_dir()
    geo_db_path = os.path.join(c_dir, "geocode_cache.db")
    conn = sqlite3.connect(geo_db_path)
    df = pd.read_sql_query("SELECT full_address, latitude, longitude FROM geocode_cache", conn)
    conn.close()
    return dict(zip(df['full_address'], zip(df['latitude'], df['longitude'])))

def save_geocode_cache(run_dir, new_geo_data):
    if not new_geo_data:
        return
    c_dir = cache_dir()
    geo_db_path = os.path.join(c_dir, "geocode_cache.db")
    conn = sqlite3.connect(geo_db_path)
    cursor = conn.cursor()
    cursor.executemany("""
        INSERT OR REPLACE INTO geocode_cache (full_address, latitude, longitude)
        VALUES (?, ?, ?)
    """, [(k, v[0], v[1]) for k, v in new_geo_data.items()])
    conn.commit()
    conn.close()

def get_cached_ai_results(run_dir):
    c_dir = cache_dir()
    ai_db_path = os.path.join(c_dir, "ai_matching_cache.db")
    conn = sqlite3.connect(ai_db_path)
    df_cache = pd.read_sql_query("SELECT * FROM ai_cache", conn)
    conn.close()
    return df_cache

def save_batch_to_ai_cache(run_dir, batch_data):
    if not batch_data:
        return
    c_dir = cache_dir()
    ai_db_path = os.path.join(c_dir, "ai_matching_cache.db")
    conn = sqlite3.connect(ai_db_path)
    cursor = conn.cursor()
    cursor.executemany("""
        INSERT OR REPLACE INTO ai_cache (rec_id, cmpny_id, result, reason, confidence, entity_1_parent, entity_2_parent)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, batch_data)
    conn.commit()
    conn.close()

# -------------------------------------------------------------------------
# 2. Text Normalization & Spatial Fuzzy Matcher
# -------------------------------------------------------------------------
def normalize_numbers(text: str) -> str:
    if not text:
        return ""
    tokens = text.upper().split()
    return " ".join([str(NUMBER_WORDS.get(token, token)) for token in tokens])

def master_text_cleaner(text):
    if not text or not isinstance(text, str):
        return ""
    clean_str = re.sub(r"[^\w\s]", " ", text.upper().strip())
    clean_str = normalize_numbers(clean_str)
    tokens = clean_str.split()
    return " ".join([HEALTHCARE_SYNONYMS.get(t, t) for t in tokens if HEALTHCARE_SYNONYMS.get(t, t)])

def calculate_master_weighted_fuzzy_score(raw_name_clean, master_name_clean):
    if not raw_name_clean or not master_name_clean:
        return 0.0
    if raw_name_clean == master_name_clean:
        return 100.0

    raw_tokens = raw_name_clean.split()
    master_tokens = master_name_clean.split()

    if not master_tokens or not raw_tokens:
        return 0.0

    matched_score_total = 0.0
    matched_count = 0
    raw_used = set()

    # Word-by-word comparison allowing for minor typos
    for m_token in master_tokens:
        best_score = 0.0
        best_r_idx = -1

        for i, r_token in enumerate(raw_tokens):
            if i in raw_used:
                continue

            # Exact match is 100
            if m_token == r_token:
                best_score = 100.0
                best_r_idx = i
                break

            # Check for typo (using ratio)
            score = fuzz.ratio(m_token, r_token)
            if score > best_score:
                best_score = score
                best_r_idx = i

        # 80.0 threshold means minor typos allowed, but random letters won't match
        if best_score >= 80.0:
            matched_score_total += best_score
            matched_count += 1
            raw_used.add(best_r_idx)

    # If NO valid words or typos matched, prevent character-level false positives
    if matched_count == 0:
        return 0.0

    # Base coverage: what percentage of master words found a match?
    coverage_ratio = matched_count / len(master_tokens)

    # Average quality of the word matches (penalizes if words only match via typos)
    word_quality_score = matched_score_total / len(master_tokens)

    # Penalty for significant length mismatch (strict 75%)
    length_ratio = min(len(raw_name_clean), len(master_name_clean)) / len(master_name_clean)

    adjusted_score = (word_quality_score * 0.40) + (coverage_ratio * 100.0 * 0.60)

    if length_ratio < 0.75:
        adjusted_score *= (0.7 + (0.3 * length_ratio))

    return round(min(adjusted_score, 100.0), 2)

def run_spatial_fuzzy_match_split(df_temp_input, df_company, threshold_meters, stage_label):
    EARTH_RADIUS_METERS = 6371000
    temp_valid = df_temp_input.dropna(subset=['geo_latitude', 'geo_longitude']).reset_index(drop=True)
    comp_valid = df_company.dropna(subset=['GEO_LATITUDE', 'GEO_LONGITUDE']).reset_index(drop=True)

    if temp_valid.empty or comp_valid.empty:
        return pd.DataFrame(), pd.DataFrame()

    lat_temp_rad = np.radians(temp_valid['geo_latitude'].values)
    lon_temp_rad = np.radians(temp_valid['geo_longitude'].values)
    lat_comp_rad = np.radians(comp_valid['GEO_LATITUDE'].values)
    lon_comp_rad = np.radians(comp_valid['GEO_LONGITUDE'].values)

    match_records = []
    for i in range(len(comp_valid)):
        lat_c, lon_c = lat_comp_rad[i], lon_comp_rad[i]
        dlat = lat_temp_rad - lat_c
        dlon = lon_temp_rad - lon_c
        a = np.sin(dlat / 2.0)**2 + np.cos(lat_c) * np.cos(lat_temp_rad) * np.sin(dlon / 2.0)**2
        distances = EARTH_RADIUS_METERS * (2 * np.arcsin(np.sqrt(a)))

        within_threshold = np.where(distances <= threshold_meters)[0]
        for temp_idx in within_threshold:
            match_records.append({
                '__comp_idx': i, '__temp_idx': temp_idx,
                'CALCULATED_DISTANCE_METERS': round(distances[temp_idx], 2)
            })

    if not match_records:
        return pd.DataFrame(), pd.DataFrame()

    df_candidates = pd.DataFrame(match_records)
    df_merged = df_candidates.merge(comp_valid, left_on='__comp_idx', right_index=True)
    df_merged = df_merged.merge(temp_valid, left_on='__temp_idx', right_index=True, suffixes=('_comp', '_temp'))
    df_merged.drop(columns=['__comp_idx', '__temp_idx'], inplace=True)

    df_merged['_UPD_NM_CLEAN'] = df_merged['upd_nm_1'].apply(master_text_cleaner)
    df_merged['_CMPNY_NM_CLEAN'] = df_merged['CMPNY_NM'].apply(master_text_cleaner)
    df_merged['FUZZY_SCORE'] = df_merged.apply(
        lambda r: calculate_master_weighted_fuzzy_score(r['_UPD_NM_CLEAN'], r['_CMPNY_NM_CLEAN']), axis=1
    )
    df_merged.drop(columns=['_UPD_NM_CLEAN', '_CMPNY_NM_CLEAN'], inplace=True)
    df_merged['MATCH_STAGE'] = stage_label

    return df_merged[df_merged['FUZZY_SCORE'] >= 75.0].copy(), df_merged[df_merged['FUZZY_SCORE'] < 75.0].copy()

# -------------------------------------------------------------------------
# 3. AI Async Engine
# -------------------------------------------------------------------------
async def call_gemini_api(bp_name, bp_addr, dm_name, dm_addr, semaphore) -> EntityMatchDecision:
    prompt = f"Compare ENTITY 1 (UPD): Name: {bp_name}, Addr: {bp_addr}\nENTITY 2 (CMPNY): Name: {dm_name}, Addr: {dm_addr}"
    system_instruction = (
        "Your task is to determine if the two healthcare entities below refer to the SAME organization, are AFFILIATED, or are DIFFERENT organizations.\n\n"
        "CRITICAL RULES:\n"
        "- NAME VARIATIONS & FUZZY MATCHING: Normalize names by expanding common healthcare abbreviations, removing punctuation, and ignoring case and extra spaces. Treat two names as the SAME entity only if their normalized names have a minimum similarity score of 75% or if they are obvious aliases of the same organization. If similarity is below 75%, do not consider them a name match.\n"
        "- Name is the PRIMARY signal. Verify the legal entity name, DBA (Doing Business As) name, acronyms, and official registered names.\n"
        "- ADDRESS IS NOT DEFINITIVE: Multiple unrelated organizations frequently share the exact same address or suite (e.g., co-working spaces, shared buildings). Therefore, sharing an address alone is NOT proof that they are the same organization, even if the names are slightly similar. You must find stronger, direct evidence to link them.\n"
        "- INDIVIDUALS VS CORPORATIONS: If an entity is clearly named after an individual person (e.g., 'Dr. John Smith Clinic', 'Ahmed Khan MD') and the other entity is a large healthcare organization or hospital system, default to DIFFERENT unless authoritative sources explicitly show they are the same operating entity.\n"
        "- PARENT ORGANIZATION IDENTIFICATION: Using your extensive background healthcare industry knowledge, identify the parent organization or overarching health network (e.g., HCA Healthcare, Ascension, Providence, etc.) for BOTH Entity 1 and Entity 2. Extract this independently for both entities regardless of your 'SAME/AFFILIATED/DIFFERENT' match choice. If an entity is standalone, independent, or its parent system cannot be verified, return null for that entity's parent.\n"
        "- Cross-reference using authoritative sources from your vast historical web knowledge base: Google Search data, CMS Provider Directory (NPPES), official hospital/clinic websites, state health department registries, and healthcare network directories.\n\n"
        "DEFINITIONS FOR CLASSIFICATION:\n"
        "- SAME = Identical legal/operating entity, or a minor linguistic/formatting variant of the exact same organization (e.g., same org, or a specific branch/department of the exact same entity).\n"
        "- AFFILIATED = Part of the same health system or network, but distinct legal or operating entities (e.g., two separate hospitals under one parent healthcare system).\n"
        "- DIFFERENT = Completely unrelated organizations that happen to share a zip code/building, or entities with entirely distinct core names and ownership.\n\n"
        "OUTPUT REQUIREMENT:\n"
        "- When classifying entities as AFFILIATED, identify the parent health system or organization whenever possible and include the parent organization name in the REASON as well as structural parent fields."
    )
    async with semaphore:
        for attempt in range(3):
            if CANCEL_EVENT.is_set():
                return EntityMatchDecision(result="CANCELLED", reason="User cancelled execution", confidence=0)
            try:
                response = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            system_instruction=system_instruction,
                            response_mime_type="application/json",
                            response_schema=EntityMatchDecision,
                            temperature=0.0
                        ),
                    ),
                    timeout=15.0
                )
                return EntityMatchDecision.model_validate_json(response.text)
            except Exception:
                await asyncio.sleep(1 + attempt)
        return EntityMatchDecision(result="ERROR", reason="Failed API calls or timeout", confidence=0)

async def process_batch_async(batch_df, semaphore):
    tasks = [
        call_gemini_api(r.get('upd_nm_1', ''), r.get('upd_addr_1', ''), r.get('CMPNY_NM', ''), r.get('CMPNY_ADDR_1', ''), semaphore)
        for _, r in batch_df.iterrows()
    ]
    return await asyncio.gather(*tasks)

# -------------------------------------------------------------------------
# 4. Database Updater & Excel Exporter
# -------------------------------------------------------------------------
def ensure_required_columns_exist(cursor, target_schema_table, log_func):
    """Checks if MTCH_MTHD and distance columns exist in target table; adds them if missing."""
    try:
        parts = target_schema_table.split('.')
        table_name = parts[-1].replace("[", "").replace("]", "")
        schema_name = parts[-2].replace("[", "").replace("]", "") if len(parts) > 1 else 'dbo'

        # Check MTCH_MTHD
        cursor.execute(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? AND COLUMN_NAME = 'MTCH_MTHD'",
            (schema_name, table_name)
        )
        if cursor.fetchone()[0] == 0:
            log_func(f"Adding column 'MTCH_MTHD' to {target_schema_table}...")
            cursor.execute(f"ALTER TABLE {target_schema_table} ADD MTCH_MTHD VARCHAR(50) NULL")
            cursor.commit()
            log_func("Column 'MTCH_MTHD' added successfully.")

        # Check distance
        cursor.execute(
            "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ? AND COLUMN_NAME = 'distance'",
            (schema_name, table_name)
        )
        if cursor.fetchone()[0] == 0:
            log_func(f"Adding column 'distance' to {target_schema_table}...")
            cursor.execute(f"ALTER TABLE {target_schema_table} ADD distance FLOAT NULL")
            cursor.commit()
            log_func("Column 'distance' added successfully.")
    except Exception as e:
        log_func(f"Note on column check/addition: {str(e)}")

def filter_and_update_db(candidate_matches, target_schema_table, log_func, conn_str):
    """
    Candidate structure: list of dicts/tuples containing:
    (rec_id, cmpny_id, match_type, distance_meters, fuzzy_score, is_hsp)
    Prioritizes:
      1. Decision: SAME (1) > AFFILIATED (2)
      2. HSP Status: HSP (1) > NON-HSP (0)
      3. Distance: Lowest distance first
      4. Fuzzy Score: Highest fuzzy score first
    Updates:
      - enduser_id = cmpny_id
      - MTCH_MTHD = 'DIST_AI'
    """
    if not candidate_matches:
        log_func("No candidates available for DB update.")
        return 0

    df = pd.DataFrame(candidate_matches, columns=['rec_id', 'cmpny_id', 'match_type', 'distance', 'fuzzy_score', 'is_hsp'])
    df['type_priority'] = df['match_type'].map({'SAME': 1, 'AFFILIATED': 2}).fillna(99)
    df['hsp_priority'] = df['is_hsp'].apply(lambda x: 0 if x == 1 else 1) # 0 is better (HSP) than 1 (Non-HSP)
    df['distance'] = df['distance'].fillna(999999.0)
    df['fuzzy_score'] = df['fuzzy_score'].fillna(0.0)

    # Sort hierarchy: type_priority ASC, hsp_priority ASC, distance ASC, fuzzy_score DESC
    df_sorted = df.sort_values(
        by=['type_priority', 'hsp_priority', 'distance', 'fuzzy_score'],
        ascending=[True, True, True, False]
    )

    # Pick top candidate per unique rec_id
    df_deduped = df_sorted.groupby('rec_id', as_index=False).first()
    update_pairs = [
        (str(r.cmpny_id), float(r.distance) if pd.notna(r.distance) and r.distance != 999999.0 else None, str(r.rec_id))
        for _, r in df_deduped.iterrows()
    ]

    sql_conn = pyodbc.connect(conn_str)
    cursor = sql_conn.cursor()

    # Ensure required columns exist before issuing UPDATE statement
    ensure_required_columns_exist(cursor, target_schema_table, log_func)

    cursor.fast_executemany = True

    log_func(f"Updating {len(update_pairs):,} prioritized records in {target_schema_table}...")
    update_sql = f"""
        UPDATE {target_schema_table}
        SET enduser_id = ?,
            distance = ?,
            MTCH_MTHD = 'DIST_AI'
        WHERE rec_id = ? AND enduser_id IS NULL
    """
    cursor.executemany(update_sql, update_pairs)
    sql_conn.commit()
    cursor.close()
    sql_conn.close()
    return len(update_pairs)

def export_results_to_excel(run_dir, unmatched_context_df, log_func):
    o_dir = output_dir()
    o_dir.mkdir(parents=True, exist_ok=True)
    excel_path = os.path.join(o_dir, "dist_ai.xlsx")
    log_func(f"Generating Excel Export: {excel_path}")

    cached_df = get_cached_ai_results(run_dir)
    if cached_df.empty:
        log_func("No AI results found to export.")
        return

    cached_df['rec_id'] = cached_df['rec_id'].astype(str)
    cached_df['cmpny_id'] = cached_df['cmpny_id'].astype(str)

    if not unmatched_context_df.empty:
        unmatched_context_df['rec_id_str'] = unmatched_context_df['rec_id'].astype(str)
        unmatched_context_df['CMPNY_ID_str'] = unmatched_context_df['CMPNY_ID'].astype(str)

        export_df = cached_df.merge(
            unmatched_context_df[['rec_id_str', 'CMPNY_ID_str', 'upd_nm_1', 'upd_addr_1', 'CMPNY_NM', 'CMPNY_ADDR_1', 'FUZZY_SCORE', 'MATCH_STAGE', 'CALCULATED_DISTANCE_METERS']],
            left_on=['rec_id', 'cmpny_id'], right_on=['rec_id_str', 'CMPNY_ID_str'], how='left'
        )
    else:
        export_df = cached_df

    from shared.ui.ui_common import save_dataframe
    excel_path = save_dataframe(export_df, excel_path)
    log_func(f"Excel/CSV report saved: {excel_path} ({len(export_df):,} rows)")

# -------------------------------------------------------------------------
# 5. Pipeline Logic
# -------------------------------------------------------------------------
def get_global_company_data(table_name, sql_query, csv_path, log_func, conn_str):
    if os.path.exists(csv_path):
        log_func(f"[GLOBAL CACHE HIT] Loading '{table_name}' from CSV...")
        return pd.read_csv(csv_path)
    else:
        log_func(f"[GLOBAL CACHE MISS] Fetching '{table_name}' from SQL Server...")
        sql_conn = pyodbc.connect(conn_str)
        df = pd.read_sql_query(sql_query, sql_conn)
        sql_conn.close()
        df.to_csv(csv_path, index=False)
        return df

async def execute_pipeline(target_table, run_dir, log_func, progress_func, conn_str, db, schema):
    CANCEL_EVENT.clear()
    progress_func(0)
    init_run_databases(run_dir)
    log_func("=== 1. Loading Global Company Master Data ===")

    sql_hsp = f"SELECT * FROM SNDBX_PDIDW.CMPNY.COMPANY_MASTER WHERE CMPNY_SGMNT_NM = 'hsp'"
    sql_non_hsp = f"SELECT * FROM SNDBX_PDIDW.CMPNY.COMPANY_MASTER WHERE CMPNY_SGMNT_NM <> 'hsp'"
    df_hsp = get_global_company_data("company_master_hsp", sql_hsp, GLOBAL_HSP_CACHE_CSV, log_func, conn_str)
    df_non_hsp = get_global_company_data("company_master_non_hsp", sql_non_hsp, GLOBAL_NON_HSP_CACHE_CSV, log_func, conn_str)
    progress_func(10)

    if CANCEL_EVENT.is_set():
        log_func("Execution stopped by user.")
        return

    log_func(f"=== 2. Loading Records from DB ({target_table}) ===")
    sql_conn = pyodbc.connect(conn_str)
    sql_temp = f"SELECT rec_id, upd_nm_1, upd_addr_1, upd_addr_2, upd_city, upd_st, upd_zip FROM {target_table} WHERE enduser_id IS NULL"
    df_temp = pd.read_sql_query(sql_temp, sql_conn)
    sql_conn.close()

    if df_temp.empty:
        log_func("No pending records to process.")
        progress_func(100)
        return

    progress_func(20)
    log_func("=== 3. Geocoding with Local Run Cache ===")
    df_temp['FULL_ADDR'] = (
        df_temp['upd_addr_1'].fillna('').astype(str).str.strip() + " " +
        df_temp['upd_city'].fillna('').astype(str).str.strip() + ", " +
        df_temp['upd_st'].fillna('').astype(str).str.strip() + " " +
        df_temp['upd_zip'].fillna('').astype(str).str.strip()
    ).str.strip(", ")

    existing_geo_cache = load_geocode_cache(run_dir)
    new_geo_data = {}

    unique_addrs = df_temp['FULL_ADDR'].replace("", None).dropna().unique()
    total_unique = len(unique_addrs)
    log_func(f"Total Unique Addresses: {total_unique:,} (Cached: {len(existing_geo_cache):,})")

    for idx, addr in enumerate(unique_addrs, start=1):
        if CANCEL_EVENT.is_set():
            log_func("Geocoding aborted by user.")
            save_geocode_cache(run_dir, new_geo_data)
            return

        if addr in existing_geo_cache:
            log_func(f"[Geocode {idx}/{total_unique}] CACHED: {addr[:45]}...")
        else:
            try:
                loc = geolocator.geocode(addr, timeout=5)
                coords = (loc.latitude, loc.longitude) if loc else (None, None)
                log_func(f"[Geocode {idx}/{total_unique}] FETCHED: {addr[:45]}...")
            except Exception:
                coords = (None, None)
                log_func(f"[Geocode {idx}/{total_unique}] FAILED: {addr[:45]}...")
            existing_geo_cache[addr] = coords
            new_geo_data[addr] = coords
            time.sleep(0.04)

        if total_unique > 0:
            progress_func(20 + int((idx / total_unique) * 30))

    save_geocode_cache(run_dir, new_geo_data)

    df_temp['geo_latitude'] = df_temp['FULL_ADDR'].map(lambda x: existing_geo_cache.get(x, (None, None))[0])
    df_temp['geo_longitude'] = df_temp['FULL_ADDR'].map(lambda x: existing_geo_cache.get(x, (None, None))[1])

    progress_func(55)
    log_func("=== 4. Spatial Fuzzy Matching ===")
    ge75_hsp, lt75_hsp = run_spatial_fuzzy_match_split(df_temp, df_hsp, 200.0, "HSP_200M")
    matched_ids = set()
    if not ge75_hsp.empty:
        rec_ids = ge75_hsp['rec_id']
        matched_ids = set(pd.Series(rec_ids).dropna().unique())

    df_remaining = df_temp[~df_temp['rec_id'].isin(list(matched_ids))].copy()
    ge75_non_hsp, lt75_non_hsp = run_spatial_fuzzy_match_split(df_remaining, df_non_hsp, 30.0, "NON_HSP_30M")

    progress_func(65)
    log_func("=== 5. Updating DB for Direct Fuzzy Matches (>= 75%) ===")
    final_matched = pd.concat([ge75_hsp, ge75_non_hsp], ignore_index=True)

    if not final_matched.empty:
        fuzzy_excel_path = os.path.join(output_dir(), "dist_fuzzy.ai.xlsx")
        from shared.ui.ui_common import save_dataframe
        fuzzy_excel_path = save_dataframe(final_matched, fuzzy_excel_path)
        log_func(f"Fuzzy matches exported to: {fuzzy_excel_path}")

    fuzzy_candidates = [
        (
            r.rec_id,
            r.CMPNY_ID,
            'SAME',
            r.get('CALCULATED_DISTANCE_METERS', 0.0),
            r.get('FUZZY_SCORE', 0.0),
            1 if str(r.get('MATCH_STAGE', '')).startswith('HSP') else 0
        )
        for _, r in final_matched.iterrows()
    ]
    updated_fuzzy = filter_and_update_db(fuzzy_candidates, target_table, log_func, conn_str)
    log_func(f"Direct Matches Updated: {updated_fuzzy:,}")

    progress_func(70)
    log_func("=== 6. Processing AI Engine Evaluation ===")
    unmatched_df = pd.concat([lt75_hsp, lt75_non_hsp], ignore_index=True)
    if not unmatched_df.empty:
        cached_ai = get_cached_ai_results(run_dir)
        ai_candidates = []

        if not cached_ai.empty:
            cached_ai['rec_id'] = cached_ai['rec_id'].astype(str)
            cached_ai['cmpny_id'] = cached_ai['cmpny_id'].astype(str)
            unmatched_df['rec_id_str'] = unmatched_df['rec_id'].astype(str)
            unmatched_df['CMPNY_ID_str'] = unmatched_df['CMPNY_ID'].astype(str)

            merged = unmatched_df.merge(
                cached_ai[['rec_id', 'cmpny_id', 'result']],
                left_on=['rec_id_str', 'CMPNY_ID_str'],
                right_on=['rec_id', 'cmpny_id'],
                how='left'
            )
            unprocessed_df = merged[merged['result'].isna()].copy()
            processed_df = merged[merged['result'].notna()].copy()

            for _, r in processed_df.iterrows():
                if r['result'] in ['SAME', 'AFFILIATED']:
                    ai_candidates.append((
                        r['rec_id_str'],
                        r['CMPNY_ID_str'],
                        r['result'],
                        r.get('CALCULATED_DISTANCE_METERS', 0.0),
                        r.get('FUZZY_SCORE', 0.0),
                        1 if str(r.get('MATCH_STAGE', '')).startswith('HSP') else 0
                    ))
        else:
            unprocessed_df = unmatched_df.copy()

        if not unprocessed_df.empty:
            total_unproc = len(unprocessed_df)
            log_func(f"Found {total_unproc:,} pairs requiring AI classification.")

            ai_semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)

            for i in range(0, total_unproc, BATCH_SIZE):
                if CANCEL_EVENT.is_set():
                    log_func("AI execution stopped by user. Progress saved in cache.")
                    break

                batch_df = unprocessed_df.iloc[i:i + BATCH_SIZE]
                current_batch_end = min(i + BATCH_SIZE, total_unproc)
                log_func(f"AI Batch {i+1} to {current_batch_end} of {total_unproc}...")

                results = await process_batch_async(batch_df, ai_semaphore)

                cache_to_save = []
                for idx, res in enumerate(results):
                    row = batch_df.iloc[idx]
                    rec_id_str, cmpny_id_str = str(row['rec_id']), str(row['CMPNY_ID'])
                    cache_to_save.append((
                        rec_id_str, cmpny_id_str, res.result, res.reason, res.confidence, res.entity_1_parent, res.entity_2_parent
                    ))
                    if res.result in ['SAME', 'AFFILIATED']:
                        ai_candidates.append((
                            rec_id_str,
                            cmpny_id_str,
                            res.result,
                            row.get('CALCULATED_DISTANCE_METERS', 0.0),
                            row.get('FUZZY_SCORE', 0.0),
                            1 if str(row.get('MATCH_STAGE', '')).startswith('HSP') else 0
                        ))

                save_batch_to_ai_cache(run_dir, cache_to_save)
                progress_func(70 + int((current_batch_end / total_unproc) * 25))

        updated_ai = filter_and_update_db(ai_candidates, target_table, log_func, conn_str)
        log_func(f"AI Matches Updated: {updated_ai:,}")
        export_results_to_excel(run_dir, unmatched_df, log_func)

    progress_func(100)
    log_func("=== Execution Finished ===")
