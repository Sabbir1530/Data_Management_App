import os
import threading
import asyncio
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from project_app.services.distance_pipeline import execute_pipeline, CANCEL_EVENT, logger
from shared.services.workspace import get_workspace_path, set_custom_workspace_path, get_last_schema


class DistanceMatchingPage(ttk.Frame):
    def __init__(self, master, connection_settings=None, shared_workspace_var=None, shared_schema_var=None):
        super().__init__(master)
        self.connection_settings = connection_settings
        self.shared_workspace_var = shared_workspace_var
        self.shared_schema_var = shared_schema_var

        # Database Name Entry
        tk.Label(self, text="Database Name:", font=("Arial", 10, "bold")).pack(anchor="w", padx=15, pady=(15, 2))
        
        default_db = getattr(self.connection_settings, "database", "TRAINING_DB") if self.connection_settings else "TRAINING_DB"
        self.db_var = tk.StringVar(value=default_db)
        
        self.ent_db = ttk.Entry(self, textvariable=self.db_var, width=78)
        self.ent_db.pack(anchor="w", padx=15, pady=2)

        # Database Schema Entry
        tk.Label(self, text="Database Schema:", font=("Arial", 10, "bold")).pack(anchor="w", padx=15, pady=(10, 2))
        
        self.schema_var = self.shared_schema_var if self.shared_schema_var else tk.StringVar(value=get_last_schema() or "suham_q1")
        
        self.ent_schema = ttk.Entry(self, textvariable=self.schema_var, width=78)
        self.ent_schema.pack(anchor="w", padx=15, pady=2)
        
        def _on_schema_changed(*args):
            from shared.services.workspace import set_last_schema
            set_last_schema(self.schema_var.get())
        self.schema_var.trace_add("write", _on_schema_changed)

        # Workspace Directory Picker
        tk.Label(self, text="Run Workspace Directory:", font=("Arial", 10, "bold")).pack(anchor="w", padx=15, pady=(10, 2))
        frame_dir = tk.Frame(self)
        frame_dir.pack(fill="x", padx=15, pady=2)

        self.ws_dir_var = self.shared_workspace_var if self.shared_workspace_var else tk.StringVar(value=str(get_workspace_path()))
        self.ent_dir = ttk.Entry(frame_dir, textvariable=self.ws_dir_var, width=65)
        self.ent_dir.pack(side="left", expand=True, fill="x")

        btn_browse = ttk.Button(frame_dir, text="Browse...", command=self.browse_folder)
        btn_browse.pack(side="right", padx=(5, 0))

        self.ent_dir.bind("<Return>", lambda e: self._on_workspace_input_changed())
        self.ent_dir.bind("<FocusOut>", lambda e: self._on_workspace_input_changed())


        # Action Buttons Control Frame
        frame_btns = tk.Frame(self)
        frame_btns.pack(anchor="w", padx=15, pady=12)

        self.btn_run = tk.Button(frame_btns, text="Run Pipeline", bg="#2e7d32", fg="white", font=("Arial", 10, "bold"), command=self.start_pipeline)
        self.btn_run.pack(side="left", padx=(0, 10))

        self.btn_rerun = tk.Button(frame_btns, text="Rerun / Resume Stuck Run", bg="#0288d1", fg="white", font=("Arial", 10, "bold"), command=self.rerun_pipeline)
        self.btn_rerun.pack(side="left", padx=(0, 10))

        self.btn_stop = tk.Button(frame_btns, text="Stop Execution", bg="#c62828", fg="white", font=("Arial", 10, "bold"), state="disabled", command=self.stop_pipeline)
        self.btn_stop.pack(side="left")

        # Progress Bar
        tk.Label(self, text="Overall Progress:", font=("Arial", 10, "bold")).pack(anchor="w", padx=15, pady=(5, 2))
        self.progress = ttk.Progressbar(self, orient="horizontal", length=685, mode="determinate")
        self.progress.pack(anchor="w", padx=15, pady=(0, 10))

        # Execution Terminal Output
        tk.Label(self, text="Execution Log:", font=("Arial", 10, "bold")).pack(anchor="w", padx=15, pady=(5, 2))
        self.txt_log = tk.Text(self, state="disabled", bg="#1e1e1e", fg="#00ff00", font=("Consolas", 9))
        self.txt_log.pack(fill="both", expand=True, padx=15, pady=(0, 15))

    def browse_folder(self):
        from pathlib import Path
        folder = filedialog.askdirectory(title="Select Run Folder", initialdir=self.ws_dir_var.get())
        if folder:
            self.ws_dir_var.set(folder)
            self._on_workspace_input_changed()

    def _on_workspace_input_changed(self):
        from pathlib import Path
        path_str = self.ws_dir_var.get().strip()
        if path_str:
            try:
                set_custom_workspace_path(Path(path_str))
            except Exception:
                pass

    def log(self, text):
        self.after(0, self._append_log, text)

    def _append_log(self, text):
        self.txt_log.config(state="normal")
        self.txt_log.insert(tk.END, text + "\n")
        self.txt_log.see(tk.END)
        self.txt_log.config(state="disabled")

    def update_progress(self, val):
        self.after(0, self._set_progress, val)

    def _set_progress(self, val):
        self.progress['value'] = val

    def start_pipeline(self):
        self._launch_thread("Starting new execution pipeline...")

    def rerun_pipeline(self):
        self._launch_thread("Resuming execution pipeline (leveraging existing SQLite cache)...")

    def _launch_thread(self, init_msg):
        schema = self.schema_var.get().strip()
        db = self.db_var.get().strip()
        target_table = f"{db}.{schema}.TEMP_NAME_ADDR_PRCS" if schema and db else ""
        run_dir = self.ws_dir_var.get().strip()

        if not db or not schema or not run_dir:
            messagebox.showerror("Input Error", "Database, Schema, and Run folder paths are required.")
            return


        self.btn_run.config(state="disabled")
        self.btn_rerun.config(state="disabled")
        self.btn_stop.config(state="normal")
        
        self.log(f"\n[SYSTEM] {init_msg}")
        
        # Build the dynamic connection string based on the provided DB and existing creds
        from project_app.services.matching import build_pyodbc_conn_str
        conn_str = ""
        if self.connection_settings:
            conn_str = build_pyodbc_conn_str(
                server=self.connection_settings.server,
                database=db,
                username=self.connection_settings.username,
                password=self.connection_settings.password,
                auth_mode=self.connection_settings.auth_mode,
                trust_server_certificate=self.connection_settings.trust_server_certificate,
                encrypt=self.connection_settings.encrypt
            )

        threading.Thread(target=self.run_thread, args=(target_table, run_dir, conn_str, db, schema), daemon=True).start()

    def stop_pipeline(self):
        self.log("[SYSTEM] Cancellation requested. Stopping after active tasks complete...")
        CANCEL_EVENT.set()
        self.btn_stop.config(state="disabled")

    def run_thread(self, target_table, run_dir, conn_str, db, schema):
        try:
            asyncio.run(execute_pipeline(target_table, run_dir, self.log, self.update_progress, conn_str, db, schema))
        except Exception as e:
            self.log(f"ERROR: {str(e)}")
            logger.exception(e)
        finally:
            self.after(0, self._reset_buttons)

    def _reset_buttons(self):
        self.btn_run.config(state="normal")
        self.btn_rerun.config(state="normal")
        self.btn_stop.config(state="disabled")
