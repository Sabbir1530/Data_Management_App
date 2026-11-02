"""Pure matching / picking logic (no UI, no DB) so it can be tested on its own."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from itertools import combinations
from typing import Callable, Dict, List, Optional, Tuple, Any

from shared.services.dict import HEALTHCARE_SYNONYMS

try:  
    from rapidfuzz import fuzz

    def _ratio(a: str, b: str) -> float:
        return float(fuzz.token_sort_ratio(a, b))
except ImportError:  
    from difflib import SequenceMatcher

    def _ratio(a: str, b: str) -> float:
        a = " ".join(sorted(a.split()))
        b = " ".join(sorted(b.split()))
        return SequenceMatcher(None, a, b).ratio() * 100.0

NULL_TOKENS = {"", "NULL", "NA", "N/A", "NAN", "NONE"}


def to_str(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        if v != v:
            return ""
        if v.is_integer():
            return str(int(v))
    return str(v).strip()

def display(v) -> str:
    return to_str(v)

def null_if_token(s: str) -> str:
    return "" if s.strip().upper() in NULL_TOKENS else s.strip()

_NON_ALNUM = re.compile(r"[^A-Z0-9]+")

def norm_text(s: str) -> str:
    """Upper-case, strip punctuation, apply healthcare synonym dictionary."""
    base = _NON_ALNUM.sub(" ", s.upper().replace("'", "").replace("’", "")).split()
    mapped: List[str] = []
    for tok in base:
        mapped.extend(HEALTHCARE_SYNONYMS.get(tok, tok).split())
    return " ".join(mapped) if mapped else " ".join(base)

def norm_plain(s: str) -> str:
    return " ".join(_NON_ALNUM.sub(" ", s.upper()).split())

def norm_zip(s: str) -> str:
    d = re.sub(r"\D", "", s)
    if not d:
        return ""
    if len(d) <= 5:
        return d.zfill(5)          
    if len(d) == 8:
        return d.zfill(9)[:5]      
    return d[:5]


@dataclass
class Rec:
    idx: int
    case: str
    eid: str
    name: str
    addr: str
    city: str
    state: str
    zip: str
    idn: str
    rank: float
    terr_id: str
    sales_type: str
    n_name: str = ""
    n_addr: str = ""
    n_city: str = ""
    n_state: str = ""
    n_zip: str = ""

    def prepare(self) -> "Rec":
        self.n_name = norm_text(self.name)
        self.n_addr = norm_text(self.addr)
        self.n_city = norm_plain(self.city)
        self.n_state = norm_plain(self.state)
        self.n_zip = norm_zip(self.zip)
        return self

def build_records(df, m: Dict[str, str]) -> List[Rec]:
    n = len(df)

    def col(key):
        name = m.get(key)
        return df[name].tolist() if name and name in df.columns else [None] * n

    case, eid, name, addr = col("case_id"), col("enduser_id"), col("name"), col("addr")
    city, state, zp, idn = col("city"), col("state"), col("zip"), col("idn")
    rank, terr_id, sales_type = col("rank"), col("terr_id"), col("sales_type")
    
    def safe_float(v):
        try:
            return float(v)
        except (ValueError, TypeError):
            return float('inf')

    return [
        Rec(i, str(case[i] or ""), str(eid[i] or ""), str(name[i] or ""),
            str(addr[i] or ""), str(city[i] or ""), str(state[i] or ""),
            str(zp[i] or ""), str(idn[i] or ""), safe_float(rank[i]),
            str(terr_id[i] or "") if terr_id[i] is not None else "",
            str(sales_type[i] or "") if sales_type[i] is not None else "").prepare()
        for i in range(n)
    ]

def group_by_case(recs: List[Rec]) -> Dict[str, List[Rec]]:
    d: Dict[str, List[Rec]] = {}
    for r in recs:
        if r.case:
            d.setdefault(r.case, []).append(r)
    return {k: v for k, v in d.items() if len(v) > 1}


@dataclass
class PairResult:
    i: int
    j: int
    name_score: float
    addr_score: float
    fuzzy_same: bool
    ai_result: Optional[str] = None      
    ai_conf: int = 0
    ai_reason: str = ""

def fuzzy_pair(a: Rec, b: Rec, thr: float) -> Tuple[float, float, bool]:
    ns = _ratio(a.n_name, b.n_name) if a.n_name and b.n_name else 0.0
    ads = _ratio(a.n_addr, b.n_addr) if a.n_addr and b.n_addr else 0.0
    same = (
        ns >= thr and ads >= thr
        and a.n_city == b.n_city and a.n_state == b.n_state and a.n_zip == b.n_zip
    )
    return ns, ads, same

def run_fuzzy(recs: List[Rec], thr: float, progress: Optional[Callable[[float], None]] = None):
    cases = group_by_case(recs)
    pairs: Dict[Tuple[int, int], PairResult] = {}
    total = max(len(cases), 1)
    for k, members in enumerate(cases.values()):
        if len(members) < 2:
            continue
        
        
        
        members_sorted = sorted(members, key=lambda x: (x.rank, x.idx))
        ref = members_sorted[0]
        
        for b in members_sorted[1:]:
            ns, ads, same = fuzzy_pair(ref, b, thr)
            i, j = (ref.idx, b.idx) if ref.idx < b.idx else (b.idx, ref.idx)
            pairs[(i, j)] = PairResult(i, j, ns, ads, same)
            
        if progress and k % 50 == 0:
            progress((k + 1) / total)
    if progress: progress(1.0)
    return pairs

class UF:
    def __init__(self):
        self.p: Dict[int, int] = {}

    def find(self, x: int) -> int:
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra

def ai_candidates(pairs: Dict[Tuple[int, int], PairResult]) -> List[PairResult]:
    """Pairs fuzzy could not confirm (and not already linked through a fuzzy chain)."""
    uf = UF()
    for p in pairs.values():
        if p.fuzzy_same:
            uf.union(p.i, p.j)
    return [
        p for p in pairs.values()
        if not p.fuzzy_same and p.ai_result in (None, "ERROR") and uf.find(p.i) != uf.find(p.j)
    ]

def pair_key(a: Rec, b: Rec) -> str:
    ta = "|".join((a.name, a.addr, a.city, a.state))
    tb = "|".join((b.name, b.addr, b.city, b.state))
    return hashlib.sha1("||".join(sorted([ta.lower(), tb.lower()])).encode("utf-8")).hexdigest()[:20]

def build_groups(pairs, ai_min_conf: int):
    """Union-find over SAME pairs. Returns (groups[list of row idx], labels{row idx: 'SAME-FUZZY'|'SAME-AI'})."""
    uf = UF()
    labels: Dict[int, str] = {}
    for p in pairs.values():
        if p.fuzzy_same:
            uf.union(p.i, p.j)
            labels[p.i] = labels[p.j] = "SAME-FUZZY"
        elif p.ai_result == "SAME" and p.ai_conf >= ai_min_conf:
            uf.union(p.i, p.j)
            labels.setdefault(p.i, "SAME-AI")
            labels.setdefault(p.j, "SAME-AI")
    groups: Dict[int, List[int]] = {}
    for x in list(uf.p):
        groups.setdefault(uf.find(x), []).append(x)
    return [sorted(g) for g in groups.values() if len(g) > 1], labels

def split_weak_groups(pairs: Dict[Tuple[int, int], PairResult], groups: List[List[int]], recs: List[Rec], ai_min_conf: int) -> Tuple[List[List[int]], List[str], List[int]]:
    """Splits weakly connected groups based on bridges and cut vertices."""
    adj = {}
    edge_info = {}
    for (i, j), p in pairs.items():
        is_same = False
        weight = 100.0
        if p.fuzzy_same:
            is_same = True
            weight = (p.name_score + p.addr_score) / 2.0
        elif p.ai_result == "SAME" and p.ai_conf >= ai_min_conf:
            is_same = True
            weight = float(p.ai_conf)

        if is_same:
            adj.setdefault(i, set()).add(j)
            adj.setdefault(j, set()).add(i)
            edge_info[(i, j)] = (weight, p)
            edge_info[(j, i)] = (weight, p)

    new_groups = []
    logs = []
    dropped_nodes = []

    def get_comps(nodes, ignore_edge=None, ignore_node=None):
        visited = set()
        comps = []
        for n in nodes:
            if n == ignore_node:
                continue
            if n not in visited:
                comp = []
                q = [n]
                visited.add(n)
                for curr in q:
                    comp.append(curr)
                    for to in adj.get(curr, set()):
                        if to not in nodes or to == ignore_node:
                            continue
                        if ignore_edge and ((curr, to) == ignore_edge or (to, curr) == ignore_edge):
                            continue
                        if to not in visited:
                            visited.add(to)
                            q.append(to)
                comps.append(comp)
        return comps

    def find_bridges(nodes):
        visited = set()
        tin = {}
        low = {}
        timer = 0
        bridges = []

        def dfs(v, p=-1):
            nonlocal timer
            visited.add(v)
            tin[v] = low[v] = timer
            timer += 1
            for to in adj.get(v, set()):
                if to not in nodes:
                    continue
                if to == p:
                    continue
                if to in visited:
                    low[v] = min(low[v], tin[to])
                else:
                    dfs(to, v)
                    low[v] = min(low[v], low[to])
                    if low[to] > tin[v]:
                        bridges.append((v, to))

        for node in nodes:
            if node not in visited:
                dfs(node)
        return bridges

    def find_cut_vertices(nodes):
        visited = set()
        tin = {}
        low = {}
        timer = 0
        cut_vertices = set()

        def dfs(v, p=-1):
            nonlocal timer
            visited.add(v)
            tin[v] = low[v] = timer
            timer += 1
            children = 0
            for to in adj.get(v, set()):
                if to not in nodes:
                    continue
                if to == p:
                    continue
                if to in visited:
                    low[v] = min(low[v], tin[to])
                else:
                    children += 1
                    dfs(to, v)
                    low[v] = min(low[v], low[to])
                    if low[to] >= tin[v] and p != -1:
                        cut_vertices.add(v)
            if p == -1 and children > 1:
                cut_vertices.add(v)

        for node in nodes:
            if node not in visited:
                dfs(node)
        return cut_vertices

    for g in groups:
        subgroups = [g]
        final_subgroups = []

        while subgroups:
            curr = subgroups.pop()
            if len(curr) <= 2:
                final_subgroups.append(curr)
                continue

            bridges = find_bridges(curr)
            split_bridges = []
            for b in bridges:
                comps = get_comps(curr, ignore_edge=b)
                large_comps = sum(1 for c in comps if len(c) >= 2)
                if large_comps >= 2:
                    split_bridges.append((b, comps))

            if split_bridges:
                def edge_weight(b):
                    return edge_info[b][0] if b in edge_info else 100.0

                split_bridges.sort(key=lambda x: edge_weight(x[0]))
                best_b, best_comps = split_bridges[0]

                p = edge_info[best_b][1] if best_b in edge_info else None
                conf_str = f"AI conf {p.ai_conf}%" if p and p.ai_result == "SAME" else ("Fuzzy" if p else "Unknown")
                c_id = recs[curr[0]].case
                sizes = ", ".join(f"{len(c)} rows" for c in best_comps)
                logs.append(f"CASE_ID {c_id}: group of {len(curr)} split into {len(best_comps)} sub-clusters ({sizes}) — removed bridge edge (row {best_b[0]+2} ↔ row {best_b[1]+2}, {conf_str}).")

                subgroups.extend(best_comps)
                continue

            cut_vertices = find_cut_vertices(curr)
            split_nodes = []
            for v in cut_vertices:
                comps = get_comps(curr, ignore_node=v)
                large_comps = sum(1 for c in comps if len(c) >= 2)
                if large_comps >= 2:
                    split_nodes.append((v, comps))

            if split_nodes:
                best_v, best_comps = split_nodes[0]
                c_id = recs[curr[0]].case
                sizes = ", ".join(f"{len(c)} rows" for c in best_comps)
                logs.append(f"CASE_ID {c_id}: group of {len(curr)} split into {len(best_comps)} sub-clusters ({sizes}) — removed ambiguous bridge node (row {best_v+2}).")

                dropped_nodes.append(best_v)
                subgroups.extend(best_comps)
                continue

            final_subgroups.append(curr)

        for sg in final_subgroups:
            if len(sg) > 1:
                new_groups.append(sorted(sg))
            elif len(sg) == 1:
                
                dropped_nodes.append(sg[0])

    return new_groups, logs, dropped_nodes


def _fmt(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(x)

def pick_group(members: List[Rec]) -> Tuple[Rec, Any]:
    best = min(members, key=lambda x: (x.rank, x.idx))
    reason = f"Lowest rank ({_fmt(best.rank)})" if best.rank != float('inf') else "Lowest ID (No rank)"
    
    reasons = {m.idx: reason for m in members}
    return best, reasons


def build_sql_text(old: str, new: str, usr: str) -> str:
    return (
        f"EXEC  [MDM].[spRemap_NAD_EU_XREF] @OLD_ID ={old},  @OLD_ID_TYP  ='EU' , "
        f"@NEW_ENDUSER_ID  = {new}, @USR_ID ={usr}"
    )
