"""
logic/distance_match.py
Haversine-distance spatial join, adapted from the user's original
distance.py. Extended with a helper that runs the HSP file at one
threshold and the NonHSP file at another threshold, then combines
the pairings the GPO workflow needs.
"""
import numpy as np
import pandas as pd

EARTH_RADIUS_METERS = 6371000

def _invalid_coord_mask(series):
    """True where a coordinate cell can't be used for matching (missing or
    non-numeric). Using numeric coercion instead of plain isna() catches
    blank/whitespace-only cells that isna() alone would miss, so rows aren't
    silently dropped from the join without being accounted for."""
    return pd.to_numeric(series, errors="coerce").isna()

def spatial_join(
    df_left,
    df_right,
    left_lat_col,
    left_lon_col,
    right_lat_col,
    right_lon_col,
    threshold_meters,
    progress_cb=None,
    left_suffix="_FILE1",
    right_suffix="_FILE2",
):
    """Left-join df_left to df_right, keeping pairs within threshold_meters.

    Returns (matches_df, skipped_left, skipped_right) — skipped_* counts
    rows that were excluded because they had no usable Lat/Lon, so the
    caller can reconcile the pair count against a manual count.
    """
    left_bad = _invalid_coord_mask(df_left[left_lat_col]) | _invalid_coord_mask(df_left[left_lon_col])
    right_bad = _invalid_coord_mask(df_right[right_lat_col]) | _invalid_coord_mask(df_right[right_lon_col])
    df_left_valid = df_left[~left_bad].reset_index(drop=True)
    df_right_valid = df_right[~right_bad].reset_index(drop=True)
    skipped_left = int(left_bad.sum())
    skipped_right = int(right_bad.sum())

    if df_left_valid.empty or df_right_valid.empty:
        return pd.DataFrame(), skipped_left, skipped_right

    lat_r = np.radians(pd.to_numeric(df_right_valid[right_lat_col], errors="coerce").values)
    lon_r = np.radians(pd.to_numeric(df_right_valid[right_lon_col], errors="coerce").values)

    records = []
    total = len(df_left_valid)
    for i in range(total):
        lat_l = np.radians(pd.to_numeric(df_left_valid.loc[i, left_lat_col], errors="coerce"))
        lon_l = np.radians(pd.to_numeric(df_left_valid.loc[i, left_lon_col], errors="coerce"))

        dlat = lat_r - lat_l
        dlon = lon_r - lon_l
        a = np.sin(dlat / 2.0) ** 2 + np.cos(lat_l) * np.cos(lat_r) * np.sin(dlon / 2.0) ** 2
        c = 2 * np.arcsin(np.sqrt(a))
        distances = EARTH_RADIUS_METERS * c

        matching = np.where(distances <= threshold_meters)[0]
        for j in matching:
            records.append({
                "__l_idx": i,
                "__r_idx": j,
                "DISTANCE_M": round(float(distances[j]), 2),
            })

        if progress_cb and (i % 10 == 0 or i == total - 1):
            progress_cb(i + 1, total)

    if not records:
        return pd.DataFrame(), skipped_left, skipped_right

    df_matches = pd.DataFrame(records)
    merged = df_matches.merge(df_left_valid, left_on="__l_idx", right_index=True)
    merged = merged.merge(
        df_right_valid, left_on="__r_idx", right_index=True,
        suffixes=(left_suffix, right_suffix),
    )
    merged = merged.drop(columns=["__l_idx", "__r_idx"])
    dist_col = merged.pop("DISTANCE_M")
    merged.insert(0, "DISTANCE_M", dist_col)
    return merged, skipped_left, skipped_right

def run_tiered_distance_matching(
    hsp_df,
    nonhsp_df,
    reference_df,
    hsp_lat_col,
    hsp_lon_col,
    ref_lat_col,
    ref_lon_col,
    hsp_threshold_m=200,
    nonhsp_threshold_m=50,
    progress_cb=None,
):
    """Run the two-tier distance matching the GPO workflow needs:
    - HSP rows matched to the reference file within hsp_threshold_m
    - NonHSP rows matched to the reference file within nonhsp_threshold_m
    Adds a TIER column ('HSP' / 'NonHSP') and returns (combined_df, stats)
    where stats reports how many rows in each tier (and the reference file)
    were skipped for having no usable Lat/Lon — use this to reconcile the
    pair count against a manual count.
    """
    pieces = []
    stats = {
        "hsp_skipped": 0, "hsp_ref_skipped": 0,
        "nonhsp_skipped": 0, "nonhsp_ref_skipped": 0,
    }

    if hsp_df is not None and not hsp_df.empty:
        def hsp_progress(i, total):
            if progress_cb:
                progress_cb("HSP", i, total)

        r, skipped_left, skipped_right = spatial_join(
            hsp_df, reference_df, hsp_lat_col, hsp_lon_col, ref_lat_col, ref_lon_col,
            hsp_threshold_m, progress_cb=hsp_progress,
        )
        stats["hsp_skipped"] = skipped_left
        stats["hsp_ref_skipped"] = skipped_right
        if not r.empty:
            r.insert(0, "TIER", "HSP")
            pieces.append(r)

    if nonhsp_df is not None and not nonhsp_df.empty:
        def nonhsp_progress(i, total):
            if progress_cb:
                progress_cb("NonHSP", i, total)

        r, skipped_left, skipped_right = spatial_join(
            nonhsp_df, reference_df, hsp_lat_col, hsp_lon_col, ref_lat_col, ref_lon_col,
            nonhsp_threshold_m, progress_cb=nonhsp_progress,
        )
        stats["nonhsp_skipped"] = skipped_left
        stats["nonhsp_ref_skipped"] = skipped_right
        if not r.empty:
            r.insert(0, "TIER", "NonHSP")
            pieces.append(r)

    if not pieces:
        return pd.DataFrame(), stats

    combined = pd.concat(pieces, ignore_index=True)
    return combined, stats
