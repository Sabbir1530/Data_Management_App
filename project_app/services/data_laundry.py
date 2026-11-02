"""Data Laundry pipeline — matches cleaning_temp.sql step for step."""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

import pandas as pd
import pyodbc

try:
    from uszipcode import SearchEngine
    _zip_search = SearchEngine()
except Exception:
    _zip_search = None

CANADIAN_PROVINCES = {
    "AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"
}

LogFn = Callable[[str], None]

def verify_and_fix_city_state_zip(city, state, zip_code):
    clean_city = str(city).strip().upper() if pd.notna(city) else ""
    clean_state = str(state).strip().upper() if pd.notna(state) else ""
    clean_zip = str(zip_code).strip().upper() if pd.notna(zip_code) else ""

    if clean_city in ("NAN", "NONE", "NULL"):
        clean_city = ""
    if clean_state in ("NAN", "NONE", "NULL"):
        clean_state = ""
    if clean_zip in ("NAN", "NONE", "NULL"):
        clean_zip = ""

    if clean_city == "EVANSTON" and not clean_state:
        clean_state = "IL"
    elif clean_city == "LITTLE ROCK" and clean_state == "AK":
        clean_state = "AR"

    if clean_state in CANADIAN_PROVINCES:
        return clean_city, clean_state, clean_zip

    clean_zip_num = clean_zip.split("-")[0].split(".")[0]
    if clean_zip_num.isdigit() and len(clean_zip_num) < 5:
        clean_zip_num = clean_zip_num.zfill(5)

    if _zip_search:
        try:
            if not clean_zip_num:
                if clean_city and clean_state:
                    res = _zip_search.by_city_and_state(city=clean_city, state=clean_state, returns=1)
                    if res and res[0].zipcode:
                        return res[0].major_city.upper(), clean_state, str(res[0].zipcode).zfill(5)
                return clean_city, clean_state, ""

            res = _zip_search.by_zipcode(clean_zip_num)
            if res and res.zipcode:
                official_state = res.state.upper() if res.state else clean_state
                official_city = res.major_city.upper() if res.major_city else clean_city
                if clean_state == official_state:
                    return official_city, clean_state, clean_zip_num

            city_res = _zip_search.by_city_and_state(city=clean_city, state=clean_state, returns=1)
            if city_res and city_res[0].zipcode:
                fixed_zip = str(city_res[0].zipcode).zfill(5)
                official_city = city_res[0].major_city.upper() if city_res[0].major_city else clean_city
                return official_city, clean_state, fixed_zip
        except Exception:
            
            
            pass

    return clean_city, clean_state, clean_zip_num

def sanitize_identifier(identifier: str) -> str:
    if not identifier:
        return ""
    cleaned = identifier.replace("[", "").replace("]", "").strip()
    if not re.match(r"^[a-zA-Z0-9_]+$", cleaned):
        raise ValueError(f"Invalid character in database object: '{identifier}'")
    return cleaned

def sql_table(db: str, schema: str, table: str) -> str:
    """Three-part name: [db].[schema].[table]  (matches place_holder_db.[place_holder_schema].[...])."""
    return f"[{sanitize_identifier(db)}].[{sanitize_identifier(schema)}].[{sanitize_identifier(table)}]"

def build_pyodbc_conn_str(
    server: str,
    database: str,
    username: str,
    password: str,
    auth_mode: str = "sql",
    trust_server_certificate: bool = True,
    encrypt: bool = False,
) -> str:
    driver = "{SQL Server}"
    try:
        for d in ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server", "SQL Server"):
            if d in pyodbc.drivers():
                driver = "{" + d + "}"
                break
    except Exception:
        pass

    encrypt_flag = "yes" if encrypt else "no"
    trust_flag = "yes" if trust_server_certificate else "no"
    extra = f"Encrypt={encrypt_flag};TrustServerCertificate={trust_flag};"

    if auth_mode == "windows":
        return (
            f"DRIVER={driver};SERVER={server};DATABASE={sanitize_identifier(database)};"
            f"Trusted_Connection=yes;{extra}Network=DBMSSOCN;"
        )
    return (
        f"DRIVER={driver};SERVER={server};DATABASE={sanitize_identifier(database)};"
        f"UID={username};PWD={password};{extra}Network=DBMSSOCN;"
    )

def _connect(
    server, database, username, password, auth_mode="sql",
    trust_server_certificate=True, encrypt=False,
):
    """Open pyodbc connection with autocommit (required for reliable EXEC of laundry SPs)."""
    conn_str = build_pyodbc_conn_str(
        server, database, username, password, auth_mode,
        trust_server_certificate=trust_server_certificate, encrypt=encrypt,
    )
    conn = pyodbc.connect(conn_str, autocommit=True)
    cursor = conn.cursor()
    
    try:
        cursor.execute("SET NOCOUNT ON;")
    except Exception:
        pass
    return conn, cursor

def _exec(cursor, sql: str, log: LogFn, label: str, params=None):
    log(f"  → {label}")
    try:
        if params is not None:
            cursor.execute(sql, params)
        else:
            cursor.execute(sql)
        
        while True:
            try:
                if not cursor.nextset():
                    break
            except Exception:
                break
    except Exception as e:
        log(f"  ✗ FAILED at [{label}]: {e}")
        raise

def _scalar(cursor, sql: str) -> int:
    cursor.execute(sql)
    row = cursor.fetchone()
    return int(row[0]) if row and row[0] is not None else 0

def run_preview(
    server: str,
    database: str,
    username: str,
    password: str,
    query: str,
    auth_mode: str = "sql",
    log: Optional[LogFn] = None,
    trust_server_certificate: bool = True,
    encrypt: bool = False,
) -> Tuple[List[str], List[list]]:
    log = log or (lambda _m: None)
    log("Fetching preview data from SQL Server...")
    conn, cursor = _connect(
        server, database, username, password, auth_mode,
        trust_server_certificate, encrypt,
    )
    try:
        preview_query = f"SELECT TOP 10 * FROM ({query.strip().rstrip(';')}) AS PREVIEW_SUB"
        cursor.execute(preview_query)
        columns = [col[0] for col in cursor.description]
        rows = [list(row) for row in cursor.fetchall()]
        log(f"Preview loaded successfully ({len(rows)} rows returned).")
        return columns, rows
    finally:
        conn.close()

def run_stage1(
    server: str,
    database: str,
    schema: str,
    username: str,
    password: str,
    query: str,
    auth_mode: str = "sql",
    log: Optional[LogFn] = None,
    trust_server_certificate: bool = True,
    encrypt: bool = False,
) -> Tuple[bool, List[Dict[str, Any]], int]:
    """
    Stage 1 mirrors cleaning_temp.sql through ZIP laundry + invalid-ZIP detection.
    Manual/offline suggestions are generated for unresolved rows (app review step).
    """
    log = log or (lambda _m: None)
    conn = None
    try:
        log("Connecting to SQL Server...")
        conn, cursor = _connect(
            server, database, username, password, auth_mode,
            trust_server_certificate, encrypt,
        )
        db, sch = database, schema
        t_prcs = sql_table(db, sch, "TEMP_NAME_ADDR_PRCS")
        t_zip = sql_table(db, sch, "TEMP_ZIP")
        sp_zip = sql_table(db, sch, "spZIPCODE_LAUNDRY")

        
        log("Step 1/4 — Truncate + extract source rows...")
        _exec(cursor, f"TRUNCATE TABLE {t_prcs};", log, "TRUNCATE TEMP_NAME_ADDR_PRCS")

        cleaned_query = query.strip().rstrip(";")
        insert_sql = f"""
            INSERT INTO {t_prcs} (
                MMBR_ID, MMBR_ALT_ID, ORIG_NM_1, ORIG_ADDR_1, ORIG_ADDR_2,
                ORIG_CITY, ORIG_ST, ORIG_ZIP, HIN_NR, DEA_NR, GLN_NR
            )
            {cleaned_query};
        """
        _exec(cursor, insert_sql, log, "INSERT extract into TEMP_NAME_ADDR_PRCS")
        total_extracted = _scalar(cursor, f"SELECT COUNT(*) FROM {t_prcs}")
        log(f"  Extracted {total_extracted} rows into TEMP_NAME_ADDR_PRCS.")

        
        log("Step 2/4 — ZIP / City / State laundry...")
        _exec(cursor, f"TRUNCATE TABLE {t_zip};", log, "TRUNCATE TEMP_ZIP")
        _exec(
            cursor,
            f"""
            INSERT INTO {t_zip} ([SRC_ID], [CITY], [STATE], [ZIP])
            SELECT T.REC_ID, T.ORIG_CITY, T.ORIG_ST, T.ORIG_ZIP
            FROM {t_prcs} T;
            """,
            log,
            "INSERT TEMP_ZIP from ORIG_*",
        )
        _exec(cursor, f"EXEC {sp_zip};", log, "EXEC spZIPCODE_LAUNDRY")

        _exec(
            cursor,
            f"""
            UPDATE T SET
                T.[UPD_CITY] = UPPER(TZ.[CITY]),
                T.[UPD_ST]   = TZ.[STATE],
                T.[UPD_ZIP]  = TZ.[ZIP]
            FROM {t_prcs} T
            JOIN {t_zip} TZ ON T.[REC_ID] = TZ.[SRC_ID]
            WHERE TRIM(ISNULL(TZ.[CITY], '')) <> ''
              AND TRIM(ISNULL(TZ.[STATE], '')) <> ''
              AND TRIM(ISNULL(TZ.[ZIP], '')) <> '';
            """,
            log,
            "UPDATE UPD_CITY/ST/ZIP from TEMP_ZIP",
        )

        
        log("Step 3/4 — Validate ZIP against PDIDW_DEV.REF.ZIP_CODE...")
        _exec(
            cursor,
            f"""
            UPDATE T SET T.ISSUE_FLG = 'Invalid ZIP'
            FROM {t_prcs} T
            LEFT JOIN PDIDW_DEV.REF.ZIP_CODE Z ON T.UPD_ZIP = Z.Zipcode
            WHERE Z.Zipcode IS NULL;
            """,
            log,
            "Flag Invalid ZIP",
        )

        _exec(
            cursor,
            f"""
            UPDATE T SET
                T.[UPD_CITY] = UPPER(ZC.[CITY]),
                T.[UPD_ST]   = ZC.[STATE],
                T.[UPD_ZIP]  = ZC.[ZIPCODE],
                T.ISSUE_FLG  = NULL
            FROM {t_prcs} T
            JOIN PDIDW_DEV.REF.ZIP_CODE ZC ON '0' + T.ORIG_ZIP = ZC.ZIPCODE
            WHERE T.ISSUE_FLG = 'Invalid ZIP'
              AND LEN(T.ORIG_ZIP) = 4
              AND ZC.ZipCodeType = 'STANDARD'
              AND ZC.LocationType = 'PRIMARY';
            """,
            log,
            "Fix 4-digit ZIPs via REF.ZIP_CODE",
        )

        
        log("Step 4/4 — Collect unresolved (Invalid ZIP / missing ZIP)...")
        cursor.execute(
            f"""
            SELECT
                T.REC_ID, T.MMBR_ID, T.MMBR_ALT_ID, T.ORIG_NM_1, T.ORIG_ADDR_1,
                T.ORIG_CITY, T.ORIG_ST, T.ORIG_ZIP, T.UPD_CITY, T.UPD_ST, T.UPD_ZIP, T.ISSUE_FLG
            FROM {t_prcs} T
            WHERE T.ISSUE_FLG = 'Invalid ZIP'
               OR T.UPD_ZIP IS NULL
               OR LEN(TRIM(ISNULL(T.UPD_ZIP, ''))) < 5;
            """
        )
        columns = [col[0] for col in cursor.description]
        unresolved = [dict(zip(columns, row)) for row in cursor.fetchall()]

        if unresolved:
            log(f"Found {len(unresolved)} unresolved records — offline ZIP suggestions...")
            for r in unresolved:
                s_city, s_st, s_zip = verify_and_fix_city_state_zip(
                    r.get("ORIG_CITY"), r.get("ORIG_ST"), r.get("ORIG_ZIP")
                )
                r["APPLY_FIX"] = True
                r["SUGG_CITY"], r["SUGG_ST"], r["SUGG_ZIP"] = s_city, s_st, s_zip
        else:
            log("No unresolved ZIP rows.")

        log(f"Stage 1 complete. Extracted={total_extracted}, Unresolved={len(unresolved)}.")
        return True, unresolved, total_extracted

    except Exception as e:
        log(f"ERROR Stage 1: {e}")
        return False, [], 0
    finally:
        if conn:
            conn.close()

def run_stage2(
    server: str,
    database: str,
    schema: str,
    username: str,
    password: str,
    manual_data: List[Dict[str, Any]],
    auth_mode: str = "sql",
    log: Optional[LogFn] = None,
    trust_server_certificate: bool = True,
    encrypt: bool = False,
) -> bool:
    """
    Stage 2: apply manual ZIP fixes, then address laundry + name laundry
    exactly as in cleaning_temp.sql (address + name sections).
    """
    log = log or (lambda _m: None)
    conn = None
    try:
        log("Connecting for Stage 2...")
        conn, cursor = _connect(
            server, database, username, password, auth_mode,
            trust_server_certificate, encrypt,
        )
        db, sch = database, schema
        t_prcs = sql_table(db, sch, "TEMP_NAME_ADDR_PRCS")
        t_addr = sql_table(db, sch, "TEMP_ADDR")
        t_parts = sql_table(db, sch, "TEMP_ADDR_PARTS")
        t_nm = sql_table(db, sch, "TEMP_NM")
        sp_addr = sql_table(db, sch, "spADDR_LAUNDRY")
        sp_nm = sql_table(db, sch, "spNM_LAUNDRY")

        
        log("Step 1/3 — Commit reviewed ZIP / city / state corrections...")
        applied = 0
        for r in manual_data:
            apply_fix = r.get("APPLY_FIX", True)
            if apply_fix:
                c = r.get("SUGG_CITY") if pd.notnull(r.get("SUGG_CITY")) else r.get("UPD_CITY")
                s = r.get("SUGG_ST") if pd.notnull(r.get("SUGG_ST")) else r.get("UPD_ST")
                z = r.get("SUGG_ZIP") if pd.notnull(r.get("SUGG_ZIP")) else r.get("UPD_ZIP")
            else:
                c = r.get("UPD_CITY")
                s = r.get("UPD_ST")
                z = r.get("UPD_ZIP")

            cursor.execute(
                f"""
                UPDATE {t_prcs}
                SET UPD_CITY = ?, UPD_ST = ?, UPD_ZIP = ?, ISSUE_FLG = NULL
                WHERE REC_ID = ?;
                """,
                (
                    str(c).upper() if pd.notnull(c) and str(c).strip() else None,
                    str(s).upper() if pd.notnull(s) and str(s).strip() else None,
                    str(z) if pd.notnull(z) and str(z).strip() else None,
                    int(r["REC_ID"]),
                ),
            )
            applied += 1
        log(f"  Applied corrections to {applied} reviewed rows.")

        
        log("Step 2/3 — Address laundry (spADDR_LAUNDRY)...")
        _exec(cursor, f"TRUNCATE TABLE {t_addr};", log, "TRUNCATE TEMP_ADDR")
        _exec(
            cursor,
            f"""
            INSERT INTO {t_addr} (
                [SRC_ID], [ADDR_1], [ADDR_2], [WB_1], [WB_2], [CITY], [STATE], [ZIP]
            )
            SELECT
                [REC_ID], [ORIG_ADDR_1], [ORIG_ADDR_2],
                UPPER(PDIDW_DEV.STAGE.fnRemoveSpace_SpChar([ORIG_ADDR_1])),
                UPPER(PDIDW_DEV.STAGE.fnRemoveSpace_SpChar([ORIG_ADDR_2])),
                [UPD_CITY], [UPD_ST], [UPD_ZIP]
            FROM {t_prcs} T;
            """,
            log,
            "INSERT TEMP_ADDR",
        )
        _exec(cursor, f"EXEC {sp_addr} @Src = 'MNL';", log, "EXEC spADDR_LAUNDRY @Src='MNL'")

        
        _exec(
            cursor,
            f"""
            UPDATE T SET
                T.[UPD_ADDR_1] = LTRIM(RTRIM(A.ADDR_1)),
                T.[UPD_ADDR_2] = LTRIM(RTRIM(A.ADDR_2)),
                T.ST_NM        = LTRIM(RTRIM(A.ST_NM)),
                T.[STE_NR]     = LTRIM(RTRIM(A.[STE_NR])),
                T.ST_NR        = A.ST_NR,
                T.ST_TYP       = A.ST_TYP,
                T.[DIR_1]      = A.[DIR_1],
                T.[ST_DIR]     = A.[ST_DIR],
                T.[BLDG_NR]    = A.[BLDG_NR],
                T.[FL_NR]      = A.[FL_NR],
                T.ST_NR_2      = A.ST_NR_2
            FROM {t_prcs} T
            JOIN {t_parts} A ON T.[REC_ID] = A.[SRC_ID];
            """,
            log,
            "UPDATE address parts from TEMP_ADDR_PARTS",
        )

        
        _exec(
            cursor,
            f"""
            UPDATE T SET
                [ST_NR] = PDIDW_DEV.[STAGE].[FNFIRSTWORD](T.[ORIG_ADDR_1]),
                [ST_NM] = REPLACE(T.[ST_NR], '-', ' '),
                [ST_TYP] = 'HWY',
                [UPD_ADDR_1] = PDIDW_DEV.[STAGE].[FNREMOVESPACE_SPCHAR](T.[ORIG_ADDR_1])
            FROM {t_prcs} T
            JOIN PDIDW_DEV.[REF].[STATE_LIST] SL
              ON T.[ST_NR] LIKE SL.[ST] + '-%'
             AND SL.[ST_TYP] = 'State';
            """,
            log,
            "UPDATE highway/state-prefix addresses",
        )

        
        log("Step 3/3 — Name laundry (spNM_LAUNDRY)...")
        _exec(cursor, f"TRUNCATE TABLE {t_nm};", log, "TRUNCATE TEMP_NM")
        _exec(
            cursor,
            f"""
            INSERT INTO {t_nm} ([SRC_ID], [ORIG_NM], [CITY], [STATE], [ZIP], [PRCS_STAT_CD])
            SELECT T.REC_ID, T.ORIG_NM_1, T.ORIG_CITY, T.UPD_ST, T.UPD_ZIP, 'P'
            FROM {t_prcs} T;
            """,
            log,
            "INSERT TEMP_NM",
        )
        _exec(cursor, f"EXEC {sp_nm} @vSrc_Nm = 'GPR';", log, "EXEC spNM_LAUNDRY @vSrc_Nm='GPR'")

        _exec(
            cursor,
            f"""
            UPDATE T SET
                T.[UPD_NM_1] = REPLACE(TN.[UPD_NM], ',', ''),
                T.[PRP_NM]   = TN.PRP_NM,
                T.[CMN_NM]   = TN.CMN_NM,
                T.[COT_ID]   = TN.COT_ID
            FROM {t_prcs} T
            JOIN {t_nm} TN ON T.REC_ID = TN.[SRC_ID]
              AND ISNULL(TN.[ALT_NM_IN], 'N') = 'N';
            """,
            log,
            "UPDATE name fields from TEMP_NM",
        )

        final_count = _scalar(cursor, f"SELECT COUNT(*) FROM {t_prcs}")
        log(f"Pipeline finished successfully. Rows in TEMP_NAME_ADDR_PRCS: {final_count}.")
        return True

    except Exception as e:
        log(f"ERROR Stage 2: {e}")
        return False
    finally:
        if conn:
            conn.close()

def is_column_empty(
    server: str,
    database: str,
    username: str,
    password: str,
    column_name: str,
    where_clause: str = "",
    auth_mode: str = "sql",
    trust_server_certificate: bool = True,
    encrypt: bool = False,
) -> bool:
    """Return True if the specified column in STAGE.GPO_MEMBER has no non-null/non-empty values under the given where condition."""
    try:
        conn, cursor = _connect(
            server, database, username, password, auth_mode,
            trust_server_certificate, encrypt,
        )
        query = f"SELECT COUNT(*) FROM sndbx_PDIDW.STAGE.GPO_MEMBER GM WHERE {column_name} IS NOT NULL AND {column_name} <> ''"
        if where_clause:
            stripped = where_clause.strip()
            if stripped.upper().startswith("WHERE"):
                stripped = re.sub(r"^WHERE\s+", "", stripped, flags=re.IGNORECASE)
            query += f" AND ({stripped})"

        cursor.execute(query)
        row = cursor.fetchone()
        count = int(row[0]) if row and row[0] is not None else 0
        conn.close()
        return count == 0
        return count == 0
    except Exception as e:
        print(f"[WARN] Column emptiness check failed: {e}", flush=True)
        return False
