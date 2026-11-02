"""
tabs/gpo_tab.py
GUI tab for the GPO Matching pipeline:
  1. Load File 1 (internal/UPD) + File 2 (GPO/company reference) & map columns
  2. Geocode missing coordinates in either file
  3. Split File 1 into HSP / NonHSP by keyword
  4. Distance-match HSP @200m and NonHSP @50m against File 2
  5. Proceed -> AI matching (Gemini) on the resulting pairs
"""
import os
import threading
import tkinter as tk
from tkinter import ttk

import pandas as pd

from shared.ui.ui_common import (
    LabeledCombo, LabeledEntry, FilePicker, Section, StatCard, ScrollableFrame,
    make_treeview, fill_treeview, rebuild_treeview_columns, guess_column,
    browse_folder, open_path, show_error, show_info,
)
from fuzzy_app.logic.geocode import geocode_missing, count_missing
from fuzzy_app.logic.splitter import split_by_keyword
from fuzzy_app.logic.distance_match import run_tiered_distance_matching
from fuzzy_app.logic.ai_matcher import run_ai_matching
from fuzzy_app.logic.config import load_api_key, env_file_status, ENV_PATH, AI_MODEL, AI_BATCH_SIZE, AI_MAX_CONCURRENT

NONE_OPTION = ""
ID_COL = "Unique_ID"

class FileMapping(ttk.Frame):
    """Column-mapping widget block for one source file."""

    def __init__(self, parent, title):
        super().__init__(parent, padding=8, relief="groove", borderwidth=1)
        ttk.Label(self, text=title, font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 6))

        self.name_combo = LabeledCombo(self, "Name column:")
        self.name_combo.pack(anchor="w", pady=1)
        self.street_combo = LabeledCombo(self, "Street address column:")
        self.street_combo.pack(anchor="w", pady=1)
        self.addr2_combo = LabeledCombo(self, "Address line 2 (optional):")
        self.addr2_combo.pack(anchor="w", pady=1)
        self.city_combo = LabeledCombo(self, "City column:")
        self.city_combo.pack(anchor="w", pady=1)
        self.state_combo = LabeledCombo(self, "State column:")
        self.state_combo.pack(anchor="w", pady=1)
        self.zip_combo = LabeledCombo(self, "ZIP column (optional):")
        self.zip_combo.pack(anchor="w", pady=1)
        self.lat_combo = LabeledCombo(self, "Latitude column:", editable=True)
        self.lat_combo.pack(anchor="w", pady=1)
        self.lon_combo = LabeledCombo(self, "Longitude column:", editable=True)
        self.lon_combo.pack(anchor="w", pady=1)

        self.missing_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.missing_var, foreground="#b45309").pack(anchor="w", pady=(6, 0))

        self.btn_create_cols = ttk.Button(self, text="➕ Create Empty Lat/Lon", state="disabled")
        self.btn_create_cols.pack(anchor="w", pady=(4, 0))

    def set_columns(self, cols, df=None, callback=None):
        self.df_ref = df
        self.callback_ref = callback
        
        opt_cols = [NONE_OPTION] + cols
        self.name_combo.set_values(opt_cols, default=guess_column(opt_cols, [], ["nm", "name", "facility"], fallback=NONE_OPTION, required=False))
        self.street_combo.set_values(opt_cols, default=guess_column(opt_cols, [], ["addr", "address", "street"], fallback=NONE_OPTION, required=False))
        self.addr2_combo.set_values(opt_cols, default=guess_column(opt_cols, [], ["addr_2", "address2", "addr2"], fallback=NONE_OPTION, required=False))
        self.city_combo.set_values(opt_cols, default=guess_column(opt_cols, [], ["city"], fallback=NONE_OPTION, required=False))
        self.state_combo.set_values(opt_cols, default=guess_column(opt_cols, [], ["st", "state"], fallback=NONE_OPTION, required=False))
        self.zip_combo.set_values(opt_cols, default=guess_column(opt_cols, [], ["zip", "postal"], fallback=NONE_OPTION, required=False))
        
        lat_opts = list(opt_cols)
        lon_opts = list(opt_cols)
        cols_lower = [c.lower() for c in cols]
        
        if not any("lat" in c for c in cols_lower):
            lat_opts.append("Create 'Latitude'")
        if not any(x in c for c in cols_lower for x in ["lon", "lng"]):
            lon_opts.append("Create 'Longitude'")
            
        lat_default = guess_column(opt_cols, [], ["lat"], fallback="Create 'Latitude'" if "Create 'Latitude'" in lat_opts else "Latitude", required=False)
        lon_default = guess_column(opt_cols, [], ["lon", "lng"], fallback="Create 'Longitude'" if "Create 'Longitude'" in lon_opts else "Longitude", required=False)

        self.lat_combo.set_values(lat_opts, default=lat_default)
        self.lon_combo.set_values(lon_opts, default=lon_default)

        if df is not None and ("Latitude" not in df.columns or "Longitude" not in df.columns):
            self.btn_create_cols.config(state="normal", command=self._create_empty_cols)
        else:
            self.btn_create_cols.config(state="disabled")

    def _create_empty_cols(self):
        if self.df_ref is not None:
            if "Latitude" not in self.df_ref.columns:
                self.df_ref["Latitude"] = None
            if "Longitude" not in self.df_ref.columns:
                self.df_ref["Longitude"] = None
            if self.callback_ref:
                self.callback_ref(list(self.df_ref.columns), self.df_ref)

    def get_mapping(self):
        def val(combo):
            v = combo.get()
            return None if (v is None or v == NONE_OPTION) else v
        return {
            "name_col": val(self.name_combo),
            "street_col": val(self.street_combo),
            "addr2_col": val(self.addr2_combo),
            "city_col": val(self.city_combo),
            "state_col": val(self.state_combo),
            "zip_col": val(self.zip_combo),
            "lat_col": val(self.lat_combo),
            "lon_col": val(self.lon_combo),
        }

    def set_missing_note(self, text):
        self.missing_var.set(text)

class GpoMatchTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=0)

        self.df1 = None  
        self.df2 = None  
        self.path1 = None
        self.path2 = None
        self.hsp_df = None
        self.nonhsp_df = None
        self.paired_df = None
        self.ai_result_df = None
        self.output_dir = None
        self.ai_stop_flag = None
        self.ai_running = False

        scroller = ScrollableFrame(self)
        scroller.pack(fill="both", expand=True)
        self.body = scroller.body
        self._build_ui()

    
    def _build_ui(self):
        pad = dict(padx=16, pady=8)

        
        sec1 = Section(self.body, "Step 1 — Load Files & Map Columns")
        sec1.pack(fill="x", **pad)

        file_row = ttk.Frame(sec1)
        file_row.pack(fill="x")
        self.file1_picker = FilePicker(file_row, "File 1 (internal/UPD):", on_pick=self._on_file1_loaded)
        self.file1_picker.pack(fill="x", pady=2)
        self.file2_picker = FilePicker(file_row, "File 2 (GPO reference):", on_pick=self._on_file2_loaded)
        self.file2_picker.pack(fill="x", pady=2)

        maps_row = ttk.Frame(sec1)
        maps_row.pack(fill="x", pady=(8, 0))
        self.map1 = FileMapping(maps_row, "File 1 column mapping")
        self.map1.pack(side="left", fill="both", expand=True, padx=(0, 8))
        self.map2 = FileMapping(maps_row, "File 2 column mapping")
        self.map2.pack(side="left", fill="both", expand=True)

        out_row = ttk.Frame(sec1)
        out_row.pack(fill="x", pady=(10, 0))
        ttk.Label(out_row, text="Output folder:", width=22, anchor="w").pack(side="left")
        self.out_dir_var = tk.StringVar(value="(same as File 1)")
        ttk.Label(out_row, textvariable=self.out_dir_var, foreground="#555", width=40, anchor="w").pack(side="left", padx=6)
        ttk.Button(out_row, text="Choose...", command=self._choose_output_dir).pack(side="left")

        
        sec2 = Section(self.body, "Step 2 — Fill Missing Coordinates")
        sec2.pack(fill="x", **pad)
        g_row = ttk.Frame(sec2)
        g_row.pack(fill="x")
        self.geo1_btn = ttk.Button(g_row, text="Geocode File 1", command=lambda: self._geocode(1), state="disabled", style="Accent.TButton")
        self.geo1_btn.pack(side="left", padx=(0, 10))
        self.geo2_btn = ttk.Button(g_row, text="Geocode File 2", command=lambda: self._geocode(2), state="disabled", style="Accent.TButton")
        self.geo2_btn.pack(side="left", padx=(0, 10))
        self.export_geo1_btn = ttk.Button(g_row, text="Export Geocoded File 1", command=lambda: self._export_geocoded(1), state="disabled")
        self.export_geo1_btn.pack(side="left", padx=(0, 10))
        self.export_geo2_btn = ttk.Button(g_row, text="Export Geocoded File 2", command=lambda: self._export_geocoded(2), state="disabled")
        self.export_geo2_btn.pack(side="left")
        self.geo_progress = ttk.Progressbar(sec2, mode="determinate", length=400)
        self.geo_progress.pack(fill="x", pady=(8, 2))
        self.geo_status_var = tk.StringVar(value="Load both files to check for missing coordinates.")
        ttk.Label(sec2, textvariable=self.geo_status_var, foreground="#555").pack(anchor="w")
        ttk.Label(
            sec2,
            text=("Note: geocoding fills coordinates in memory only — it never modifies your original "
                  "uploaded data file. Use the Export buttons above to save a copy with the filled-in "
                  "coordinates so you can double-check them."),
            foreground="#b45309", wraplength=1000, justify="left",
        ).pack(anchor="w", pady=(4, 0))

        
        sec3 = Section(self.body, "Step 3 — Split File 1 into HSP / Non-HSP")
        sec3.pack(fill="x", **pad)
        split_row = ttk.Frame(sec3)
        split_row.pack(fill="x")
        self.split_col_combo = LabeledCombo(split_row, "Search column:")
        self.split_col_combo.pack(side="left", padx=(0, 16))
        self.split_keyword_entry = LabeledEntry(split_row, "Keyword:", default="Hospital", width=16)
        self.split_keyword_entry.pack(side="left", padx=(0, 16))
        self.split_btn = ttk.Button(split_row, text="Split File 1", command=self._run_split, state="disabled", style="Accent.TButton")
        self.split_btn.pack(side="left")
        self.split_status_var = tk.StringVar(value="")
        ttk.Label(sec3, textvariable=self.split_status_var, foreground="#555").pack(anchor="w", pady=(6, 0))

        
        sec4 = Section(self.body, "Step 4 — Distance Matching")
        sec4.pack(fill="x", **pad)
        thresh_row = ttk.Frame(sec4)
        thresh_row.pack(fill="x")
        self.hsp_thresh_entry = LabeledEntry(thresh_row, "HSP threshold (meters):", default="200", width=8)
        self.hsp_thresh_entry.pack(side="left", padx=(0, 16))
        self.nonhsp_thresh_entry = LabeledEntry(thresh_row, "Non-HSP threshold (meters):", default="50", width=8)
        self.nonhsp_thresh_entry.pack(side="left")
        run_dist_row = ttk.Frame(sec4)
        run_dist_row.pack(fill="x", pady=(8, 0))
        self.dist_btn = ttk.Button(run_dist_row, text="Run Distance Matching", command=self._run_distance, state="disabled", style="Primary.TButton")
        self.dist_btn.pack(side="left")
        self.dist_progress = ttk.Progressbar(run_dist_row, mode="determinate", length=300)
        self.dist_progress.pack(side="left", padx=12)
        self.dist_status_var = tk.StringVar(value="")
        ttk.Label(run_dist_row, textvariable=self.dist_status_var, foreground="#555").pack(side="left")

        pairs_stats_row = ttk.Frame(sec4)
        pairs_stats_row.pack(fill="x", pady=(10, 4))
        self.stat_hsp_pairs = StatCard(pairs_stats_row, "HSP Pairs", color="#2563eb")
        self.stat_hsp_pairs.pack(side="left", padx=4)
        self.stat_nonhsp_pairs = StatCard(pairs_stats_row, "Non-HSP Pairs", color="#0891b2")
        self.stat_nonhsp_pairs.pack(side="left", padx=4)
        self.stat_total_pairs = StatCard(pairs_stats_row, "Total Pairs", color="#1a202c")
        self.stat_total_pairs.pack(side="left", padx=4)
        self.stat_skipped = StatCard(pairs_stats_row, "Skipped (no coords)", color="#b45309")
        self.stat_skipped.pack(side="left", padx=4)

        self.export_pairs_btn = ttk.Button(sec4, text="Export Pairs", command=self._export_pairs,
                                            state="disabled", style="Accent.TButton")
        self.export_pairs_btn.pack(anchor="w", pady=(0, 6))

        self.pairs_frame, self.pairs_tree = make_treeview(sec4, columns=("DISTANCE_M", "TIER"), height=6)
        self.pairs_frame.pack(fill="both", expand=True)

        
        sec5 = Section(self.body, "Step 5 — AI Matching (Gemini)")
        sec5.pack(fill="x", **pad)

        ai_map_row = ttk.Frame(sec5)
        ai_map_row.pack(fill="x")
        self.ai_name1_combo = LabeledCombo(ai_map_row, "Entity 1 name column:")
        self.ai_name1_combo.grid(row=0, column=0, padx=(0, 12))
        self.ai_addr1_combo = LabeledCombo(ai_map_row, "Entity 1 address column:")
        self.ai_addr1_combo.grid(row=0, column=1, padx=(0, 12))
        self.ai_name2_combo = LabeledCombo(ai_map_row, "Entity 2 name column:")
        self.ai_name2_combo.grid(row=1, column=0, padx=(0, 12), pady=(4, 0))
        self.ai_addr2_combo = LabeledCombo(ai_map_row, "Entity 2 address column:")
        self.ai_addr2_combo.grid(row=1, column=1, padx=(0, 12), pady=(4, 0))

        ttk.Label(
            sec5,
            text=(f"Model, batch size and concurrency are fixed in code (gemini-3.5-flash, "
                  f"batch {AI_BATCH_SIZE}, concurrency {AI_MAX_CONCURRENT}). "
                  f"The Gemini API key is read from the .env file next to the app:\n{ENV_PATH}"),
            foreground="#555", justify="left", wraplength=1000,
        ).pack(anchor="w", pady=(8, 0))

        run_ai_row = ttk.Frame(sec5)
        run_ai_row.pack(fill="x", pady=(10, 0))
        self.apply_fuzzy_btn = ttk.Button(run_ai_row, text="Apply Fuzzy (80% Match)", command=self._run_apply_fuzzy,
                                          state="disabled", style="Accent.TButton")
        self.apply_fuzzy_btn.pack(side="left")
        self.ai_btn = ttk.Button(run_ai_row, text="▶ Proceed to AI Matching", command=self._run_ai,
                                  state="disabled", style="Primary.TButton")
        self.ai_btn.pack(side="left", padx=(8, 0))
        self.ai_stop_btn = ttk.Button(run_ai_row, text="⏹ Stop", command=self._stop_ai,
                                       state="disabled", style="Danger.TButton")
        self.ai_stop_btn.pack(side="left", padx=8)
        self.ai_export_partial_btn = ttk.Button(run_ai_row, text="💾 Export Results So Far",
                                                 command=self._export_ai_partial,
                                                 state="disabled", style="Accent.TButton")
        self.ai_export_partial_btn.pack(side="left", padx=8)
        self.ai_progress = ttk.Progressbar(run_ai_row, mode="determinate", length=260)
        self.ai_progress.pack(side="left", padx=12)
        self.ai_status_var = tk.StringVar(value="")
        ttk.Label(run_ai_row, textvariable=self.ai_status_var, foreground="#555").pack(side="left")

        ai_stats_row = ttk.Frame(sec5)
        ai_stats_row.pack(fill="x", pady=(10, 4))
        self.stat_same = StatCard(ai_stats_row, "SAME", color="#047857")
        self.stat_same.pack(side="left", padx=4)
        self.stat_aff = StatCard(ai_stats_row, "AFFILIATED", color="#b45309")
        self.stat_aff.pack(side="left", padx=4)
        self.stat_diff2 = StatCard(ai_stats_row, "DIFFERENT", color="#b91c1c")
        self.stat_diff2.pack(side="left", padx=4)
        self.stat_err = StatCard(ai_stats_row, "ERROR", color="#4b5563")
        self.stat_err.pack(side="left", padx=4)

        ai_open_row = ttk.Frame(sec5)
        ai_open_row.pack(fill="x")
        self.open_ai_excel_btn = ttk.Button(ai_open_row, text="Open Results File", command=self._open_ai_excel, state="disabled")
        self.open_ai_excel_btn.pack(side="left", padx=(0, 8))
        self.open_ai_html_btn = ttk.Button(ai_open_row, text="Open HTML Report", command=self._open_ai_html, state="disabled")
        self.open_ai_html_btn.pack(side="left", padx=(0, 8))
        self.open_ai_folder_btn = ttk.Button(ai_open_row, text="Open Output Folder", command=self._open_ai_folder, state="disabled")
        self.open_ai_folder_btn.pack(side="left")

    
    
    
    def _on_file1_loaded(self, path):
        try:
            if path.lower().endswith('.csv'):
                self.df1 = pd.read_csv(path, low_memory=False)
            else:
                self.df1 = pd.read_excel(path)
        except Exception as e:
            show_error("Failed to read File 1", e)
            return
        self.path1 = path
        cols = list(self.df1.columns)
        self._update_map1(cols)
        self.geo1_btn.config(state="normal")
        self.split_btn.config(state="normal")
        self._refresh_missing_note(1)

    def _update_map1(self, cols=None, df=None):
        if df is not None:
            self.df1 = df
        
        current_cols = cols or list(self.df1.columns)
        self.map1.set_columns(current_cols, self.df1, self._update_map1)
        self.split_col_combo.set_values(current_cols, default=guess_column([""] + current_cols, ["name", "facility", "nm"], fallback="", required=False))

    def _on_file2_loaded(self, path):
        try:
            if path.lower().endswith('.csv'):
                self.df2 = pd.read_csv(path, low_memory=False)
            else:
                self.df2 = pd.read_excel(path)
        except Exception as e:
            show_error("Failed to read File 2", e)
            return
        self.path2 = path
        cols = list(self.df2.columns)
        self._update_map2(cols)
        self.geo2_btn.config(state="normal")
        self._refresh_missing_note(2)

    def _update_map2(self, cols=None, df=None):
        if df is not None:
            self.df2 = df
        self.map2.set_columns(cols or list(self.df2.columns), self.df2, self._update_map2)
        self._maybe_enable_geocode()

    def _choose_output_dir(self):
        folder = browse_folder()
        if folder:
            self.output_dir = folder
            self.out_dir_var.set(folder)

    def _current_output_dir(self):
        if self.output_dir:
            return self.output_dir
        if self.path1:
            return os.path.dirname(self.path1) or "."
        return "."

    def _refresh_missing_note(self, which):
        if which == 1 and self.df1 is not None:
            m = self.map1.get_mapping()
            if m["lat_col"] and m["lon_col"]:
                n = count_missing(self.df1, m["lat_col"], m["lon_col"])
                self.map1.set_missing_note(f"{n} of {len(self.df1)} rows missing coordinates.")
        if which == 2 and self.df2 is not None:
            m = self.map2.get_mapping()
            if m["lat_col"] and m["lon_col"]:
                n = count_missing(self.df2, m["lat_col"], m["lon_col"])
                self.map2.set_missing_note(f"{n} of {len(self.df2)} rows missing coordinates.")

    def _maybe_enable_geocode(self):
        if self.df1 is not None:
            self.geo1_btn.config(state="normal")
            self.export_geo1_btn.config(state="normal")
        if self.df2 is not None:
            self.geo2_btn.config(state="normal")
            self.export_geo2_btn.config(state="normal")
        if self.df1 is not None and self.df2 is not None:
            self.geo_status_var.set("Ready. Click a button to fill in missing coordinates for that file "
                                     "(skips automatically if none are missing).")

    def _maybe_enable_split(self):
        if self.df1 is not None:
            self.split_btn.config(state="normal")

    
    
    
    def _geocode(self, which):
        df = self.df1 if which == 1 else self.df2
        mapping_widget = self.map1 if which == 1 else self.map2
        m = mapping_widget.get_mapping()
        required = [m["street_col"], m["city_col"], m["state_col"], m["lat_col"], m["lon_col"]]
        if not all(required):
            show_error("Missing mapping", f"Please map Street, City, State, Latitude and Longitude for File {which}.")
            return

        missing = count_missing(df, m["lat_col"], m["lon_col"])
        if missing == 0:
            show_info("Nothing to do", f"File {which} has no missing coordinates.")
            return

        self.geo1_btn.config(state="disabled")
        self.geo2_btn.config(state="disabled")
        self.geo_progress.config(value=0, maximum=missing)
        self.geo_status_var.set(f"Geocoding File {which}: 0/{missing}")

        def progress_cb(i, total, addr):
            self.after(0, lambda: (self.geo_progress.config(value=i, maximum=total),
                                    self.geo_status_var.set(f"Geocoding File {which}: {i}/{total} — {addr[:60]}")))

        def worker():
            try:
                new_df, filled, total_missing, failed_addrs = geocode_missing(
                    df, m["street_col"], m["city_col"], m["state_col"],
                    m["lat_col"], m["lon_col"], addr2_col=m["addr2_col"], zip_col=m["zip_col"],
                    progress_cb=progress_cb,
                )
                self.after(0, lambda: self._on_geocode_done(which, new_df, filled, total_missing, failed_addrs))
            except Exception as e:
                self.after(0, lambda: self._on_geocode_error(e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_geocode_error(self, e):
        self.geo1_btn.config(state="normal")
        self.geo2_btn.config(state="normal")
        show_error("Geocoding failed", e)

    def _on_geocode_done(self, which, new_df, filled, total_missing, failed_addrs=None):
        failed_addrs = failed_addrs or []
        if which == 1:
            self.df1 = new_df
            mapping_widget = self.map1
        else:
            self.df2 = new_df
            mapping_widget = self.map2
            
        if mapping_widget.lat_combo.var.get() == "Create 'Latitude'":
            mapping_widget.lat_combo.set("Latitude")
        if mapping_widget.lon_combo.var.get() == "Create 'Longitude'":
            mapping_widget.lon_combo.set("Longitude")
            
        self.geo1_btn.config(state="normal")
        self.geo2_btn.config(state="normal")
        self.geo_status_var.set(f"File {which}: filled {filled} of {total_missing} missing coordinates.")
        self._refresh_missing_note(which)

        
        
        saved_path = self._export_geocoded(which, silent=True)

        msg = f"File {which}: filled {filled} of {total_missing} rows."
        if failed_addrs:
            msg += (f"\n\n{len(failed_addrs)} address(es) could not be located by the geocoder "
                    f"(bad/incomplete address, not an app issue) — the first few:\n"
                    + "\n".join(f"\u2022 {a}" for a in failed_addrs[:5]))
        if saved_path:
            msg += (f"\n\nSaved a copy with the filled-in coordinates to:\n{saved_path}\n"
                    "(your original uploaded file is untouched — check this file, not the original, "
                    "to see the new coordinates)")
        show_info("Geocoding Complete", msg)

    def _export_geocoded(self, which, silent=False):
        """Save the in-memory (possibly geocoded) File 1/2 to a new data
        file next to the original — never overwrites the original upload."""
        df = self.df1 if which == 1 else self.df2
        src_path = self.path1 if which == 1 else self.path2
        if df is None or not src_path:
            if not silent:
                show_error("Nothing to export", f"Load File {which} first.")
            return None
        out_dir = self._current_output_dir()
        base = os.path.splitext(os.path.basename(src_path))[0]
        path = os.path.join(out_dir, f"{base}_geocoded.xlsx")
        try:
            from shared.ui.ui_common import save_dataframe
            path = save_dataframe(df, path)
        except Exception as e:
            if not silent:
                show_error("Export failed", e)
            return None
        if not silent:
            show_info("Exported", f"Saved to:\n{path}")
        return path

    
    
    
    def _run_split(self):
        if self.df1 is None:
            return
        col = self.split_col_combo.get()
        keyword = self.split_keyword_entry.get() or "Hospital"
        if not col:
            show_error("Missing column", "Please choose a search column for the split.")
            return
        try:
            self.hsp_df, self.nonhsp_df = split_by_keyword(self.df1, col, keyword)
        except Exception as e:
            show_error("Split failed", e)
            return
        self.split_status_var.set(
            f"HSP (matches '{keyword}'): {len(self.hsp_df)} rows   |   "
            f"Non-HSP: {len(self.nonhsp_df)} rows"
        )
        self.dist_btn.config(state="normal")

    
    
    
    def _run_distance(self):
        if self.hsp_df is None or self.nonhsp_df is None or self.df2 is None:
            show_error("Not ready", "Complete Step 1-3 first.")
            return
        m1 = self.map1.get_mapping()
        m2 = self.map2.get_mapping()
        if not (m1["lat_col"] and m1["lon_col"] and m2["lat_col"] and m2["lon_col"]):
            show_error("Missing mapping", "Latitude/Longitude columns must be mapped for both files.")
            return
        try:
            hsp_t = float(self.hsp_thresh_entry.get())
            nonhsp_t = float(self.nonhsp_thresh_entry.get())
        except ValueError:
            show_error("Invalid threshold", "Thresholds must be numbers.")
            return

        self.dist_btn.config(state="disabled")
        self.dist_status_var.set("Running distance matching...")
        self.dist_progress.config(value=0, maximum=100)

        def progress_cb(tier, i, total):
            self.after(0, lambda: (self.dist_progress.config(value=i, maximum=max(total, 1)),
                                    self.dist_status_var.set(f"{tier}: {i}/{total}")))

        def worker():
            try:
                combined, dist_stats = run_tiered_distance_matching(
                    self.hsp_df, self.nonhsp_df, self.df2,
                    m1["lat_col"], m1["lon_col"], m2["lat_col"], m2["lon_col"],
                    hsp_threshold_m=hsp_t, nonhsp_threshold_m=nonhsp_t,
                    progress_cb=progress_cb,
                )
                self.after(0, lambda: self._on_distance_done(combined, dist_stats))
            except Exception as e:
                self.after(0, lambda: self._on_distance_error(e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_distance_error(self, e):
        self.dist_btn.config(state="normal")
        show_error("Distance matching failed", e)

    def _on_distance_done(self, combined, dist_stats=None):
        dist_stats = dist_stats or {}
        total_skipped = (dist_stats.get("hsp_skipped", 0) + dist_stats.get("nonhsp_skipped", 0)
                          + dist_stats.get("hsp_ref_skipped", 0) + dist_stats.get("nonhsp_ref_skipped", 0))
        self.stat_skipped.set(total_skipped)
        self.dist_btn.config(state="normal")

        if combined.empty:
            self.paired_df = combined
            self.dist_status_var.set("No pairs found within the given thresholds.")
            self.stat_hsp_pairs.set(0)
            self.stat_nonhsp_pairs.set(0)
            self.stat_total_pairs.set(0)
            return

        
        
        combined = combined.reset_index(drop=True)
        combined.insert(0, ID_COL, [f"P{n:06d}" for n in range(1, len(combined) + 1)])
        self.paired_df = combined

        hsp_n = int((combined["TIER"] == "HSP").sum())
        nonhsp_n = int((combined["TIER"] == "NonHSP").sum())
        self.stat_hsp_pairs.set(hsp_n)
        self.stat_nonhsp_pairs.set(nonhsp_n)
        self.stat_total_pairs.set(len(combined))
        skip_note = f"  ({total_skipped} rows skipped for missing/unusable coordinates)" if total_skipped else ""
        self.dist_status_var.set(f"Done ✅{skip_note}")

        preview_cols = list(combined.columns)[:8]
        rebuild_treeview_columns(self.pairs_tree, preview_cols)
        fill_treeview(self.pairs_tree, combined)

        self.export_pairs_btn.config(state="normal")
        self._populate_ai_mapping(combined)
        self.apply_fuzzy_btn.config(state="normal")
        self.ai_btn.config(state="normal")

    def _export_pairs(self):
        if self.paired_df is None or self.paired_df.empty:
            return
        out_dir = self._current_output_dir()
        path = os.path.join(out_dir, "gpo_distance_pairs.xlsx")
        try:
            from shared.ui.ui_common import save_dataframe
            path = save_dataframe(self.paired_df, path)
        except Exception as e:
            show_error("Export failed", e)
            return
        show_info("Exported", f"Saved to:\n{path}")

    
    
    
    def _populate_ai_mapping(self, combined):
        cols = list(combined.columns)
        m1 = self.map1.get_mapping()
        m2 = self.map2.get_mapping()

        def resolve(col):
            if col in cols:
                return col
            if f"{col}_FILE1" in cols:
                return f"{col}_FILE1"
            if f"{col}_FILE2" in cols:
                return f"{col}_FILE2"
            return None

        name1_default = resolve(m1["name_col"])
        name2_default = resolve(m2["name_col"])
        addr1_default = resolve(m1["street_col"])
        addr2_default = resolve(m2["street_col"])

        opt_cols = [NONE_OPTION] + cols
        self.ai_name1_combo.set_values(opt_cols, default=name1_default)
        self.ai_addr1_combo.set_values(opt_cols, default=addr1_default)
        self.ai_name2_combo.set_values(opt_cols, default=name2_default)
        self.ai_addr2_combo.set_values(opt_cols, default=addr2_default)

    def _run_apply_fuzzy(self):
        if self.paired_df is None or self.paired_df.empty:
            show_error("No pairs", "Run distance matching first.")
            return

        n1, a1 = self.ai_name1_combo.get(), self.ai_addr1_combo.get()
        n2, a2 = self.ai_name2_combo.get(), self.ai_addr2_combo.get()
        if not all([n1, a1, n2, a2]):
            show_error("Missing columns", "Please map all four AI-matching columns before applying Fuzzy.")
            return

        try:
            from thefuzz import fuzz
        except ImportError:
            try:
                from rapidfuzz import fuzz
            except ImportError:
                show_error("Dependency missing", "Neither 'thefuzz' nor 'rapidfuzz' is installed.")
                return

        for col in ["result", "relation", "entity_1_parent", "entity_2_parent", "confidence", "result_reason"]:
            if col not in self.paired_df.columns:
                self.paired_df[col] = pd.Series(dtype=object)
            else:
                self.paired_df[col] = self.paired_df[col].astype(object)

        count = 0
        for idx, row in self.paired_df.iterrows():
            curr_res = str(row.get("result", "")).strip()
            if pd.notna(row.get("result")) and curr_res not in ["", "nan", "None", "<NA>"]:
                continue

            name1 = str(row.get(n1, "")).strip().lower()
            name2 = str(row.get(n2, "")).strip().lower()
            addr1 = str(row.get(a1, "")).strip().lower()
            addr2 = str(row.get(a2, "")).strip().lower()

            name_score = fuzz.token_set_ratio(name1, name2)
            addr_score = fuzz.token_set_ratio(addr1, addr2)

            if name_score >= 80 and addr_score >= 80:
                self.paired_df.at[idx, "result"] = "SAME"
                self.paired_df.at[idx, "relation"] = "SELF"
                self.paired_df.at[idx, "confidence"] = 100
                self.paired_df.at[idx, "result_reason"] = "Fuzzy Match"
                count += 1

        unmatched_count = sum(1 for _, row in self.paired_df.iterrows() if str(row.get("result", "")).strip() in ["", "nan", "None", "<NA>"])
        
        show_info("Fuzzy Match Applied", 
                  f"Successfully matched {count} pairs using 80% threshold for both name and address.\n\n"
                  f"'Proceed to AI Matching' will only run on the remaining {unmatched_count} unmatched pairs.")

    def _run_ai(self):
        if self.paired_df is None or self.paired_df.empty:
            show_error("No pairs", "Run distance matching first.")
            return
        api_key = (load_api_key() or "").strip()
        if not api_key:
            exists, _ = env_file_status()
            if exists:
                detail = (f"Found the .env file, but couldn't find a valid GEMINI_API_KEY= line in it:\n{ENV_PATH}\n\n"
                           "Double-check it looks exactly like this (no extra quotes/spaces needed):\n"
                           "GEMINI_API_KEY=your-key-here\n\n"
                           "If you edited it in Notepad, try 'Save As' \u2192 Encoding: UTF-8 (not "
                           "'UTF-8 with BOM') and save again.")
            else:
                detail = (f"No .env file found at:\n{ENV_PATH}\n\n"
                           "Create it and add this line:\nGEMINI_API_KEY=your-key-here")
            show_error("Missing API key", detail)
            return

        n1, a1 = self.ai_name1_combo.get(), self.ai_addr1_combo.get()
        n2, a2 = self.ai_name2_combo.get(), self.ai_addr2_combo.get()
        if not all([n1, a1, n2, a2]):
            show_error("Missing columns", "Please map all four AI-matching columns.")
            return

        out_dir = self._current_output_dir()
        self.ai_stop_flag = threading.Event()
        self.ai_running = True
        self.ai_btn.config(state="disabled")
        self.ai_stop_btn.config(state="normal")
        self.ai_export_partial_btn.config(state="normal")
        self.ai_status_var.set("Starting AI matching (resumes from cache automatically)...")
        n_rows = len(self.paired_df)
        self.ai_progress.config(value=0, maximum=n_rows)

        def progress_cb(done, total, elapsed, stopped=False):
            def upd():
                self.ai_progress.config(value=done, maximum=max(total, 1))
                speed = f"{(done / elapsed):.1f} rows/s" if elapsed and elapsed > 0 else ""
                suffix = " (stopped — click Resume to continue)" if stopped else ""
                self.ai_status_var.set(f"Processed {done}/{total} {speed}{suffix}")
            self.after(0, upd)

        def worker():
            try:
                result_df, excel_path, html_path, stats = run_ai_matching(
                    self.paired_df, api_key, n1, a1, n2, a2,
                    id_col=ID_COL, output_dir=out_dir,
                    batch_size=AI_BATCH_SIZE, max_concurrent=AI_MAX_CONCURRENT, model=AI_MODEL,
                    progress_cb=progress_cb, stop_flag=self.ai_stop_flag,
                )
                self.after(0, lambda: self._on_ai_done(result_df, excel_path, html_path, stats))
            except Exception as e:
                self.after(0, lambda e=e: self._on_ai_error(e))

        threading.Thread(target=worker, daemon=True).start()

    def _stop_ai(self):
        if self.ai_stop_flag is not None:
            self.ai_stop_flag.set()
            self.ai_status_var.set("Stopping... (finishing current batch)")
            self.ai_stop_btn.config(state="disabled")

    def _export_ai_partial(self):
        """Export whatever AI results exist so far (from the on-disk cache),
        even mid-run or after a Stop, without waiting for the full run."""
        if self.paired_df is None or self.paired_df.empty:
            return
        out_dir = self._current_output_dir()
        cache_csv = os.path.join(out_dir, "ai_cache_results.csv")
        if not os.path.exists(cache_csv):
            show_info("Nothing yet", "No AI results have been cached yet.")
            return
        try:
            cache_df = pd.read_csv(cache_csv)
            merged = self.paired_df.merge(cache_df, on=ID_COL, how="left")
        except Exception as e:
            show_error("Export failed", e)
            return
        path = os.path.join(out_dir, "gpo_ai_matching_partial_results.xlsx")
        try:
            from shared.ui.ui_common import save_dataframe
            path = save_dataframe(merged, path)
        except Exception as e:
            show_error("Export failed", e)
            return
        done_n = int(merged["result"].notna().sum()) if "result" in merged.columns else 0
        show_info("Exported", f"Saved {done_n} of {len(merged)} completed rows so far to:\n{path}")

    def _on_ai_error(self, e):
        self.ai_running = False
        self.ai_btn.config(state="normal", text="▶ Proceed to AI Matching")
        self.ai_stop_btn.config(state="disabled")
        show_error("AI matching failed", e)

    def _on_ai_done(self, result_df, excel_path, html_path, stats):
        self.ai_result_df = result_df
        self.ai_excel_path = excel_path
        self.ai_html_path = html_path
        self.ai_out_dir = self._current_output_dir()
        self.ai_running = False

        completed_all = stats["same"] + stats["affiliated"] + stats["different"] + stats["error"] >= len(self.paired_df)
        self.ai_btn.config(state="normal", text="▶ Proceed to AI Matching" if completed_all else "▶ Resume AI Matching")
        self.ai_stop_btn.config(state="disabled")
        self.ai_status_var.set("Done ✅" if completed_all else "Stopped — click Resume to continue")
        self.stat_same.set(stats["same"])
        self.stat_aff.set(stats["affiliated"])
        self.stat_diff2.set(stats["different"])
        self.stat_err.set(stats["error"])

        self.open_ai_excel_btn.config(state="normal")
        self.open_ai_folder_btn.config(state="normal")
        if html_path:
            self.open_ai_html_btn.config(state="normal")

        if completed_all:
            show_info("AI Matching Complete",
                       f"SAME: {stats['same']}  AFFILIATED: {stats['affiliated']}  "
                       f"DIFFERENT: {stats['different']}  ERROR: {stats['error']}\n\nSaved to:\n{excel_path}")
        else:
            show_info("AI Matching Stopped",
                       f"Progress saved. So far — SAME: {stats['same']}  AFFILIATED: {stats['affiliated']}  "
                       f"DIFFERENT: {stats['different']}  ERROR: {stats['error']}\n\n"
                       f"Partial results saved to:\n{excel_path}\n\nClick 'Resume AI Matching' to continue.")

    def _open_ai_excel(self):
        if getattr(self, "ai_excel_path", None):
            open_path(self.ai_excel_path)

    def _open_ai_html(self):
        if getattr(self, "ai_html_path", None):
            open_path(self.ai_html_path)

    def _open_ai_folder(self):
        if getattr(self, "ai_out_dir", None):
            open_path(self.ai_out_dir)
