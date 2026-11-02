# Facility Matching Automation (Tkinter)

A desktop GUI that turns your four scripts (fuzzy matcher, geocoder,
HSP/Non-HSP splitter, distance matcher, Gemini AI matcher) into one
click-through app with two modules:

1. **Fuzzy Matching** — weighted Name + Address similarity scoring with an
   editable acceptance threshold, auto-detected columns, priority-keyword
   suggestions, and matched/different Excel exports + on-screen summary.
2. **GPO Matching (AI)** — a 5-step pipeline:
   `Load & map columns → Geocode missing coordinates → Split HSP/Non-HSP →
   Distance-match (200m / 50m) → Proceed to Gemini AI matching`

## 1. Setup

```bash
python -m venv venv
# Windows: venv\Scripts\activate
source venv/bin/activate

pip install -r requirements.txt
```

## 2. Get a Gemini API key

Create one at https://aistudio.google.com/apikey, then either:
- Paste it into the app's **Settings** tab and click **Save** (stored in a
  local `.env` file next to `main.py`), or
- Set it as an environment variable before launching:
  `export GEMINI_API_KEY=your_key_here` (macOS/Linux) or
  `set GEMINI_API_KEY=your_key_here` (Windows).

## 3. Run

```bash
python main.py
```

## How each tab works

### 🔍 Fuzzy Matching
1. Browse to your Excel file.
2. Column mapping auto-fills (looks for `MMBR_NM` / `ENDUSER_NAME` /
   `*_ADDR_1`-style names first, then falls back to fuzzy guessing by
   keyword). Override any dropdown if it guessed wrong.
3. Adjust the acceptance threshold (default 75), Name/Address weights
   (default 70/30), and priority keywords (default:
   `healthcare, hospital, health, clinic`) if needed.
4. Click **Run Fuzzy Matching**. Two Excel files are created
   (`..._matched.xlsx`, `..._different.xlsx`) alongside on-screen stats
   (total / matched / different / suggested / match rate %) and a preview
   table.

### 🤖 GPO Matching (AI)
1. **Step 1** — load File 1 (your internal/UPD list) and File 2 (the GPO
   reference list). Map Name / Street / City / State / ZIP / Address Line 2
   / Latitude / Longitude for each (guessed automatically where possible).
2. **Step 2** — click **Geocode File 1** / **Geocode File 2**. Each button
   reports how many rows were missing coordinates and skips instantly if
   there's nothing to do. Uses the free ArcGIS geocoder (no API key
   needed), same as your original `coordinates.py`.
3. **Step 3** — pick the column to search and a keyword (default
   `Hospital`) and click **Split File 1** to produce the HSP / Non-HSP
   groups.
4. **Step 4** — thresholds default to 200m (HSP) and 50m (Non-HSP), matching
   your spec. Click **Run Distance Matching** to left-join each tier against
   File 2 (Haversine distance) and preview the resulting pairs. You can
   export the raw pairs to Excel at this point too.
5. **Step 5** — the entity Name/Address columns for AI matching are
   pre-filled from your Step 1 mapping. Click **▶ Proceed to AI Matching**
   to send each pair to Gemini (`gemini-2.5-flash` by default) for a
   SAME / AFFILIATED / DIFFERENT verdict with confidence, relation, and
   parent-organization extraction — same logic as your original
   `CMPNY_UPD_Matching_AI.py`, including incremental CSV caching so a
   re-run only processes new/failed rows. Produces a results Excel and a
   styled HTML report (`gpo_ai_matching_report.html`).

## Notes

- All long-running steps (fuzzy scoring, geocoding, distance matching, AI
  calls) run on a background thread so the window never freezes, with a
  progress bar and status text.
- Batch size / concurrency / model name for the AI step are exposed as
  advanced fields if you want to tune throughput vs. rate limits.
- Nothing here calls Google Search — the AI step matches purely on the
  name/address text you provide, exactly like your original script.

## Project layout

```
main.py                    Entry point (launches the window)
ui_common.py                Reusable widgets (dropdowns, file pickers, tables...)
tabs/
  fuzzy_tab.py               Fuzzy Matching tab
  gpo_tab.py                 GPO Matching (AI) tab — the 5-step pipeline
  settings_tab.py            Gemini API key storage
logic/
  fuzzy_match.py              Weighted fuzzy scoring engine
  geocode.py                  ArcGIS geocoding for missing coordinates
  splitter.py                 HSP / Non-HSP keyword split
  distance_match.py           Haversine spatial join (two-tier thresholds)
  ai_matcher.py                Gemini entity matching + HTML report
requirements.txt
```
