# Unified MDM Dashboard

This project is a unified desktop application merging three distinct Python Tkinter projects:
1. **EU Deduplication & Remap**
2. **Facility Matching Automation (Fuzzy & GPO)**
3. **MDM - Data Laundry & Matching**

## Folder Hierarchy & Architecture
The codebase has been refactored to remove duplicate logic, prevent name collisions, and adopt a scalable object-oriented architecture.

### Root Directory
- `main.py`: The central application dashboard. Run this file to start the unified app.
- `requirements.txt`: Combined dependencies for all three applications.
- `patch.py` (internal tool used for initial imports patching, can be safely ignored or deleted).

### 1. `shared/`
Contains deduplicated utilities, UI components, and configuration files. By consolidating these shared files, the overall size of the codebase is reduced and maintenance is centralized.
- **`config.py`**: Merged global application settings (e.g., window geometries, page sizes, fuzzy thresholds).
- **`models/`**: Reusable data schemas (e.g., `connection.py` and `connection_store.py`).
- **`services/`**: Core shared business logic components such as database engines (`database.py`), shared workspace management (`workspace.py`), and helper dictionaries (`dict.py`).
- **`ui/`**: Common user interface modules like the database `connection_dialog.py` and common theme styling (`ui_common.py`).

### 2. `eu_app/`
Contains logic specifically bound to the **EU Deduplication & Remap** project.
- **`app.py`**: The `EUDedupWindow(tk.Toplevel)` class which manages the lifecycle and initial login sequence of this application.
- **`ui/`**: Custom user interface parts like the original `main_window.py`.
- **`services/`**: Services unique to this app (e.g., `ai_same.py`, `excel_io.py`, `sp_client.py`).

### 3. `fuzzy_app/`
Contains the **Facility Matching Automation** logic, split logically for processing and UI.
- **`app.py`**: The `FuzzyAppWindow(tk.Toplevel)` class wrapping the original Tkinter notebook interface.
- **`tabs/`**: UI tabs preserving the multi-tab layout structure (`fuzzy_tab.py`, `gpo_tab.py`, `ai_validation_tab.py`).
- **`logic/`**: The core AI logic, geocoding, and scoring algorithms.

### 4. `project_app/`
Contains logic for the **MDM - Data Laundry & Matching** system.
- **`app.py`**: The `ProjectAppWindow(tk.Toplevel)` class handling initial database connection settings before opening the inner app shell.
- **`ui/`**: Windows and pages like `data_laundry_page.py`, `matching_page.py`, and `distance_page.py`.
- **`services/`**: The underlying components governing laundering, distances pipelines, matching functionality, and match cache processing.

## Running the Application

1. Open a terminal and navigate to the `Final` folder.
2. Install the necessary dependencies: `pip install -r requirements.txt`.
3. Ensure that your environment variables (like `GEMINI_API_KEY`) and `.env` files are appropriately set.
4. Run the application: `python main.py`

This will load the Central Homepage. From there, click any of the distinct options to open its full interface in an isolated top-level window. Each window completely isolates its logic while safely sharing utility caches and connections in the `shared` module without risk of widget/name collisions!