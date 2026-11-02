"""
logic/fuzzy_match.py
Weighted fuzzy-matching engine (Name + Address), adapted from the user's
original standalone script into a reusable, parameterized function that a
GUI can drive and report progress from.
"""
import re
import math
import pandas as pd

from shared.services.dict import HEALTHCARE_SYNONYMS

def _normalize_to_word_set(text: str) -> set:
    if not text or pd.isna(text):
        return set()
    words = re.findall(r'[a-zA-Z0-9]+', str(text).lower())
    normalized = set()
    for w in words:
        w_upper = w.upper()
        if w_upper in HEALTHCARE_SYNONYMS:
            syn = HEALTHCARE_SYNONYMS[w_upper]
            if syn:
                normalized.update(syn.lower().split())
        else:
            normalized.add(w)
    return normalized

def _word_similarity(set1: set, set2: set) -> float:
    if not set1 and not set2:
        return 100.0
    if not set1 or not set2:
        return 0.0
    intersect = len(set1 & set2)
    union = len(set1 | set2)
    return (intersect / union) * 100.0

DEFAULT_PRIORITY_KEYWORDS = ["healthcare", "hospital", "health", "clinic"]
EARTH_RADIUS_METERS = 6371000

def _check_priority_keywords(text, keywords):
    text_lower = str(text).lower()
    return any(re.search(r"\b" + re.escape(kw.strip()) + r"\b", text_lower)
               for kw in keywords if kw.strip())

def _haversine_m(lat1, lon1, lat2, lon2):
    try:
        lat1, lon1, lat2, lon2 = float(lat1), float(lon1), float(lat2), float(lon2)
    except (TypeError, ValueError):
        return None
    if any(pd.isna(v) for v in (lat1, lon1, lat2, lon2)):
        return None
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return EARTH_RADIUS_METERS * 2 * math.asin(math.sqrt(a))

def run_fuzzy_matching(
    df,
    name1_col,
    addr1_col,
    name2_col,
    addr2_col,
    threshold=75,
    name_weight=70,
    addr_weight=30,
    priority_keywords=None,
    suggestion_group_col=None,
    progress_cb=None,
    lat1_col=None,
    lon1_col=None,
    lat2_col=None,
    lon2_col=None,
    state1_col=None,
    state2_col=None,
    generate_suggestions=False,
):
    """Run weighted fuzzy matching over every row of df.

    If lat1_col/lon1_col/lat2_col/lon2_col are all given, a per-row
    DISTANCE_M (meters) is also computed, and it becomes the tie-breaker
    when suggesting the best match within a group: among rows with an
    equal MATCH_SCORE, the one with the smaller distance is suggested.

    Returns (df_matches, df_differences, stats_dict)
    """
    priority_keywords = priority_keywords or DEFAULT_PRIORITY_KEYWORDS
    total_w = (name_weight or 0) + (addr_weight or 0)
    if total_w <= 0:
        name_weight, addr_weight, total_w = 70, 30, 100
    n_w = name_weight / total_w
    a_w = addr_weight / total_w

    use_distance = bool(lat1_col and lon1_col and lat2_col and lon2_col)

    df = df.copy()
    n_rows = len(df)

    name_scores, addr_scores, weighted_scores, is_match_flags = [], [], [], []
    distances = []

    for i, (_, row) in enumerate(df.iterrows(), start=1):
        n1 = str(row.get(name1_col, "")).strip().lower()
        n2 = str(row.get(name2_col, "")).strip().lower()
        a1 = str(row.get(addr1_col, "")).strip().lower()
        a2 = str(row.get(addr2_col, "")).strip().lower()

        n1_set = _normalize_to_word_set(n1)
        n2_set = _normalize_to_word_set(n2)
        a1_set = _normalize_to_word_set(a1)
        a2_set = _normalize_to_word_set(a2)

        state_match = True
        if state1_col and state2_col:
            v1 = row.get(state1_col)
            v2 = row.get(state2_col)
            s1 = str(v1).replace(" ", "").lower() if pd.notna(v1) else ""
            s2 = str(v2).replace(" ", "").lower() if pd.notna(v2) else ""
            if s1 == "nan": s1 = ""
            if s2 == "nan": s2 = ""
            if s1 != s2:
                state_match = False

        name_score = _word_similarity(n1_set, n2_set)
        addr_score = _word_similarity(a1_set, a2_set)
        weighted = round(name_score * n_w + addr_score * a_w, 2)

        name_scores.append(name_score)
        addr_scores.append(addr_score)
        weighted_scores.append(weighted)
        is_match_flags.append((weighted >= threshold) and state_match)

        if use_distance:
            distances.append(_haversine_m(
                row.get(lat1_col), row.get(lon1_col), row.get(lat2_col), row.get(lon2_col),
            ))

        if progress_cb and (i % 25 == 0 or i == n_rows):
            progress_cb(i, n_rows)

    df["NAME_SCORE"] = name_scores
    df["ADDR_SCORE"] = addr_scores
    df["MATCH_SCORE"] = weighted_scores
    if use_distance:
        df["DISTANCE_M"] = [round(d, 2) if d is not None else None for d in distances]
    df["_IS_MATCH"] = is_match_flags

    df_matches = df[df["_IS_MATCH"] == True].copy()      
    df_differences = df[df["_IS_MATCH"] == False].copy()  

    df_matches["SUGGESTION"] = ""
    if generate_suggestions and not df_matches.empty:
        df_matches["_HAS_PRIORITY"] = df_matches[name1_col].apply(
            lambda t: _check_priority_keywords(t, priority_keywords)
        )

        group_col = suggestion_group_col if (suggestion_group_col and
                                              suggestion_group_col in df_matches.columns) else name2_col

        
        
        
        sort_cols = [group_col, "_HAS_PRIORITY", "MATCH_SCORE"]
        sort_asc = [True, False, False]
        if use_distance:
            sort_cols.append("DISTANCE_M")
            sort_asc.append(True)

        sorted_matches = df_matches.sort_values(by=sort_cols, ascending=sort_asc)
        best_indices = sorted_matches.groupby(group_col).head(1).index
        df_matches.loc[best_indices, "SUGGESTION"] = "Suggested"
        df_matches.drop(columns=["_HAS_PRIORITY"], inplace=True)

    df_matches.drop(columns=["_IS_MATCH"], inplace=True, errors="ignore")
    df_differences["SUGGESTION"] = ""
    df_differences.drop(columns=["_IS_MATCH"], inplace=True, errors="ignore")

    stats = {
        "total": n_rows,
        "matched": len(df_matches),
        "different": len(df_differences),
        "suggested": int((df_matches["SUGGESTION"] == "Suggested").sum()) if not df_matches.empty else 0,
        "match_rate": round(100 * len(df_matches) / n_rows, 2) if n_rows else 0.0,
        "threshold": threshold,
        "name_weight": name_weight,
        "addr_weight": addr_weight,
        "used_distance": use_distance,
    }
    return df_matches, df_differences, stats
