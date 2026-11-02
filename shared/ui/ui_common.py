"""
ui_common.py
Reusable Tkinter widgets and small helper functions shared across tabs.
"""
import os
import re
import sys
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox



def _tokenize(col_name):
    """Split a column name into lowercase word tokens (handles _, -, spaces,
    camelCase) so we can match whole words instead of raw substrings."""
    spaced = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", col_name)
    return [t for t in re.split(r"[^a-zA-Z0-9]+", spaced.lower()) if t]

def guess_column(columns, exact_candidates=None, contains_tokens=None, fallback=None, required=True, reverse=False):
    """Try to auto-detect a column by exact name match first, then a
    whole-word token match, then (as a last resort) a raw substring match.

    columns: list of available column names (strings)
    exact_candidates: list of exact names to try (case-insensitive), in priority order
    contains_tokens: list of words to look for within column names
    fallback: value to return if nothing matches; only used if it is
        actually one of `columns` (a fallback that isn't a real column would
        silently point at the wrong data, e.g. mis-flagging coordinates as
        missing), otherwise the first column is used.
    required: when True (default, matches existing behavior everywhere this
        was already called), a genuinely optional field that finds no match
        falls back to the first column. Set False for optional fields (e.g.
        an optional Lat/Lon mapping) where guessing an unrelated column
        would be actively misleading — those return `fallback` (often None)
        untouched instead.
    reverse: if True, search columns from right to left instead of left to right.
    """
    if not columns:
        return fallback

    lower_map = {c.lower(): c for c in columns}

    if exact_candidates:
        for cand in exact_candidates:
            if cand.lower() in lower_map:
                return lower_map[cand.lower()]

    cols_to_search = list(reversed(columns)) if reverse else columns

    if contains_tokens:
        toks = [t.lower() for t in contains_tokens]

        
        
        
        for c in cols_to_search:
            words = _tokenize(c)
            for tok in toks:
                if tok in words or any(w.startswith(tok) and len(tok) >= 3 for w in words):
                    return c

        
        
        for c in cols_to_search:
            cl = c.lower()
            for tok in toks:
                if tok in cl:
                    return c

    if fallback is not None and (fallback in columns or fallback == ""):
        return fallback
    if not required:
        return fallback
    return columns[0]




PALETTE = {
    "bg": "#f4f6fb",
    "panel": "#ffffff",
    "primary": "#4338ca",
    "primary_dark": "#3730a3",
    "accent": "#0891b2",
    "success": "#047857",
    "warning": "#b45309",
    "danger": "#b91c1c",
    "text": "#1a202c",
    "navy": "#0b1f66",
    "muted": "#6b7280",
    "border": "#dfe3ee",
}

def apply_theme(root):
    """Apply a colorful, consistent ttk theme across the whole app.

    IMPORTANT: this only reliably works on the 'clam' base theme. Windows'
    native 'vista'/'xpnative' ttk themes draw buttons and notebook tabs
    with the OS theme engine and silently ignore most custom
    background/foreground colors set via style.configure() — that mismatch
    (e.g. a white foreground forced onto a native light background) is what
    made tab/button text unreadable. main.py selects 'clam' before calling
    this so every color below actually renders.
    """
    style = ttk.Style(root)
    root.configure(bg=PALETTE["bg"])

    style.configure("TFrame", background=PALETTE["bg"])
    style.configure("TLabel", background=PALETTE["bg"], foreground=PALETTE["text"])
    style.configure(
        "TLabelframe", background=PALETTE["bg"], relief="solid", borderwidth=1,
        bordercolor="#d8dce8",
    )
    style.configure(
        "TLabelframe.Label", background=PALETTE["bg"], foreground=PALETTE["primary_dark"],
        font=("Segoe UI", 10, "bold"),
    )
    style.configure(
        "TNotebook", background=PALETTE["bg"], tabmargins=(6, 6, 6, 0),
    )
    style.configure(
        "TNotebook.Tab", padding=(14, 8), font=("Segoe UI", 10, "bold"),
        background="#e0e4f5", foreground=PALETTE["navy"],
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", PALETTE["primary"])],
        foreground=[("selected", "white"), ("!selected", PALETTE["navy"])],
    )

    
    
    style.configure(
        "TButton", padding=6, font=("Segoe UI", 9),
        background="#eef0fa", foreground=PALETTE["navy"],
    )
    style.map(
        "TButton",
        background=[("active", "#dfe3f7"), ("disabled", "#eef0fa")],
        foreground=[("disabled", "#9aa0b4"), ("!disabled", PALETTE["navy"])],
    )

    style.configure(
        "Primary.TButton", background=PALETTE["primary"], foreground="white",
        font=("Segoe UI", 9, "bold"), padding=8,
    )
    style.map("Primary.TButton",
              background=[("active", PALETTE["primary_dark"]), ("disabled", "#a5a6c9")],
              foreground=[("disabled", "#e5e7f5"), ("!disabled", "white")])
    style.configure(
        "Accent.TButton", background=PALETTE["accent"], foreground="white",
        font=("Segoe UI", 9, "bold"), padding=8,
    )
    style.map("Accent.TButton",
              background=[("active", "#0e7490"), ("disabled", "#9cc7cf")],
              foreground=[("disabled", "#eaf7f9"), ("!disabled", "white")])
    style.configure(
        "Danger.TButton", background=PALETTE["danger"], foreground="white",
        font=("Segoe UI", 9, "bold"), padding=6,
    )
    style.map("Danger.TButton",
              background=[("active", "#991b1b"), ("disabled", "#dda3a3")],
              foreground=[("disabled", "#fbeaea"), ("!disabled", "white")])
    style.configure(
        "Success.TButton", background=PALETTE["success"], foreground="white",
        font=("Segoe UI", 9, "bold"), padding=6,
    )
    style.map("Success.TButton",
              background=[("active", "#065f46"), ("disabled", "#9fcabd")],
              foreground=[("disabled", "#eafaf3"), ("!disabled", "white")])

    style.configure("TProgressbar", background=PALETTE["accent"], troughcolor="#e5e9f5")
    return style




def browse_excel(title="Select Data File"):
    path = filedialog.askopenfilename(
        title=title,
        filetypes=[("Excel files", "*.xlsx *.xls"), ("CSV files", "*.csv"), ("All files", "*.*")],
    )
    return path or None

def browse_folder(title="Select Folder"):
    path = filedialog.askdirectory(title=title)
    return path or None

def open_path(path):
    """Open a file or folder with the OS default application."""
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception as e:
        messagebox.showerror("Could not open", f"Couldn't open:\n{path}\n\n{e}")

def show_error(title, err):
    messagebox.showerror(title, str(err))

def show_info(title, msg):
    messagebox.showinfo(title, msg)

def save_dataframe(df, default_path):
    """
    Saves a DataFrame to default_path. 
    If the DataFrame has more than 1,048,575 rows, it automatically changes the 
    extension to .csv and saves as a CSV to bypass Excel's limit.
    Returns the actual path saved to.
    """
    path = str(default_path)
    if len(df) > 1048575:
        if path.lower().endswith('.xlsx') or path.lower().endswith('.xls'):
            path = path.rsplit('.', 1)[0] + '.csv'
        elif not path.lower().endswith('.csv'):
            path += '.csv'
            
    if path.lower().endswith('.csv'):
        df.to_csv(path, index=False)
    else:
        df.to_excel(path, index=False)
        
    return path




class LabeledCombo(ttk.Frame):
    """A label + combobox pair, used for column selection dropdowns.

    editable=True allows the user to type a value not in the list (useful
    for e.g. naming a brand-new Latitude/Longitude column).
    """

    def __init__(self, parent, label, values=None, width=32, editable=False, **kwargs):
        super().__init__(parent)
        self.editable = editable
        self.label = ttk.Label(self, text=label, width=22, anchor="w")
        self.label.grid(row=0, column=0, sticky="w", padx=(0, 6), pady=3)
        self.var = tk.StringVar()
        state = "normal" if editable else "readonly"
        self.combo = ttk.Combobox(self, textvariable=self.var, width=width, state=state)
        self.combo.grid(row=0, column=1, sticky="w", pady=3)
        if values:
            self.set_values(values)

    def set_values(self, values, default=None):
        self.combo["values"] = values
        
        
        
        
        
        
        if default is not None and (default in values or self.editable):
            self.var.set(default)
        elif default is None and self.editable:
            self.var.set("")
        elif values:
            self.var.set(values[0])
        else:
            self.var.set("")

    def get(self):
        v = self.var.get().strip()
        if v.startswith("Create '") and v.endswith("'"):
            return v[8:-1]
        return v if v else None

    def set(self, value):
        self.var.set(value or "")

class LabeledEntry(ttk.Frame):
    """A label + text entry pair."""

    def __init__(self, parent, label, default="", width=20, **kwargs):
        super().__init__(parent)
        self.label = ttk.Label(self, text=label, width=22, anchor="w")
        self.label.grid(row=0, column=0, sticky="w", padx=(0, 6), pady=3)
        self.var = tk.StringVar(value=default)
        self.entry = ttk.Entry(self, textvariable=self.var, width=width)
        self.entry.grid(row=0, column=1, sticky="w", pady=3)

    def get(self):
        return self.var.get().strip()

    def set(self, value):
        self.var.set(value)

class FilePicker(ttk.Frame):
    """A label + path display + Browse button for choosing an Excel file."""

    def __init__(self, parent, label, on_pick=None, **kwargs):
        super().__init__(parent)
        self.on_pick = on_pick
        self.path = None
        ttk.Label(self, text=label, width=22, anchor="w").grid(row=0, column=0, sticky="w")
        self.path_var = tk.StringVar(value="(no file selected)")
        self.path_label = ttk.Label(self, textvariable=self.path_var, foreground="#555",
                                     width=48, anchor="w")
        self.path_label.grid(row=0, column=1, sticky="w", padx=(6, 6))
        ttk.Button(self, text="Browse...", command=self._browse).grid(row=0, column=2)

    def _browse(self):
        p = browse_excel()
        if p:
            self.path = p
            display = p if len(p) < 60 else "..." + p[-57:]
            self.path_var.set(display)
            if self.on_pick:
                self.on_pick(p)

    def get(self):
        return self.path

class ScrollableFrame(ttk.Frame):
    """A vertically-scrollable frame; put widgets inside `.body`."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        canvas = tk.Canvas(self, highlightthickness=0)
        vscroll = ttk.Scrollbar(self, orient="vertical", command=canvas.yview)
        self.body = ttk.Frame(canvas)

        self.body.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas_window = canvas.create_window((0, 0), window=self.body, anchor="nw")
        canvas.configure(yscrollcommand=vscroll.set)

        def _resize_body(event):
            canvas.itemconfig(canvas_window, width=event.width)

        canvas.bind("<Configure>", _resize_body)

        canvas.pack(side="left", fill="both", expand=True)
        vscroll.pack(side="right", fill="y")

        
        
        
        
        def _on_mousewheel(event):
            canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        def _bind_wheel(_event):
            canvas.bind_all("<MouseWheel>", _on_mousewheel)

        def _unbind_wheel(_event):
            canvas.unbind_all("<MouseWheel>")

        canvas.bind("<Enter>", _bind_wheel)
        canvas.bind("<Leave>", _unbind_wheel)

class StatCard(ttk.Frame):
    """A small colored summary card showing a number + label (used for stats rows)."""

    def __init__(self, parent, title, value="0", color="#1a202c", **kwargs):
        super().__init__(parent, relief="solid", borderwidth=1, padding=10)
        self.value_var = tk.StringVar(value=str(value))
        self.value_label = tk.Label(self, textvariable=self.value_var,
                                     font=("Segoe UI", 20, "bold"), fg=color)
        self.value_label.pack()
        tk.Label(self, text=title, font=("Segoe UI", 9), fg="#555").pack()

    def set(self, value):
        self.value_var.set(str(value))

class Section(ttk.LabelFrame):
    """A titled section container with consistent padding."""

    def __init__(self, parent, title, **kwargs):
        super().__init__(parent, text=title, padding=12, **kwargs)

def make_treeview(parent, columns, height=8):
    """Create a Treeview + scrollbars for previewing tabular data."""
    frame = ttk.Frame(parent)
    tree = ttk.Treeview(frame, columns=columns, show="headings", height=height)
    for c in columns:
        tree.heading(c, text=c)
        tree.column(c, width=140, anchor="w")
    vsb = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
    hsb = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
    tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
    tree.grid(row=0, column=0, sticky="nsew")
    vsb.grid(row=0, column=1, sticky="ns")
    hsb.grid(row=1, column=0, sticky="ew")
    frame.grid_rowconfigure(0, weight=1)
    frame.grid_columnconfigure(0, weight=1)
    return frame, tree

def rebuild_treeview_columns(tree, columns):
    """Reset a Treeview's columns entirely (used when the preview schema changes)."""
    tree.delete(*tree.get_children())
    tree["columns"] = columns
    for c in columns:
        tree.heading(c, text=c)
        tree.column(c, width=130, anchor="w")

def fill_treeview(tree, df, max_rows=200):
    tree.delete(*tree.get_children())
    if df is None or df.empty:
        return
    cols = list(tree["columns"])
    for _, row in df.head(max_rows).iterrows():
        values = [row.get(c, "") for c in cols]
        tree.insert("", "end", values=values)
