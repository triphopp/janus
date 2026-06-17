"""Mock pipeline diff demo — all 5 CDC change types with fully controlled frames.

Builds each stage DataFrame explicitly so every change type fires predictably.
No dependency on provider/adapter/validator internals.

  schema_add    ingestion->adapter: adapter derives price_std, return_std, vol_std...
  cell_mod      adapter->validators: price_std spike clipped (reason=outlier_cap)
  cell_mod      ingestion->adapter: raw_close silently revised -20%  (UNATTRIBUTED)
  row_drop      validators->post_filter: negative-price row removed
  row_add       validators->post_filter: late-arriving correction row

Run:
    python demo_mock_diff.py
Output (open in browser):
    outputs/diff/mock_demo_20260617_diff.html
"""

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from core import cdc, breaks as bk, diff_report

# ── Helpers ───────────────────────────────────────────────────────────────────
RUN_ID = "mock_demo_20260617"
N = 80
np.random.seed(42)
DATES = [(date(2023, 1, 3) + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(N)]

raw_close = 100.0 * np.cumprod(1 + np.random.normal(0.001, 0.012, N))


# ══════════════════════════════════════════════════════════════════════════════
# Stage 1: INGESTION  (raw provider output)
#   - idx 15: negative price  → will fail logical_bounds → row_drop later
#   - idx 40: normal now, but adapter will silently revise it (UNATTRIBUTED break)
# ══════════════════════════════════════════════════════════════════════════════
raw_close_ing = raw_close.copy()
raw_close_ing[15] = -1.5   # explicit bad price (negative)

frame_ingestion = pd.DataFrame({
    "as_of_date":  DATES,
    "symbol":      "MOCK",
    "raw_close":   raw_close_ing,
    "adj_factor":  np.ones(N),
    "volume":      np.random.randint(1_000_000, 10_000_000, N).astype(float),
    "is_delisted": False,
    "provider":    "mock",
})


# ══════════════════════════════════════════════════════════════════════════════
# Stage 2: ADAPTER  (derived columns + one silent mutation)
#   NEW cols: price_std, adjusted_price_provider, return_std, vol_std, volume_std,
#             survivor_flag, _return_outlier_flag, _return_outlier_reason, price_adj_warning
#   MUTATION: raw_close[40] revised -20% without any flag (UNATTRIBUTED)
#   PRICE SPIKE: price_std[55] = 3× normal (will be clipped in validators stage)
# ══════════════════════════════════════════════════════════════════════════════
frame_adapter = frame_ingestion.copy()

# --- Silent mutation on raw_close[40] ---
original_40 = frame_adapter.loc[40, "raw_close"]
frame_adapter.loc[40, "raw_close"] = original_40 * 0.80   # -20% revision, no flag

# --- Derive core columns ---
price_std = frame_adapter["raw_close"].copy()
frame_adapter["adjusted_price_provider"] = price_std
frame_adapter["price_std"] = price_std
frame_adapter["price_adjustment_warning"] = False

returns = price_std.pct_change()
frame_adapter["return_raw"] = returns
frame_adapter["return_std"] = returns

vol = returns.rolling(21, min_periods=5).std()
frame_adapter["vol_std"] = vol
frame_adapter["volume_std"] = frame_adapter["volume"]
frame_adapter["survivor_flag"] = False
frame_adapter["_return_outlier_flag"] = False
frame_adapter["_return_outlier_reason"] = ""

# --- Inject price spike at idx 55 (adapter sends bad value; validators will fix it) ---
normal_price = frame_adapter.loc[55, "price_std"]
frame_adapter.loc[55, "price_std"] = normal_price * 3.5   # 3.5x spike


# ══════════════════════════════════════════════════════════════════════════════
# Stage 3: VALIDATORS  (clips spike, flags bad rows, adds 5 flag cols)
#   cell_mod: price_std[55] clipped back to MAD upper bound  → _outlier_flag=True
#   schema_add: _bound_flag, _bound_reason, _missing_flag, _missing_reason, _outlier_flag
# ══════════════════════════════════════════════════════════════════════════════
frame_v = frame_adapter.copy()

# -- logical bounds: flag negative price --
frame_v["_bound_flag"]  = frame_v["price_std"] <= 0    # idx 15 fires
frame_v["_bound_reason"] = frame_v["_bound_flag"].map(
    {True: "price<=0;", False: ""}
)

# -- missing completeness: nothing flagged in this clean series --
frame_v["_missing_flag"]   = False
frame_v["_missing_reason"] = ""

# -- outlier_cap: clip the spike at idx 55, set flag --
mad_upper = float(frame_adapter["price_std"].median() * 1.20)   # reasonable cap
clipped_val = min(frame_adapter.loc[55, "price_std"], mad_upper)
frame_v["_outlier_flag"] = False
frame_v.loc[55, "price_std"]     = clipped_val
frame_v.loc[55, "_outlier_flag"] = True   # attribution flag


# ══════════════════════════════════════════════════════════════════════════════
# Stage 4: POST_FILTER  (remove bad-price row, add late-arriving row)
#   row_drop: idx 15 (negative price, _bound_flag=True) removed
#   row_add:  correction row for 2023-05-01
# ══════════════════════════════════════════════════════════════════════════════
frame_filtered = frame_v[~frame_v["_bound_flag"]].copy().reset_index(drop=True)

# Late correction row
late_row = pd.DataFrame([{
    "as_of_date":               "2023-05-01",
    "symbol":                   "MOCK",
    "raw_close":                118.0,
    "adj_factor":               1.0,
    "volume":                   5_000_000.0,
    "is_delisted":              False,
    "provider":                 "mock",
    "adjusted_price_provider":  118.0,
    "price_std":                118.0,
    "price_adjustment_warning": False,
    "return_raw":               0.003,
    "return_std":               0.003,
    "vol_std":                  0.011,
    "volume_std":               5_000_000.0,
    "survivor_flag":            False,
    "_return_outlier_flag":     False,
    "_return_outlier_reason":   "",
    "_bound_flag":              False,
    "_bound_reason":            "",
    "_missing_flag":            False,
    "_missing_reason":          "",
    "_outlier_flag":            False,
}])
frame_filtered = pd.concat([frame_filtered, late_row], ignore_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# Normalise as_of_date so CDC identity key aligns across all hops
# ══════════════════════════════════════════════════════════════════════════════
def _norm(df):
    df = df.copy()
    if "as_of_date" in df.columns:
        df["as_of_date"] = (
            pd.to_datetime(df["as_of_date"], errors="coerce")
            .dt.strftime("%Y-%m-%d")
        )
    return df

stage_frames = [
    ("ingestion",   _norm(frame_ingestion)),
    ("adapter",     _norm(frame_adapter)),
    ("validators",  _norm(frame_v)),
    ("post_filter", _norm(frame_filtered)),
]

# ══════════════════════════════════════════════════════════════════════════════
# Reason maps — which flag column covers which column mutation
# ══════════════════════════════════════════════════════════════════════════════
reason_maps = {
    "adapter->validators": {
        "price_std": {"flag_col": "_outlier_flag", "reason": "outlier_cap"},
        "_row_drop":  {"reason": "validator_bound_drop"},
    },
    "validators->post_filter": {
        "_row_drop": {"reason": "bound_flag_drop"},
    },
}

# ══════════════════════════════════════════════════════════════════════════════
# Run CDC across the full stage chain
# ══════════════════════════════════════════════════════════════════════════════
cdc_records = cdc.diff_run(
    stage_frames,
    identity_cols=["as_of_date", "symbol"],
    reason_maps=reason_maps,
    run_id=RUN_ID,
)

ledger_path    = cdc.write_ledger(cdc_records, RUN_ID)
pipeline_breaks = bk.raise_breaks(cdc_records, RUN_ID)
bk.write_breaks(pipeline_breaks, RUN_ID)
diff_html_path = diff_report.write_diff_html(cdc_records, pipeline_breaks, RUN_ID)
rollup         = cdc.rollup(cdc_records)

# ══════════════════════════════════════════════════════════════════════════════
# Print summary
# ══════════════════════════════════════════════════════════════════════════════
SEP = "=" * 64

print(f"\n{SEP}")
print(f"  MOCK PIPELINE DIFF DEMO   run={RUN_ID}")
print(SEP)
print(f"  Total CDC records : {len(cdc_records)}")
print(f"  Breaks raised     : {len(pipeline_breaks)}")
print()

print("  Stage pipeline:")
for hop, cols in rollup.items():
    counts = {k: 0 for k in
              ["cell_mod","schema_add","schema_drop","row_add","row_drop","unattributed"]}
    for c in cols.values():
        for k in counts:
            counts[k] += c.get(k, 0)
    print(f"    {hop}")
    print(f"      cell_mod={counts['cell_mod']}  schema_add={counts['schema_add']}"
          f"  row_add={counts['row_add']}  row_drop={counts['row_drop']}"
          f"  UNATTR={counts['unattributed']}")
    for col, cnts in sorted(cols.items()):
        parts = [f"{k}={cnts[k]}" for k in
                 ["schema_add","schema_drop","cell_mod","row_add","row_drop"] if cnts.get(k)]
        if cnts.get("unattributed"):
            parts.append(f"UNATTR={cnts['unattributed']}")
        if not parts:
            continue
        # find sample value for schema_add
        sample = ""
        for r in cdc_records:
            if r.column == col and r.stage_from == hop.split("->")[0]:
                if r.change_type == "schema_add" and r.after is not None:
                    v = f"{r.after:.4f}" if isinstance(r.after, float) else repr(r.after)
                    sample = f"  [sample={v}, n={r.sample_count}]"
                break
        print(f"        {col:30} {', '.join(parts)}{sample}")
print()

print("  Breaks:")
sev_order = {"high": 0, "medium": 1, "low": 2}
for b in sorted(pipeline_breaks, key=lambda x: sev_order.get(x["severity"], 9)):
    before = f"{b['before']:.4f}" if isinstance(b['before'], float) else str(b['before'])
    after  = f"{b['after']:.4f}"  if isinstance(b['after'],  float) else str(b['after'])
    delta  = f"{b['delta']:+.4f}" if isinstance(b.get('delta'), float) else "none"
    print(f"    [{b['severity'].upper():6}] {b['type']:26}"
          f"  stage={b['stage']}  col={b['field']}")
    print(f"             before={before}  after={after}  d={delta}")
print()

types_found   = {r.change_type for r in cdc_records}
reasons_found = {r.reason for r in cdc_records if r.reason}
labels = {
    "schema_add":  "derived column appeared   (price_std, return_std, vol_std...)",
    "schema_drop": "column vanished from frame",
    "cell_mod":    "in-place value changed    (outlier_cap attributed  OR  UNATTRIBUTED)",
    "row_add":     "new row appeared          (late-arriving correction)",
    "row_drop":    "row removed               (bound_flag_drop: price<=0)",
}
print("  Change types demonstrated:")
for ct in ["schema_add","cell_mod","row_drop","row_add","schema_drop"]:
    mark = "OK" if ct in types_found else "--"
    print(f"    [{mark}] {ct:14} {labels.get(ct,'')}")
print()
print(f"  Reasons : {sorted(reasons_found)}")
print(f"  Diff HTML: {diff_html_path}")
print(f"  Ledger  : {ledger_path}")
print()
