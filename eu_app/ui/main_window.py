from __future__ import annotations

import os
import queue
import threading
import traceback
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog
import tkinter as tk
from tkinter import ttk

from shared.config import APP_CONFIG
from eu_app.services import ai_same, excel_io, sp_client
from shared.services import workspace
from eu_app.services.dedup import (
    ai_candidates, build_groups, build_records, display, pair_key, pick_group,
    run_fuzzy,
)
from shared.services.database import create_database_engine

CHECK_ON, CHECK_OFF = "\u2611", "\u2610"


def _run_bg(fn, *a, **kw):
    t = threading.Thread(target=fn, args=a, kwargs=kw, daemon=True)
    t.start()
    return t

class LogPanel(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent)
        self.progress = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.progress.pack(fill="x", padx=4, pady=(4, 2))
        self.text = tk.Text(self, height=6, state="disabled", wrap="word", font=("Consolas", 9))
        self.text.pack(fill="both", expand=True, padx=4, pady=(0, 4))

    def log(self, msg: str):
        self.text.config(state="normal")
        self.text.insert("end", msg + "\n")
        self.text.see("end")
        self.text.config(state="disabled")

    def set_progress(self, frac: float):
        self.progress["value"] = max(0.0, min(1.0, frac)) * 100

class ScrollableFrame(ttk.Frame):
    def __init__(self, container, *args, **kwargs):
        super().__init__(container, *args, **kwargs)
        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        self.scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.content = ttk.Frame(self.canvas)

        self.content.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )
        self.canvas_frame = self.canvas.create_window((0, 0), window=self.content, anchor="nw")

        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfig(self.canvas_frame, width=e.width)
        )

        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.content.bind("<Enter>", self._bind_mousewheel)
        self.content.bind("<Leave>", self._unbind_mousewheel)

    def _bind_mousewheel(self, event):
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)

    def _unbind_mousewheel(self, event):
        self.canvas.unbind_all("<MouseWheel>")

    def _on_mousewheel(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")


class LoadFrame(ttk.Frame):
    def __init__(self, parent, on_ready):
        super().__init__(parent, padding=12)
        self.on_ready = on_ready
        self.path_var = tk.StringVar(value="No file selected.")
        self.sheet_var = tk.StringVar()
        self.map_vars: dict[str, tk.StringVar] = {}
        self.df = None
        self._build()

    def _build(self):
        top = ttk.Frame(self)
        top.pack(fill="x")
        ttk.Button(top, text="Browse Data File…", command=self._browse).pack(side="left")
        ttk.Label(top, textvariable=self.path_var, foreground="gray").pack(side="left", padx=10)

        sheet_row = ttk.Frame(self)
        sheet_row.pack(fill="x", pady=(8, 0))
        ttk.Label(sheet_row, text="Sheet:").pack(side="left")
        self.sheet_combo = ttk.Combobox(sheet_row, textvariable=self.sheet_var, state="readonly", width=30)
        self.sheet_combo.pack(side="left", padx=6)
        ttk.Button(sheet_row, text="Load Sheet", command=self._load_sheet).pack(side="left", padx=6)

        ttk.Separator(self).pack(fill="x", pady=10)
        ttk.Label(self, text="Column Mapping", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Label(
            self, foreground="gray",
            text="Auto-detected from your file — change any dropdown if it guessed wrong.",
        ).pack(anchor="w", pady=(0, 6))

        self.map_frame = ttk.Frame(self)
        self.map_frame.pack(fill="x")

        self.info_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.info_var, foreground="#1a7a1a").pack(anchor="w", pady=(8, 0))

        ttk.Button(self, text="Confirm Mapping & Continue \u2192", command=self._confirm).pack(
            anchor="e", pady=(12, 0)
        )

    def _browse(self):
        path = filedialog.askopenfilename(
            title="Select Data file", filetypes=[("Excel files", "*.xlsx *.xlsm *.xls"), ("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if not path:
            return
        try:
            names = excel_io.sheet_names(path)
        except Exception as exc:
            messagebox.showerror("Load Error", f"Could not open file:\n{exc}")
            return
        self.path = path
        self.path_var.set(Path(path).name)
        self.sheet_combo["values"] = names
        self.sheet_var.set(names[0])
        self._load_sheet()

    def _load_sheet(self):
        if not getattr(self, "path", None):
            return
        try:
            self.df = excel_io.read_excel(self.path, self.sheet_var.get())
        except Exception as exc:
            messagebox.showerror("Load Error", f"Could not read sheet:\n{exc}")
            return
        self._build_mapping_ui()
        self.info_var.set(f"Loaded {len(self.df):,} rows, {len(self.df.columns)} columns.")

    def _build_mapping_ui(self):
        for w in self.map_frame.winfo_children():
            w.destroy()
        self.map_vars.clear()
        guess = excel_io.guess_mapping(self.df.columns)
        options = [excel_io.NONE] + [str(c) for c in self.df.columns]

        
        half = (len(excel_io.FIELDS) + 1) // 2

        for i, (key, label, required, _aliases) in enumerate(excel_io.FIELDS):
            star = " *" if required else ""

            row = i % half
            col_offset = (i // half) * 2

            ttk.Label(self.map_frame, text=label + star).grid(row=row, column=col_offset, sticky="w", padx=(0, 8) if col_offset == 0 else (24, 8), pady=3)
            var = tk.StringVar(value=guess.get(key) or excel_io.NONE)
            combo = ttk.Combobox(self.map_frame, textvariable=var, values=options, state="readonly", width=30)
            combo.grid(row=row, column=col_offset + 1, sticky="w", pady=3)
            self.map_vars[key] = var

    def _confirm(self):
        if self.df is None:
            messagebox.showwarning("No file", "Load a data file first.")
            return
        mapping = {}
        missing = []
        for key, label, required, _aliases in excel_io.FIELDS:
            val = self.map_vars[key].get()
            mapping[key] = "" if val == excel_io.NONE else val
            if required and not mapping[key]:
                missing.append(label)
        if missing:
            messagebox.showerror("Mapping incomplete", "Please map these required columns:\n" + "\n".join(missing))
            return
        self.on_ready(self.df.reset_index(drop=True), mapping)


class ReviewFrame(ttk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, padding=8)
        self.app = app
        self.page = 0
        self.filter_case = tk.StringVar()
        self.only_matches = tk.BooleanVar(value=False)
        self._build()

    
    def _build(self):
        ctrl = ttk.LabelFrame(self, text="Run", padding=8)
        ctrl.pack(fill="x")

        r1 = ttk.Frame(ctrl)
        r1.pack(fill="x")
        ttk.Label(r1, text="Fuzzy threshold %").pack(side="left")
        self.thr_var = tk.IntVar(value=APP_CONFIG.default_fuzzy_threshold)
        ttk.Spinbox(r1, from_=50, to=100, textvariable=self.thr_var, width=5).pack(side="left", padx=(4, 16))
        ttk.Label(r1, text="AI min confidence %").pack(side="left")
        self.ai_conf_var = tk.IntVar(value=APP_CONFIG.default_ai_min_confidence)
        ttk.Spinbox(r1, from_=0, to=100, textvariable=self.ai_conf_var, width=5).pack(side="left", padx=(4, 16))
        ttk.Label(r1, text="USR_ID (for SQL)").pack(side="left")
        self.usr_var = tk.StringVar()
        ttk.Entry(r1, textvariable=self.usr_var, width=10).pack(side="left", padx=4)

        r2 = ttk.Frame(ctrl)
        r2.pack(fill="x", pady=(6, 0))
        ttk.Button(r2, text="1) Run Fuzzy Match", command=self.run_fuzzy).pack(side="left", padx=(0, 6))
        ttk.Button(r2, text="2) Run AI Check (remaining pairs)", command=self.run_ai).pack(side="left", padx=6)
        ttk.Button(r2, text="3) Compute Suggestions (DB lookup)", command=self.compute_suggestions).pack(
            side="left", padx=6
        )
        ttk.Button(r2, text="Run All 1\u21922\u21923", command=self.run_all).pack(side="left", padx=6)

        r3 = ttk.Frame(ctrl)
        r3.pack(fill="x", pady=(6, 0))
        ttk.Button(r3, text="Export Full Results", command=self.export_all).pack(side="left", padx=(0, 6))
        ttk.Button(r3, text="Export Excluded Only", command=self.export_excluded).pack(side="left", padx=6)
        ttk.Button(r3, text="Execute ALL pending SQL \u2192 Database", command=self.execute_all_sql).pack(
            side="right", padx=(6, 0)
        )

        self.log = LogPanel(ctrl)
        self.log.pack(fill="x", pady=(8, 0))

        filt = ttk.Frame(self)
        filt.pack(fill="x", pady=(8, 4))
        ttk.Label(filt, text="Filter CASE_ID:").pack(side="left")
        e = ttk.Entry(filt, textvariable=self.filter_case, width=12)
        e.pack(side="left", padx=4)
        e.bind("<Return>", lambda ev: self.refresh())
        ttk.Checkbutton(filt, text="Only rows with a match", variable=self.only_matches, command=self.refresh).pack(
            side="left", padx=10
        )
        ttk.Button(filt, text="Apply", command=self.refresh).pack(side="left")
        self.count_var = tk.StringVar(value="")
        ttk.Label(filt, textvariable=self.count_var, foreground="gray").pack(side="right")

        table_wrap = ttk.Frame(self)
        table_wrap.pack(fill="both", expand=True)
        self.columns = [
            "ROW", "CASE_ID", "ENDUSER_ID", "ENDUSER_NAME", "ENDUSER_ADDR1", "ENDUSER_CITY",
            "ENDUSER_STATE", "ENDUSER_ZIP", "MATCH", "CORR_EU_ID", "REASON", "SQL_STATUS", "EXCLUDE",
        ]
        self.tree = ttk.Treeview(table_wrap, columns=self.columns, show="headings", height=20)
        widths = {
            "ROW": 50, "CASE_ID": 70, "ENDUSER_ID": 90, "ENDUSER_NAME": 220, "ENDUSER_ADDR1": 160,
            "ENDUSER_CITY": 90, "ENDUSER_STATE": 50, "ENDUSER_ZIP": 60, "MATCH": 90, "CORR_EU_ID": 90,
            "REASON": 260, "SQL_STATUS": 90, "EXCLUDE": 70,
        }
        for c in self.columns:
            self.tree.heading(c, text=c)
            self.tree.column(c, width=widths.get(c, 100), anchor="w")
        vsb = ttk.Scrollbar(table_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<Button-1>", self._on_click)
        self.tree.bind("<Double-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)

        pager = ttk.Frame(self)
        pager.pack(fill="x", pady=(4, 0))
        self.btn_prev = ttk.Button(pager, text="\u25c0 Prev", command=self._prev_page)
        self.btn_prev.pack(side="left")
        self.page_var = tk.StringVar(value="Page 1")
        ttk.Label(pager, textvariable=self.page_var).pack(side="left", padx=10)
        self.btn_next = ttk.Button(pager, text="Next \u25b6", command=self._next_page)
        self.btn_next.pack(side="left")

    
    def _filtered_indices(self):
        s = self.app
        idxs = list(range(len(s.df))) if s.df is not None else []
        cf = self.filter_case.get().strip()
        if cf:
            idxs = [i for i in idxs if s.recs[i].case == cf]
        if self.only_matches.get():
            idxs = [i for i in idxs if s.match_label[i] or s.corr[i]]
        return idxs

    def refresh(self):
        s = self.app
        if s.df is None:
            return
        idxs = self._filtered_indices()
        self.count_var.set(f"{len(idxs):,} rows match filter")
        per = APP_CONFIG.page_size
        max_page = max(0, (len(idxs) - 1) // per)
        self.page = min(self.page, max_page)
        page_idxs = idxs[self.page * per: self.page * per + per]
        self.page_var.set(f"Page {self.page + 1} of {max_page + 1}")

        self.btn_prev.state(["!disabled"] if self.page > 0 else ["disabled"])
        self.btn_next.state(["!disabled"] if self.page < max_page else ["disabled"])

        self.tree.delete(*self.tree.get_children())
        for i in page_idxs:
            r = s.recs[i]
            vals = [
                i + 2, r.case, r.eid, r.name, r.addr, r.city, r.state, r.zip,
                s.match_label[i], s.corr[i], s.reason[i], s.sql_status[i],
                CHECK_ON if s.excluded[i] else CHECK_OFF,
            ]
            tag = "excluded" if s.excluded[i] else ("matched" if s.corr[i] else "")
            self.tree.insert("", "end", iid=str(i), values=vals, tags=(tag,))
        self.tree.tag_configure("matched", background="#eaf7ea")
        self.tree.tag_configure("excluded", background="#f2f2f2", foreground="#999999")

    def _prev_page(self):
        if self.page > 0:
            self.page -= 1
            self.refresh()

    def _next_page(self):
        self.page += 1
        self.refresh()

    
    def _row_at(self, event):
        iid = self.tree.identify_row(event.y)
        col = self.tree.identify_column(event.x)
        return iid, col

    def _on_click(self, event):
        iid, col = self._row_at(event)
        if not iid:
            return
        colname = self.columns[int(col.replace("#", "")) - 1] if col else ""
        if colname == "EXCLUDE":
            i = int(iid)
            self.app.excluded[i] = not self.app.excluded[i]
            self.refresh()

    def _on_double_click(self, event):
        iid, _col = self._row_at(event)
        if iid:
            self._show_detail(int(iid))

    def _on_right_click(self, event):
        iid, _col = self._row_at(event)
        if not iid:
            return
        self.tree.selection_set(iid)
        i = int(iid)
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="View details / reason", command=lambda: self._show_detail(i))
        menu.add_command(
            label=("Un-exclude row" if self.app.excluded[i] else "Exclude row"),
            command=lambda: self._toggle_exclude(i),
        )
        if self.app.corr[i]:
            menu.add_separator()
            menu.add_command(label="Run this row's SQL now", command=lambda: self.execute_one_sql(i))
        menu.tk_popup(event.x_root, event.y_root)

    def _toggle_exclude(self, i):
        self.app.excluded[i] = not self.app.excluded[i]
        self.refresh()

    def _show_detail(self, i):
        s = self.app
        r = s.recs[i]
        lines = [
            f"Row {i + 2}  |  CASE_ID {r.case}  |  ENDUSER_ID {r.eid}",
            f"Name:  {r.name}",
            f"Addr:  {r.addr}, {r.city}, {r.state} {r.zip}",
            "",
            f"MATCH:       {s.match_label[i] or '(none)'}",
            f"CORR_EU_ID:  {s.corr[i] or '(none)'}",
            f"Reason:      {s.reason[i] or ''}",
            f"SQL status:  {s.sql_status[i] or ''}",
        ]
        if s.sql_text[i]:
            lines += ["", "SQL:", s.sql_text[i]]
        if r.case in s.recs_by_case():
            lines += ["", "Other rows in the same CASE_ID:"]
            for other in s.recs_by_case()[r.case]:
                if other.idx != i:
                    lines.append(f"  - {other.eid}: {other.name} | {other.addr}, {other.city}, {other.state} {other.zip}")
        messagebox.showinfo(f"Row {i + 2} details", "\n".join(lines), parent=self)

    
    def _guard(self):
        s = self.app
        if s.df is None:
            messagebox.showwarning("No data", "Load a file first.")
            return False
        return True

    def run_fuzzy(self):
        if not self._guard():
            return
        s = self.app
        thr = self.thr_var.get()
        self.log.log(f"Running fuzzy match (threshold {thr}%)…")

        def work():
            pairs = run_fuzzy(s.recs, thr, progress=lambda f: s.after0(self.log.set_progress, f))
            s.pairs = pairs
            groups, labels = build_groups(pairs, self.ai_conf_var.get())
            for i, lbl in labels.items():
                s.match_label[i] = lbl
            n_same = sum(1 for p in pairs.values() if p.fuzzy_same)
            n_cases = len({s.recs[i].case for g in groups for i in g})

            def done():
                self.log.log(f"Fuzzy done: {n_same} pair(s) matched directly, {len(groups)} group(s) so far "
                              f"across {n_cases} case(s). Remaining ambiguous pairs are ready for AI.")
                self.log.set_progress(1.0)
                self.refresh()

            s.after0(done)

        _run_bg(work)

    def run_ai(self):
        if not self._guard():
            return
        s = self.app
        if s.pairs is None:
            messagebox.showwarning("Run fuzzy first", "Run step 1 (fuzzy match) before the AI check.")
            return
        cands = ai_candidates(s.pairs)
        if not cands:
            self.log.log("No pairs need AI review — fuzzy already resolved every case, or nothing is ambiguous.")
            return

        cache_path = workspace.cache_dir() / "ai_same_cache.csv"
        cache = ai_same.load_cache(cache_path)
        items, key_of = [], {}
        for p in cands:
            a, b = s.recs[p.i], s.recs[p.j]
            k = pair_key(a, b)
            key_of[(p.i, p.j)] = k
            if k in cache:
                p.ai_result = cache[k]["result"]
                p.ai_conf = cache[k]["confidence"]
                p.ai_reason = cache[k]["reason"]
            else:
                items.append({
                    "key": k, "n1": a.name, "a1": a.addr, "c1": a.city, "s1": a.state,
                    "n2": b.name, "a2": b.addr, "c2": b.city, "s2": b.state,
                })

        self.log.log(f"{len(cands)} pair(s) need an AI check ({len(cands) - len(items)} already cached).")
        if not items:
            self._apply_ai_results(cands, key_of, {})
            return

        def log_cb(m):
            s.after0(self.log.log, m)

        def prog_cb(f):
            s.after0(self.log.set_progress, f)

        def on_batch(chunk):
            ai_same.append_cache(cache_path, chunk)

        def work():
            try:
                ai_same.ensure_available()
            except ai_same.AISameUnavailable as exc:
                s.after0(messagebox.showerror, "AI unavailable", str(exc))
                return
            try:
                results = ai_same.classify_pairs(items, log=log_cb, progress=prog_cb, on_batch_done=on_batch)
            except Exception as exc:
                s.after0(messagebox.showerror, "AI error", f"{exc}\n\n{traceback.format_exc()[-1500:]}")
                return
            s.after0(self._apply_ai_results, cands, key_of, results)

        _run_bg(work)

    def _apply_ai_results(self, cands, key_of, fresh_results):
        s = self.app
        same_ct = diff_ct = err_ct = 0
        for p in cands:
            k = key_of[(p.i, p.j)]
            r = fresh_results.get(k)
            if r:
                p.ai_result, p.ai_conf, p.ai_reason = r["result"], r["confidence"], r["reason"]
            if p.ai_result == "SAME":
                same_ct += 1
            elif p.ai_result == "DIFFERENT":
                diff_ct += 1
            else:
                err_ct += 1
        
        groups, labels = build_groups(s.pairs, self.ai_conf_var.get())
        
        for p in s.pairs.values():
            if p.ai_result == "DIFFERENT":
                if p.i not in labels:
                    labels[p.i] = "DIFFERENT"
                if p.j not in labels:
                    labels[p.j] = "DIFFERENT"
                    
        for i in range(len(s.match_label)):
            s.match_label[i] = labels.get(i, "")
            
        if self.only_matches.get():
            self.refresh()
        else:
            self.refresh()
        self.log.log(f"AI done: {same_ct} SAME, {diff_ct} DIFFERENT, {err_ct} ERROR.")
        self.app.after0(self.compute_suggestions)

    def compute_suggestions(self):
        if not self._guard():
            return
        s = self.app
        if s.pairs is None:
            messagebox.showwarning("Run fuzzy first", "Run step 1 (and ideally step 2) before computing suggestions.")
            return
        if s.engine is None:
            messagebox.showwarning("Not connected", "No database connection is available for the lookup SP.")
            return
        groups, labels = build_groups(s.pairs, self.ai_conf_var.get())
        for i, lbl in labels.items():
            s.match_label[i] = lbl
        if not groups:
            self.log.log("No confirmed SAME groups yet — nothing to compute.")
            return

        from eu_app.services.dedup import split_weak_groups
        groups, split_logs, dropped_nodes = split_weak_groups(s.pairs, groups, s.recs, self.ai_conf_var.get())
        for lg in split_logs:
            self.log.log(lg)

        for i in dropped_nodes:
            s.reason[i] = "ambiguous — needs manual review"

        self.log.log(f"Computing suggestions for {len(groups)} group(s)…")

        def work():
            done_groups = 0
            for g in groups:
                members = [s.recs[i] for i in g]
                try:
                    winner, why = pick_group(members)
                except Exception as exc:
                    s.after0(self.log.log, f"  Group {[m.eid for m in members]} failed: {exc}")
                    continue
                for i in g:
                    s.corr[i] = winner.eid
                    s.reason[i] = why[i] if isinstance(why, dict) else why
                    
                    if i != winner.idx:
                        mem = s.recs[i]
                        t_diff = mem.terr_id != winner.terr_id
                        s_diff = mem.sales_type != winner.sales_type
                        
                        if t_diff and s_diff:
                            s.difference_status[i] = "Sales Type and Territory ID Different"
                        elif t_diff:
                            s.difference_status[i] = "Territory ID Different"
                        elif s_diff:
                            s.difference_status[i] = "Sales Type Different"
                        else:
                            s.difference_status[i] = ""
                    else:
                        s.difference_status[i] = ""
                        
                for i in g:
                    s.group_id[i] = done_groups + 1
                done_groups += 1

                if done_groups % 10 == 0:
                    s.after0(self.log.set_progress, done_groups / len(groups))

            def finish():
                self.log.log(f"Suggestions ready for {done_groups} group(s).")
                self.log.set_progress(1.0)
                self.refresh()

            s.after0(finish)

        _run_bg(work)

    def run_all(self):
        if not self._guard():
            return
        self.run_fuzzy()
        self.app.after0_delayed(400, self.run_ai)
        self.app.after0_delayed(1200, self.compute_suggestions)

    
    def _sql_for_row(self, i):
        s = self.app
        r = s.recs[i]
        if not s.corr[i] or s.corr[i] == r.eid:
            return ""
        from eu_app.services.dedup import build_sql_text
        return build_sql_text(r.eid, s.corr[i], self.usr_var.get().strip() or "?")

    def execute_one_sql(self, i):
        s = self.app
        if s.engine is None:
            messagebox.showwarning("Not connected", "No database connection available.")
            return
        if not self.usr_var.get().strip():
            messagebox.showwarning("USR_ID required", "Enter a USR_ID before running SQL.")
            return
        r = s.recs[i]
        if not s.corr[i] or s.corr[i] == r.eid or s.excluded[i]:
            messagebox.showinfo("Nothing to run", "This row has no pending remap.")
            return
        if not messagebox.askyesno(
            "Confirm", f"Remap ENDUSER_ID {r.eid} \u2192 {s.corr[i]} for CASE_ID {r.case}?"
        ):
            return
        try:
            sp_client.run_remap(s.engine, r.eid, s.corr[i], self.usr_var.get().strip())
            s.sql_status[i] = "DONE"
            self.log.log(f"Row {i + 2}: remapped {r.eid} -> {s.corr[i]} OK.")
        except Exception as exc:
            s.sql_status[i] = f"ERROR: {exc}"
            self.log.log(f"Row {i + 2}: FAILED ({exc}).")
        self.refresh()

    def execute_all_sql(self):
        s = self.app
        if s.engine is None:
            messagebox.showwarning("Not connected", "No database connection available.")
            return
        usr = self.usr_var.get().strip()
        if not usr:
            messagebox.showwarning("USR_ID required", "Enter a USR_ID before running SQL.")
            return
        targets = [
            i for i in range(len(s.df))
            if s.corr[i] and s.corr[i] != s.recs[i].eid and not s.excluded[i] and s.sql_status[i] != "DONE"
        ]
        if not targets:
            messagebox.showinfo("Nothing to run", "No pending (non-excluded) remaps to execute.")
            return
        if not messagebox.askyesno(
            "Warning",
            f"You are about to execute {len(targets)} remap SQL statement(s) directly against the database.\n\nThis action cannot be undone.\n\nAre you sure you want to proceed?",
            icon="warning"
        ):
            return
        self.log.log(f"Executing {len(targets)} remap statement(s)…")

        def work():
            ok = err = 0
            for n, i in enumerate(targets, 1):
                r = s.recs[i]
                try:
                    sp_client.run_remap(s.engine, r.eid, s.corr[i], usr)
                    s.sql_status[i] = "DONE"
                    ok += 1
                except Exception as exc:
                    s.sql_status[i] = f"ERROR: {exc}"
                    err += 1
                    s.after0(self.log.log, f"  Row {i + 2} ({r.eid}) FAILED: {exc}")
                s.after0(self.log.set_progress, n / len(targets))

            def finish():
                self.log.log(f"Done: {ok} succeeded, {err} failed.")
                self.refresh()

            s.after0(finish)

        _run_bg(work)

    
    def export_all(self):
        s = self.app
        if s.df is None:
            return
        self._sync_sql_text()
        total_rows = len(s.df)
        if total_rows > 1048575:
            messagebox.showinfo("Large Dataset", "The output dataset exceeds Excel's row limit. It will be exported as a CSV file.")
            path = filedialog.asksaveasfilename(
                initialdir=str(workspace.output_dir()), initialfile="eu_dedup_result.csv",
                defaultextension=".csv", filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            )
        else:
            path = filedialog.asksaveasfilename(
                initialdir=str(workspace.output_dir()), initialfile="eu_dedup_result.xlsx",
                defaultextension=".xlsx", filetypes=[("Excel files", "*.xlsx")],
            )
        if not path:
            return
        info = [{"match": s.match_label[i], "group": s.group_id[i], "why": s.reason[i], "status": s.sql_status[i], "difference_status": s.difference_status[i]}
                for i in range(len(s.df))]
        try:
            excel_io.export_workbook(
                Path(path), s.df, s.mapping["enduser_id"], s.corr, s.excluded,
                self.usr_var.get().strip() or "?", info, mode="all",
            )
            self.log.log(f"Exported full workbook to {path}")
            messagebox.showinfo("Exported", f"Saved:\n{path}")
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))

    def export_excluded(self):
        s = self.app
        if s.df is None:
            return
        self._sync_sql_text()
        if not any(s.excluded):
            messagebox.showinfo("Nothing excluded", "No rows are marked excluded yet.")
            return
        total_rows = sum(s.excluded)
        if total_rows > 1048575:
            messagebox.showinfo("Large Dataset", "The output dataset exceeds Excel's row limit. It will be exported as a CSV file.")
            path = filedialog.asksaveasfilename(
                initialdir=str(workspace.output_dir()), initialfile="eu_dedup_excluded.csv",
                defaultextension=".csv", filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            )
        else:
            path = filedialog.asksaveasfilename(
                initialdir=str(workspace.output_dir()), initialfile="eu_dedup_excluded.xlsx",
                defaultextension=".xlsx", filetypes=[("Excel files", "*.xlsx")],
            )
        if not path:
            return
        info = [{"match": s.match_label[i], "group": s.group_id[i], "why": s.reason[i], "status": s.sql_status[i], "difference_status": s.difference_status[i]}
                for i in range(len(s.df))]
        try:
            excel_io.export_workbook(
                Path(path), s.df, s.mapping["enduser_id"], s.corr, s.excluded,
                self.usr_var.get().strip() or "?", info, mode="excluded",
            )
            self.log.log(f"Exported excluded rows to {path}")
            messagebox.showinfo("Exported", f"Saved:\n{path}")
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))

    def _sync_sql_text(self):
        s = self.app
        for i in range(len(s.df)):
            s.sql_text[i] = self._sql_for_row(i)


class MDMApp(ttk.Frame):
    def __init__(self, root, connection_settings):
        super().__init__(root)
        self.root = root
        self.connection_settings = connection_settings
        self.engine = None
        self.df = None
        self.mapping = {}
        self.recs = []
        self.pairs = None
        self._recs_by_case_cache = None

        try:
            self.engine = create_database_engine(connection_settings, connection_settings.database or "master")
        except Exception as exc:
            messagebox.showwarning("Database", f"Could not create a database engine yet:\n{exc}")

        self.pack(fill="both", expand=True)
        self._build()

    def recs_by_case(self):
        if self._recs_by_case_cache is None:
            d = {}
            for r in self.recs:
                d.setdefault(r.case, []).append(r)
            self._recs_by_case_cache = d
        return self._recs_by_case_cache

    def after0(self, fn, *a):
        self.root.after(0, lambda: fn(*a))

    def after0_delayed(self, ms, fn, *a):
        self.root.after(ms, lambda: fn(*a))

    def _build(self):
        bar = ttk.Frame(self, padding=(8, 4))
        bar.pack(fill="x")
        server = getattr(self.connection_settings, "server", "") or ""
        db = getattr(self.connection_settings, "database", "") or ""
        ttk.Label(bar, text=f"Connected: {server} / {db}", foreground="#1a7a1a").pack(side="left")
        ttk.Button(bar, text="Change Connection…", command=self._change_connection).pack(side="right")

        self.main_scroll = ScrollableFrame(self)
        self.main_scroll.pack(fill="both", expand=True)

        self.load_frame = LoadFrame(self.main_scroll.content, self._on_data_ready)
        self.load_frame.pack(fill="x", pady=(0, 10))

        ttk.Separator(self.main_scroll.content).pack(fill="x", pady=10)

        self.review_frame = ReviewFrame(self.main_scroll.content, self)
        self.review_frame.pack(fill="both", expand=True)

    def _change_connection(self):
        from shared.ui.connection_dialog import ConnectionDialog
        dlg = ConnectionDialog(self.root, force=True)
        self.root.wait_window(dlg)
        if dlg.result is not None:
            self.connection_settings = dlg.result
            try:
                self.engine = create_database_engine(dlg.result, dlg.result.database or "master")
                messagebox.showinfo("Connected", f"Now connected to {dlg.result.server}.")
            except Exception as exc:
                messagebox.showerror("Connection failed", str(exc))

    def _on_data_ready(self, df, mapping):
        self.df = df
        self.mapping = mapping
        self.recs = build_records(df, mapping)
        self._recs_by_case_cache = None
        n = len(df)
        self.pairs = None
        self.match_label = [""] * n
        self.difference_status = [""] * n
        self.corr = [""] * n
        self.reason = [""] * n
        self.group_id = [None] * n
        self.excluded = [False] * n
        self.sql_status = [""] * n
        self.sql_text = [""] * n

        cases = self.recs_by_case()
        multi = {k: v for k, v in cases.items() if len(v) > 1}

        self.review_frame.log.log(
            f"Loaded {n:,} rows. {len(multi):,} CASE_ID(s) have more than one ENDUSER row and need review."
        )
        self.review_frame.refresh()
