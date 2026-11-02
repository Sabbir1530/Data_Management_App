"""
logic/geocode.py
Fills in missing Latitude/Longitude values by geocoding an address built
from selected columns. Adapted from the user's original coordinates.py
into a reusable function with progress reporting + a cooperative stop flag.
"""
import time
import pandas as pd
from geopy.geocoders import ArcGIS
from geopy.exc import GeocoderTimedOut, GeocoderServiceError


def build_address(row, street_col, city_col, state_col, addr2_col=None, zip_col=None):
    addr1 = str(row[street_col]).strip() if pd.notna(row.get(street_col)) else ""

    addr2 = ""
    if addr2_col and pd.notna(row.get(addr2_col)):
        val = str(row[addr2_col]).strip()
        if val.lower() != "nan":
            addr2 = val

    city = str(row[city_col]).strip() if pd.notna(row.get(city_col)) else ""
    state = str(row[state_col]).strip() if pd.notna(row.get(state_col)) else ""

    zip_code = ""
    if zip_col and pd.notna(row.get(zip_col)):
        val = str(row[zip_col]).strip()
        if val.lower() != "nan":
            zip_code = val

    street = f"{addr1} {addr2}".strip()
    parts = [p for p in [street, city, state, zip_code] if p]
    return ", ".join(parts)


def _invalid_coord_mask(series):
    """True where a coordinate cell is genuinely unusable: empty/NaN, or
    text that isn't a real number. Coercing through pd.to_numeric catches
    blank strings / stray whitespace that plain .isna() would miss, without
    flagging perfectly valid numeric cells as missing."""
    return pd.to_numeric(series, errors="coerce").isna()


def count_missing(df, lat_col, lon_col):
    if lat_col not in df.columns or lon_col not in df.columns:
        return len(df)
    return int((_invalid_coord_mask(df[lat_col]) | _invalid_coord_mask(df[lon_col])).sum())


def _geocode_addresses(unique_addrs, stop_flag=None, delay_seconds=1.0, max_retries=3, progress_cb=None):
    """Shared retry/backoff geocoding core. Returns {address: (lat, lon)|
    (None, None)} plus the list of addresses that genuinely couldn't be
    located."""
    geolocator = ArcGIS(user_agent="gpo_automation_geocoder")
    total_unique = len(unique_addrs)
    cache = {}
    failed_addrs = []
    for i, addr in enumerate(unique_addrs, start=1):
        if stop_flag is not None and stop_flag.is_set():
            break

        result = (None, None)
        for attempt in range(max_retries):
            try:
                location = geolocator.geocode(addr, timeout=8)
                result = (location.latitude, location.longitude) if location else (None, None)
                break
            except (GeocoderTimedOut, GeocoderServiceError):
                if attempt < max_retries - 1:
                    time.sleep(delay_seconds * (attempt + 1) * 2)  # backoff before retrying
            except Exception:
                break  # non-retryable error for this address

        cache[addr] = result
        if result == (None, None):
            failed_addrs.append(addr)

        if progress_cb:
            progress_cb(i, total_unique, addr)
        time.sleep(delay_seconds)

    return cache, failed_addrs


def geocode_missing(
    df,
    street_col,
    city_col,
    state_col,
    lat_col,
    lon_col,
    addr2_col=None,
    zip_col=None,
    progress_cb=None,
    stop_flag=None,
    delay_seconds=1.0,
    max_retries=3,
):
    """Geocode rows in df missing lat/lon, building the search address from
    separate street/city/state/zip columns (used by the GPO tab, which maps
    those columns individually).

    delay_seconds defaults to 1.0 (previously 0.1) — ArcGIS's free geocoder
    throttles/blocks bursts of rapid requests, which was silently causing
    many addresses to come back empty on larger files. Each address also
    gets up to `max_retries` attempts with backoff before being given up on.

    Returns (df_with_coords, filled_count, total_missing, failed_addresses)
    — failed_addresses lists the addresses ArcGIS genuinely couldn't locate
    (bad/incomplete address, not an app bug), so callers can show the user
    exactly what still needs manual attention.
    """
    df = df.copy()
    if lat_col not in df.columns:
        df[lat_col] = None
    if lon_col not in df.columns:
        df[lon_col] = None

    null_mask = _invalid_coord_mask(df[lat_col]) | _invalid_coord_mask(df[lon_col])
    missing_idx = df[null_mask].index
    total_missing = len(missing_idx)
    if total_missing == 0:
        return df, 0, 0, []

    addr_by_idx = {
        idx: build_address(df.loc[idx], street_col, city_col, state_col, addr2_col, zip_col)
        for idx in missing_idx
    }
    unique_addrs = sorted(set(a for a in addr_by_idx.values() if a))

    cache, failed_addrs = _geocode_addresses(
        unique_addrs, stop_flag=stop_flag, delay_seconds=delay_seconds,
        max_retries=max_retries, progress_cb=progress_cb,
    )

    filled = 0
    for idx, addr in addr_by_idx.items():
        lat, lon = cache.get(addr, (None, None))
        if lat is not None and lon is not None:
            df.loc[idx, lat_col] = lat
            df.loc[idx, lon_col] = lon
            filled += 1

    return df, filled, total_missing, failed_addrs


def geocode_missing_by_address(
    df,
    addr_col,
    lat_col,
    lon_col,
    progress_cb=None,
    stop_flag=None,
    delay_seconds=1.0,
    max_retries=3,
):
    """Geocode rows in df missing lat/lon, using a single free-text address
    column directly as the search string (used by Fuzzy Matching, where an
    entity's address is one combined column rather than separate
    street/city/state columns).

    Returns (df_with_coords, filled_count, total_missing, failed_addresses).
    """
    df = df.copy()
    if lat_col not in df.columns:
        df[lat_col] = None
    if lon_col not in df.columns:
        df[lon_col] = None

    null_mask = _invalid_coord_mask(df[lat_col]) | _invalid_coord_mask(df[lon_col])
    missing_idx = df[null_mask].index
    total_missing = len(missing_idx)
    if total_missing == 0:
        return df, 0, 0, []

    addr_by_idx = {
        idx: (str(df.loc[idx, addr_col]).strip() if pd.notna(df.loc[idx].get(addr_col)) else "")
        for idx in missing_idx
    }
    unique_addrs = sorted(set(a for a in addr_by_idx.values() if a and a.lower() != "nan"))

    cache, failed_addrs = _geocode_addresses(
        unique_addrs, stop_flag=stop_flag, delay_seconds=delay_seconds,
        max_retries=max_retries, progress_cb=progress_cb,
    )

    filled = 0
    for idx, addr in addr_by_idx.items():
        lat, lon = cache.get(addr, (None, None))
        if lat is not None and lon is not None:
            df.loc[idx, lat_col] = lat
            df.loc[idx, lon_col] = lon
            filled += 1

    return df, filled, total_missing, failed_addrs
