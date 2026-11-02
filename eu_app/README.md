# MDM — End User Dedup & Remap

Finds duplicate `ENDUSER_ID` rows that share the same `CASE_ID`, decides which
one to keep (fuzzy match → AI check → DB priority rule), and generates the
`CORR_EU_ID` + `SQL` remap statement for the rest — same as the manual process
you described, wired into one desktop app.

## 1. Install

```bash
pip install -r requirements.txt
```

`pyodbc` needs a SQL Server ODBC driver installed on the machine (same as your
existing app). `google-genai` needs a `GEMINI_API_KEY`, set as an environment
variable or in a `.env` file next to `main.py`:

```
GEMINI_API_KEY=your-key-here
```

## 2. Run

```bash
python main.py
```

First run asks for the SQL Server connection (same dialog as your existing
tool) and offers to remember it. All app data — saved login, the AI checkpoint
cache, and any exports you don't redirect elsewhere — live under
`./mdm_workspace/` next to `main.py`.

## 3. Workflow (matches the process you described)

**Tab 1 — Load & Map**
Browse to the Excel file, pick a sheet, and the app auto-detects
`CASE_ID`, `ENDUSER_ID`, `ENDUSER_NAME`, `ENDUSER_ADDR1`, `ENDUSER_CITY`,
`ENDUSER_STATE`, `ENDUSER_ZIP` and (optional) `L1_IDN_NAME` from your column
headers. Every dropdown can be changed manually before continuing.

**Tab 2 — Match & Review**, in order:

1. **Run Fuzzy Match** — self-joins on `CASE_ID`; for every pair in the same
   case, name and address are compared with fuzzy matching (default 90%
   threshold, adjustable) and city/state/ZIP must match exactly. Matches are
   flagged `SAME-FUZZY`.
2. **Run AI Check** — every pair fuzzy could *not* confirm gets sent to
   Gemini with name/address/city/state, asking only "same entity or not."
   `DIFFERENT` pairs are left alone (no suggestion). `SAME` pairs (above the
   confidence threshold, default 70%) are flagged `SAME-AI`. Results are
   checkpointed to `mdm_workspace/cache/ai_same_cache.csv`, so a crash or
   re-run never re-asks the AI about a pair it already resolved.
3. **Compute Suggestions** — for every confirmed group (2, 3, or 4+ rows):
   - If exactly one row has an 8-digit `ENDUSER_ID`, that one wins outright.
   - Otherwise, `EXEC MDM.spFindCompany @ID=<id>` is called for each
     8-digit candidate; the highest `Facility_Priority` wins, ties broken by
     lowest `ENDUSER_ID`.
   - If no row has a `Facility_Priority`, the row with an `IDN_NM` wins (if
     only some rows have one); otherwise lowest `ENDUSER_ID` wins.
   - If no row is 8-digit at all, the same SP/priority/IDN logic runs over
     all rows in the group instead.

   The winner's ID is written to `CORR_EU_ID` on every row in the group,
   with the reasoning shown in `REASON`. The `SQL` formula
   (`EXEC [MDM].[spRemap_NAD_EU_XREF] ...`) is generated for every row that
   actually needs a remap (i.e. not the winner itself).

**Run All 1→2→3** does the three steps back to back.

### Reviewing results

- The table is paginated (50 rows/page) and filterable by `CASE_ID` or
  "only rows with a match."
- Click the **EXCLUDE** column on any row to toggle it off (checkbox) —
  excluded rows are skipped by both the bulk SQL run and the full export.
- Right-click a row for a context menu: view full details/reason side by
  side, toggle exclude, or run that single row's SQL immediately.
- Double-click a row to see both records in the pair and the full reasoning.

### Running the SQL

Enter a `USR_ID`, then either right-click → **Run this row's SQL now** for
one row, or **Execute ALL pending SQL → Database** for everything that has a
suggestion, isn't excluded, and hasn't already run. Each row's status
(`DONE` / `ERROR: ...`) is tracked so a partial run can be safely re-run —
already-`DONE` rows are skipped.

### Exporting

- **Export Full Excel** — your original columns plus `CORR_EU_ID` and `SQL`
  (Excel formula form, matching your manual template), a blank for excluded
  rows, and a `Review` sheet summarizing every match/group/reason/status.
- **Export Excluded Only** — just the rows you excluded, with the
  suggestion kept intact, so you can review or hand them off separately.

## Files

```
main.py                     entry point (login → main window)
config.py                   app-wide settings (page size, default thresholds)
models/connection.py        ConnectionSettings dataclass (unchanged)
models/connection_store.py  save/load/clear saved login (unchanged)
services/workspace.py       local mdm_workspace/ folder (cache, output, login file)
services/database.py        SQLAlchemy engine + ODBC driver detection (unchanged)
services/dict.py            healthcare synonym dictionary (unchanged)
services/dedup.py           normalization, fuzzy matching, grouping, pick logic, SQL text
services/sp_client.py       spFindCompany / spRemap_NAD_EU_XREF wrappers
services/ai_same.py         Gemini SAME/DIFFERENT check + checkpoint cache
services/excel_io.py        column auto-mapping + Excel export (data + Review sheet)
ui/connection_dialog.py     login dialog (unchanged)
ui/main_window.py           Load&Map tab, Match&Review tab, all app logic
```

## Notes / assumptions

- `spFindCompany` is assumed to return a result set with columns
  `Facility_Priority` and `IDN_NM` (matched case-insensitively). If your SP's
  actual column names differ, update `services/sp_client.py::fetch_company_info`.
- ZIP codes are normalized to 5 digits and zero-padded (Excel often drops a
  leading zero, e.g. `8110` → `08110`), so a dropped leading zero won't block
  a fuzzy match.
- The AI step only ever answers `SAME` or `DIFFERENT` for one pair — it never
  invents a parent/IDN — matching the narrower question you wanted asked.
