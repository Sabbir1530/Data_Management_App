import tkinter as tk
from tkinter import messagebox, ttk

from sqlalchemy import text

from shared.models.connection import ConnectionSettings
from shared.models.connection_store import clear_saved_connection, load_saved_connection, save_connection
from shared.services.database import create_database_engine

class ConnectionDialog(tk.Toplevel):
    """Modal connection form attached to the single app root. Does NOT call wait_window itself."""

    def __init__(self, parent, prefill=None, force: bool = False):
        super().__init__(parent)
        self.title("MSSQL Connection Setup")
        self.geometry("500x400")
        self.resizable(False, False)
        self.result = None
        self._force = force
        self.protocol("WM_DELETE_WINDOW", self._cancel)

        try:
            self.transient(parent)
        except tk.TclError:
            pass

        self._saved = prefill or load_saved_connection()
        self._build_widgets()
        if self._saved:
            self._apply_settings(self._saved)

        
        self.attributes("-topmost", True)
        self.lift()
        self.focus_force()
        self.update_idletasks()
        try:
            self.grab_set()
        except tk.TclError:
            pass

        
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        self.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

    def _build_widgets(self):
        container = ttk.Frame(self, padding=12)
        container.pack(fill="both", expand=True)

        ttk.Label(container, text="Connection Setup", font=("Segoe UI", 12, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 4)
        )
        hint = (
            "Saved login found — confirm or edit, then Connect."
            if self._saved and not self._force
            else "Enter server details. Login is saved after a successful connect."
        )
        ttk.Label(container, text=hint, foreground="gray").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(0, 10)
        )

        self.server_var = tk.StringVar(master=self)
        self.database_var = tk.StringVar(master=self, value="master")
        self.username_var = tk.StringVar(master=self)
        self.password_var = tk.StringVar(master=self)
        self.auth_mode_var = tk.StringVar(master=self, value="sql")
        self.trust_cert_var = tk.BooleanVar(master=self, value=True)
        self.encrypt_var = tk.BooleanVar(master=self, value=False)
        self.remember_var = tk.BooleanVar(master=self, value=True)

        self._add_labeled_entry(container, "Server name", self.server_var, 2)
        self._add_labeled_entry(container, "Database", self.database_var, 3)
        self.username_entry = self._add_labeled_entry(container, "Username", self.username_var, 4)
        self.password_entry = self._add_labeled_entry(
            container, "Password", self.password_var, 5, show="*"
        )

        ttk.Label(container, text="Authentication").grid(row=6, column=0, sticky="w", pady=(8, 0))
        auth_combo = ttk.Combobox(
            container, textvariable=self.auth_mode_var,
            values=["sql", "windows"], state="readonly", width=18,
        )
        auth_combo.grid(row=6, column=1, sticky="w", pady=(8, 0))
        auth_combo.bind("<<ComboboxSelected>>", self._sync_auth_controls)

        ttk.Checkbutton(container, text="Trust server certificate", variable=self.trust_cert_var).grid(
            row=7, column=0, columnspan=2, sticky="w", pady=(8, 0)
        )
        ttk.Checkbutton(container, text="Encrypt connection", variable=self.encrypt_var).grid(
            row=8, column=0, columnspan=2, sticky="w"
        )
        ttk.Checkbutton(
            container, text="Remember this login (skip next time)", variable=self.remember_var
        ).grid(row=9, column=0, columnspan=2, sticky="w", pady=(4, 0))

        button_row = ttk.Frame(container)
        button_row.grid(row=10, column=0, columnspan=2, sticky="e", pady=(16, 0))
        ttk.Button(button_row, text="Forget Saved Login", command=self._forget).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(button_row, text="Test Connection", command=self._test_connection).pack(
            side="left", padx=(0, 8)
        )
        ttk.Button(button_row, text="Connect", command=self._connect).pack(side="left", padx=(0, 8))
        ttk.Button(button_row, text="Cancel", command=self._cancel).pack(side="left")
        self._sync_auth_controls()

    def _add_labeled_entry(self, parent, label_text, variable, row, show=None):
        ttk.Label(parent, text=label_text).grid(row=row, column=0, sticky="w", pady=4)
        entry = ttk.Entry(parent, textvariable=variable, width=34, show=show)
        entry.grid(row=row, column=1, sticky="w", pady=4)
        return entry

    def _apply_settings(self, settings):
        self.server_var.set(settings.server or "")
        self.database_var.set(settings.database or "master")
        self.username_var.set(settings.username or "")
        self.password_var.set(settings.password or "")
        self.auth_mode_var.set(settings.auth_mode or "sql")
        self.trust_cert_var.set(bool(settings.trust_server_certificate))
        self.encrypt_var.set(bool(settings.encrypt))
        self._sync_auth_controls()

    def _sync_auth_controls(self, event=None):
        use_sql = self.auth_mode_var.get() == "sql"
        state = "normal" if use_sql else "disabled"
        self.username_entry.config(state=state)
        self.password_entry.config(state=state)
        if not use_sql:
            self.username_var.set("")
            self.password_var.set("")

    def _settings(self):
        return ConnectionSettings(
            server=self.server_var.get().strip(),
            database=self.database_var.get().strip(),
            username=self.username_var.get().strip(),
            password=self.password_var.get(),
            auth_mode=self.auth_mode_var.get().strip(),
            trust_server_certificate=self.trust_cert_var.get(),
            encrypt=self.encrypt_var.get(),
        )

    def _validate(self):
        settings = self._settings()
        if not settings.server:
            messagebox.showerror("Connection Setup", "Server name is required.", parent=self)
            return None
        if settings.auth_mode == "sql" and (not settings.username or not settings.password):
            messagebox.showerror(
                "Connection Setup",
                "Username and password are required for SQL authentication.",
                parent=self,
            )
            return None
        return settings

    def _test_sql(self, settings):
        engine = create_database_engine(settings, settings.database or "master")
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    def _test_connection(self):
        settings = self._validate()
        if settings is None:
            return
        try:
            self._test_sql(settings)
            messagebox.showinfo("Connection Setup", "Connection successful.", parent=self)
        except Exception as exc:
            messagebox.showerror("Connection Setup", f"Connection failed:\n{exc}", parent=self)

    def _connect(self):
        settings = self._validate()
        if settings is None:
            return
        try:
            self._test_sql(settings)
        except Exception as exc:
            messagebox.showerror("Connection Setup", f"Connection failed:\n{exc}", parent=self)
            return

        if self.remember_var.get():
            try:
                save_connection(settings)
            except Exception as exc:
                messagebox.showwarning(
                    "Connection Setup",
                    f"Connected, but could not save login:\n{exc}",
                    parent=self,
                )
        else:
            clear_saved_connection()

        self.result = settings
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()

    def _forget(self):
        clear_saved_connection()
        self.server_var.set("")
        self.database_var.set("master")
        self.username_var.set("")
        self.password_var.set("")
        self.auth_mode_var.set("sql")
        self.trust_cert_var.set(True)
        self.encrypt_var.set(False)
        self._saved = None
        self._sync_auth_controls()
        messagebox.showinfo("Connection Setup", "Saved login cleared.", parent=self)

    def _cancel(self):
        self.result = None
        try:
            self.grab_release()
        except tk.TclError:
            pass
        self.destroy()
