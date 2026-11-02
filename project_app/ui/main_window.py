"""MDM main window — Data Laundry & Data Matching only."""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

from shared.config import APP_CONFIG
from project_app.ui.data_laundry_page import DataLaundryPage
from project_app.ui.matching_page import DataMatchingPage
from project_app.ui.distance_page import DistanceMatchingPage


class MDMApp:
    """Main application shell with tool switcher for Laundry and Matching."""

    def __init__(self, root: tk.Tk, connection_settings):
        self.root = root
        self.config = APP_CONFIG
        self.connection_settings = connection_settings

        self.root.title(self.config.project_window_title)
        self.root.geometry(self.config.project_window_geometry)
        self.root.minsize(800, 520)
        self.root.resizable(True, True)

        self.laundry_page = None
        self.matching_page = None
        self.tool_frames: dict = {}

        self._build_ui()

    def _build_ui(self):
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        shell = ttk.Frame(self.root, padding=8)
        shell.grid(row=0, column=0, sticky="nsew")
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(0, weight=1)

        self.notebook = ttk.Notebook(shell)
        self.notebook.grid(row=0, column=0, sticky="nsew")

        from shared.services.workspace import get_workspace_path, get_last_schema
        self.shared_workspace_var = tk.StringVar(value=str(get_workspace_path()))
        self.shared_schema_var = tk.StringVar(value=get_last_schema())

        # Instantiate all pages as tabs using the shared string variables
        self.laundry_page = DataLaundryPage(self.notebook, self.connection_settings, self.shared_workspace_var, self.shared_schema_var)
        self.matching_page = DataMatchingPage(self.notebook, self.connection_settings, self.shared_workspace_var, self.shared_schema_var)
        self.distance_page = DistanceMatchingPage(self.notebook, self.connection_settings, self.shared_workspace_var, self.shared_schema_var)


        self.notebook.add(self.laundry_page, text="Data Laundry")
        self.notebook.add(self.matching_page, text="Data Matching")
        self.notebook.add(self.distance_page, text="Distance Matching")
