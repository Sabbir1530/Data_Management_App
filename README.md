## Master Data Management (MDM)

This project is a comprehensive desktop application designed to streamline, deduplicate, and master healthcare and facility data. It unifies three powerful data-processing workflows into a single interface, combining traditional fuzzy matching algorithms with AI-powered entity resolution via the Google Gemini API.

## Core Capabilities

### 1. Data Deduplication & Remapping (Excel-based)
Designed to process bulk data from Excel spreadsheets, this module automatically detects data schemas, maps columns, and groups duplicate entities together.
- **Fuzzy Pre-filtering:** Uses token-sort ratios to group similar names and addresses.
- **AI Classification:** Leverages Google Gemini to logically determine if two distinct records actually represent the same physical entity.
- **Cluster Splitting:** Intelligently breaks apart large, loosely-matched clusters based on AI confidence.

### 2. Facility Matching Automation
A specialized workflow for matching hospitals, clinics, and Group Purchasing Organizations (GPOs) against master reference files.
- **Spatial Awareness:** Uses ArcGIS to geocode text addresses into precise Lat/Long coordinates.
- **Weighted Scoring:** Evaluates name and address similarity with adjustable acceptance thresholds and priority keywords (e.g., "Health System" modifiers).
- **AI Validation:** Flags false-positives and confirms complex matches using natural language reasoning.

### 3. Direct SQL Data Laundry & Distance Pipeline
Interfaces directly with SQL Server databases (via ODBC) to perform large-scale data normalization without needing to export to Excel.
- **Text Laundering:** Standardizes suffixes, abbreviations, and formatting directly against target database tables.
- **Geospatial Distance Pipeline:** Calculates the physical distance between facilities to ensure text-matches are geographically viable.
- **Automated Updates:** Pushes cleaned data, matched IDs, and confidence scores back into the SQL database in real-time.

## Setup & Configuration

1. **Install Dependencies**
   Ensure Python is installed, then run:
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure Environment Variables**
   Create a `.env` file in the root directory (you can use the provided `.env.example` as a template). 
   - `GEMINI_API_KEY`: Required for all AI matching and classification features.
   - `DB_SERVER`, `DB_NAME`, `DB_USER`, `DB_PASS`: Required for the SQL Database Laundry module.

3. **Launch the Dashboard**
   ```bash
   python main.py
   ```
   From the main dashboard, you can launch any of the distinct workspaces. The modules share underlying caches and database connections, preventing the need to re-authenticate or manage keys in multiple places.
