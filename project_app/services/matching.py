"""Data Matching core — fuzzy scoring, tasks, SQL helpers."""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any, Dict, List, Optional

import pandas as pd
import pyodbc

SYNONYM_MAP = {
    "KIDS": {"PEDIATRIC", "PEDIATRICS", "CHILDREN", "CHILDRENS", "PED"},
    "PEDIATRIC": {"KIDS", "PEDIATRICS", "CHILDREN", "CHILDRENS", "PED"},
    "PEDIATRICS": {"KIDS", "PEDIATRIC", "CHILDREN", "CHILDRENS", "PED"},
    "CHILDREN": {"KIDS", "PEDIATRIC", "PEDIATRICS", "PED"},
    "ORTHOPEDIC": {"ORTHOPEDICS", "ORTHO"},
    "ORTHOPEDICS": {"ORTHOPEDIC", "ORTHO"},
    "HOSP": {"HOSPITAL", "MEDICAL"},
    "HOSPITAL": {"HOSP", "MEDICAL"},
    "CTR": {"CENTER", "CENTRE"},
    "CENTER": {"CTR", "CENTRE"},
    "DOC": {"DOCTOR", "PHYSICIAN"},
    "DOCTOR": {"DOC", "PHYSICIAN"},
    "PHYSICIAN": {"DOCTOR", "DOC"},
    "CLIN": {"CLINIC"},
    "CLINIC": {"CLIN"},
}

def clean_str(value):
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    val_str = str(value).strip()
    return "" if val_str.lower() == "nan" else val_str

@lru_cache(maxsize=500_000)
def are_words_matching(w1, w2, similarity_threshold=0.82):
    w1_u, w2_u = w1.upper(), w2.upper()
    if w1_u == w2_u:
        return True
    if w1_u in SYNONYM_MAP and w2_u in SYNONYM_MAP[w1_u]:
        return True
    if min(len(w1_u), len(w2_u)) >= 4:
        ratio = SequenceMatcher(None, w1_u, w2_u).ratio()
        if ratio >= similarity_threshold:
            return True
    return False

def normalize_match_text(value):
    val_str = clean_str(value)
    if not val_str:
        return ""
    text = re.sub(r"[^A-Z0-9]+", " ", val_str.upper())
    return re.sub(r"\s+", " ", text).strip()

def get_common_word_matches(text_a, text_b):
    tokens_a = re.findall(r"\b\w+\b", normalize_match_text(text_a))
    tokens_b = re.findall(r"\b\w+\b", normalize_match_text(text_b))
    matches_a, matches_b = set(), set()
    for t_a in tokens_a:
        for t_b in tokens_b:
            if are_words_matching(t_a, t_b):
                matches_a.add(t_a.upper())
                matches_b.add(t_b.upper())
    return matches_a, matches_b

def build_highlight_segments(text, match_set):
    """Return list of (substring, is_highlighted) covering the whole text,
    for rendering into a Tk Text widget with a highlight tag."""
    segments = []
    last_end = 0
    for m in re.finditer(r"\b\w+\b", text):
        word = m.group(0)
        if word.upper() in match_set:
            if m.start() > last_end:
                segments.append((text[last_end:m.start()], False))
            segments.append((word, True))
            last_end = m.end()
    if last_end < len(text):
        segments.append((text[last_end:], False))
    if not segments:
        segments = [(text, False)]
    return segments

def score_and_highlight(text_a, text_b):
    str_a = clean_str(text_a)
    str_b = clean_str(text_b)

    norm_a = normalize_match_text(str_a)
    norm_b = normalize_match_text(str_b)

    if not norm_a or not norm_b:
        return 0.0, False, [(str_a, False)], [(str_b, False)]

    matches_a, matches_b = get_common_word_matches(norm_a, norm_b)
    if not matches_a or not matches_b:
        return 0.0, False, [(str_a, False)], [(str_b, False)]

    tokens_a = set(re.findall(r"\b\w+\b", norm_a))
    tokens_b = set(re.findall(r"\b\w+\b", norm_b))

    intersection = len(matches_a)
    union = len(tokens_a.union(tokens_b))
    token_overlap = intersection / union if union > 0 else 0.0

    fuzzy_ratio = SequenceMatcher(None, norm_a, norm_b).ratio()
    score = round(((token_overlap * 0.7) + (fuzzy_ratio * 0.3)) * 100, 2)

    seg_a = build_highlight_segments(str_a, matches_a)
    seg_b = build_highlight_segments(str_b, matches_b)
    return score, True, seg_a, seg_b

def analyze_candidate_row(row):
    name_score, name_has_sim, seg_orig_nm, seg_cm_nm = score_and_highlight(
        row.get("UPD_NM_1", ""), row.get("CMPNY_NM", "")
    )
    addr_score, addr_has_sim, seg_orig_addr, seg_cm_addr = score_and_highlight(
        row.get("UPD_ADDR_1", ""), row.get("CMPNY_ADDR_1", "")
    )

    has_similarity = name_has_sim or addr_has_sim
    combined_score = round((name_score * 0.6) + (addr_score * 0.4), 2) if has_similarity else 0.0

    return {
        "_similarity_score": combined_score,
        "_has_similarity": has_similarity,
        "_name_score": name_score,
        "_addr_score": addr_score,
        "_seg_orig_nm": seg_orig_nm,
        "_seg_cm_nm": seg_cm_nm,
        "_seg_orig_addr": seg_orig_addr,
        "_seg_cm_addr": seg_cm_addr,
    }

ANALYSIS_COLUMNS = [
    "_similarity_score", "_has_similarity", "_name_score", "_addr_score",
    "_seg_orig_nm", "_seg_cm_nm", "_seg_orig_addr", "_seg_cm_addr",
]

def build_similarity_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        out = df.copy()
        for col in ANALYSIS_COLUMNS:
            out[col] = pd.Series(dtype="object")
        return out

    records = df.to_dict("records")
    analysis = [analyze_candidate_row(r) for r in records]
    analysis_df = pd.DataFrame(analysis, index=df.index)

    out = pd.concat([df, analysis_df], axis=1)
    out = out.sort_values(
        by=["_similarity_score", "REC_ID"], ascending=[False, True], kind="mergesort"
    ).reset_index(drop=True)
    return out



def sanitize_identifier(identifier: str) -> str:
    if not identifier:
        return ""
    cleaned = identifier.replace("[", "").replace("]", "").strip()
    if not re.match(r"^[a-zA-Z0-9_]+$", cleaned):
        raise ValueError(f"Invalid characters detected in database object name: '{identifier}'")
    return cleaned

def sql_table(database: str, schema: str, table: str) -> str:
    safe_db = sanitize_identifier(database)
    safe_schema = sanitize_identifier(schema)
    safe_table = sanitize_identifier(table)
    return f"[{safe_db}].[{safe_schema}].[{safe_table}]"

def get_installed_sql_drivers():
    """Prefer modern ODBC drivers (17/18); fall back to legacy SQL Server."""
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
    ordered = [d for d in preferred if d in available]
    if ordered:
        return ordered
    
    extras = [d for d in available if "SQL" in d]
    return extras if extras else ["SQL Server"]

TASKS = {
    "Task 1: Match by HIN": {
        "select": lambda db, schema: f"""
            SELECT T.REC_ID, T.UPD_NM_1, T.UPD_ADDR_1, T.UPD_CITY, T.UPD_ST, T.UPD_ZIP,
                   CM.CMPNY_ID, CM.CMPNY_NM, CM.CMPNY_ADDR_1, CM.CMPNY_CITY, CM.CMPNY_ST,
                   T.HIN_NR, C.HIN_NR AS CM_HIN_NR
            FROM {sql_table(db, schema, 'TEMP_NAME_ADDR_PRCS')} T
            JOIN SNDBX_PDIDW.CMPNY.COMPANY C
                ON T.HIN_NR = C.HIN_NR
            JOIN SNDBX_PDIDW.CMPNY.COMPANY_MASTER CM
                ON CONVERT(NVARCHAR(255), C.CMPNY_ID) = CONVERT(NVARCHAR(255), CM.CMPNY_ID)
            WHERE LEN(TRIM(ISNULL(T.HIN_NR, ''))) > 0
              AND ISNULL(T.ENDUSER_ID, 0) = 0
              AND UPPER(LTRIM(RTRIM(C.CMPNY_NM))) = UPPER(LTRIM(RTRIM(CM.CMPNY_NM)));
        """,
        "update": lambda db, schema: f"UPDATE T SET T.ENDUSER_ID = ? FROM {sql_table(db, schema, 'TEMP_NAME_ADDR_PRCS')} T WHERE T.REC_ID = ?;",
    },
    "Task 2: Match by GPM EU XREF": {
        "select": lambda db, schema: f"""
            SELECT T.REC_ID, T.MMBR_ID, T.MMBR_ALT_ID, T.UPD_NM_1, T.UPD_ADDR_1, T.UPD_CITY,
                   T.UPD_ST, T.UPD_ZIP, T.ENDUSER_ID,
                   EX.EU_ID AS CMPNY_ID, CM.CMPNY_NM, CM.CMPNY_ADDR_1, CM.CMPNY_CITY
            FROM {sql_table(db, schema, 'TEMP_NAME_ADDR_PRCS')} T
            JOIN SNDBX_PDIDW.GPR.GPM_EU_XREF EX
                ON CONVERT(VARCHAR(50), T.MMBR_ID) = EX.GPO_MEMBER_ID
                AND ISNULL(NULLIF(CONVERT(VARCHAR(250), T.MMBR_ALT_ID), ''), 'dflt') = ISNULL(NULLIF(EX.GPO_MEMBER_ALT_ID, ''), 'dflt')
                AND EX.REC_STAT_CD = 'A'
                AND EX.VRFD_IN = 'Y'
                AND EX.GPO_CMPNY_ID = ?
            JOIN SNDBX_PDIDW.CMPNY.COMPANY_MASTER CM
                ON CONVERT(NVARCHAR(255), EX.EU_ID) = CM.CMPNY_ID
            WHERE T.ENDUSER_ID IS NULL;
        """,
        "update": lambda db, schema: f"UPDATE T SET T.ENDUSER_ID = ? FROM {sql_table(db, schema, 'TEMP_NAME_ADDR_PRCS')} T WHERE T.REC_ID = ?;",
    },
    "Task 3: Match by Exact Name and Address": {
        "select": lambda db, schema: f"""
            SELECT T.REC_ID, CM.CMPNY_ID, T.MMBR_ID, T.UPD_NM_1, T.UPD_ADDR_1, T.UPD_CITY, T.UPD_ST, T.UPD_ZIP,
                   CM.CMPNY_NM, CM.CMPNY_ADDR_1, CM.CMPNY_CITY, CM.CMPNY_ST, CM.L2_IDN_NAME
            FROM {sql_table(db, schema, 'TEMP_NAME_ADDR_PRCS')} T
            JOIN SNDBX_PDIDW.CMPNY.COMPANY_MASTER CM ON T.UPD_ADDR_1 = CM.CMPNY_ADDR_1 AND T.UPD_ZIP = CM.CMPNY_ZIP
            AND T.UPD_NM_1 = CM.CMPNY_NM AND ISNULL(T.ENDUSER_ID ,0) = 0;
        """,
        "update": lambda db, schema: f"UPDATE T SET T.ENDUSER_ID = ? FROM {sql_table(db, schema, 'TEMP_NAME_ADDR_PRCS')} T WHERE T.REC_ID = ?;",
    },
    "Task 3a: Exact Address, Name >3 words, Score ≥67": {
        "select": lambda db, schema: f"""
            SELECT T.REC_ID, CM.CMPNY_ID, CM.L2_IDN_NAME, T.MMBR_ID, T.UPD_NM_1, CM.CMPNY_NM,
                   T.UPD_ADDR_1, CM.CMPNY_ADDR_1, T.UPD_CITY, T.UPD_ST, T.UPD_ZIP, CM.CMPNY_CITY, CM.CMPNY_ST
            FROM {sql_table(db, schema, 'TEMP_NAME_ADDR_PRCS')} T
            JOIN SNDBX_PDIDW.CMPNY.COMPANY_MASTER CM
                ON T.UPD_ADDR_1 = CM.CMPNY_ADDR_1
               AND T.UPD_ZIP = CM.CMPNY_ZIP
            WHERE ISNULL(T.ENDUSER_ID, 0) = 0
              AND (
                    CASE
                        WHEN NULLIF(LTRIM(RTRIM(ISNULL(T.UPD_NM_1, ''))), '') IS NULL THEN 0
                        ELSE LEN(LTRIM(RTRIM(T.UPD_NM_1)))
                             - LEN(REPLACE(LTRIM(RTRIM(T.UPD_NM_1)), ' ', ''))
                             + 1
                    END
                  ) > 3
              AND SNDBX_PDIDW.STAGE.WORD_MATCH_SCORE(T.UPD_NM_1, CM.CMPNY_NM) >= 67;
        """,
        "update": lambda db, schema: f"UPDATE T SET T.ENDUSER_ID = ? FROM {sql_table(db, schema, 'TEMP_NAME_ADDR_PRCS')} T WHERE T.REC_ID = ?;",
    },
    "Task 3b: Exact Address, Name ≤3 words, Score ≥80": {
        "select": lambda db, schema: f"""
            SELECT T.REC_ID, CM.CMPNY_ID, CM.L2_IDN_NAME, T.MMBR_ID, T.UPD_NM_1, CM.CMPNY_NM,
                   T.UPD_ADDR_1, CM.CMPNY_ADDR_1, T.UPD_CITY, T.UPD_ST, T.UPD_ZIP, CM.CMPNY_CITY, CM.CMPNY_ST
            FROM {sql_table(db, schema, 'TEMP_NAME_ADDR_PRCS')} T
            JOIN SNDBX_PDIDW.CMPNY.COMPANY_MASTER CM
                ON T.UPD_ADDR_1 = CM.CMPNY_ADDR_1
               AND T.UPD_ZIP = CM.CMPNY_ZIP
            WHERE ISNULL(T.ENDUSER_ID, 0) = 0
              AND (
                    CASE
                        WHEN NULLIF(LTRIM(RTRIM(ISNULL(T.UPD_NM_1, ''))), '') IS NULL THEN 0
                        ELSE LEN(LTRIM(RTRIM(T.UPD_NM_1)))
                             - LEN(REPLACE(LTRIM(RTRIM(T.UPD_NM_1)), ' ', ''))
                             + 1
                    END
                  ) <= 3
              AND SNDBX_PDIDW.STAGE.WORD_MATCH_SCORE(T.UPD_NM_1, CM.CMPNY_NM) >= 80;
        """,
        "update": lambda db, schema: f"UPDATE T SET T.ENDUSER_ID = ? FROM {sql_table(db, schema, 'TEMP_NAME_ADDR_PRCS')} T WHERE T.REC_ID = ?;",
    },
    "Task 4: Match by Exact Address 1 & 2": {
        "select": lambda db, schema: f"""
            SELECT T.REC_ID, CM.CMPNY_ID, T.MMBR_ID, T.UPD_NM_1, T.UPD_ADDR_1, T.UPD_ADDR_2, T.UPD_CITY, T.UPD_ST, T.UPD_ZIP,
                   CM.CMPNY_NM, CM.CMPNY_ADDR_1, CM.CMPNY_ADDR_2, CM.CMPNY_CITY, CM.CMPNY_ST, CM.L2_IDN_NAME
            FROM {sql_table(db, schema, 'TEMP_NAME_ADDR_PRCS')} T
            JOIN SNDBX_PDIDW.CMPNY.COMPANY_MASTER CM
                ON T.UPD_ADDR_1 = CM.CMPNY_ADDR_1
               AND T.UPD_ZIP = CM.CMPNY_ZIP
               AND T.UPD_ADDR_2 = CM.CMPNY_ADDR_2
            WHERE LEN(TRIM(ISNULL(T.UPD_ADDR_2, ''))) > 0
              AND ISNULL(T.ENDUSER_ID, 0) = 0;
        """,
        "update": lambda db, schema: f"UPDATE T SET T.ENDUSER_ID = ? FROM {sql_table(db, schema, 'TEMP_NAME_ADDR_PRCS')} T WHERE T.REC_ID = ?;",
    },
    "Task 5: Match Score (> 75)": {
        "select": lambda db, schema: f"""
            SELECT T.REC_ID, CM.CMPNY_ID, CM.L2_IDN_NAME, T.UPD_NM_1, CM.CMPNY_NM, T.UPD_ADDR_1, CM.CMPNY_ADDR_1,
                   T.UPD_CITY, T.UPD_ST, T.UPD_ZIP, CM.CMPNY_CITY, CM.CMPNY_ST
            FROM {sql_table(db, schema, 'TEMP_NAME_ADDR_PRCS')} T
            JOIN SNDBX_PDIDW.CMPNY.COMPANY_MASTER CM ON T.UPD_ADDR_1 = CM.CMPNY_ADDR_1
            AND LEFT(T.UPD_ZIP,3) = LEFT(CM.CMPNY_ZIP,3)
            AND SNDBX_PDIDW.STAGE.WORD_MATCH_SCORE(T.UPD_NM_1, CM.CMPNY_NM) > 75
            WHERE ISNULL(T.ENDUSER_ID,0) = 0;
        """,
        "update": lambda db, schema: f"UPDATE T SET T.ENDUSER_ID = ? FROM {sql_table(db, schema, 'TEMP_NAME_ADDR_PRCS')} T WHERE T.REC_ID = ?;",
    },
    "Task 6: Match Score (Between 67 and 75)": {
        "select": lambda db, schema: f"""
            SELECT T.REC_ID, CM.CMPNY_ID, CM.L2_IDN_NAME, T.UPD_NM_1, CM.CMPNY_NM, T.UPD_ADDR_1, CM.CMPNY_ADDR_1,
                   T.UPD_CITY, T.UPD_ST, T.UPD_ZIP, CM.CMPNY_CITY, CM.CMPNY_ST
            FROM {sql_table(db, schema, 'TEMP_NAME_ADDR_PRCS')} T
            JOIN SNDBX_PDIDW.CMPNY.COMPANY_MASTER CM ON T.UPD_ADDR_1 = CM.CMPNY_ADDR_1
            AND LEFT(T.UPD_ZIP,3) = LEFT(CM.CMPNY_ZIP,3)
            AND SNDBX_PDIDW.STAGE.WORD_MATCH_SCORE(T.UPD_NM_1, CM.CMPNY_NM) BETWEEN 67 AND 75
            WHERE ISNULL(T.ENDUSER_ID,0) = 0;
        """,
        "update": lambda db, schema: f"UPDATE T SET T.ENDUSER_ID = ? FROM {sql_table(db, schema, 'TEMP_NAME_ADDR_PRCS')} T WHERE T.REC_ID = ?;",
    },
    "Task 7: Match Score (Between 50 and 66)": {
        "select": lambda db, schema: f"""
            SELECT T.REC_ID, CM.CMPNY_ID, CM.L2_IDN_NAME, T.MMBR_ID, T.UPD_NM_1, CM.CMPNY_NM, T.UPD_ADDR_1, CM.CMPNY_ADDR_1,
                   T.UPD_CITY, T.UPD_ST, T.UPD_ZIP, CM.CMPNY_CITY, CM.CMPNY_ST
            FROM {sql_table(db, schema, 'TEMP_NAME_ADDR_PRCS')} T
            JOIN SNDBX_PDIDW.CMPNY.COMPANY_MASTER CM ON T.UPD_ADDR_1 = CM.CMPNY_ADDR_1
            AND LEFT(T.UPD_ZIP,3) = LEFT(CM.CMPNY_ZIP,3)
            AND SNDBX_PDIDW.STAGE.WORD_MATCH_SCORE(T.UPD_NM_1, CM.CMPNY_NM) BETWEEN 50 AND 66
            WHERE ISNULL(T.ENDUSER_ID,0) = 0;
        """,
        "update": lambda db, schema: f"UPDATE T SET T.ENDUSER_ID = ? FROM {sql_table(db, schema, 'TEMP_NAME_ADDR_PRCS')} T WHERE T.REC_ID = ?;",
    },
}

TASK_KEYS = list(TASKS.keys())

AUTO_MATCH_TASK_KEYS = [
    k for k in TASK_KEYS
    if k.startswith(("Task 4:", "Task 5:", "Task 6:", "Task 7:"))
]

STAGING_INSERT_BATCH_SIZE = 5000
SORT_OPTIONS = {"Similarity: High to Low": False, "Similarity: Low to High": True}
PAGE_SIZE_OPTIONS = [10, 25, 50, 100]

def build_pyodbc_conn_str(
    server: str,
    database: str,
    username: str = "",
    password: str = "",
    auth_mode: str = "sql",
    trust_server_certificate: bool = True,
    encrypt: bool = False,
    driver: Optional[str] = None,
) -> str:
    """
    Build ODBC connection string.
    ODBC Driver 18 defaults to Encrypt=yes and fails on untrusted certs unless
    TrustServerCertificate=yes. Legacy "SQL Server" driver rejects those keywords.
    """
    drivers = get_installed_sql_drivers()
    d = (driver or (drivers[0] if drivers else "SQL Server")).strip()
    d_bare = d.strip("{}")
    d_braced = "{" + d_bare + "}"

    db = sanitize_identifier(database)
    modern = d_bare.startswith("ODBC Driver")

    parts = [
        f"DRIVER={d_braced}",
        f"SERVER={server}",
        f"DATABASE={db}",
    ]
    if auth_mode == "windows":
        parts.append("Trusted_Connection=yes")
    else:
        parts.append(f"UID={username}")
        parts.append(f"PWD={password}")

    if modern:
        
        parts.append(f"Encrypt={'yes' if encrypt else 'no'}")
        parts.append(f"TrustServerCertificate={'yes' if trust_server_certificate else 'no'}")
    

    parts.append("Network=DBMSSOCN")
    return ";".join(parts) + ";"

def run_match_query(
    server, database, username, password, query, params=None,
    auth_mode="sql", trust_server_certificate=True, encrypt=False,
):
    conn_str = build_pyodbc_conn_str(
        server, database, username, password, auth_mode,
        trust_server_certificate, encrypt,
    )
    conn = pyodbc.connect(conn_str)
    try:
        return pd.read_sql(query, conn, params=params)
    finally:
        conn.close()

def ensure_mtch_mthd_column(cursor, database: str, schema: str, log=None) -> None:
    """Add MTCH_MTHD column to TEMP_NAME_ADDR_PRCS if it does not already exist.

    Safe to call repeatedly: checks INFORMATION_SCHEMA before ALTER TABLE.
    Column is NVARCHAR(20) NULL so existing rows are unaffected and future
    method codes longer than NAD_AI / NAD_MN still fit.
    """
    log = log or (lambda m: None)
    safe_db = sanitize_identifier(database)
    safe_schema = sanitize_identifier(schema)
    cursor.execute(
        """
        SELECT 1
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_CATALOG = ?
          AND TABLE_SCHEMA = ?
          AND TABLE_NAME = 'TEMP_NAME_ADDR_PRCS'
          AND COLUMN_NAME = 'MTCH_MTHD';
        """,
        (safe_db, safe_schema),
    )
    if cursor.fetchone() is not None:
        return
    target = sql_table(database, schema, "TEMP_NAME_ADDR_PRCS")
    log(f"Adding MTCH_MTHD column to {target}…")
    cursor.execute(
        f"ALTER TABLE {target} ADD MTCH_MTHD NVARCHAR(20) NULL;"
    )

def commit_matches(
    server, database, username, password, schema, update_rows,
    auth_mode="sql", trust_server_certificate=True, encrypt=False,
    log=None, progress=None, match_method: str = "NAD_MN",
):
    """update_rows: list of (cmpny_id, rec_id). Writes ENDUSER_ID + MTCH_MTHD via #MatchUpdates staging.

    match_method:
      - "NAD_AI" when ENDUSER_ID is set by Gemini Auto-Match
      - "NAD_MN" for manual apply (default)
      - any other non-empty code for downstream / future processes
      Blank or None falls back to "NAD_MN".
    """
    log = log or (lambda m: None)
    progress = progress or (lambda p: None)
    
    method = (match_method or "").strip().upper() or "NAD_MN"

    conn_str = build_pyodbc_conn_str(
        server, database, username, password, auth_mode,
        trust_server_certificate, encrypt,
    )
    target = sql_table(database, schema, "TEMP_NAME_ADDR_PRCS")
    total = len(update_rows)
    if total == 0:
        log("No valid rows left to save.")
        return 0

    conn = pyodbc.connect(conn_str)
    conn.autocommit = False
    try:
        cursor = conn.cursor()
        try:
            cursor.fast_executemany = True
        except Exception:
            pass

        
        ensure_mtch_mthd_column(cursor, database, schema, log=log)

        cursor.execute("""
            CREATE TABLE #MatchUpdates (
                CMPNY_ID BIGINT NOT NULL,
                REC_ID BIGINT NOT NULL PRIMARY KEY
            );
        """)
        processed = 0
        for start in range(0, total, STAGING_INSERT_BATCH_SIZE):
            batch = update_rows[start:start + STAGING_INSERT_BATCH_SIZE]
            cursor.executemany(
                "INSERT INTO #MatchUpdates (CMPNY_ID, REC_ID) VALUES (?, ?);", batch
            )
            processed += len(batch)
            log(f"Progress: {processed} of {total}…")
            progress(min(processed / total, 0.85))

        log(f"Writing matches (MTCH_MTHD={method})…")
        cursor.execute(
            f"""
            UPDATE T
            SET T.ENDUSER_ID = S.CMPNY_ID,
                T.MTCH_MTHD  = ?
            FROM {target} T
            INNER JOIN #MatchUpdates S
                ON CAST(T.REC_ID AS BIGINT) = S.REC_ID;
            """,
            (method,),
        )
        rows_affected = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else processed
        conn.commit()
        progress(1.0)
        log(f"Done — {rows_affected} record(s) linked successfully (method={method}).")
        return rows_affected
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            c2 = conn.cursor()
            c2.execute("DROP TABLE IF EXISTS #MatchUpdates;")
            conn.commit()
        except Exception:
            pass
        conn.close()
