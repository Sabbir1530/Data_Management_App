"""Data Matching & Verification tool page (integrated with shared login)."""
from __future__ import annotations

import queue
import threading
import time
import tkinter as tk
import urllib.parse
import webbrowser
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, Optional, Set
from pathlib import Path

from shared.services.workspace import get_workspace_path, set_custom_workspace_path

import pandas as pd

from project_app.services.matching import (
    TASK_KEYS,
    TASKS,
    AUTO_MATCH_TASK_KEYS,
    SORT_OPTIONS,
    PAGE_SIZE_OPTIONS,
    build_similarity_dataframe,
    clean_str,
    commit_matches,
    run_match_query,
)
from project_app.services.ai_matching import AIMatchingUnavailable, classify_candidates, RESULT_COLUMNS
from project_app.services.match_cache import (
    append_cache,
    cache_path,
    cache_status,
    clear_cache,
    final_results_path,
    load_cache,
    make_uni_id,
    CACHE_COLUMNS,
)

# Fixed GPO Company IDs available in the dropdown (Task 2 / Auto-Match pause).
GPO_COMPANY_IDS = [
    "70281985",
    "70278588",
    "70265938",
    "70278587",
    "70169788",
    "70280859",
]


class DataMatchingPage(ttk.Frame):
    def __init__(self, master, connection_settings, shared_workspace_var=None, shared_schema_var=None):
        super().__init__(master)
        self.connection_settings = connection_settings
        self.shared_workspace_var = shared_workspace_var
        self.shared_schema_var = shared_schema_var
        self.active_df: Optional[pd.DataFrame] = None
        self.active_task_name: Optional[str] = None
        self.exclusion_set: Set[Any] = set()
        self.row_by_iid: Dict[str, Any] = {}
        self.selected_row = None
        self.current_page = 1
        self.log_queue: queue.Queue = queue.Queue()
        self.result_queue: queue.Queue = queue.Queue()
        self.progress_queue: queue.Queue = queue.Queue()
        self.auto_match_running = False
        self._gpo_continue_event = threading.Event()
        self._gpo_for_task2: str = ""
        self._auto_match_cancel = False

        self._build_ui()
        self._on_task_changed()
        self._update_cache_status_ui()
        self.after(120, self._poll_queues)

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
                self._update_cache_status_ui()
            except Exception:
                pass

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(3, weight=1)

        # Header
        header = ttk.Frame(self)
        header.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="Server:", font=("Arial", 9, "bold")).pack(side="left")
        ttk.Label(
            header,
            text=self.connection_settings.server or "(unknown)",
            foreground="#1D4ED8",
            font=("Arial", 9, "bold"),
        ).pack(side="left", padx=(4, 12))
        ttk.Label(
            header,
            text="Data Matching & Verification — link TEMP_NAME_ADDR_PRCS to company master",
            foreground="gray",
        ).pack(side="left")

        # Workspace selector
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

        # Config
        cfg = ttk.LabelFrame(self, text=" 1. Database & Task ", padding=8)
        cfg.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        for c in (1, 3, 5):
            cfg.columnconfigure(c, weight=1)

        ttk.Label(cfg, text="Database").grid(row=0, column=0, sticky="w")
        self.db_var = tk.StringVar(value=self.connection_settings.database or "TRAINING_DB")
        self.ent_db = ttk.Entry(cfg, textvariable=self.db_var, width=18)
        self.ent_db.grid(row=0, column=1, sticky="ew", padx=(6, 12))
        self.db_var.trace_add("write", lambda *_: self._update_cache_status_ui())

        ttk.Label(cfg, text="Schema").grid(row=0, column=2, sticky="w")
        from shared.services.workspace import get_last_schema, set_last_schema
        self.schema_var = self.shared_schema_var if self.shared_schema_var else tk.StringVar(value=get_last_schema())
        self.ent_schema = ttk.Entry(cfg, textvariable=self.schema_var, width=14)
        self.ent_schema.grid(row=0, column=3, sticky="ew", padx=(6, 12))

        def _on_schema_changed(*args):
            set_last_schema(self.schema_var.get())
            self._update_cache_status_ui()

        self.schema_var.trace_add("write", _on_schema_changed)

        ttk.Label(cfg, text="Task").grid(row=0, column=4, sticky="w")
        self._enabled_tasks = [k for k in TASK_KEYS]
        self.task_var = tk.StringVar(value=self._enabled_tasks[0] if self._enabled_tasks else "")
        self.task_combo = ttk.Combobox(
            cfg, textvariable=self.task_var, values=self._enabled_tasks, state="readonly", width=36
        )
        self.task_combo.grid(row=0, column=5, sticky="ew", padx=(6, 0))
        self.task_combo.bind("<<ComboboxSelected>>", lambda e: self._on_task_changed())

        self.lbl_gpo = ttk.Label(cfg, text="GPO Company ID")
        self.gpo_var = tk.StringVar(value=GPO_COMPANY_IDS[0])
        self.ent_gpo = ttk.Combobox(
            cfg,
            textvariable=self.gpo_var,
            values=GPO_COMPANY_IDS,
            state="readonly",
            width=14,
        )
        self.lbl_gpo.grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.ent_gpo.grid(row=1, column=1, sticky="w", padx=(6, 0), pady=(6, 0))

        btn_row = ttk.Frame(cfg)
        btn_row.grid(row=2, column=0, columnspan=6, sticky="ew", pady=(10, 0))
        self.btn_load = ttk.Button(btn_row, text="Load Candidates", command=self._on_load)
        self.btn_load.pack(side="left")
        self.btn_reset = ttk.Button(btn_row, text="Reset", command=self.reset_session)
        self.btn_reset.pack(side="left", padx=(8, 0))
        self.btn_apply = ttk.Button(
            btn_row, text="Apply Matches (write ENDUSER_ID)", command=self._on_apply, state="disabled"
        )
        self.btn_apply.pack(side="right")
        self.btn_auto_match = ttk.Button(
            btn_row, text="Auto-Match Tasks 4-7 (AI)", command=self._on_auto_match,
        )
        self.btn_auto_match.pack(side="right", padx=(0, 8))

        self.btn_auto_apply_task2 = ttk.Button(
            btn_row, text="Auto-Apply Task 2", command=self._on_auto_apply_task2
        )
        self.btn_auto_apply_task2.pack(side="right", padx=(0, 8))

        auto_row = ttk.Frame(cfg)
        auto_row.grid(row=3, column=0, columnspan=6, sticky="ew", pady=(6, 0))
        self.resume_cache_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            auto_row,
            text="Resume Auto-Match from cache (skip rows already classified by AI)",
            variable=self.resume_cache_var,
        ).pack(side="left")

        self.btn_rerun_cache = ttk.Button(
            auto_row, text="Rerun from Cache ↻", command=self._on_rerun_from_cache
        )
        self.btn_rerun_cache.pack(side="left", padx=(12, 0))

        self.btn_clear_cache = ttk.Button(
            auto_row, text="Clear AI Cache", command=self._on_clear_cache
        )
        self.btn_clear_cache.pack(side="left", padx=(6, 0))

        # Cache Status Line
        status_row = ttk.Frame(cfg)
        status_row.grid(row=4, column=0, columnspan=6, sticky="ew", pady=(4, 0))
        self.lbl_cache_info = ttk.Label(status_row, text="", foreground="#4B5563", font=("Arial", 8))
        self.lbl_cache_info.pack(side="left")

        # Main split
        split = ttk.Panedwindow(self, orient="vertical")
        split.grid(row=3, column=0, sticky="nsew")

        list_frame = ttk.LabelFrame(split, text=" Candidate Matches ", padding=4)
        log_frame = ttk.LabelFrame(split, text=" Execution Log ", padding=4)
        split.add(list_frame, weight=4)
        split.add(log_frame, weight=1)

        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(1, weight=1)

        tool = ttk.Frame(list_frame)
        tool.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(tool, text="Sort:").pack(side="left")
        self.sort_var = tk.StringVar(value=list(SORT_OPTIONS.keys())[0])
        ttk.Combobox(
            tool, textvariable=self.sort_var, values=list(SORT_OPTIONS.keys()),
            state="readonly", width=22,
        ).pack(side="left", padx=4)
        self.sort_var.trace_add("write", lambda *_: self._refresh_list(reset_page=True))

        ttk.Label(tool, text="Page size:").pack(side="left", padx=(12, 0))
        self.page_size_var = tk.IntVar(value=25)
        ttk.Combobox(
            tool, textvariable=self.page_size_var, values=PAGE_SIZE_OPTIONS,
            state="readonly", width=6,
        ).pack(side="left", padx=4)
        self.page_size_var.trace_add("write", lambda *_: self._refresh_list(reset_page=True))

        self.btn_prev = ttk.Button(tool, text="◀ Prev", command=self._go_prev, state="disabled")
        self.btn_prev.pack(side="left", padx=(12, 2))
        self.btn_next = ttk.Button(tool, text="Next ▶", command=self._go_next, state="disabled")
        self.btn_next.pack(side="left", padx=2)
        self.lbl_page = ttk.Label(tool, text="Page 0 of 0", foreground="gray")
        self.lbl_page.pack(side="left", padx=8)

        self.btn_search_staging = ttk.Button(
            tool, text="Google Staging", command=self._open_staging_search, state="disabled"
        )
        self.btn_search_staging.pack(side="right", padx=2)
        self.btn_search_master = ttk.Button(
            tool, text="Google Master", command=self._open_master_search, state="disabled"
        )
        self.btn_search_master.pack(side="right", padx=2)
        self.btn_search_combined = ttk.Button(
            tool, text="Google Compare", command=self._open_combined_search, state="disabled"
        )
        self.btn_search_combined.pack(side="right", padx=2)

        cols = (
            "excl", "rec_id", "cmpny_id",
            "orig_nm", "cm_nm", "orig_addr", "cm_addr", "orig_loc", "cm_loc",
        )
        self.tree = ttk.Treeview(list_frame, columns=cols, show="headings", selectmode="browse")
        headings = {
            "excl": "Skip",
            "rec_id": "REC_ID",
            "cmpny_id": "CMPNY_ID",
            "orig_nm": "Staging Name",
            "cm_nm": "Master Name",
            "orig_addr": "Staging Addr",
            "cm_addr": "Master Addr",
            "orig_loc": "Staging Loc",
            "cm_loc": "Master Loc",
        }
        widths = {
            "excl": 36, "rec_id": 52, "cmpny_id": 58,
            "orig_nm": 180, "cm_nm": 180, "orig_addr": 170, "cm_addr": 170,
            "orig_loc": 72, "cm_loc": 64,
        }
        for c in cols:
            self.tree.heading(c, text=headings[c])
            self.tree.column(
                c,
                width=widths[c],
                minwidth=28 if c in ("excl", "rec_id", "cmpny_id", "orig_loc", "cm_loc") else 80,
                stretch=c not in ("excl", "rec_id", "cmpny_id"),
                anchor="center" if c == "excl" else "w",
            )
        self.tree.tag_configure("kept", background="#FFFFFF")
        self.tree.tag_configure("kept_alt", background="#F7F9FC")
        self.tree.tag_configure("excluded", background="#FDECEB")
        self.tree.tag_configure("auto_excluded", background="#FFF4E0")
        self.tree.grid(row=1, column=0, sticky="nsew")
        sb = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        sb.grid(row=1, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.bind("<Button-1>", self._on_tree_click)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

        excl_row = ttk.Frame(list_frame)
        excl_row.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Label(excl_row, text="Excluded REC_IDs:").pack(side="left")
        self.lbl_excl = ttk.Label(excl_row, text="(none)", foreground="gray")
        self.lbl_excl.pack(side="left", padx=6)

        self.progress = ttk.Progressbar(list_frame, orient="horizontal", mode="determinate")
        self.progress.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(4, 0))

        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self.log_text = tk.Text(
            log_frame, height=6, state="disabled",
            background="#1e1e1e", foreground="#00ff00", font=("Consolas", 9),
        )
        self.log_text.grid(row=0, column=0, sticky="nsew")
        lsb = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        lsb.grid(row=0, column=1, sticky="ns")
        self.log_text.configure(yscrollcommand=lsb.set)

    def _update_cache_status_ui(self):
        server = self.connection_settings.server or ""
        db = self.db_var.get().strip()
        schema = self.schema_var.get().strip()
        if not schema:
            self.lbl_cache_info.config(text="Workspace Active | Enter Schema to view cache status.")
            return

        st = cache_status(server, db, schema)
        cfile = st['cache_path'].name
        msg = f"Workspace: [{st['workspace']}]  |  Cache: {cfile} ({st['row_count']} row(s) stored)  |  Output: {st['output_dir']}"
        self.lbl_cache_info.config(text=msg)

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

    def _on_task_changed(self):
        if self.active_task_name and self.active_task_name != self.task_var.get():
            self.reset_session(clear_log=False)

    def reset_session(self, clear_log=True):
        if self.auto_match_running:
            self._auto_match_cancel = True
            self._gpo_continue_event.set()
            self.auto_match_running = False
        self.active_df = None
        self.active_task_name = None
        self.exclusion_set.clear()
        self._clear_tree()
        self.btn_apply.config(state="disabled")
        self.progress["value"] = 0
        self._update_exclusion_label()
        self.lbl_excl.config(text="(none)")
        if clear_log:
            self.log_text.config(state="normal")
            self.log_text.delete("1.0", tk.END)
            self.log_text.config(state="disabled")
            self._log("--- Matching session reset ---")

    def _log(self, message: str):
        self.log_queue.put(f"[{time.strftime('%H:%M:%S')}] {message}")

    def _poll_queues(self):
        try:
            while True:
                try:
                    msg = self.log_queue.get_nowait()
                except queue.Empty:
                    break
                self.log_text.config(state="normal")
                self.log_text.insert(tk.END, msg + "\n")
                self.log_text.see(tk.END)
                self.log_text.config(state="disabled")
            while True:
                try:
                    pct = self.progress_queue.get_nowait()
                except queue.Empty:
                    break
                self.progress["value"] = pct * 100
            while True:
                try:
                    kind, payload = self.result_queue.get_nowait()
                except queue.Empty:
                    break
                if kind == "fetch_done":
                    self._handle_fetch_result(payload)
                elif kind == "fetch_error":
                    self.btn_load.config(state="normal")
                    self.btn_auto_match.config(state="normal")
                    messagebox.showerror("Load failed", str(payload))
                elif kind == "commit_done":
                    self._handle_commit_done(payload)
                elif kind == "commit_error":
                    self._set_busy(False)
                    messagebox.showerror("Update failed", str(payload))
                elif kind == "auto_match_done":
                    self._handle_auto_match_done(payload[0], payload[1]) if isinstance(payload, tuple) else self._handle_auto_match_done(payload)
                elif kind == "auto_match_error":
                    self._handle_auto_match_error(payload)
                elif kind == "auto_match_need_gpo":
                    self._handle_need_gpo()
        except tk.TclError:
            return
        self.after(120, self._poll_queues)

    def _set_busy(self, busy: bool):
        state = "disabled" if busy else "normal"
        self.btn_load.config(state=state)
        self.btn_auto_match.config(state=state)
        self.btn_rerun_cache.config(state=state)
        self.btn_clear_cache.config(state=state)
        self.btn_apply.config(
            state=state if self.active_df is not None and not busy else "disabled"
        )

    def _on_clear_cache(self):
        creds = self._creds()
        cpath = cache_path(creds["server"], creds["database"], creds["schema"])
        if not cpath.is_file():
            messagebox.showinfo(
                "Clear AI Cache", "No cache file exists yet for this server/database/schema."
            )
            return
        if not messagebox.askyesno(
            "Clear AI Cache",
            "Delete the local Auto-Match resume cache for this server/database/schema?\n\n"
            f"{cpath}\n\n"
            "This only clears locally-cached AI classifications — it does not touch anything "
            "already written to the database. The next Auto-Match run will re-classify every "
            "candidate with AI from scratch. Continue?",
        ):
            return
        if clear_cache(cpath):
            self._log(f"Cleared AI cache: {cpath}")
            self._update_cache_status_ui()
            messagebox.showinfo("Clear AI Cache", "Cache cleared.")

    def _on_rerun_from_cache(self):
        """Forces resume mode ON and triggers Auto-Match."""
        self.resume_cache_var.set(True)
        self._on_auto_match()

    def _on_load(self):
        if self.auto_match_running:
            return
        if not self.schema_var.get().strip():
            messagebox.showerror("Missing schema", "Please enter a Schema.")
            return
        self.btn_load.config(state="disabled")
        self.btn_auto_match.config(state="disabled")
        self.progress["value"] = 0
        task_name = self.task_var.get()
        creds = self._creds()
        gpo = self.gpo_var.get()
        threading.Thread(
            target=self._fetch_worker, args=(task_name, creds, gpo), daemon=True
        ).start()

    def _fetch_worker(self, task_name, creds, gpo_id_text):
        try:
            self._log(f"Looking up matches for: {task_name}")
            query = TASKS[task_name]["select"](creds["database"], creds["schema"])
            if task_name == "Task 2: Match by GPM EU XREF":
                gpo_id = int(float(gpo_id_text or 0))
                df = run_match_query(
                    creds["server"], creds["database"], creds["username"], creds["password"],
                    query, params=[gpo_id], auth_mode=creds["auth_mode"],
                    trust_server_certificate=creds["trust_server_certificate"],
                    encrypt=creds["encrypt"],
                )
            else:
                df = run_match_query(
                    creds["server"], creds["database"], creds["username"], creds["password"],
                    query, auth_mode=creds["auth_mode"],
                    trust_server_certificate=creds["trust_server_certificate"],
                    encrypt=creds["encrypt"],
                )
            self._log(f"Found {len(df)} possible match(es). Scoring similarity…")
            df = build_similarity_dataframe(df)
            self.result_queue.put(("fetch_done", (task_name, df)))
        except Exception as e:
            self._log(f"Could not load matches: {e}")
            self.result_queue.put(("fetch_error", e))

    def _handle_fetch_result(self, payload):
        task_name, df = payload
        self.active_task_name = task_name
        self.active_df = df
        self.exclusion_set = set()
        self.current_page = 1
        self.btn_load.config(state="normal")
        self.btn_auto_match.config(state="normal")
        if df is None or df.empty:
            messagebox.showinfo("No results", "No candidate rows for this task.")
            self.btn_apply.config(state="disabled")
            self._clear_tree()
            return
        self._log(f"Ready to review — {len(df)} row(s) loaded.")
        self.btn_apply.config(state="normal")
        self._refresh_list(reset_page=True)
        self._update_exclusion_label()

    def _clear_tree(self):
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        self.row_by_iid = {}
        self.selected_row = None
        for b in (self.btn_search_staging, self.btn_search_master, self.btn_search_combined):
            b.config(state="disabled")

    def _refresh_list(self, reset_page=False):
        if self.active_df is None:
            self._clear_tree()
            return
        df = self.active_df.copy()
        ascending = SORT_OPTIONS.get(self.sort_var.get(), False)
        df = df.sort_values(
            by=["_similarity_score", "REC_ID"], ascending=[ascending, True], kind="mergesort"
        ).reset_index(drop=True)

        try:
            page_size = int(self.page_size_var.get())
        except Exception:
            page_size = 25
        total_rows = len(df)
        total_pages = max(1, -(-total_rows // page_size))
        if reset_page:
            self.current_page = 1
        self.current_page = min(max(1, self.current_page), total_pages)
        start = (self.current_page - 1) * page_size
        page_df = df.iloc[start:start + page_size]

        self._clear_tree()
        for idx, row in page_df.iterrows():
            rec_id = row["REC_ID"]
            iid = f"{rec_id}_{idx}"
            is_excluded = rec_id in self.exclusion_set
            orig_nm = clean_str(row.get("UPD_NM_1", ""))
            cm_nm = clean_str(row.get("CMPNY_NM", ""))
            orig_addr = clean_str(row.get("UPD_ADDR_1", ""))
            cm_addr = clean_str(row.get("CMPNY_ADDR_1", ""))
            orig_loc = f"{clean_str(row.get('UPD_CITY', ''))}, {clean_str(row.get('UPD_ST', ''))} {clean_str(row.get('UPD_ZIP', ''))}".strip(", ")
            cm_loc = f"{clean_str(row.get('CMPNY_CITY', ''))}, {clean_str(row.get('CMPNY_ST', ''))}".strip(", ")
            if is_excluded:
                tag = "excluded"
            else:
                tag = "kept" if (len(self.tree.get_children()) % 2 == 0) else "kept_alt"
            values = (
                "☑" if is_excluded else "☐",
                rec_id,
                row.get("CMPNY_ID", ""),
                orig_nm, cm_nm, orig_addr, cm_addr, orig_loc, cm_loc,
            )
            self.tree.insert("", "end", iid=iid, values=values, tags=(tag,))
            self.row_by_iid[iid] = row

        self.lbl_page.config(text=f"Page {self.current_page} of {total_pages}  ·  {total_rows} row(s)")
        self.btn_prev.config(state="disabled" if self.current_page <= 1 else "normal")
        self.btn_next.config(state="disabled" if self.current_page >= total_pages else "normal")
        self._update_exclusion_label()

    def _go_prev(self):
        self.current_page = max(1, self.current_page - 1)
        self._refresh_list()

    def _go_next(self):
        self.current_page += 1
        self._refresh_list()

    def _on_tree_click(self, event):
        region = self.tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        col = self.tree.identify_column(event.x)
        iid = self.tree.identify_row(event.y)
        if not iid or col != "#1":
            return
        row = self.row_by_iid.get(iid)
        if row is None:
            return
        rec_id = row["REC_ID"]
        if rec_id in self.exclusion_set:
            self.exclusion_set.discard(rec_id)
        else:
            self.exclusion_set.add(rec_id)
        self._refresh_list()

    def _on_tree_select(self, event):
        sel = self.tree.selection()
        if not sel:
            self.selected_row = None
            for b in (self.btn_search_staging, self.btn_search_master, self.btn_search_combined):
                b.config(state="disabled")
            return
        row = self.row_by_iid.get(sel[0])
        self.selected_row = row
        state = "normal" if row is not None else "disabled"
        for b in (self.btn_search_staging, self.btn_search_master, self.btn_search_combined):
            b.config(state=state)

    def _entity_info(self, row, staging):
        if staging:
            nm = clean_str(row.get("UPD_NM_1", ""))
            addr = clean_str(row.get("UPD_ADDR_1", ""))
            city = clean_str(row.get("UPD_CITY", ""))
            st_ = clean_str(row.get("UPD_ST", ""))
            zip_ = clean_str(row.get("UPD_ZIP", ""))
        else:
            nm = clean_str(row.get("CMPNY_NM", ""))
            addr = clean_str(row.get("CMPNY_ADDR_1", ""))
            city = clean_str(row.get("CMPNY_CITY", ""))
            st_ = clean_str(row.get("CMPNY_ST", ""))
            zip_ = ""
        loc = f"{city}, {st_} {zip_}".strip(", ")
        return nm, addr, loc

    def _search_url(self, row, staging):
        nm, addr, loc = self._entity_info(row, staging)
        q = f"{nm} {addr} {loc}".strip()
        return f"https://www.google.com/search?q={urllib.parse.quote_plus(q)}"

    def _combined_search_url(self, row):
        s_nm, s_addr, s_loc = self._entity_info(row, staging=True)
        m_nm, m_addr, m_loc = self._entity_info(row, staging=False)
        q = f"{s_nm} {s_addr} {s_loc} and {m_nm} {m_addr} {m_loc} same?"
        return f"https://www.google.com/search?q={urllib.parse.quote_plus(q)}"

    def _open_staging_search(self):
        if self.selected_row is not None:
            webbrowser.open(self._search_url(self.selected_row, staging=True))

    def _open_master_search(self):
        if self.selected_row is not None:
            webbrowser.open(self._search_url(self.selected_row, staging=False))

    def _open_combined_search(self):
        if self.selected_row is not None:
            webbrowser.open(self._combined_search_url(self.selected_row))

    def _update_exclusion_label(self):
        all_ids = set(self.exclusion_set)
        text = ", ".join(map(str, sorted(all_ids))) if all_ids else "(none)"
        if len(text) > 120:
            text = text[:117] + "…"
        self.lbl_excl.config(text=text)

    def _prioritize_and_deduplicate(self, df: pd.DataFrame, score_col: str = None) -> pd.DataFrame:
        if not df.duplicated(subset=["REC_ID"], keep=False).any():
            return df

        hsp_set = set()
        try:
            from shared.services.workspace import global_cache_dir
            hsp_path = global_cache_dir() / "company_master_hsp.csv"
            if hsp_path.exists():
                hsp_df = pd.read_csv(hsp_path, usecols=['CMPNY_ID'], dtype=str)
                hsp_set = set(hsp_df['CMPNY_ID'].dropna().str.strip())
        except Exception as e:
            self._log(f"Warning: Could not load HSP cache for prioritization ({e})")

        def is_hsp(val):
            try:
                return 1 if str(int(float(val))) in hsp_set else 0
            except:
                return 0

        df = df.copy()
        df['is_hsp'] = df['CMPNY_ID'].apply(is_hsp)

        sort_cols = ["is_hsp"]
        ascending_flags = [False]

        if "result" in df.columns:
            df['type_priority'] = df['result'].map({'SAME': 1, 'AFFILIATED': 2}).fillna(99)
            sort_cols.append("type_priority")
            ascending_flags.append(True)

        if score_col and score_col in df.columns:
            sort_cols.append(score_col)
            ascending_flags.append(False)

        df = df.sort_values(by=sort_cols, ascending=ascending_flags)
        df = df.drop_duplicates(subset=["REC_ID"], keep="first")

        drop_cols = ['is_hsp']
        if 'type_priority' in df.columns:
            drop_cols.append('type_priority')
        df = df.drop(columns=drop_cols)

        self._log("Duplicate REC_IDs: keeping strongest match only (HSP prioritized).")
        return df

    def _on_apply(self):
        if self.active_df is None or self.auto_match_running:
            return
        if not messagebox.askyesno(
            "Confirm update",
            "This will write ENDUSER_ID matches to TEMP_NAME_ADDR_PRCS for all "
            "non-excluded rows in this task. Continue?",
        ):
            return
        self._set_busy(True)
        self.progress["value"] = 0
        creds = self._creds()
        df = self.active_df.copy()
        full_exclusions = set(self.exclusion_set)
        current_task = self.task_var.get()
        threading.Thread(
            target=self._commit_worker, args=(creds, df, full_exclusions, current_task), daemon=True
        ).start()

    def _commit_worker(self, creds, df, exclusions, task_name):
        try:
            update_df = df[~df["REC_ID"].isin(exclusions)].copy()
            if update_df.empty:
                self._log("Everything was set aside — nothing was saved.")
                self.result_queue.put(("commit_done", 0))
                return
            update_df = self._prioritize_and_deduplicate(update_df, score_col="_similarity_score")

            params_list = []
            for _, row in update_df.iterrows():
                try:
                    params_list.append((int(float(row["CMPNY_ID"])), int(float(row["REC_ID"]))))
                except (ValueError, TypeError) as err:
                    self._log(f"Skipped invalid ID: {err}")

            self._log(f"Saving links for {len(params_list)} row(s)…")
            if task_name == "Task 2: Match by GPM EU XREF":
                method = "GPM_XREF"
            elif task_name.startswith("Task 1"):
                method = "HIN"
            else:
                method = "NAD_MN"
            n = commit_matches(
                creds["server"], creds["database"], creds["username"], creds["password"],
                creds["schema"], params_list,
                auth_mode=creds["auth_mode"],
                trust_server_certificate=creds["trust_server_certificate"],
                encrypt=creds["encrypt"],
                log=self._log,
                progress=lambda p: self.progress_queue.put(p),
                match_method=method,
            )
            self.result_queue.put(("commit_done", n))
        except Exception as e:
            self._log(f"Save failed: {e}")
            self.result_queue.put(("commit_error", e))

    def _handle_commit_done(self, rows_affected):
        self._set_busy(False)
        if rows_affected:
            messagebox.showinfo("Success", f"Successfully matched and saved {rows_affected} records.")
        self.reset_session(clear_log=False)
        self.progress["value"] = 0

    def _on_auto_apply_task2(self):
        """Run and apply Task 2 automatically."""
        if self.auto_match_running:
            return

        creds = self._creds()
        if not creds["schema"]:
            messagebox.showerror("Missing schema", "Please enter a Schema.")
            return

        gpo = self.gpo_var.get().strip()
        if not gpo:
            messagebox.showwarning(
                "GPO Company ID",
                "Please select a GPO Company ID from the dropdown before continuing.",
            )
            return

        resume_from_cache = self.resume_cache_var.get()
        warning = (
            "This will run Task 2 automatically using AI classification.\n\n"
            "Candidates are fetched using the selected GPO ID, and each pair is classified by AI "
            "as SAME, AFFILIATED, or DIFFERENT.\n"
            "  • SAME and AFFILIATED matches are written to ENDUSER_ID right away.\n"
            "  • DIFFERENT matches are skipped (not written).\n\n"
            + (
                "Resume from cache is ON — rows already classified in a previous run "
                "will be reused instead of calling the AI again.\n\n"
                if resume_from_cache
                else "Resume from cache is OFF — every candidate will be sent to the AI, "
                "even ones classified in a previous run.\n\n"
            )
            + "This writes directly to the active workspace cache and database as it runs.\n\nContinue?"
        )
        if not messagebox.askyesno("Auto-Apply Task 2", warning):
            return

        self.auto_match_running = True
        self._auto_match_cancel = False
        self._set_busy(True)
        self.progress["value"] = 0
        self._log(f"=== Auto-Apply Task 2 started for GPO ID: {gpo} ===")
        threading.Thread(target=self._auto_apply_task2_worker, args=(creds, gpo, resume_from_cache), daemon=True).start()

    def _auto_apply_task2_worker(self, creds, gpo_id_text, resume_from_cache):
        try:
            from project_app.services.ai_matching import ensure_available, classify_candidates
            from project_app.services.match_cache import cache_path, load_cache, make_uni_id
            ensure_available()
        except Exception as e:
            self._log(f"Cannot start AI matching: {e}")
            self.result_queue.put(("auto_match_error", e))
            return

        task_name = "Task 2: Match by GPM EU XREF"
        cpath = cache_path(creds["server"], creds["database"], creds["schema"])
        cache_map = load_cache(cpath) if resume_from_cache else {}

        if resume_from_cache:
            self._log(f"Cache file: {cpath} ({len(cache_map)} previously classified row(s) available).")
        else:
            self._log(f"Cache file: {cpath} (resume disabled — classifying everything fresh).")

        try:
            self._log(f"\n--- Starting {task_name} ---")

            task_details = TASKS.get(task_name)
            if not task_details:
                self._log(f"ERROR: Task '{task_name}' not found.")
                self.result_queue.put(("auto_match_error", Exception(f"Task '{task_name}' not found.")))
                return

            gpo_id = int(float(gpo_id_text))
            query = task_details["select"](creds["database"], creds["schema"])
            df = run_match_query(
                server=creds["server"],
                database=creds["database"],
                username=creds["username"],
                password=creds["password"],
                query=query,
                params=[gpo_id],
                auth_mode=creds["auth_mode"],
                trust_server_certificate=creds["trust_server_certificate"],
                encrypt=creds["encrypt"],
            )

            if df.empty:
                self._log(f"{task_name}: No candidates found.")
                self.result_queue.put(("auto_match_done", (None, "4-7")))
                return

            df["_source_task"] = task_name
            df["uni_id"] = [
                make_uni_id(task_name, row.get("REC_ID"), row.get("CMPNY_ID"))
                for _, row in df.iterrows()
            ]

            is_cached = df["uni_id"].isin(cache_map.keys()) if cache_map else pd.Series(
                [False] * len(df), index=df.index
            )
            cached_df = df[is_cached]
            fresh_df = df[~is_cached].copy()

            if not cached_df.empty:
                resumed = self._apply_cache_results(cached_df, cache_map)
                self._log(f"{task_name}: resumed {len(resumed)} row(s) from cache — skipped API calls.")
                classified = resumed
            else:
                classified = pd.DataFrame()

            if not fresh_df.empty:
                self._log(f"{task_name}: evaluating {len(fresh_df)} row(s) with Gemini AI...")
                self.progress_queue.put(0.0)
                try:
                    classified_fresh = classify_candidates(
                        fresh_df,
                        log=self._log,
                        progress=lambda p: self.progress_queue.put(p),
                        on_batch_done=lambda batch_df: self._persist_batch_to_cache(cpath, batch_df),
                    )
                except Exception as e:
                    self._log(f"{task_name}: AI classification failed — {e}. "
                               f"Any rows classified before the failure were saved to cache.")
                    self.result_queue.put(("auto_match_error", e))
                    return

                if classified.empty:
                    classified = classified_fresh
                else:
                    classified = pd.concat([classified, classified_fresh], ignore_index=True)

            same_aff = classified[classified["result"].isin(["SAME", "AFFILIATED"])].copy()
            if same_aff.empty:
                self._log(f"{task_name}: No SAME/AFFILIATED matches found by AI.")
                self.result_queue.put(("auto_match_done", (classified, "2")))
                return

            # Keep only the first best match for a given REC_ID
            same_aff = self._prioritize_and_deduplicate(same_aff, score_col="confidence")

            matches = [
                (row["CMPNY_ID"], row["REC_ID"])
                for _, row in same_aff.iterrows()
            ]

            committed_count = commit_matches(
                server=creds["server"],
                database=creds["database"],
                username=creds["username"],
                password=creds["password"],
                schema=creds["schema"],
                update_rows=matches,
                auth_mode=creds["auth_mode"],
                trust_server_certificate=creds["trust_server_certificate"],
                encrypt=creds["encrypt"],
                log=self._log,
                match_method="GPM_XREF",
            )

            self._log(f"{task_name}: Committed {committed_count} matches.")

        except Exception as e:
            self._log(f"ERROR running {task_name}: {e}")
            self.result_queue.put(("auto_match_error", e))
            return


        self._log(f"=== Auto-Apply Task 2 finished. Total committed: {committed_count}. ===")

        try:
            final_path = final_results_path("2")
            final_path.parent.mkdir(parents=True, exist_ok=True)
            from shared.ui.ui_common import save_dataframe
            final_path = save_dataframe(classified, final_path)
            self._log(f"Auto-saved final results: {final_path}")
        except Exception as e:
            self._log(f"Warning: could not auto-save final nad_ai_2.xlsx — {e}")

        self.result_queue.put(("auto_match_done", (classified, "2")))


    def _on_auto_match(self):
        if self.auto_match_running:
            return
        if not self.schema_var.get().strip():
            messagebox.showerror("Missing schema", "Please enter a Schema.")
            return

        resume_from_cache = self.resume_cache_var.get()
        warning = (
            "This will run Tasks 4 through 7 in order automatically.\n\n"
            "For each task: candidates are fetched, and each pair is classified by AI "
            "as SAME, AFFILIATED, or DIFFERENT.\n"
            "  • SAME and AFFILIATED matches are written to ENDUSER_ID right away.\n"
            "  • DIFFERENT matches are skipped (not written).\n\n"
            + (
                "Resume from cache is ON — rows already classified in a previous run "
                "will be reused instead of calling the AI again.\n\n"
                if resume_from_cache
                else "Resume from cache is OFF — every candidate will be sent to the AI, "
                "even ones classified in a previous run.\n\n"
            )
            + "This writes directly to the active workspace cache and database as it runs.\n\nContinue?"
        )
        if not messagebox.askyesno("Auto-Match Tasks 4-7 (AI)", warning):
            return

        self.auto_match_running = True
        self._auto_match_cancel = False
        self._set_busy(True)
        self.progress["value"] = 0
        creds = self._creds()
        self._log("=== Auto-Match (Tasks 4-7, AI) started ===")
        threading.Thread(
            target=self._auto_match_worker, args=(creds, resume_from_cache), daemon=True
        ).start()

    def _auto_match_worker(self, creds, resume_from_cache):
        try:
            from project_app.services.ai_matching import ensure_available
            ensure_available()
        except Exception as e:
            self._log(f"Cannot start AI matching: {e}")
            self.result_queue.put(("auto_match_error", e))
            return

        cpath = cache_path(creds["server"], creds["database"], creds["schema"])
        cache_map = load_cache(cpath) if resume_from_cache else {}
        if resume_from_cache:
            self._log(f"Cache file: {cpath} ({len(cache_map)} previously classified row(s) available).")
        else:
            self._log(f"Cache file: {cpath} (resume disabled — classifying everything fresh).")

        all_frames = []
        grand_same = grand_affiliated = grand_different = grand_error = grand_committed = 0
        grand_resumed = 0
        gpo_text = ""

        for task_name in AUTO_MATCH_TASK_KEYS:
            if self._auto_match_cancel:
                self._log("Auto-Match cancelled by user.")
                break

            if task_name == "Task 2: Match by GPM EU XREF":
                self._log("--- Pausing before Task 2: select GPO Company ID, then click Continue Task 2 ---")
                self._gpo_continue_event.clear()
                self.result_queue.put(("auto_match_need_gpo", None))
                while not self._gpo_continue_event.wait(timeout=0.4):
                    if self._auto_match_cancel:
                        break
                if self._auto_match_cancel:
                    self._log("Auto-Match cancelled before Task 2.")
                    break
                gpo_text = (self._gpo_for_task2 or "").strip()
                if not gpo_text:
                    self._log(f"{task_name}: skipped (no GPO Company ID selected).")
                    continue
                self._log(f"{task_name}: using GPO Company ID = {gpo_text}")

            self._log(f"--- {task_name}: fetching candidates ---")
            try:
                query = TASKS[task_name]["select"](creds["database"], creds["schema"])
                if task_name == "Task 2: Match by GPM EU XREF":
                    gpo_id = int(float(gpo_text))
                    df = run_match_query(
                        creds["server"], creds["database"], creds["username"], creds["password"],
                        query, params=[gpo_id], auth_mode=creds["auth_mode"],
                        trust_server_certificate=creds["trust_server_certificate"],
                        encrypt=creds["encrypt"],
                    )
                else:
                    df = run_match_query(
                        creds["server"], creds["database"], creds["username"], creds["password"],
                        query, auth_mode=creds["auth_mode"],
                        trust_server_certificate=creds["trust_server_certificate"],
                        encrypt=creds["encrypt"],
                    )
            except Exception as e:
                self._log(f"{task_name}: failed to fetch candidates — {e}. Skipping this task.")
                continue

            if df is None or df.empty:
                self._log(f"{task_name}: no candidate rows.")
                continue

            df = df.copy()
            df["_source_task"] = task_name
            df["uni_id"] = [
                make_uni_id(task_name, rec_id, cmpny_id)
                for rec_id, cmpny_id in zip(df["REC_ID"], df["CMPNY_ID"])
            ]

            is_cached = df["uni_id"].isin(cache_map.keys()) if cache_map else pd.Series(
                [False] * len(df), index=df.index
            )
            cached_df = df[is_cached]
            fresh_df = df[~is_cached].copy()

            task_frames = []
            if not cached_df.empty:
                resumed = self._apply_cache_results(cached_df, cache_map)
                self._log(f"{task_name}: resumed {len(resumed)} row(s) from cache — skipped API calls.")
                grand_resumed += len(resumed)
                task_frames.append(resumed)

            if not fresh_df.empty:
                self._log(f"{task_name}: {len(fresh_df)} candidate(s) need AI classification.")
                self.progress_queue.put(0.0)
                try:
                    classified_fresh = classify_candidates(
                        fresh_df,
                        log=self._log,
                        progress=lambda p: self.progress_queue.put(p),
                        on_batch_done=lambda batch_df: self._persist_batch_to_cache(cpath, batch_df),
                    )
                except Exception as e:
                    self._log(f"{task_name}: AI classification failed — {e}. "
                               f"Any rows classified before the failure were saved to cache.")
                    classified_fresh = None
                if classified_fresh is not None:
                    task_frames.append(classified_fresh)

            if not task_frames:
                continue
            classified = pd.concat(task_frames, ignore_index=True)
            classified["_committed"] = False

            same_n = int((classified["result"] == "SAME").sum())
            affiliated_n = int((classified["result"] == "AFFILIATED").sum())
            different_n = int((classified["result"] == "DIFFERENT").sum())
            error_n = int((classified["result"] == "ERROR").sum())
            self._log(
                f"{task_name}: SAME={same_n}, AFFILIATED={affiliated_n}, "
                f"DIFFERENT={different_n} (skipped), ERROR={error_n} (skipped)."
            )

            same_aff = classified[classified["result"].isin(["SAME", "AFFILIATED"])].copy()
            committed_n = 0
            if not same_aff.empty:
                same_aff = self._prioritize_and_deduplicate(same_aff, score_col="confidence")

                params_list = []
                keep_rec_ids = []
                for _, row in same_aff.iterrows():
                    try:
                        params_list.append((int(float(row["CMPNY_ID"])), int(float(row["REC_ID"]))))
                        keep_rec_ids.append(row["REC_ID"])
                    except (ValueError, TypeError) as err:
                        self._log(f"{task_name}: skipped invalid ID row — {err}")

                self._log(f"{task_name}: writing {len(params_list)} match(es)…")
                try:
                    committed_n = commit_matches(
                        creds["server"], creds["database"], creds["username"], creds["password"],
                        creds["schema"], params_list,
                        auth_mode=creds["auth_mode"],
                        trust_server_certificate=creds["trust_server_certificate"],
                        encrypt=creds["encrypt"],
                        log=self._log,
                        progress=lambda p: self.progress_queue.put(p),
                        match_method="NAD_AI",
                    )
                    committed_mask = classified["REC_ID"].isin(keep_rec_ids) & classified["result"].isin(
                        ["SAME", "AFFILIATED"]
                    )
                    classified.loc[committed_mask, "_committed"] = True
                except Exception as e:
                    self._log(f"{task_name}: save failed — {e}")

            self._log(f"{task_name}: committed {committed_n} update(s).")

            grand_same += same_n
            grand_affiliated += affiliated_n
            grand_different += different_n
            grand_error += error_n
            grand_committed += committed_n
            all_frames.append(classified)

        if not all_frames:
            self._log("=== Auto-Match finished — no candidates were found in Tasks 4-7. ===")
            self.result_queue.put(("auto_match_done", (None, "2")))
            return

        combined = pd.concat(all_frames, ignore_index=True, sort=False)

        # Auto-save final result directly into active workspace output dir
        try:
            final_path = final_results_path("4-7")
            final_path.parent.mkdir(parents=True, exist_ok=True)
            from shared.ui.ui_common import save_dataframe
            final_path = save_dataframe(combined, final_path)
            self._log(f"Auto-saved final results: {final_path}")
        except Exception as e:
            self._log(f"Warning: could not auto-save final nad_ai.xlsx — {e}")

        self._log(
            f"=== Auto-Match finished — SAME={grand_same}, AFFILIATED={grand_affiliated}, "
            f"DIFFERENT={grand_different} (skipped), ERROR={grand_error} (skipped), "
            f"committed={grand_committed} total, resumed from cache={grand_resumed}. ==="
        )
        self.result_queue.put(("auto_match_done", (combined, "4-7")))

    def _apply_cache_results(self, df: pd.DataFrame, cache_map: dict) -> pd.DataFrame:
        out = df.copy()
        for col in RESULT_COLUMNS:
            out[col] = None
        for idx, row in out.iterrows():
            rec = cache_map.get(row["uni_id"])
            if not rec:
                continue
            out.at[idx, "result"] = rec.get("result")
            out.at[idx, "result_reason"] = rec.get("result_reason")
            conf = rec.get("confidence")
            try:
                out.at[idx, "confidence"] = int(float(conf)) if conf not in (None, "", "nan") else None
            except (ValueError, TypeError):
                out.at[idx, "confidence"] = None
            out.at[idx, "entity_1_parent"] = rec.get("entity_1_parent")
            out.at[idx, "entity_2_parent"] = rec.get("entity_2_parent")
        return out

    def _persist_batch_to_cache(self, cpath, batch_df: pd.DataFrame):
        try:
            records = batch_df.to_dict("records")
            append_cache(cpath, records)
            self._log(f"Cache checkpoint: +{len(records)} row(s) → {cpath.name}")
            self._update_cache_status_ui()
        except Exception as e:
            self._log(f"Warning: could not write AI cache checkpoint — {e}")

    def _handle_need_gpo(self):
        pass

    def _on_continue_task2(self):
        pass

    def _handle_auto_match_done(self, combined_df, task_group="4-7"):
        self.auto_match_group = task_group
        self.auto_match_running = False
        self._set_busy(False)
        self.progress["value"] = 0
        self._update_cache_status_ui()

        if combined_df is None or combined_df.empty:
            messagebox.showinfo("Auto-Match", "No candidate rows were found.")
            return

        final_path = str(final_results_path(self.auto_match_group))
        if len(combined_df) > 1048575:
            final_path = final_path.rsplit('.', 1)[0] + '.csv'
            
        self._log(f"Auto-Match complete. Results are available at: {final_path}")
        messagebox.showinfo("Auto-Match Complete", f"Results automatically saved to:\n{final_path}")

    def _handle_auto_match_error(self, error):

        self.auto_match_running = False
        self._set_busy(False)
        self.progress["value"] = 0
        self._update_cache_status_ui()
        messagebox.showerror("Auto-Match unavailable", str(error))
