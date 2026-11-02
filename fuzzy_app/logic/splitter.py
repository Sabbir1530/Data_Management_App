"""
logic/splitter.py
Splits a dataframe into two groups (keyword-present / keyword-absent) in a
target column. Adapted from the user's original spliter.py.
"""


def split_by_keyword(df, target_column, keyword="Hospital"):
    """Returns (df_with_keyword, df_without_keyword)."""
    if target_column not in df.columns:
        raise ValueError(f"Column '{target_column}' not found. Available: {list(df.columns)}")

    mask = df[target_column].astype(str).str.contains(keyword, case=False, na=False)
    has_keyword_df = df[mask].copy()
    no_keyword_df = df[~mask].copy()
    return has_keyword_df, no_keyword_df
