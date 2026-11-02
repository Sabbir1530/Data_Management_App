"""Excel read, column auto-mapping and export."""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

NONE = "(none)"

# key, label, required, aliases (normalised: upper, letters+digits only)
FIELDS = [
    ("case_id", "CASE_ID", True, ["CASEID"]),
    ("enduser_id", "ENDUSER_ID", True, ["ENDUSERID", "EUID"]),
    ("name", "ENDUSER_NAME", True, ["ENDUSERNAME", "ENDUSERNM", "EUNAME", "NAME"]),
    ("addr", "ENDUSER_ADDR1", True, ["ENDUSERADDR1", "ENDUSERADDR", "ENDUSERADDRESS", "ENDUSERADDRESS1", "ADDRESS"]),
    ("city", "ENDUSER_CITY", True, ["ENDUSERCITY", "CITY"]),
    ("state", "ENDUSER_STATE", True, ["ENDUSERSTATE", "ENDUSERST", "STATE"]),
    ("zip", "ENDUSER_ZIP", True, ["ENDUSERZIP", "ENDUSERPOSTALCODE", "ZIPCODE", "ZIP"]),
    ("idn", "L1_IDN_NAME (optional)", False, ["L1IDNNAME", "L1IDNNM", "IDNNAME", "IDNNM"]),
    ("rank", "RANK", True, ["RANK", "RNK", "ORDINAL"]),
    ("terr_id", "TERRITORY_ID (optional)", False, ["TERRITORYID", "TERRID", "TERRITORY"]),
    ("sales_type", "SALES_TYPE (optional)", False, ["SALESTYPE", "SALES"]),
]


def sheet_names(path: str) -> List[str]:
    if str(path).lower().endswith(".csv"):
        return ["CSV Data"]
    return pd.ExcelFile(path).sheet_names


def read_excel(path: str, sheet) -> pd.DataFrame:
    if str(path).lower().endswith(".csv"):
        # read_csv with low_memory=False to handle mixed types gracefully
        return pd.read_csv(path, low_memory=False)
    return pd.read_excel(path, sheet_name=sheet)


def guess_mapping(columns) -> Dict[str, str]:
    normed = {c: re.sub(r"[^A-Z0-9]", "", str(c).upper()) for c in columns}
    used, out = set(), {}
    for key, _label, _req, aliases in FIELDS:
        pick = ""
        for a in aliases:                                   # exact alias, best alias first
            for c, n in normed.items():
                if n == a and c not in used:
                    pick = c
                    break
            if pick:
                break
        if not pick:                                        # fuzzy fallback
            best, best_s = "", 0.0
            for c, n in normed.items():
                if c in used or not n:
                    continue
                s = max(SequenceMatcher(None, n, a).ratio() for a in aliases)
                if s > best_s:
                    best, best_s = c, s
            if best_s >= 0.85:
                pick = best
        if pick:
            used.add(pick)
        out[key] = pick
    return out


def _clean(v):
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        try:
            return v.item()
        except Exception:
            pass
    return v


def export_workbook(path: Path, df: pd.DataFrame, id_col_name: str, corr: List[str], excluded: List[bool],
                    usr: str, info: List[dict], mode: str = "all") -> Path:
    """mode 'all': every row, excluded rows get blank CORR_EU_ID/SQL, plus a Review sheet.
       mode 'excluded': only excluded rows, keeping the suggested CORR_EU_ID/SQL."""
    cols = [str(c) for c in df.columns]
    n = len(cols)
    id_pos = cols.index(str(id_col_name))
    values = df.astype(object).where(df.notna(), None).values.tolist()

    idxs = [i for i in range(len(values)) if (excluded[i] if mode == "excluded" else True)]

    # If saving to CSV
    if str(path).lower().endswith(".csv"):
        import csv
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(cols + ["CORR_EU_ID", "SQL", "Pick Reason", "Match", "Difference Status"])
            for pos, i in enumerate(idxs):
                keep = bool(corr[i]) and (mode == "excluded" or not excluded[i])
                row = [_clean(v) for v in values[i]]
                row.append((int(corr[i]) if corr[i].isdigit() else corr[i]) if keep else None)
                old = str(_clean(values[i][id_pos]) or "").strip()
                old = old[:-2] if old.endswith(".0") else old
                need_sql = keep and old != corr[i]
                sql_str = f"EXEC  [MDM].[spRemap_NAD_EU_XREF] @OLD_ID ={old},  @OLD_ID_TYP  ='EU' , @NEW_ENDUSER_ID  = {corr[i]}, @USR_ID ={usr}" if need_sql else ""
                row.append(sql_str)
                row.append(info[i].get("why", ""))
                row.append(info[i].get("match", ""))
                row.append(info[i].get("difference_status", ""))
                writer.writerow(row)
        
        # We also generate the review file as a separate CSV if mode is all
        return path

    id_letter = get_column_letter(id_pos + 1)
    corr_letter = get_column_letter(n + 1)
    
    wb = Workbook()
    ws = wb.active
    ws.title = "Data" if mode == "all" else "Excluded"
    ws.append(cols + ["CORR_EU_ID", "SQL", "Pick Reason", "Match", "Difference Status"])
    for c in ws[1]:
        c.font = Font(bold=True)
    ws.freeze_panes = "A2"

    idxs = [i for i in range(len(values)) if (excluded[i] if mode == "excluded" else True)]
    for pos, i in enumerate(idxs):
        r = pos + 2
        keep = bool(corr[i]) and (mode == "excluded" or not excluded[i])
        row = [_clean(v) for v in values[i]]
        row.append((int(corr[i]) if corr[i].isdigit() else corr[i]) if keep else None)
        old = str(_clean(values[i][id_pos]) or "").strip()
        old = old[:-2] if old.endswith(".0") else old
        need_sql = keep and old != corr[i]
        row.append(
            f'="EXEC  [MDM].[spRemap_NAD_EU_XREF] @OLD_ID ="&{id_letter}{r}&",  @OLD_ID_TYP  =\'EU\' , '
            f'@NEW_ENDUSER_ID  = "&{corr_letter}{r}&", @USR_ID ={usr}"' if need_sql else None
        )
        row.append(info[i].get("why", ""))
        row.append(info[i].get("match", ""))
        row.append(info[i].get("difference_status", ""))
        ws.append(row)

    wb.save(path)
    return path
