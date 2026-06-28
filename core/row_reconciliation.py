"""Row reconciliation — map option contract rows to their underlying futures row.

Issue 001 (WTI incident regression): the pipeline must reconcile option rows to the
matching underlying futures settlement using *domain keys* — never raw row index /
file line order. An option chain has many rows per trade date, so any positional
1:1 join silently pairs an option with the wrong (or a coincidental) future and was a
root cause of the WTI incident.

This module produces a reconciliation table (one row per option-contract identity per
trade date) recording whether a domain-key match to the underlying future was found.
It also hard-rejects any attempt to reconcile by a positional/row-index key.

Public-safe: pure pandas over already-normalized frames; no provider data, no I/O.
"""

from __future__ import annotations

import pandas as pd

# Domain keys that uniquely tie an option row to its underlying futures row.
# A future is identified by (trade date, product identity, delivery month); the
# option carries the same identity plus its own (expiry, right, strike).
DOMAIN_RECONCILIATION_KEYS = (
    "as_of_date",
    "product_id",
    "contract_root",
    "hub",
    "delivery_month",
)

# Keys that name a position/line rather than a domain identity. Reconciling on any of
# these reintroduces the WTI incident, so they are rejected outright.
_FORBIDDEN_INDEX_KEYS = {
    "row_index",
    "row_number",
    "rownum",
    "line_number",
    "line_no",
    "index",
    "__index__",
    "_row",
    "_rowid",
    "rowid",
    "position",
    "ordinal",
}


def _reject_row_index_keys(keys) -> None:
    """Raise if any reconciliation key names a positional/row-index column."""
    bad = [k for k in keys if str(k).strip().lower() in _FORBIDDEN_INDEX_KEYS]
    if bad:
        raise ValueError(
            "row reconciliation must use domain keys, not row index; "
            f"forbidden positional keys requested: {bad}"
        )


def _option_mask(df: pd.DataFrame) -> pd.Series:
    it = df.get("instrument_type")
    if it is None:
        return pd.Series(False, index=df.index)
    return it.astype("string").str.lower().eq("option").fillna(False)


def _future_mask(df: pd.DataFrame) -> pd.Series:
    it = df.get("instrument_type")
    if it is None:
        return pd.Series(False, index=df.index)
    return it.astype("string").str.lower().eq("future").fillna(False)


def reconcile_options_to_underlying(
    df: pd.DataFrame,
    keys=DOMAIN_RECONCILIATION_KEYS,
    price_col: str = "price",
) -> pd.DataFrame:
    """Reconcile option rows to their underlying futures row by domain keys.

    Args:
        df: a frame containing both ``instrument_type == "future"`` and
            ``instrument_type == "option"`` rows (RAW_SCHEMA-compatible).
        keys: domain keys used to join options to futures. Must not be positional.
        price_col: column holding the settlement price for the futures match.

    Returns:
        A reconciliation table, one row per option contract identity, with columns:
        the join keys, ``expiry``, ``right``, ``strike``, ``option_settlement_price``,
        ``underlying_settlement_price``, and ``match_status`` in
        {``matched``, ``missing_underlying``, ``ambiguous_underlying``}.

    Raises:
        ValueError: if ``keys`` names a positional/row-index column, or required
            columns are absent.
    """
    _reject_row_index_keys(keys)

    join_keys = [k for k in keys if k in df.columns]
    if not join_keys:
        raise ValueError(
            f"none of the requested domain keys are present: {list(keys)}"
        )
    if price_col not in df.columns:
        raise ValueError(f"price column {price_col!r} not in frame")

    opt = _option_mask(df)
    fut = _future_mask(df)
    if not opt.any():
        raise ValueError("reconciliation input contains no option rows")
    if not fut.any():
        raise ValueError("reconciliation input contains no underlying future rows")

    futures = df.loc[fut & df[price_col].notna(), join_keys + [price_col]].copy()
    # Detect ambiguous underlying: more than one distinct future settlement per key.
    fut_counts = (
        futures.groupby(join_keys, dropna=False)[price_col]
        .nunique()
        .rename("_n_distinct_underlying")
    )
    fut_price = (
        futures.sort_values(join_keys)
        .groupby(join_keys, dropna=False)[price_col]
        .first()
        .rename("underlying_settlement_price")
    )

    option_cols = join_keys + [
        c for c in ("expiry", "right", "strike") if c in df.columns
    ] + [price_col]
    options = df.loc[opt, option_cols].copy()
    options = options.rename(columns={price_col: "option_settlement_price"})

    recon = options.merge(
        pd.concat([fut_price, fut_counts], axis=1).reset_index(),
        how="left",
        on=join_keys,
    )

    n = pd.to_numeric(recon.get("_n_distinct_underlying"), errors="coerce")
    matched = recon["underlying_settlement_price"].notna() & (n == 1)
    ambiguous = recon["underlying_settlement_price"].notna() & (n > 1)
    recon["match_status"] = "missing_underlying"
    recon.loc[matched, "match_status"] = "matched"
    recon.loc[ambiguous, "match_status"] = "ambiguous_underlying"

    return recon.drop(columns=["_n_distinct_underlying"]).reset_index(drop=True)


def reconciliation_summary(recon: pd.DataFrame) -> dict:
    """Summarize a reconciliation table for summary.json / dashboard."""
    total = int(len(recon))
    by_status = (
        recon["match_status"].value_counts().to_dict() if total else {}
    )
    matched = int(by_status.get("matched", 0))
    return {
        "option_rows": total,
        "matched": matched,
        "missing_underlying": int(by_status.get("missing_underlying", 0)),
        "ambiguous_underlying": int(by_status.get("ambiguous_underlying", 0)),
        "match_rate": (matched / total) if total else None,
        "join": "domain_keys",
    }
