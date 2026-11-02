"""Data Laundry tool page — Tkinter UI matching the Geocoder layout."""
from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk, filedialog
from typing import Any, Dict, List
from pathlib import Path

from shared.services.workspace import get_workspace_path, set_custom_workspace_path

class ExpandingEntry(ttk.Entry):
    def __init__(self, master, textvariable=None, min_width=10, max_width=60, **kwargs):
        self._min_width = min_width
        self._max_width = max_width
        if textvariable is None:
            textvariable = tk.StringVar()
        self._var = textvariable
        kwargs.setdefault("textvariable", self._var)
        super().__init__(master, **kwargs)
        self._var.trace_add("write", self._grow)
        self._grow()

    def _grow(self, *_a):
        text = self._var.get() if self._var else self.get()
        needed = max(self._min_width, min(self._max_width, len(text) + 2))
        try:
            self.configure(width=needed)
        except tk.TclError:
            pass

class DataLaundryPage(ttk.Frame):
    def __init__(self, master, connection_settings, shared_workspace_var=None, shared_schema_var=None):
        super().__init__(master)
        self.connection_settings = connection_settings
        self.shared_workspace_var = shared_workspace_var
        self.shared_schema_var = shared_schema_var
        self.unresolved_records: List[Dict[str, Any]] = []
        self._busy = False
        self._cancel = False
        self._ui_lock = threading.Lock()
        self._pending_log: List[str] = []
        self._apply_flags: Dict[str, bool] = {}

        self._build_ui()
        self._update_query_preview()
        self.after(80, self._pump_log)

    def _browse_workspace(self):
        chosen = filedialog.askdirectory(title="Select Run Folder", initialdir=self.ws_dir_var.get())
        if chosen:
            self.ws_dir_var.set(chosen)
            self._on_workspace_input_changed()

    def _on_workspace_input_changed(self, *args):
        path_str = self.ws_dir_var.get().strip()
        if path_str:
            try:
                set_custom_workspace_path(Path(path_str))
            except Exception:
                pass

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        
        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        header.columnconfigure(1, weight=1)

        left_h = ttk.Frame(header)
        left_h.grid(row=0, column=0, sticky="w")
        ttk.Label(left_h, text="Server:", font=("Arial", 9, "bold")).pack(side="left")
        ttk.Label(
            left_h,
            text=self.connection_settings.server or "(unknown)",
            foreground="#1D4ED8",
            font=("Arial", 9, "bold"),
        ).pack(side="left", padx=(4, 0))
        auth = self.connection_settings.auth_mode or "sql"
        ttk.Label(left_h, text=f"  ·  auth: {auth}", foreground="gray").pack(side="left")

        right_h = ttk.Frame(header)
        right_h.grid(row=0, column=1, sticky="e")
        self.lbl_extracted = ttk.Label(right_h, text="Extracted: 0", font=("Arial", 9, "bold"))
        self.lbl_issues = ttk.Label(
            right_h, text="Unresolved: 0", font=("Arial", 9, "bold"), foreground="#B45309"
        )
        self.lbl_fixed = ttk.Label(
            right_h, text="Auto-Fixed: 0", font=("Arial", 9, "bold"), foreground="#059669"
        )
        self.lbl_extracted.pack(side="left", padx=(12, 0))
        self.lbl_issues.pack(side="left", padx=(12, 0))
        self.lbl_fixed.pack(side="left", padx=(12, 0))

        
        ws_container = ttk.LabelFrame(self, text=" Run Workspace Directory ", padding=8)
        ws_container.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        ws_container.columnconfigure(0, weight=1)

        ws_picker_frame = ttk.Frame(ws_container)
        ws_picker_frame.grid(row=0, column=0, sticky="ew")
        ws_picker_frame.columnconfigure(0, weight=1)

        self.ws_dir_var = self.shared_workspace_var if self.shared_workspace_var else tk.StringVar(value=str(get_workspace_path()))
        self.ent_ws_dir = ttk.Entry(ws_picker_frame, textvariable=self.ws_dir_var)
        self.ent_ws_dir.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        btn_ws_browse = ttk.Button(ws_picker_frame, text="Browse...", command=self._browse_workspace)
        btn_ws_browse.grid(row=0, column=1, sticky="e")

        self.ent_ws_dir.bind("<Return>", lambda e: self._on_workspace_input_changed())
        self.ent_ws_dir.bind("<FocusOut>", lambda e: self._on_workspace_input_changed())

        
        config = ttk.LabelFrame(self, text=" 1. Database & Query ", padding=8)
        config.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        config.columnconfigure(1, weight=1)
        config.columnconfigure(3, weight=1)

        
        ttk.Label(config, text="Database").grid(row=0, column=0, sticky="w")
        self.db_var = tk.StringVar(value=self.connection_settings.database or "TRAINING_DB")
        ExpandingEntry(config, textvariable=self.db_var, min_width=14, max_width=40).grid(
            row=0, column=1, sticky="ew", padx=(6, 12), pady=2
        )

        ttk.Label(config, text="Schema").grid(row=0, column=2, sticky="w")
        from shared.services.workspace import get_last_schema, set_last_schema
        self.schema_var = self.shared_schema_var if self.shared_schema_var else tk.StringVar(value=get_last_schema())

        def _on_schema_changed(*args):
            set_last_schema(self.schema_var.get())
            self._update_query_preview()

        self.schema_var.trace_add("write", _on_schema_changed)
        ExpandingEntry(config, textvariable=self.schema_var, min_width=10, max_width=28).grid(
            row=0, column=3, sticky="ew", padx=(6, 0), pady=2
        )

        
        ttk.Label(config, text="Member Column").grid(row=1, column=0, sticky="w", pady=(4, 0))
        self.col_var = tk.StringVar(value="GM.SRC_GPO_MEMBER_ALT_ID")
        self.col_combo = ttk.Combobox(
            config,
            textvariable=self.col_var,
            values=["GM.SRC_GPO_MEMBER_ALT_ID", "GM.gpo_member_lic_nr"],
            state="readonly",
            width=28,
        )
        self.col_combo.grid(row=1, column=1, sticky="ew", padx=(6, 12), pady=(4, 0))
        self.col_combo.bind("<<ComboboxSelected>>", lambda e: self._update_query_preview())

        ttk.Label(config, text="WHERE").grid(row=1, column=2, sticky="w", pady=(4, 0))
        self.where_var = tk.StringVar()
        self.where_entry = ExpandingEntry(
            config, textvariable=self.where_var, min_width=20, max_width=80
        )
        self.where_entry.grid(row=1, column=3, sticky="ew", padx=(6, 0), pady=(4, 0))
        self.where_var.trace_add("write", lambda *_: self._update_query_preview())

        
        note_lbl = ttk.Label(
            config,
            text="Note: Use GM.SRC_GPO_MEMBER_ALT_ID if GM.gpo_member_lic_nr has no value in this column, and use GM.gpo_member_lic_nr if the column GM.SRC_GPO_MEMBER_ALT_ID has no value.",
            foreground="#D97706",
            font=("Arial", 8, "italic"),
            wraplength=720,
        )
        note_lbl.grid(row=2, column=1, columnspan=3, sticky="w", pady=(2, 6))

        
        ttk.Label(config, text="Generated SQL").grid(row=3, column=0, sticky="nw", pady=(8, 0))
        sql_wrap = ttk.Frame(config)
        sql_wrap.grid(row=3, column=1, columnspan=3, sticky="nsew", padx=(6, 0), pady=(8, 0))
        sql_wrap.columnconfigure(0, weight=1)
        sql_wrap.rowconfigure(0, weight=1)
        config.rowconfigure(3, weight=0)

        self.query_preview = tk.Text(
            sql_wrap, height=3, font=("Consolas", 10), wrap="none", undo=True
        )
        self.query_preview.grid(row=0, column=0, sticky="nsew")
        sql_ysb = ttk.Scrollbar(sql_wrap, orient="vertical", command=self.query_preview.yview)
        sql_ysb.grid(row=0, column=1, sticky="ns")
        sql_xsb = ttk.Scrollbar(sql_wrap, orient="horizontal", command=self.query_preview.xview)
        sql_xsb.grid(row=1, column=0, sticky="ew")
        self.query_preview.configure(xscrollcommand=sql_xsb.set, yscrollcommand=sql_ysb.set)
        self.query_preview.bind("<<Modified>>", self._on_sql_modified)

        
        btn_row = ttk.Frame(config)
        btn_row.grid(row=4, column=0, columnspan=4, sticky="ew", pady=(10, 0))
        self.btn_preview = ttk.Button(btn_row, text="Preview (TOP 10)", command=self._run_preview)
        self.btn_preview.pack(side="left")
        self.btn_reset = ttk.Button(btn_row, text="Reset", command=self.reset_session)
        self.btn_reset.pack(side="left", padx=(8, 0))
        self.btn_start = ttk.Button(
            btn_row, text="Run Data Laundry Pipeline", command=self._start_pipeline
        )
        self.btn_start.pack(side="right")
        self.btn_commit = ttk.Button(
            btn_row,
            text="Commit Corrections & Finalize",
            command=self._commit_corrections,
            state="disabled",
        )
        self.btn_commit.pack(side="right", padx=(0, 8))

        
        split = ttk.Panedwindow(self, orient="vertical")
        split.grid(row=3, column=0, sticky="nsew")

        table_frame = ttk.LabelFrame(split, text=" Review Suggestions ", padding=4)
        log_frame = ttk.LabelFrame(split, text=" Execution Log ", padding=4)
        split.add(table_frame, weight=3)
        split.add(log_frame, weight=2)

        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(1, weight=1)
        tool = ttk.Frame(table_frame)
        tool.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Button(tool, text="Check All", command=lambda: self._toggle_all(True)).pack(
            side="left", padx=2
        )
        ttk.Button(tool, text="Uncheck All", command=lambda: self._toggle_all(False)).pack(
            side="left", padx=2
        )
        self.lbl_table_hint = ttk.Label(
            tool,
            text="Click ☑/☐ to apply · Double-click Sugg City / ST / ZIP to edit",
            foreground="gray",
        )
        self.lbl_table_hint.pack(side="left", padx=10)

        
        style = ttk.Style()
        try:
            style.configure(
                "Excel.Treeview",
                background="#FFFFFF",
                fieldbackground="#FFFFFF",
                foreground="#111827",
                rowheight=26,
                borderwidth=1,
                relief="solid",
                font=("Segoe UI", 9),
            )
            style.configure(
                "Excel.Treeview.Heading",
                background="#E8E8E8",
                foreground="#1F2937",
                relief="raised",
                borderwidth=1,
                font=("Segoe UI", 9, "bold"),
            )
            style.map(
                "Excel.Treeview",
                background=[("selected", "#C6EFCE")],
                foreground=[("selected", "#006100")],
            )
        except tk.TclError:
            pass

        cols = ("apply", "id", "member", "facility", "ocity", "ost", "ozip", "scity", "sst", "szip")
        self.tree = ttk.Treeview(
            table_frame,
            columns=cols,
            show="headings",
            selectmode="browse",
            style="Excel.Treeview",
        )
        self._review_headings = {
            "apply": "☑",
            "id": "REC_ID",
            "member": "Member ID",
            "facility": "Facility",
            "ocity": "Orig City",
            "ost": "ST",
            "ozip": "ZIP",
            "scity": "Sugg City",
            "sst": "Sugg ST",
            "szip": "Sugg ZIP",
        }
        
        self._review_weights = {
            "apply": 0.05,
            "id": 0.08,
            "member": 0.11,
            "facility": 0.16,
            "ocity": 0.10,
            "ost": 0.06,
            "ozip": 0.08,
            "scity": 0.14,
            "sst": 0.10,
            "szip": 0.12,
        }
        for c in cols:
            self.tree.heading(c, text=self._review_headings[c])
            self.tree.column(
                c,
                width=40,
                minwidth=28,
                stretch=True,
                anchor="center" if c in ("apply", "ost", "sst", "ozip", "szip") else "w",
            )
        self.tree.tag_configure("odd", background="#FFFFFF")
        self.tree.tag_configure("even", background="#F2F2F2")
        self.tree.tag_configure("apply_on", foreground="#006100")
        self.tree.tag_configure("apply_off", foreground="#9CA3AF")
        self.tree.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<Double-1>", self._on_tree_double_click)
        self.tree.bind("<Configure>", self._fit_review_columns)
        table_frame.bind("<Configure>", self._fit_review_columns)
        self._edit_entry = None
        self._edit_info = None
        self.after(50, self._fit_review_columns)

        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(
            log_frame,
            height=8,
            state="disabled",
            background="#1e1e1e",
            foreground="#00ff00",
            font=("Consolas", 9),
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        lsb = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        lsb.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=lsb.set)

    
    def _on_sql_modified(self, event=None):
        try:
            self.query_preview.edit_modified(False)
        except tk.TclError:
            pass
        self._resize_sql_box()

    def _resize_sql_box(self):
        content = self.query_preview.get("1.0", "end-1c")
        lines = content.count("\n") + 1
        
        height = max(3, min(12, lines + 1))
        try:
            self.query_preview.configure(height=height)
        except tk.TclError:
            pass

    def _update_query_preview(self):
        selected_col = self.col_var.get()
        where_clause = self.where_var.get().strip()
        full_query = (
            f"SELECT DISTINCT GM.SRC_GPO_MEMBER_ID, {selected_col}, GM.MMBR_NM, "
            f"GM.MMBR_ADDR_1, GM.MMBR_ADDR_2, GM.MMBR_CITY, GM.MMBR_ST, GM.MMBR_ZIP, "
            f"GM.HIN_NR, GM.DEA_NR, GM.GLN_NR\n"
            f"FROM sndbx_PDIDW.STAGE.GPO_MEMBER GM"
        )
        if where_clause:
            if not where_clause.upper().startswith("WHERE"):
                full_query += f"\nWHERE {where_clause}"
            else:
                full_query += f"\n{where_clause}"
        self.query_preview.delete("1.0", tk.END)
        self.query_preview.insert("1.0", full_query)
        self._resize_sql_box()

    
    def reset_session(self):
        """Full new session: cancel work, clear WHERE, metrics, review table, and log."""
        self._cancel = True
        self._busy = False
        self.unresolved_records = []
        self._apply_flags.clear()

        
        self._close_cell_editor()
        self.tree.delete(*self.tree.get_children())
        cols = ("apply", "id", "member", "facility", "ocity", "ost", "ozip", "scity", "sst", "szip")
        self.tree["columns"] = cols
        headings = {
            "apply": "Apply", "id": "REC_ID", "member": "Member ID", "facility": "Facility",
            "ocity": "Orig City", "ost": "Orig ST", "ozip": "Orig ZIP",
            "scity": "Sugg City", "sst": "Sugg ST", "szip": "Sugg ZIP",
        }
        for c in cols:
            self.tree.heading(c, text=headings[c])

        
        self.lbl_extracted.config(text="Extracted: 0")
        self.lbl_issues.config(text="Unresolved: 0")
        self.lbl_fixed.config(text="Auto-Fixed: 0")
        self.lbl_table_hint.config(text="Session reset — ready for a new run")

        
        self.where_var.set("")
        try:
            self.where_entry.delete(0, tk.END)
        except tk.TclError:
            pass
        self.col_var.set("GM.SRC_GPO_MEMBER_ALT_ID")
        self._update_query_preview()

        
        try:
            self.log_text.config(state="normal")
            self.log_text.delete("1.0", tk.END)
            self.log_text.config(state="disabled")
        except tk.TclError:
            pass

        self.btn_preview.config(state="normal")
        self.btn_start.config(state="normal")
        self.btn_commit.config(state="disabled")
        self.log("--- New session. WHERE, metrics, and review cleared. ---")

        def _clear_cancel():
            self._cancel = False

        self.after(150, _clear_cancel)

    
    def log(self, message: str):
        line = f"[{time.strftime('%H:%M:%S')}] {message}\n"
        with self._ui_lock:
            self._pending_log.append(line)

    def _pump_log(self):
        try:
            with self._ui_lock:
                logs = self._pending_log[:]
                self._pending_log.clear()
            if logs:
                self.log_text.config(state="normal")
                self.log_text.insert(tk.END, "".join(logs))
                self.log_text.see(tk.END)
                self.log_text.config(state="disabled")
        except tk.TclError:
            pass
        self.after(80, self._pump_log)

    def _set_busy(self, busy: bool):
        self._busy = busy
        state = "disabled" if busy else "normal"
        self.btn_preview.config(state=state)
        self.btn_start.config(state=state)
        if not busy and self.unresolved_records:
            self.btn_commit.config(state="normal")
        elif busy:
            self.btn_commit.config(state="disabled")

    def _creds(self):
        cs = self.connection_settings
        return {
            "server": cs.server,
            "database": self.db_var.get().strip() or "TRAINING_DB",
            "schema": self.schema_var.get().strip(),
            "username": cs.username,
            "password": cs.password,
            "auth_mode": cs.auth_mode,
            "trust_server_certificate": bool(getattr(cs, "trust_server_certificate", True)),
            "encrypt": bool(getattr(cs, "encrypt", False)),
        }

    def _check_and_prompt_column(self, on_done_callback):
        """Check if the selected column is empty, and prompt the user to switch if so.
        Calls on_done_callback() afterwards."""
        creds = self._creds()
        selected_col = self.col_var.get()
        where_clause = self.where_var.get().strip()

        
        if selected_col == "GM.SRC_GPO_MEMBER_ALT_ID":
            alt_col = "GM.gpo_member_lic_nr"
        else:
            alt_col = "GM.SRC_GPO_MEMBER_ALT_ID"

        self._set_busy(True)
        self.lbl_table_hint.config(text="Checking column emptiness…")
        msg_condition = f" under WHERE condition: '{where_clause}'" if where_clause else ""
        self.log(f"Checking if selected column '{selected_col}' contains any values{msg_condition}…")

        def check_thread():
            from project_app.services.data_laundry import is_column_empty
            empty = is_column_empty(
                creds["server"],
                creds["database"],
                creds["username"],
                creds["password"],
                selected_col,
                where_clause=where_clause,
                auth_mode=creds["auth_mode"],
                trust_server_certificate=creds["trust_server_certificate"],
                encrypt=creds["encrypt"],
            )

            if empty:
                self.log(f"  ⚠ Selected column '{selected_col}' is COMPLETELY EMPTY in the database under this WHERE condition.")

                
                alt_empty = is_column_empty(
                    creds["server"],
                    creds["database"],
                    creds["username"],
                    creds["password"],
                    alt_col,
                    where_clause=where_clause,
                    auth_mode=creds["auth_mode"],
                    trust_server_certificate=creds["trust_server_certificate"],
                    encrypt=creds["encrypt"],
                )

                def ask_user():
                    if alt_empty:
                        
                        cond_msg = f" under the current WHERE condition" if where_clause else ""
                        messagebox.showwarning(
                            "Empty Columns Warning",
                            f"Both '{selected_col}' and '{alt_col}' are completely empty in the source table{cond_msg}. "
                            "Proceeding anyway, but no member IDs will be extracted."
                        )
                        self.log(f"  ⚠ Both columns are empty. Proceeding anyway.")
                        self.after(0, on_done_callback)
                    else:
                        
                        cond_msg = f" under the current WHERE condition" if where_clause else ""
                        msg = (
                            f"The selected column '{selected_col}' is completely empty in the STAGE.GPO_MEMBER table{cond_msg}.\n\n"
                            f"We detected that the alternative column '{alt_col}' has data values.\n\n"
                            f"Would you like to switch to '{alt_col}' instead?"
                        )
                        if messagebox.askyesno("Empty Column Detected", msg):
                            self.log(f"Switching column from '{selected_col}' to '{alt_col}' based on user selection.")
                            self.col_var.set(alt_col)
                            self._update_query_preview()

                        
                        self.after(0, on_done_callback)

                self.after(0, ask_user)
            else:
                self.log(f"  ✓ Column '{selected_col}' contains data. Proceeding.")
                self.after(0, on_done_callback)

        threading.Thread(target=check_thread, daemon=True).start()

    def _run_preview(self):
        if self._busy:
            return
        creds = self._creds()
        if creds["auth_mode"] == "sql" and not creds["username"]:
            messagebox.showwarning("Credentials", "SQL auth requires a username (re-login if needed).")
            return
        self._check_and_prompt_column(self._execute_preview_pipeline)

    def _execute_preview_pipeline(self):
        from project_app.services.data_laundry import run_preview
        creds = self._creds()
        query = self.query_preview.get("1.0", tk.END).strip()
        self._set_busy(True)
        self._cancel = False
        self.lbl_table_hint.config(text="Loading preview…")

        def work():
            try:
                if self._cancel:
                    return
                cols, rows = run_preview(
                    creds["server"],
                    creds["database"],
                    creds["username"],
                    creds["password"],
                    query,
                    creds["auth_mode"],
                    log=self.log,
                    trust_server_certificate=creds["trust_server_certificate"],
                    encrypt=creds["encrypt"],
                )
                self.after(0, lambda: self._show_preview(cols, rows))
            except Exception as e:
                self.log(f"PREVIEW ERROR: {e}")
                self.after(0, lambda: self._set_busy(False))

        threading.Thread(target=work, daemon=True).start()

    def _show_preview(self, columns, rows):
        self._set_busy(False)
        if self._cancel:
            return
        self.tree.delete(*self.tree.get_children())
        self._apply_flags.clear()
        if not columns:
            self.lbl_table_hint.config(text="Preview failed")
            return
        self.tree["columns"] = columns
        for c in columns:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=max(80, min(180, len(c) * 9)), anchor="w")
        for row in rows:
            values = ["" if v is None else str(v) for v in row]
            self.tree.insert("", "end", values=values)
        self.lbl_table_hint.config(text=f"Preview · {len(rows)} rows (read-only)")

    def _start_pipeline(self):
        if self._busy:
            return
        creds = self._creds()
        if not creds["schema"]:
            messagebox.showwarning("Missing Schema", "Please enter a Schema before running the pipeline.")
            return
        self._check_and_prompt_column(self._execute_laundry_pipeline)

    def _execute_laundry_pipeline(self):
        from project_app.services.data_laundry import run_stage1
        creds = self._creds()
        query = self.query_preview.get("1.0", tk.END).strip()
        self._set_busy(True)
        self._cancel = False
        self.btn_commit.config(state="disabled")
        self.unresolved_records = []
        self.lbl_table_hint.config(text="Stage 1 running…")
        self.log("Starting Stage 1 Execution Pipeline…")

        def work():
            if self._cancel:
                self.after(0, lambda: self._set_busy(False))
                return
            ok, unresolved, total = run_stage1(
                creds["server"],
                creds["database"],
                creds["schema"],
                creds["username"],
                creds["password"],
                query,
                creds["auth_mode"],
                log=self.log,
                trust_server_certificate=creds["trust_server_certificate"],
                encrypt=creds["encrypt"],
            )
            self.after(0, lambda: self._stage1_done(ok, unresolved, total))

        threading.Thread(target=work, daemon=True).start()

    def _stage1_done(self, success, unresolved, total_extracted):
        self._set_busy(False)
        if self._cancel:
            self.log("Stage 1 discarded (session was reset).")
            return
        if not success:
            self.lbl_table_hint.config(text="Stage 1 failed · see log")
            return

        self.unresolved_records = unresolved
        self.lbl_extracted.config(text=f"Extracted: {total_extracted}")
        self.lbl_issues.config(text=f"Unresolved: {len(unresolved)}")
        self.lbl_fixed.config(text=f"Auto-Fixed: {max(0, total_extracted - len(unresolved))}")

        if unresolved:
            self.log(f"Stage 1 complete: {len(unresolved)} rows need manual review.")
            self._populate_review(unresolved)
            self.btn_commit.config(state="normal")
            self.lbl_table_hint.config(text="Review suggestions · toggle Apply · then Commit")
        else:
            self.log("Stage 1 complete: All rows cleaned. Starting Stage 2…")
            self.lbl_table_hint.config(text="All resolved · finalizing…")
            self._commit_corrections()

    def _fit_review_columns(self, event=None):
        """Scale review columns so the full sheet fits inside the visible box."""
        try:
            self.tree.update_idletasks()
            total = self.tree.winfo_width()
            if total < 50:
                return
            
            usable = max(total - 4, 100)
            cols = ("apply", "id", "member", "facility", "ocity", "ost", "ozip", "scity", "sst", "szip")
            weights = getattr(self, "_review_weights", None) or {c: 0.1 for c in cols}
            for c in cols:
                w = max(28, int(usable * weights.get(c, 0.1)))
                self.tree.column(c, width=w, stretch=True)
        except tk.TclError:
            pass

    def _populate_review(self, records: List[Dict[str, Any]]):
        cols = ("apply", "id", "member", "facility", "ocity", "ost", "ozip", "scity", "sst", "szip")
        self.tree["columns"] = cols
        for c in cols:
            self.tree.heading(c, text=self._review_headings.get(c, c))
        self.tree.delete(*self.tree.get_children())
        self._apply_flags.clear()
        self._close_cell_editor()
        for idx, r in enumerate(records):
            apply = bool(r.get("APPLY_FIX", True))
            values = (
                "☑" if apply else "☐",
                str(r.get("REC_ID", "")),
                str(r.get("MMBR_ID", "")),
                str(r.get("ORIG_NM_1", "")),
                str(r.get("ORIG_CITY", "")),
                str(r.get("ORIG_ST", "")),
                str(r.get("ORIG_ZIP", "")),
                str(r.get("SUGG_CITY") or ""),
                str(r.get("SUGG_ST") or ""),
                str(r.get("SUGG_ZIP") or ""),
            )
            stripe = "even" if idx % 2 else "odd"
            apply_tag = "apply_on" if apply else "apply_off"
            iid = self.tree.insert("", "end", values=values, tags=(stripe, apply_tag))
            self._apply_flags[iid] = apply

    def _set_apply_state(self, iid: str, state: bool):
        self._apply_flags[iid] = state
        vals = list(self.tree.item(iid, "values"))
        if not vals:
            return
        vals[0] = "☑" if state else "☐"
        tags = list(self.tree.item(iid, "tags"))
        tags = [t for t in tags if t not in ("apply_on", "apply_off")]
        tags.append("apply_on" if state else "apply_off")
        self.tree.item(iid, values=vals, tags=tags)

    def _on_tree_click(self, event):
        self._close_cell_editor()
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        col = self.tree.identify_column(event.x)  
        row = self.tree.identify_row(event.y)
        if not row or row not in self._apply_flags:
            return
        if col == "#1":
            self._set_apply_state(row, not self._apply_flags[row])

    def _on_tree_double_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        col = self.tree.identify_column(event.x)
        row = self.tree.identify_row(event.y)
        
        col_index = int(col.replace("#", "")) - 1
        if col_index not in (7, 8, 9) or not row:
            return
        self._open_cell_editor(row, col, col_index)

    def _open_cell_editor(self, iid: str, col_id: str, col_index: int):
        self._close_cell_editor()
        bbox = self.tree.bbox(iid, col_id)
        if not bbox:
            return
        x, y, w, h = bbox
        vals = list(self.tree.item(iid, "values"))
        current = vals[col_index] if col_index < len(vals) else ""

        entry = ttk.Entry(self.tree, font=("Segoe UI", 9))
        entry.place(x=x, y=y, width=max(w, 60), height=h)
        entry.insert(0, current)
        entry.select_range(0, tk.END)
        entry.focus_set()
        self._edit_entry = entry
        self._edit_info = (iid, col_index)

        def _save(_event=None):
            if not self._edit_entry or not self._edit_info:
                return
            iid2, idx = self._edit_info
            new_val = self._edit_entry.get().strip()
            vals2 = list(self.tree.item(iid2, "values"))
            if idx < len(vals2):
                vals2[idx] = new_val
                self.tree.item(iid2, values=vals2)
            
            rec_id = vals2[1] if len(vals2) > 1 else None
            key = {7: "SUGG_CITY", 8: "SUGG_ST", 9: "SUGG_ZIP"}.get(idx)
            if key and rec_id is not None:
                for r in self.unresolved_records:
                    if str(r.get("REC_ID", "")) == str(rec_id):
                        r[key] = new_val
                        break
            self._close_cell_editor()

        entry.bind("<Return>", _save)
        entry.bind("<FocusOut>", _save)
        entry.bind("<Escape>", lambda e: self._close_cell_editor())

    def _close_cell_editor(self):
        if self._edit_entry is not None:
            try:
                self._edit_entry.destroy()
            except tk.TclError:
                pass
        self._edit_entry = None
        self._edit_info = None

    def _toggle_all(self, state: bool):
        for iid in self.tree.get_children():
            if iid in self._apply_flags:
                self._set_apply_state(iid, state)

    def _commit_corrections(self):
        from project_app.services.data_laundry import run_stage2

        if self._busy:
            return
        self._close_cell_editor()
        children = self.tree.get_children()
        for i, iid in enumerate(children):
            if i >= len(self.unresolved_records):
                break
            vals = list(self.tree.item(iid, "values"))
            if iid in self._apply_flags:
                self.unresolved_records[i]["APPLY_FIX"] = self._apply_flags[iid]
            if len(vals) >= 10:
                self.unresolved_records[i]["SUGG_CITY"] = vals[7]
                self.unresolved_records[i]["SUGG_ST"] = vals[8]
                self.unresolved_records[i]["SUGG_ZIP"] = vals[9]

        creds = self._creds()
        if not creds["schema"]:
            messagebox.showwarning("Missing Schema", "Schema is required.")
            return

        self._set_busy(True)
        self._cancel = False
        self.btn_commit.config(state="disabled")
        self.log("Starting Stage 2…")
        records = list(self.unresolved_records)

        def work():
            if self._cancel:
                self.after(0, lambda: self._set_busy(False))
                return
            ok = run_stage2(
                creds["server"],
                creds["database"],
                creds["schema"],
                creds["username"],
                creds["password"],
                records,
                creds["auth_mode"],
                log=self.log,
                trust_server_certificate=creds["trust_server_certificate"],
                encrypt=creds["encrypt"],
            )
            self.after(0, lambda: self._stage2_done(ok))

        threading.Thread(target=work, daemon=True).start()

    def _stage2_done(self, success: bool):
        self._set_busy(False)
        if self._cancel:
            self.log("Stage 2 discarded (session was reset).")
            return
        if success:
            messagebox.showinfo(
                "Pipeline Complete",
                "All stages finished successfully.\nName and address data has been standardized.",
            )
            self.lbl_table_hint.config(text="Pipeline completed successfully")
        else:
            self.lbl_table_hint.config(text="Pipeline finished with errors · see log")
