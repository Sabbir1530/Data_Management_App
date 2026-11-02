import sys
import tkinter as tk
from tkinter import ttk

from eu_app.app import EUDedupWindow
from fuzzy_app.app import FuzzyAppWindow
from project_app.app import ProjectAppWindow
from shared.ui.ui_common import apply_theme

class AppCard(tk.Frame):
    def __init__(self, parent, title, description, icon_text, command):
        super().__init__(parent, bg="#ffffff", bd=1, relief="ridge", cursor="hand2")
        self.command = command

        self.bind("<Button-1>", self.on_click)
        self.bind("<Enter>", self.on_hover)
        self.bind("<Leave>", self.on_leave)

        lbl_icon = tk.Label(self, text=icon_text, font=("Segoe UI", 36), bg="#ffffff", fg="#0078D7")
        lbl_icon.pack(pady=(25, 10))
        lbl_icon.bind("<Button-1>", self.on_click)

        lbl_title = tk.Label(self, text=title, font=("Segoe UI", 14, "bold"), bg="#ffffff", fg="#333333", wraplength=220, justify="center")
        lbl_title.pack(pady=(0, 10), padx=10)
        lbl_title.bind("<Button-1>", self.on_click)

        lbl_desc = tk.Label(self, text=description, font=("Segoe UI", 10), bg="#ffffff", fg="#666666", wraplength=220, justify="center")
        lbl_desc.pack(pady=(0, 25), padx=15, fill="both", expand=True)
        lbl_desc.bind("<Button-1>", self.on_click)

        self.widgets = [lbl_icon, lbl_title, lbl_desc]

    def on_hover(self, event):
        self.config(bg="#f0f8ff")
        for w in self.widgets:
            w.config(bg="#f0f8ff")

    def on_leave(self, event):
        self.config(bg="#ffffff")
        for w in self.widgets:
            w.config(bg="#ffffff")

    def on_click(self, event):
        self.command()

class CentralHomepage(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MDM - Central Application Dashboard")
        self.geometry("900x500")
        self.minsize(850, 450)
        self.configure(bg="#f5f5f5")

        style = ttk.Style(self)
        available = style.theme_names()
        for preferred in ("clam", "alt", "default"):
            if preferred in available:
                style.theme_use(preferred)
                break
        try:
            apply_theme(self)
        except Exception:
            pass

        self._build_ui()

    def _build_ui(self):
        header_frame = tk.Frame(self, bg="#f5f5f5")
        header_frame.pack(fill="x", pady=(40, 30))

        title_lbl = tk.Label(
            header_frame,
            text="Welcome to the Unified MDM Workspace",
            font=("Segoe UI", 22, "bold"),
            bg="#f5f5f5",
            fg="#222222"
        )
        title_lbl.pack()

        desc_lbl = tk.Label(
            header_frame,
            text="Select a module below to open its dedicated interface.",
            font=("Segoe UI", 12),
            bg="#f5f5f5",
            fg="#555555"
        )
        desc_lbl.pack(pady=(5, 0))

        cards_frame = tk.Frame(self, bg="#f5f5f5")
        cards_frame.pack(fill="both", expand=True, padx=40)

        
        card_eu = AppCard(
            cards_frame,
            title="EU Deduplication & Remap",
            description="End-user deduplication tool with AI mapping, fuzzy matching, and bulk Excel imports.",
            icon_text="👥",
            command=self.open_eu_app
        )
        card_eu.pack(side="left", fill="both", expand=True, padx=15, pady=15)

        
        card_fuzzy = AppCard(
            cards_frame,
            title="Facility Matching (Fuzzy & GPO)",
            description="Fuzzy & GPO matching automation using AI validation and distance scoring.",
            icon_text="🏢",
            command=self.open_fuzzy_app
        )
        card_fuzzy.pack(side="left", fill="both", expand=True, padx=15, pady=15)

        
        card_project = AppCard(
            cards_frame,
            title="Data Laundry & Matching",
            description="MDM tool for laundering master datasets, standardizing names, and distance matching.",
            icon_text="🧼",
            command=self.open_project_app
        )
        card_project.pack(side="left", fill="both", expand=True, padx=15, pady=15)

    def open_eu_app(self):
        self.iconify()
        win = EUDedupWindow(self)
        win.protocol("WM_DELETE_WINDOW", lambda: self._on_sub_close(win))
        win.focus_force()

    def open_fuzzy_app(self):
        self.iconify()
        win = FuzzyAppWindow(self)
        win.protocol("WM_DELETE_WINDOW", lambda: self._on_sub_close(win))
        win.focus_force()

    def open_project_app(self):
        self.iconify()
        win = ProjectAppWindow(self)
        win.protocol("WM_DELETE_WINDOW", lambda: self._on_sub_close(win))
        win.focus_force()

    def _on_sub_close(self, win):
        win.destroy()
        self.deiconify()

def main():
    app = CentralHomepage()
    app.mainloop()

if __name__ == "__main__":
    main()
