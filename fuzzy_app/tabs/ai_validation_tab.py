"""
tabs/ai_validation_tab.py
GUI tab for AI-driven validation (Pairing and Find Health System).
"""
import os
import threading
import tkinter as tk
from tkinter import ttk
import pandas as pd

from shared.ui.ui_common import (
    LabeledCombo, LabeledEntry, FilePicker, Section, ScrollableFrame,
    show_error, show_info, browse_folder, guess_column, StatCard, open_path
)
from fuzzy_app.logic.ai_matcher import run_ai_matching
from fuzzy_app.logic.find_hs import run_find_hs
from fuzzy_app.logic.config import load_api_key

class AiValidationTab(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, padding=0)
        
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)
        
        self.pairing_tab = ttk.Frame(notebook)
        self.find_hs_tab = ttk.Frame(notebook)
        
        notebook.add(self.pairing_tab, text="  🔗 AI Pairing Validation  ")
        notebook.add(self.find_hs_tab, text="  🏥 Find Health System (HS)  ")
        
        self._build_pairing_ui()
        self._build_find_hs_ui()

    def _build_pairing_ui(self):
        self.pair_df = None
        self.pair_input_path = None
        
        scroller = ScrollableFrame(self.pairing_tab)
        scroller.pack(fill="both", expand=True)
        body = scroller.body
        
        # File Input
        sec_file = Section(body, "1. Input File (Excel/CSV)")
        sec_file.pack(fill="x", padx=16, pady=(16, 10))
        self.pair_file_picker = FilePicker(sec_file, "File:", on_pick=self._on_pair_file_loaded)
        self.pair_file_picker.pack(fill="x")
        
        # Mapping
        sec_cols = Section(body, "2. Column Mapping")
        sec_cols.pack(fill="x", padx=16, pady=(0, 10))
        
        mapping_row = ttk.Frame(sec_cols)
        mapping_row.pack(fill="x")
        left = ttk.Frame(mapping_row)
        left.pack(side="left", fill="x", expand=True, padx=(0, 20))
        right = ttk.Frame(mapping_row)
        right.pack(side="left", fill="x", expand=True)
        
        ttk.Label(left, text="Entity 1 (e.g. UPD)", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.pair_n1 = LabeledCombo(left, "Name column:")
        self.pair_n1.pack(anchor="w", pady=2)
        self.pair_a1 = LabeledCombo(left, "Address column:")
        self.pair_a1.pack(anchor="w", pady=2)
        self.pair_c1 = LabeledCombo(left, "City column (optional):", editable=True)
        self.pair_c1.pack(anchor="w", pady=2)
        
        ttk.Label(right, text="Entity 2 (e.g. CMPNY)", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.pair_n2 = LabeledCombo(right, "Name column:")
        self.pair_n2.pack(anchor="w", pady=2)
        self.pair_a2 = LabeledCombo(right, "Address column:")
        self.pair_a2.pack(anchor="w", pady=2)
        self.pair_c2 = LabeledCombo(right, "City column (optional):", editable=True)
        self.pair_c2.pack(anchor="w", pady=2)
        
        id_row = ttk.Frame(sec_cols)
        id_row.pack(fill="x", pady=10)
        self.pair_id = LabeledCombo(id_row, "Unique ID column (optional):", editable=True)
        self.pair_id.pack(side="left")
        
        # AI Options
        sec_opt = Section(body, "3. AI Options")
        sec_opt.pack(fill="x", padx=16, pady=(0, 10))
        self.grounding_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            sec_opt, text="Enable Google Search Grounding (More accurate, searches live data)",
            variable=self.grounding_var
        ).pack(anchor="w", pady=5)
        
        # Run
        sec_run = Section(body, "4. Output & Run")
        sec_run.pack(fill="x", padx=16, pady=(0, 10))
        
        out_row = ttk.Frame(sec_run)
        out_row.pack(fill="x")
        ttk.Label(out_row, text="Output folder:", width=22, anchor="w").pack(side="left")
        self.pair_out_dir = tk.StringVar(value="(same as input file)")
        ttk.Label(out_row, textvariable=self.pair_out_dir, foreground="#555", width=48, anchor="w").pack(side="left", padx=6)
        ttk.Button(out_row, text="Choose...", command=lambda: self._choose_dir(self.pair_out_dir)).pack(side="left")
        
        run_row = ttk.Frame(sec_run)
        run_row.pack(fill="x", pady=(10, 0))
        self.pair_run_btn = ttk.Button(run_row, text="▶ Run AI Pairing", command=self._run_pairing, style="Primary.TButton")
        self.pair_run_btn.pack(side="left")
        self.pair_progress = ttk.Progressbar(run_row, mode="determinate", length=260)
        self.pair_progress.pack(side="left", padx=12)
        self.pair_status = tk.StringVar(value="Idle")
        ttk.Label(run_row, textvariable=self.pair_status, foreground="#555").pack(side="left")

        # Stats
        pair_stats_row = ttk.Frame(sec_run)
        pair_stats_row.pack(fill="x", pady=(10, 4))
        self.pair_stat_same = StatCard(pair_stats_row, "SAME", color="#047857")
        self.pair_stat_same.pack(side="left", padx=4)
        self.pair_stat_aff = StatCard(pair_stats_row, "AFFILIATED", color="#b45309")
        self.pair_stat_aff.pack(side="left", padx=4)
        self.pair_stat_diff = StatCard(pair_stats_row, "DIFFERENT", color="#b91c1c")
        self.pair_stat_diff.pack(side="left", padx=4)
        self.pair_stat_err = StatCard(pair_stats_row, "ERROR", color="#4b5563")
        self.pair_stat_err.pack(side="left", padx=4)

        # Open File Buttons
        pair_open_row = ttk.Frame(sec_run)
        pair_open_row.pack(fill="x")
        self.pair_open_excel_btn = ttk.Button(pair_open_row, text="Open Results Excel", command=self._open_pair_excel, state="disabled")
        self.pair_open_excel_btn.pack(side="left", padx=(0, 8))
        self.pair_open_html_btn = ttk.Button(pair_open_row, text="Open HTML Report", command=self._open_pair_html, state="disabled")
        self.pair_open_html_btn.pack(side="left", padx=(0, 8))
        self.pair_open_folder_btn = ttk.Button(pair_open_row, text="Open Output Folder", command=self._open_pair_folder, state="disabled")
        self.pair_open_folder_btn.pack(side="left")

    def _build_find_hs_ui(self):
        self.hs_df = None
        self.hs_input_path = None
        
        scroller = ScrollableFrame(self.find_hs_tab)
        scroller.pack(fill="both", expand=True)
        body = scroller.body
        
        # Target HS configuration
        sec_hs = Section(body, "1. Target Health System")
        sec_hs.pack(fill="x", padx=16, pady=(16, 10))
        self.hs_target_name = LabeledEntry(sec_hs, "Target System Name (e.g. 'BJC Health System'):", width=50)
        self.hs_target_name.pack(anchor="w", pady=2)
        self.hs_target_aliases = LabeledEntry(sec_hs, "Aliases/Divisions (comma-separated):", width=70)
        self.hs_target_aliases.pack(anchor="w", pady=2)
        
        # File Input
        sec_file = Section(body, "2. Input File (Excel/CSV)")
        sec_file.pack(fill="x", padx=16, pady=(0, 10))
        self.hs_file_picker = FilePicker(sec_file, "File:", on_pick=self._on_hs_file_loaded)
        self.hs_file_picker.pack(fill="x")
        
        # Mapping
        sec_cols = Section(body, "3. Column Mapping")
        sec_cols.pack(fill="x", padx=16, pady=(0, 10))
        
        row1 = ttk.Frame(sec_cols)
        row1.pack(fill="x", pady=2)
        self.hs_name = LabeledCombo(row1, "Name column:")
        self.hs_name.pack(side="left", padx=(0, 10))
        self.hs_addr = LabeledCombo(row1, "Address column:")
        self.hs_addr.pack(side="left")
        
        row2 = ttk.Frame(sec_cols)
        row2.pack(fill="x", pady=2)
        self.hs_city = LabeledCombo(row2, "City column:")
        self.hs_city.pack(side="left", padx=(0, 10))
        self.hs_state = LabeledCombo(row2, "State column:")
        self.hs_state.pack(side="left", padx=(0, 10))
        self.hs_zip = LabeledCombo(row2, "Zip column:")
        self.hs_zip.pack(side="left")
        
        row3 = ttk.Frame(sec_cols)
        row3.pack(fill="x", pady=10)
        self.hs_id = LabeledCombo(row3, "Unique ID column (optional):", editable=True)
        self.hs_id.pack(side="left")
        
        # Run
        sec_run = Section(body, "4. Output & Run")
        sec_run.pack(fill="x", padx=16, pady=(0, 10))
        
        out_row = ttk.Frame(sec_run)
        out_row.pack(fill="x")
        ttk.Label(out_row, text="Output folder:", width=22, anchor="w").pack(side="left")
        self.hs_out_dir = tk.StringVar(value="(same as input file)")
        ttk.Label(out_row, textvariable=self.hs_out_dir, foreground="#555", width=48, anchor="w").pack(side="left", padx=6)
        ttk.Button(out_row, text="Choose...", command=lambda: self._choose_dir(self.hs_out_dir)).pack(side="left")
        
        run_row = ttk.Frame(sec_run)
        run_row.pack(fill="x", pady=(10, 0))
        self.hs_run_btn = ttk.Button(run_row, text="▶ Run Find Health System", command=self._run_find_hs, style="Primary.TButton")
        self.hs_run_btn.pack(side="left")
        self.hs_progress = ttk.Progressbar(run_row, mode="determinate", length=260)
        self.hs_progress.pack(side="left", padx=12)
        self.hs_status = tk.StringVar(value="Idle")
        ttk.Label(run_row, textvariable=self.hs_status, foreground="#555").pack(side="left")

        # Stats
        hs_stats_row = ttk.Frame(sec_run)
        hs_stats_row.pack(fill="x", pady=(10, 4))
        self.hs_stat_true = StatCard(hs_stats_row, "TRUE", color="#047857")
        self.hs_stat_true.pack(side="left", padx=4)
        self.hs_stat_false = StatCard(hs_stats_row, "FALSE", color="#b91c1c")
        self.hs_stat_false.pack(side="left", padx=4)

        # Open File Buttons
        hs_open_row = ttk.Frame(sec_run)
        hs_open_row.pack(fill="x")
        self.hs_open_excel_btn = ttk.Button(hs_open_row, text="Open Results Excel", command=self._open_hs_excel, state="disabled")
        self.hs_open_excel_btn.pack(side="left", padx=(0, 8))
        self.hs_open_folder_btn = ttk.Button(hs_open_row, text="Open Output Folder", command=self._open_hs_folder, state="disabled")
        self.hs_open_folder_btn.pack(side="left")

    def _choose_dir(self, string_var):
        folder = browse_folder()
        if folder:
            string_var.set(folder)

    def _load_df(self, path):
        if path.endswith(".csv") or path.lower().endswith(".csv"):
            return pd.read_csv(path, low_memory=False)
        else:
            return pd.read_excel(path)

    def _on_pair_file_loaded(self, path):
        try:
            self.pair_df = self._load_df(path)
            self.pair_input_path = path
            cols = [""] + list(self.pair_df.columns)
            
            # Entity 1 (left to right)
            self.pair_n1.set_values(cols, default=guess_column(cols, [], ["nm", "name"], fallback="", required=False, reverse=False))
            self.pair_a1.set_values(cols, default=guess_column(cols, [], ["addr", "address", "street"], fallback="", required=False, reverse=False))
            self.pair_c1.set_values(cols, default=guess_column(cols, [], ["city"], fallback="", required=False, reverse=False))
            
            # Entity 2 (right to left)
            self.pair_n2.set_values(cols, default=guess_column(cols, [], ["nm", "name"], fallback="", required=False, reverse=True))
            self.pair_a2.set_values(cols, default=guess_column(cols, [], ["addr", "address", "street"], fallback="", required=False, reverse=True))
            self.pair_c2.set_values(cols, default=guess_column(cols, [], ["city"], fallback="", required=False, reverse=True))
            
            self.pair_id.set_values(cols, default=guess_column(cols, [], ["id"], fallback="", required=False, reverse=False))
            
        except Exception as e:
            show_error("Failed to read file", e)

    def _on_hs_file_loaded(self, path):
        try:
            self.hs_df = self._load_df(path)
            self.hs_input_path = path
            cols = [""] + list(self.hs_df.columns)
            
            self.hs_name.set_values(cols, default=guess_column(cols, [], ["nm", "name"], fallback="", required=False))
            self.hs_addr.set_values(cols, default=guess_column(cols, [], ["addr", "address", "street"], fallback="", required=False))
            self.hs_city.set_values(cols, default=guess_column(cols, [], ["city"], fallback="", required=False))
            self.hs_state.set_values(cols, default=guess_column(cols, [], ["st", "state"], fallback="", required=False))
            self.hs_zip.set_values(cols, default=guess_column(cols, [], ["zip", "postal"], fallback="", required=False))
            self.hs_id.set_values(cols, default=guess_column(cols, [], ["id"], fallback="", required=False))
            
        except Exception as e:
            show_error("Failed to read file", e)

    def _run_pairing(self):
        if self.pair_df is None:
            show_error("No file loaded", "Please choose an input file.")
            return
            
        n1, a1, c1 = self.pair_n1.get(), self.pair_a1.get(), self.pair_c1.get()
        n2, a2, c2 = self.pair_n2.get(), self.pair_a2.get(), self.pair_c2.get()
        
        if not all([n1, a1, n2, a2]):
            show_error("Missing mapping", "Please select at least Name and Address for both entities.")
            return
            
        out_dir = self.pair_out_dir.get()
        if out_dir == "(same as input file)":
            out_dir = os.path.dirname(self.pair_input_path) or "."
            
        api_key = load_api_key()
        if not api_key:
            show_error("Missing API Key", "GEMINI_API_KEY not found in .env file.")
            return

        self.pair_run_btn.config(state="disabled")
        self.pair_status.set("Running...")
        self.pair_progress.config(value=0, maximum=len(self.pair_df))
        
        def progress_cb(i, total, elapsed=0.0, stopped=False):
            self.after(0, lambda: (
                self.pair_progress.config(value=i, maximum=total),
                self.pair_status.set(f"Processed {i}/{total}" + (" (Stopped)" if stopped else ""))
            ))

        def worker():
            try:
                res_df, excel_out, html_out, stats = run_ai_matching(
                    df=self.pair_df,
                    api_key=api_key,
                    name1_col=n1, addr1_col=a1, city1_col=c1,
                    name2_col=n2, addr2_col=a2, city2_col=c2,
                    id_col=self.pair_id.get(),
                    output_dir=out_dir,
                    output_filename="ai_pairing_results.xlsx",
                    use_grounding=self.grounding_var.get(),
                    progress_cb=progress_cb
                )
                self.after(0, lambda eo=excel_out, ho=html_out, st=stats: self._on_pair_done(eo, ho, st))
            except Exception as e:
                self.after(0, lambda err=e: self._on_pair_error(err))
                
        threading.Thread(target=worker, daemon=True).start()

    def _on_pair_done(self, excel_out, html_out, stats):
        self.pair_run_btn.config(state="normal")
        self.pair_status.set("Done ✅")
        
        self.pair_stat_same.set(stats.get("same", 0))
        self.pair_stat_aff.set(stats.get("affiliated", 0))
        self.pair_stat_diff.set(stats.get("different", 0))
        self.pair_stat_err.set(stats.get("error", 0))
        
        self.pair_excel_path = excel_out
        self.pair_html_path = html_out
        
        self.pair_open_excel_btn.config(state="normal")
        if html_out:
            self.pair_open_html_btn.config(state="normal")
        self.pair_open_folder_btn.config(state="normal")
        
        show_info("AI Pairing Complete", f"Saved to:\n{excel_out}")

    def _open_pair_excel(self):
        if getattr(self, "pair_excel_path", None):
            open_path(self.pair_excel_path)

    def _open_pair_html(self):
        if getattr(self, "pair_html_path", None):
            open_path(self.pair_html_path)

    def _open_pair_folder(self):
        out_dir = self.pair_out_dir.get()
        if out_dir == "(same as input file)":
            out_dir = os.path.dirname(self.pair_input_path) or "."
        open_path(out_dir)

    def _on_pair_error(self, e):
        self.pair_run_btn.config(state="normal")
        self.pair_status.set("Failed ❌")
        show_error("AI Pairing Failed", e)

    def _run_find_hs(self):
        if self.hs_df is None:
            show_error("No file loaded", "Please choose an input file.")
            return
            
        target = self.hs_target_name.get().strip()
        if not target:
            show_error("Missing Input", "Please provide a Target System Name.")
            return
            
        n, a = self.hs_name.get(), self.hs_addr.get()
        c, s, z = self.hs_city.get(), self.hs_state.get(), self.hs_zip.get()
        
        if not all([n, a, c, s, z]):
            show_error("Missing mapping", "Please map Name, Address, City, State, and Zip columns.")
            return
            
        out_dir = self.hs_out_dir.get()
        if out_dir == "(same as input file)":
            out_dir = os.path.dirname(self.hs_input_path) or "."
            
        api_key = load_api_key()
        if not api_key:
            show_error("Missing API Key", "GEMINI_API_KEY not found in .env file.")
            return

        self.hs_run_btn.config(state="disabled")
        self.hs_status.set("Running...")
        self.hs_progress.config(value=0, maximum=len(self.hs_df))
        
        def progress_cb(i, total):
            self.after(0, lambda: (
                self.hs_progress.config(value=i, maximum=total),
                self.hs_status.set(f"Processed {i}/{total}")
            ))

        def worker():
            try:
                aliases = [x.strip() for x in self.hs_target_aliases.get().split(",") if x.strip()]
                res_df, excel_out = run_find_hs(
                    df=self.hs_df,
                    api_key=api_key,
                    target_system_name=target,
                    target_system_aliases=aliases,
                    name_col=n, addr_col=a, city_col=c, state_col=s, zip_col=z,
                    id_col=self.hs_id.get(),
                    output_dir=out_dir,
                    progress_cb=progress_cb
                )
                self.after(0, lambda res=res_df, eo=excel_out, t=target: self._on_hs_done(res, eo, t))
            except Exception as e:
                self.after(0, lambda err=e: self._on_hs_error(err))
                
        threading.Thread(target=worker, daemon=True).start()

    def _on_hs_done(self, res_df, excel_out, target_system_name):
        self.hs_run_btn.config(state="normal")
        self.hs_status.set("Done ✅")
        
        sanitized_name = __import__('re').sub(r'[^a-zA-Z0-9]', '', target_system_name)
        bool_col_name = f'is_under_{sanitized_name}'
        
        num_true = (res_df[bool_col_name] == True).sum()
        num_false = (res_df[bool_col_name] == False).sum()
        
        self.hs_stat_true.set(num_true)
        self.hs_stat_false.set(num_false)
        
        self.hs_excel_path = excel_out
        self.hs_open_excel_btn.config(state="normal")
        self.hs_open_folder_btn.config(state="normal")
        
        show_info("Find Health System Complete", f"Saved to:\n{excel_out}")

    def _open_hs_excel(self):
        if getattr(self, "hs_excel_path", None):
            open_path(self.hs_excel_path)

    def _open_hs_folder(self):
        out_dir = self.hs_out_dir.get()
        if out_dir == "(same as input file)":
            out_dir = os.path.dirname(self.hs_input_path) or "."
        open_path(out_dir)

    def _on_hs_error(self, e):
        self.hs_run_btn.config(state="normal")
        self.hs_status.set("Failed ❌")
        show_error("Find Health System Failed", e)
