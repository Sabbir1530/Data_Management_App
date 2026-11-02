import tkinter as tk
from tkinter import ttk

from fuzzy_app.tabs.fuzzy_tab import FuzzyMatchTab
from fuzzy_app.tabs.gpo_tab import GpoMatchTab
from fuzzy_app.tabs.ai_validation_tab import AiValidationTab
from shared.ui.ui_common import apply_theme

class FuzzyAppWindow(tk.Toplevel):
    def __init__(self, master=None):
        super().__init__(master)
        self.title("Facility Matching Automation")
        self.geometry("1180x820")
        self.minsize(980, 640)

        style = ttk.Style(self)
        available = style.theme_names()
        for preferred in ("clam", "alt", "default"):
            if preferred in available:
                style.theme_use(preferred)
                break
        apply_theme(self)

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=8, pady=8)

        self.fuzzy_tab = FuzzyMatchTab(self.notebook)
        self.gpo_tab = GpoMatchTab(self.notebook)
        self.ai_validation_tab = AiValidationTab(self.notebook)

        self.notebook.add(self.fuzzy_tab, text="  🔍  Fuzzy Matching  ")
        self.notebook.add(self.gpo_tab, text="  🤖  GPO Matching (AI)  ")
        self.notebook.add(self.ai_validation_tab, text="  ✨  Validation (AI)  ")
