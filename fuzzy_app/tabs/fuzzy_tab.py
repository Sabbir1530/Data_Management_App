"""
tabs/fuzzy_tab.py
GUI tab for the weighted Fuzzy Matching module.
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
from fuzzy_app.logic.fuzzy_match import run_fuzzy_matching, DEFAULT_PRIORITY_KEYWORDS
from fuzzy_app.logic.geocode import geocode_missing, count_missing

NAME1_CANDIDATES = ["MMBR_NM", "EU_NAME", "ENTITY1_NAME"]
NAME2_CANDIDATES = ["ENDUSER_NAME", "CMPNY_NM", "ENTITY2_NAME"]
ADDR1_CANDIDATES = ["MMBR_ADDR_1", "EU_ADDR", "ENTITY1_ADDR"]
ADDR2_CANDIDATES = ["ENDUSER_ADDR_1", "CMPNY_ADDR_1", "ENTITY2_ADDR"]

# Name/Address weighting is fixed (no longer exposed in the UI) — the
# acceptance threshold applies to the resulting average score.
NAME_WEIGHT = 70
ADDR_WEIGHT = 30


class FuzzyMatchTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=0)
        self.df = None
        self.input_path = None
        self.output_dir = None
        self.df_matches = None
        self.df_differences = None

        scroller = ScrollableFrame(self)
        scroller.pack(fill="both", expand=True)
        self.body = scroller.body

        self._build_ui()

    # ------------------------------------------------------------------
    def _build_ui(self):
        # --- File input section -----------------------------------------
        sec_file = Section(self.body, "1. Input File")
        sec_file.pack(fill="x", padx=16, pady=(16, 10))
        self.file_picker = FilePicker(sec_file, "Data file:", on_pick=self._on_file_loaded)
        self.file_picker.pack(fill="x")

        # --- Column mapping section ---------------------------------------
        sec_cols = Section(self.body, "2. Column Mapping (auto-detected — change if needed)")
        sec_cols.pack(fill="x", padx=16, pady=(0, 10))

        # NOTE: this row is its own frame (packed on its own line, fill="x")
        # so it behaves as a single side="top" block. Packing `left`/`right`
        # directly into sec_cols with side="left" would claim the *full
        # height* of sec_cols for their column, which pushes every later
        # side="top" sibling (the distance checkbox, coordinate mapping,
        # geocode buttons) into the leftover sliver to the right instead of
        # a new row underneath — that was the "everything crammed on the
        # right" layout bug.
        mapping_row = ttk.Frame(sec_cols)
        mapping_row.pack(fill="x")

        left = ttk.Frame(mapping_row)
        left.pack(side="left", fill="x", expand=True, padx=(0, 20))
        right = ttk.Frame(mapping_row)
        right.pack(side="left", fill="x", expand=True)

        ttk.Label(left, text="Entity 1 (e.g. your Member file)", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.name1_combo = LabeledCombo(left, "Name column:")
        self.name1_combo.pack(anchor="w", pady=2)
        self.addr1_combo = LabeledCombo(left, "Street Address column:")
        self.addr1_combo.pack(anchor="w", pady=2)
        self.city1_combo = LabeledCombo(left, "City column (optional):", editable=True)
        self.city1_combo.pack(anchor="w", pady=2)
        self.state1_combo = LabeledCombo(left, "State column (optional):", editable=True)
        self.state1_combo.pack(anchor="w", pady=2)
        self.zip1_combo = LabeledCombo(left, "Zip column (optional):", editable=True)
        self.zip1_combo.pack(anchor="w", pady=2)

        ttk.Label(right, text="Entity 2 (e.g. GPO / End-User file)", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.name2_combo = LabeledCombo(right, "Name column:")
        self.name2_combo.pack(anchor="w", pady=2)
        self.addr2_combo = LabeledCombo(right, "Street Address column:")
        self.addr2_combo.pack(anchor="w", pady=2)
        self.city2_combo = LabeledCombo(right, "City column (optional):", editable=True)
        self.city2_combo.pack(anchor="w", pady=2)
        self.state2_combo = LabeledCombo(right, "State column (optional):", editable=True)
        self.state2_combo.pack(anchor="w", pady=2)
        self.zip2_combo = LabeledCombo(right, "Zip column (optional):", editable=True)
        self.zip2_combo.pack(anchor="w", pady=2)

        # --- Optional distance (Lat/Lon) mapping ---------------------------
        # This whole block is its own side="top" row too, so it always
        # renders as a new full-width section BELOW the name/address
        # mapping above — never squeezed beside it.
        self.use_distance_var = tk.BooleanVar(value=False)
        dist_toggle_row = ttk.Frame(sec_cols)
        dist_toggle_row.pack(fill="x", pady=(10, 0))
        ttk.Checkbutton(
            dist_toggle_row, text="Also use distance (Latitude/Longitude) when suggesting matches",
            variable=self.use_distance_var, command=self._toggle_distance_frame,
        ).pack(anchor="w")

        self.distance_frame = ttk.Frame(sec_cols)
        # not packed by default — shown only when the checkbox above is ticked

        ttk.Label(self.distance_frame, text="Entity 1 coordinates", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(8, 0))
        e1_row = ttk.Frame(self.distance_frame)
        e1_row.pack(fill="x")
        self.lat1_combo = LabeledCombo(e1_row, "Latitude column:", editable=True)
        self.lat1_combo.pack(side="left", padx=(0, 30))
        self.lon1_combo = LabeledCombo(e1_row, "Longitude column:", editable=True)
        self.lon1_combo.pack(side="left")
        geo1_row = ttk.Frame(self.distance_frame)
        geo1_row.pack(fill="x", pady=(4, 0))
        self.geocode1_btn = ttk.Button(
            geo1_row, text="📍 Geocode Entity 1", command=lambda: self._geocode_entity(1),
            state="disabled", style="Accent.TButton",
        )
        self.geocode1_btn.pack(side="left")
        self.missing1_var = tk.StringVar(value="")
        ttk.Label(geo1_row, textvariable=self.missing1_var, foreground="#b45309").pack(side="left", padx=10)

        ttk.Label(self.distance_frame, text="Entity 2 coordinates", font=("Segoe UI", 9, "bold")).pack(anchor="w", pady=(12, 0))
        e2_row = ttk.Frame(self.distance_frame)
        e2_row.pack(fill="x")
        self.lat2_combo = LabeledCombo(e2_row, "Latitude column:", editable=True)
        self.lat2_combo.pack(side="left", padx=(0, 30))
        self.lon2_combo = LabeledCombo(e2_row, "Longitude column:", editable=True)
        self.lon2_combo.pack(side="left")
        geo2_row = ttk.Frame(self.distance_frame)
        geo2_row.pack(fill="x", pady=(4, 0))
        self.geocode2_btn = ttk.Button(
            geo2_row, text="📍 Geocode Entity 2", command=lambda: self._geocode_entity(2),
            state="disabled", style="Accent.TButton",
        )
        self.geocode2_btn.pack(side="left")
        self.missing2_var = tk.StringVar(value="")
        ttk.Label(geo2_row, textvariable=self.missing2_var, foreground="#b45309").pack(side="left", padx=10)

        geo_status_row = ttk.Frame(self.distance_frame)
        geo_status_row.pack(fill="x", pady=(8, 0))
        self.geo_progress = ttk.Progressbar(geo_status_row, mode="determinate", length=300)
        self.geo_progress.pack(side="left")
        self.geo_status_var = tk.StringVar(value="")
        ttk.Label(geo_status_row, textvariable=self.geo_status_var, foreground="#555").pack(side="left", padx=10)
        
        self.btn_create_cols = ttk.Button(self.distance_frame, text="➕ Create Empty Lat/Lon", state="disabled")
        self.btn_create_cols.pack(anchor="w", pady=(4, 0))
        
        ttk.Label(
            self.distance_frame,
            text="If Entity 1 / Entity 2 already have Latitude & Longitude columns in this same data "
                 "file, they're auto-detected above (edit if wrong). Use the matching button to fill in "
                 "any coordinates that are missing, geocoded from that entity's own Address column — "
                 "just like the GPO Matching tab.",
            foreground="#555", wraplength=1000, justify="left",
        ).pack(anchor="w", pady=(6, 0))

        # --- Suggestion mapping -------------------------------------------
        self.use_suggestion_var = tk.BooleanVar(value=False)
        sugg_toggle_row = ttk.Frame(sec_cols)
        sugg_toggle_row.pack(fill="x", pady=(10, 0))
        ttk.Checkbutton(
            sugg_toggle_row, text="Generate a 'Suggested' best match for each Entity 2 group",
            variable=self.use_suggestion_var, command=self._toggle_suggestion_frame,
        ).pack(anchor="w")

        self.suggestion_frame = ttk.Frame(sec_cols)
        sugg_row = ttk.Frame(self.suggestion_frame)
        sugg_row.pack(fill="x", pady=(8, 0))
        self.suggestion_combo = LabeledCombo(sugg_row, "Group by ID column (e.g. EndUser ID):", editable=True)
        self.suggestion_combo.pack(side="left")

        # --- Settings section -----------------------------------------
        sec_settings = Section(self.body, "3. Matching Settings")
        sec_settings.pack(fill="x", padx=16, pady=(0, 10))
        row1 = ttk.Frame(sec_settings)
        row1.pack(fill="x")
        self.threshold_entry = LabeledEntry(row1, "Acceptance threshold (avg %):", default="75", width=8)
        self.threshold_entry.pack(side="left", padx=(0, 20))
        ttk.Label(
            row1,
            text=f"(Name {NAME_WEIGHT}% + Address {ADDR_WEIGHT}% \u2192 average score, compared to this threshold)",
            foreground="#555",
        ).pack(side="left")

        row2 = ttk.Frame(sec_settings)
        row2.pack(fill="x", pady=(6, 0))
        self.priority_entry = LabeledEntry(
            row2, "Priority keywords (comma-sep):",
            default=", ".join(DEFAULT_PRIORITY_KEYWORDS), width=50,
        )
        self.priority_entry.pack(side="left")

        # --- Output + run section -----------------------------------------
        sec_run = Section(self.body, "4. Output & Run")
        sec_run.pack(fill="x", padx=16, pady=(0, 10))
        out_row = ttk.Frame(sec_run)
        out_row.pack(fill="x")
        ttk.Label(out_row, text="Output folder:", width=22, anchor="w").pack(side="left")
        self.out_dir_var = tk.StringVar(value="(same as input file)")
        ttk.Label(out_row, textvariable=self.out_dir_var, foreground="#555", width=48, anchor="w").pack(side="left", padx=6)
        ttk.Button(out_row, text="Choose...", command=self._choose_output_dir).pack(side="left")

        run_row = ttk.Frame(sec_run)
        run_row.pack(fill="x", pady=(10, 0))
        self.run_btn = ttk.Button(run_row, text="▶ Run Fuzzy Matching", command=self._run_clicked, style="Primary.TButton")
        self.run_btn.pack(side="left")
        self.progress = ttk.Progressbar(run_row, mode="determinate", length=260)
        self.progress.pack(side="left", padx=12)
        self.status_var = tk.StringVar(value="Idle")
        ttk.Label(run_row, textvariable=self.status_var, foreground="#555").pack(side="left")

        # --- Results section -----------------------------------------
        sec_results = Section(self.body, "5. Summary & Preview")
        sec_results.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        stats_row = ttk.Frame(sec_results)
        stats_row.pack(fill="x", pady=(0, 10))
        self.stat_total = StatCard(stats_row, "Total Rows", color="#1a202c")
        self.stat_total.pack(side="left", padx=4)
        self.stat_matched = StatCard(stats_row, "Matched", color="#047857")
        self.stat_matched.pack(side="left", padx=4)
        self.stat_diff = StatCard(stats_row, "Different", color="#b91c1c")
        self.stat_diff.pack(side="left", padx=4)
        self.stat_suggested = StatCard(stats_row, "Suggested", color="#b45309")
        self.stat_suggested.pack(side="left", padx=4)
        self.stat_rate = StatCard(stats_row, "Match Rate %", color="#2563eb")
        self.stat_rate.pack(side="left", padx=4)

        btn_row = ttk.Frame(sec_results)
        btn_row.pack(fill="x", pady=(0, 8))
        self.open_matches_btn = ttk.Button(btn_row, text="Open Matched File", command=self._open_matches, state="disabled")
        self.open_matches_btn.pack(side="left", padx=(0, 8))
        self.open_diffs_btn = ttk.Button(btn_row, text="Open Differences File", command=self._open_diffs, state="disabled")
        self.open_diffs_btn.pack(side="left", padx=(0, 8))
        self.open_folder_btn = ttk.Button(btn_row, text="Open Output Folder", command=self._open_folder, state="disabled")
        self.open_folder_btn.pack(side="left")

        self.preview_frame, self.preview_tree = make_treeview(
            sec_results, columns=("MATCH_SCORE", "NAME_SCORE", "ADDR_SCORE", "SUGGESTION"), height=8,
        )
        self.preview_frame.pack(fill="both", expand=True)

    # ------------------------------------------------------------------
    def _on_file_loaded(self, path):
        try:
            if path.lower().endswith('.csv'):
                self.df = pd.read_csv(path, low_memory=False)
            else:
                self.df = pd.read_excel(path)
        except Exception as e:
            show_error("Failed to read file", e)
            return
        self.input_path = path
        self._update_column_mappings(list(self.df.columns))

    def _update_column_mappings(self, cols):
        opt_cols = [""] + cols

        self.name1_combo.set_values(opt_cols, default=guess_column(opt_cols, NAME1_CANDIDATES, ["mmbr", "member", "nm", "name"], fallback="", required=False, reverse=False))
        self.addr1_combo.set_values(opt_cols, default=guess_column(opt_cols, ADDR1_CANDIDATES, ["mmbr", "member", "addr", "street"], fallback="", required=False, reverse=False))
        self.city1_combo.set_values(opt_cols, default=guess_column(opt_cols, ["MMBR_CITY", "CITY1", "CITY"], ["mmbr", "member", "city"], fallback="", required=False, reverse=False))
        self.state1_combo.set_values(opt_cols, default=guess_column(opt_cols, ["MMBR_ST", "STATE1", "STATE"], ["mmbr", "member", "st", "state"], fallback="", required=False, reverse=False))
        self.zip1_combo.set_values(opt_cols, default=guess_column(opt_cols, ["MMBR_ZIP", "ZIP1", "ZIP"], ["mmbr", "member", "zip"], fallback="", required=False, reverse=False))

        self.name2_combo.set_values(opt_cols, default=guess_column(opt_cols, NAME2_CANDIDATES, ["enduser", "cmpny", "company", "nm", "name"], fallback="", required=False, reverse=True))
        self.addr2_combo.set_values(opt_cols, default=guess_column(opt_cols, ADDR2_CANDIDATES, ["enduser", "cmpny", "company", "addr", "street"], fallback="", required=False, reverse=True))
        self.city2_combo.set_values(opt_cols, default=guess_column(opt_cols, ["ENDUSER_CITY", "CMPNY_CITY", "CITY2", "CITY"], ["enduser", "cmpny", "company", "city"], fallback="", required=False, reverse=True))
        self.state2_combo.set_values(opt_cols, default=guess_column(opt_cols, ["ENDUSER_ST", "CMPNY_ST", "STATE2", "STATE"], ["enduser", "cmpny", "company", "st", "state"], fallback="", required=False, reverse=True))
        self.zip2_combo.set_values(opt_cols, default=guess_column(opt_cols, ["ENDUSER_ZIP", "CMPNY_ZIP", "ZIP2", "ZIP"], ["enduser", "cmpny", "company", "zip"], fallback="", required=False, reverse=True))

        # Auto-detect Entity 1/2 Lat/Lon columns if this same file already
        # has them (populated whether or not the distance checkbox is on,
        # so they're ready the moment the user ticks it). Left blank (not
        # defaulted to an unrelated column) when nothing matches — these
        # are optional, so guessing wrong would be actively misleading.
        #
        # Both entities live in the SAME file/column list here (unlike the
        # GPO tab, which has two separate files), so a naive "find the
        # first column containing 'lat'" would hand Entity 1 *and* Entity 2
        # the exact same column. `used` is tracked across all four guesses
        # so each pick is a distinct column.
        used = set()

        def _pick(tokens):
            available = [c for c in cols if c not in used]
            g = guess_column(available, [], tokens, required=False)
            if g:
                used.add(g)
            return g

        lat1_guess = _pick(["lat"])
        lon1_guess = _pick(["lon", "lng"])
        lat2_guess = _pick(["lat"])
        lon2_guess = _pick(["lon", "lng"])

        lat_opts = list(opt_cols)
        lon_opts = list(opt_cols)
        cols_lower = [c.lower() for c in cols]
        
        if not any("lat" in c for c in cols_lower):
            lat_opts.append("Create 'Latitude'")
        if not any(x in c for c in cols_lower for x in ["lon", "lng"]):
            lon_opts.append("Create 'Longitude'")

        for combo, guess, opts in (
            (self.lat1_combo, lat1_guess or ("Create 'Latitude'" if "Create 'Latitude'" in lat_opts else ""), lat_opts),
            (self.lon1_combo, lon1_guess or ("Create 'Longitude'" if "Create 'Longitude'" in lon_opts else ""), lon_opts),
            (self.lat2_combo, lat2_guess or ("Create 'Latitude'" if "Create 'Latitude'" in lat_opts else ""), lat_opts),
            (self.lon2_combo, lon2_guess or ("Create 'Longitude'" if "Create 'Longitude'" in lon_opts else ""), lon_opts),
        ):
            combo.set_values(opts, default=guess)

        sugg_guess = guess_column(opt_cols, ["ENDUSER_ID", "CMPNY_ID", "ID2", "ENTITY2_ID"], ["enduser", "cmpny", "company", "id"], fallback=self.name2_combo.get() or "", required=False)
        self.suggestion_combo.set_values(opt_cols, default=sugg_guess)

        if "Latitude" not in self.df.columns or "Longitude" not in self.df.columns:
            self.btn_create_cols.config(state="normal", command=self._create_empty_cols)
        else:
            self.btn_create_cols.config(state="disabled")

        self.geocode1_btn.config(state="normal")
        self.geocode2_btn.config(state="normal")
        self._refresh_missing_note(1)
        self._refresh_missing_note(2)

        self.status_var.set(f"Loaded {len(self.df)} rows, {len(cols)} columns.")

    def _create_empty_cols(self):
        if self.df is not None:
            if "Latitude" not in self.df.columns:
                self.df["Latitude"] = None
            if "Longitude" not in self.df.columns:
                self.df["Longitude"] = None
            
            # Refresh UI combos with new columns
            self._update_column_mappings(list(self.df.columns))

    def _refresh_missing_note(self, which):
        if self.df is None:
            return
        lat_combo = self.lat1_combo if which == 1 else self.lat2_combo
        lon_combo = self.lon1_combo if which == 1 else self.lon2_combo
        note_var = self.missing1_var if which == 1 else self.missing2_var
        lat, lon = lat_combo.get(), lon_combo.get()
        if lat and lon:
            n = count_missing(self.df, lat, lon)
            note_var.set(f"{n} of {len(self.df)} rows missing coordinates.")
        else:
            note_var.set("")

    def _toggle_distance_frame(self):
        if self.use_distance_var.get():
            self.distance_frame.pack(fill="x", pady=(8, 0))
        else:
            self.distance_frame.pack_forget()

    def _toggle_suggestion_frame(self):
        if self.use_suggestion_var.get():
            self.suggestion_frame.pack(fill="x", pady=(4, 0))
        else:
            self.suggestion_frame.pack_forget()

    def _geocode_entity(self, which):
        """Geocode missing coordinates for just Entity 1 or Entity 2 —
        mirrors the GPO tab's per-file "Geocode File 1"/"Geocode File 2"
        logic, writing back to the combined input dataframe."""
        if self.df is None:
            show_error("No file loaded", "Please choose a data file first.")
            return

        addr_combo = self.addr1_combo if which == 1 else self.addr2_combo
        city_combo = self.city1_combo if which == 1 else self.city2_combo
        state_combo = self.state1_combo if which == 1 else self.state2_combo
        zip_combo = self.zip1_combo if which == 1 else self.zip2_combo
        lat_combo = self.lat1_combo if which == 1 else self.lat2_combo
        lon_combo = self.lon1_combo if which == 1 else self.lon2_combo
        addr = addr_combo.get()
        city = city_combo.get()
        state = state_combo.get()
        zip_val = zip_combo.get()
        lat, lon = lat_combo.get(), lon_combo.get()
        if not all([addr, lat, lon]):
            show_error("Missing mapping",
                       f"Please choose Entity {which}'s Address column (Step 2 above) and its "
                       f"Latitude/Longitude columns first.")
            return

        self.geocode1_btn.config(state="disabled")
        self.geocode2_btn.config(state="disabled")
        self.geo_status_var.set(f"Entity {which}: starting...")
        self.geo_progress.config(value=0, maximum=100)

        def progress_cb(i, total, current_addr):
            self.after(0, lambda: (self.geo_progress.config(value=i, maximum=max(total, 1)),
                                    self.geo_status_var.set(f"Entity {which}: {i}/{total}")))

        def worker():
            try:
                new_df, filled, missing, failed = geocode_missing(
                    self.df, addr, city, state, lat, lon, zip_col=zip_val, progress_cb=progress_cb,
                )
                self.after(0, lambda: self._on_geocode_done(which, new_df, filled, missing, failed))
            except Exception as exc:
                self.after(0, lambda e=exc: self._on_geocode_error(e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_geocode_error(self, e):
        self.geocode1_btn.config(state="normal")
        self.geocode2_btn.config(state="normal")
        show_error("Geocoding failed", e)

    def _on_geocode_done(self, which, new_df, filled, missing, failed):
        self.df = new_df
        
        lat_combo = self.lat1_combo if which == 1 else self.lat2_combo
        lon_combo = self.lon1_combo if which == 1 else self.lon2_combo
        if lat_combo.var.get() == "Create 'Latitude'":
            lat_combo.set("Latitude")
        if lon_combo.var.get() == "Create 'Longitude'":
            lon_combo.set("Longitude")
            
        self.geocode1_btn.config(state="normal")
        self.geocode2_btn.config(state="normal")
        self.geo_status_var.set("Done ✅")
        self._refresh_missing_note(which)
        msg = f"Entity {which}: filled {filled} of {missing} rows."
        if failed:
            msg += (f"\n\n{len(failed)} address(es) could not be located by the geocoder "
                    f"(bad/incomplete address) — the first few:\n"
                    + "\n".join(f"\u2022 {a}" for a in failed[:5]))
        show_info("Geocoding Complete", msg)

    def _choose_output_dir(self):
        folder = browse_folder()
        if folder:
            self.output_dir = folder
            self.out_dir_var.set(folder)

    def _run_clicked(self):
        if self.df is None:
            show_error("No file loaded", "Please choose a data file first.")
            return

        name1 = self.name1_combo.get()
        addr1 = self.addr1_combo.get()
        city1 = self.city1_combo.get()
        state1 = self.state1_combo.get()
        zip1 = self.zip1_combo.get()

        name2 = self.name2_combo.get()
        addr2 = self.addr2_combo.get()
        city2 = self.city2_combo.get()
        state2 = self.state2_combo.get()
        zip2 = self.zip2_combo.get()
        
        if not all([name1, addr1, name2, addr2]):
            show_error("Missing columns", "Please select at least Name and Street Address column mappings.")
            return
        self._cols_used = (name1, addr1, name2, addr2)
        
        generate_suggestions = self.use_suggestion_var.get()
        suggestion_col = self.suggestion_combo.get() if generate_suggestions else None
        if generate_suggestions and not suggestion_col:
            show_error("Missing suggestion column", "Please select a column to group by for suggestions.")
            return

        lat1 = lon1 = lat2 = lon2 = None
        if self.use_distance_var.get():
            lat1, lon1 = self.lat1_combo.get(), self.lon1_combo.get()
            lat2, lon2 = self.lat2_combo.get(), self.lon2_combo.get()
            if not all([lat1, lon1, lat2, lon2]):
                show_error("Missing coordinate columns",
                           "Distance matching is on — please select all four Latitude/Longitude "
                           "columns (or use 'Geocode Missing Coordinates' first).")
                return

        # Concatenate address components into full strings for better fuzzy matching
        df_run = self.df.copy()
        
        def _build_full_address(row, addr_col, city_col, state_col, zip_col):
            parts = [str(row.get(addr_col, "")).strip() if pd.notna(row.get(addr_col)) else ""]
            if city_col and pd.notna(row.get(city_col)):
                parts.append(str(row.get(city_col, "")).strip())
            if state_col and pd.notna(row.get(state_col)):
                parts.append(str(row.get(state_col, "")).strip())
            if zip_col and pd.notna(row.get(zip_col)):
                parts.append(str(row.get(zip_col, "")).strip())
            return " ".join(p for p in parts if p and p.lower() != "nan")

        full_addr1_col = "__full_addr1"
        full_addr2_col = "__full_addr2"
        df_run[full_addr1_col] = df_run.apply(lambda r: _build_full_address(r, addr1, city1, state1, zip1), axis=1)
        df_run[full_addr2_col] = df_run.apply(lambda r: _build_full_address(r, addr2, city2, state2, zip2), axis=1)

        try:
            threshold = float(self.threshold_entry.get())
        except ValueError:
            show_error("Invalid settings", "Threshold must be a number.")
            return
        name_w, addr_w = NAME_WEIGHT, ADDR_WEIGHT

        keywords = [k.strip() for k in self.priority_entry.get().split(",") if k.strip()]

        self.run_btn.config(state="disabled")
        self.progress.config(value=0, maximum=len(self.df))
        self.status_var.set("Running...")

        def progress_cb(i, total):
            self.after(0, lambda: (self.progress.config(value=i, maximum=total),
                                    self.status_var.set(f"Scoring row {i}/{total}")))

        def worker():
            try:
                matches, diffs, stats = run_fuzzy_matching(
                    df_run, name1, full_addr1_col, name2, full_addr2_col,
                    threshold=threshold, name_weight=name_w, addr_weight=addr_w,
                    priority_keywords=keywords, progress_cb=progress_cb,
                    lat1_col=lat1, lon1_col=lon1, lat2_col=lat2, lon2_col=lon2,
                    state1_col=state1, state2_col=state2,
                    generate_suggestions=generate_suggestions,
                    suggestion_group_col=suggestion_col,
                )
                
                # We drop the temporary full address columns before returning
                for df_to_clean in (matches, diffs):
                    df_to_clean.drop(columns=[full_addr1_col, full_addr2_col], inplace=True, errors="ignore")

                self.after(0, lambda: self._on_done(matches, diffs, stats))
            except Exception as exc:
                self.after(0, lambda e=exc: self._on_error(e))

        threading.Thread(target=worker, daemon=True).start()

    def _on_error(self, e):
        self.run_btn.config(state="normal")
        self.status_var.set("Failed")
        show_error("Fuzzy matching failed", e)

    def _on_done(self, matches, diffs, stats):
        self.df_matches = matches
        self.df_differences = diffs

        out_dir = self.output_dir or os.path.dirname(self.input_path) or "."
        base = os.path.splitext(os.path.basename(self.input_path))[0]
        self.matches_path = os.path.join(out_dir, f"{base}_matched.xlsx")
        self.diffs_path = os.path.join(out_dir, f"{base}_different.xlsx")
        self.output_dir_used = out_dir

        try:
            from shared.ui.ui_common import save_dataframe
            self.matches_path = save_dataframe(matches, self.matches_path)
            self.diffs_path = save_dataframe(diffs, self.diffs_path)
        except Exception as e:
            self.run_btn.config(state="normal")
            show_error("Failed to save output files", e)
            return

        self.stat_total.set(stats["total"])
        self.stat_matched.set(stats["matched"])
        self.stat_diff.set(stats["different"])
        self.stat_suggested.set(stats["suggested"])
        self.stat_rate.set(f"{stats['match_rate']}%")

        # Preview only the "Suggested" rows, showing just the essentials:
        # Entity 1 name/address, Entity 2 name/address, average score, suggestion.
        # Wrapped defensively so a preview hiccup can never block the rest
        # of this handler (buttons/status/dialog) from completing.
        try:
            name1, addr1, name2, addr2 = self._cols_used
            preview_cols = []
            for c in [name1, addr1, name2, addr2, "DISTANCE_M", "MATCH_SCORE", "SUGGESTION"]:
                if c in matches.columns and c not in preview_cols:
                    preview_cols.append(c)
            rebuild_treeview_columns(self.preview_tree, preview_cols)
            if not matches.empty and "SUGGESTION" in matches.columns and preview_cols:
                suggested_only = matches.loc[matches["SUGGESTION"] == "Suggested", preview_cols]
            else:
                suggested_only = matches.iloc[0:0]
            fill_treeview(self.preview_tree, suggested_only)
        except Exception as e:
            show_error("Preview failed to build (results were still saved)", e)

        self.run_btn.config(state="normal")
        self.status_var.set("Done ✅")
        self.open_matches_btn.config(state="normal")
        self.open_diffs_btn.config(state="normal")
        self.open_folder_btn.config(state="normal")
        show_info("Fuzzy Matching Complete",
                   f"Matched: {stats['matched']}  |  Different: {stats['different']}\n"
                   f"Saved to:\n{self.matches_path}\n{self.diffs_path}")

    def _open_matches(self):
        if getattr(self, "matches_path", None):
            open_path(self.matches_path)

    def _open_diffs(self):
        if getattr(self, "diffs_path", None):
            open_path(self.diffs_path)

    def _open_folder(self):
        if getattr(self, "output_dir_used", None):
            open_path(self.output_dir_used)
