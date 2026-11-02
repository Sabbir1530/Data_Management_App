import tkinter as tk
from tkinter import messagebox
from sqlalchemy import text

from shared.config import APP_CONFIG
from shared.models.connection_store import load_saved_connection
from shared.services.database import create_database_engine
from shared.ui.connection_dialog import ConnectionDialog
from project_app.ui.main_window import MDMApp


def _probe_connection(settings) -> bool:
    try:
        engine = create_database_engine(settings, settings.database or "master")
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        print(f"[WARN] Saved connection failed: {exc}", flush=True)
        return False


def _prompt_login(root, force: bool = False):
    dlg = ConnectionDialog(root, force=force)
    root.wait_window(dlg)
    return getattr(dlg, "result", None)


class ProjectAppWindow(tk.Toplevel):
    def __init__(self, master=None):
        super().__init__(master)
        self.title(APP_CONFIG.project_window_title)
        self.geometry("360x100")
        self.minsize(320, 80)

        self.status = tk.Label(self, text="Starting…", font=("Segoe UI", 11))
        self.status.pack(expand=True, fill="both", padx=12, pady=12)

        self.after(50, self.startup)

    def startup(self):
        connection_settings = load_saved_connection()

        if connection_settings is not None:
            if not _probe_connection(connection_settings):
                connection_settings = _prompt_login(self, force=True)
        else:
            connection_settings = _prompt_login(self, force=False)

        if connection_settings is None:
            self.destroy()
            return

        self.status.config(text="Building main window…")
        self.update_idletasks()

        for child in list(self.winfo_children()):
            try:
                child.destroy()
            except tk.TclError:
                pass

        self.geometry(APP_CONFIG.project_window_geometry)
        self.app = MDMApp(self, connection_settings)
