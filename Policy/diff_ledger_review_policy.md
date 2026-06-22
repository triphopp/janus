# Janus Diff Ledger Review Policy

## Purpose

This policy defines how Janus agents and reviewers must evaluate large CDC diff
ledgers such as `outputs/diff/<run_id>_changes.jsonl`.

The ledger is raw evidence. Humans should not inspect every row in a 100MB+
JSONL file. Review must be based on:

1. deterministic hard gates,
2. statistical rollups,
3. protected-column checks,
4. stage-specific expectations,
5. stratified samples for human evidence.

The goal is to answer:

- Did any protected value move?
- Did anything move without an owning reason?
- Did a stage mutate only what that stage is allowed to mutate?
- Are row/schema changes inside the expected operational budget?
- Are sampled examples representative enough for human sign-off?

## Scope

This policy applies to:

- within-run stage diffs from `core.cdc.diff_run()`;
- dashboard diff views and summaries;
- break generation from CDC records;
- future `outputs/diff/<run_id>_summary.json` artifacts.

It does not replace the raw JSONL ledger. It defines how that ledger must be
summarized and reviewed.

## Ledger Fields

Each diff record is expected to contain some or all of:

```json
{
  "stage_from": "adapter",
  "stage_to": "validators",
  "change_type": "cell_mod",
  "key": {"as_of_date": "2024-09-25", "symbol": "WTI"},
  "column": "price_std",
  "before": 82.1,
  "after": 81.9,
  "delta": -0.2,
  "pct": -0.0024,
  "reason": "outlier_cap",
  "reason_flag_col": "_outlier_flag",
  "run_id": "wti_2025"
}
```

Required summary code must tolerate absent optional fields, but missing
`stage_from`, `stage_to`, or `change_type` should mark the ledger as degraded.

## Review Decision

Every run receives one final diff review status:

| Status | Meaning |
| --- | --- |
| `pass` | All hard gates pass, statistical budgets are within limits, and required samples have no unjustified defects. |
| `warn` | No hard gate fails, but non-critical budgets or statistical anomaly checks require review. |
| `fail` | A hard gate fails, or a statistical budget exceeds fail criteria. |
| `degraded` | The ledger cannot be fully evaluated because it is missing, malformed, truncated, or too large without a summary. |

Default policy:

- `fail` blocks automated trust in the run.
- `warn` allows exploratory use but requires triage before promotion.
- `degraded` must not be treated as `pass`.

## Protected Columns

Protected columns are columns where silent mutation is high risk.

### Identity And Key Columns

Any `cell_mod` on these is a hard fail unless explicitly allowed by a versioned
schema migration:

- `as_of_date`
- `date`
- `timestamp`
- `available_time`
- `knowledge_time`
- `symbol`
- `instrument`
- `product_id`
- `contract_root`
- `hub`
- `instrument_type`
- `delivery_month`
- `expiry`
- `right`
- `strike`

Reason: key mutation can create false row drops/adds, leakage, or identity drift.

### Label And Target Columns

Any unexplained mutation is a hard fail:

- `label`
- `target`
- `y`
- `label_end_time`
- `label_end_date`
- `future_return`
- `forward_return`
- `realized_return`
- `realized_vol`

Reason: these columns can leak future information or change model outcomes.

### Canonical Market Data Columns

Unattributed mutation is a hard fail. Attributed mutation may still warn/fail
depending on stage and materiality:

- `raw_close`
- `close`
- `settlement`
- `price`
- `price_std`
- `return_raw`
- `return_std`
- `iv`
- `iv_provided`
- `iv_solved`
- `delta`
- `gamma`
- `vega`
- `theta`
- `rho`
- `dte`
- `dte_days`
- `T`
- `underlying_price`
- `F`
- `S`

Reason: these are core inputs to diagnostics, pricing, labels, or risk views.

## Allowed Stage Mutation Matrix

The default stage expectations are:

| Stage | Allowed changes | Disallowed by default |
| --- | --- | --- |
| `ingestion->adapter` | schema additions for derived columns, canonical standardization, expected option-universe row drops | mutation of raw/vendor fields, key columns, label columns |
| `adapter->return_clip` | return outlier flags, thresholds, optional derived `return_winsorized` | mutation of `return_raw`; mutation of `return_std` under `tag_only` policy |
| `return_clip->validators` | validation flags, attributed price caps where policy allows, row drops with `validator_or_filter` reason | unreasoned `cell_mod`; label/key mutation |
| `adapter->validators` | same as validators path when return clip stage is absent | unreasoned canonical market data mutation |
| future stages after validators | report-only metrics, summary artifacts | mutation of training input, labels, keys, or canonical market data |

Stage-specific allowlists must be versioned. A change not in the stage allowlist
is `warn` if attributed and non-protected, `fail` if protected or unattributed.

## Required Rollups

Every large ledger review must compute these values:

```json
{
  "run_id": "wti_2025",
  "total_records": 1234567,
  "ledger_bytes": 178000000,
  "by_stage": {"adapter->validators": 1200000},
  "by_change_type": {"cell_mod": 1100000, "row_drop": 95000},
  "by_reason": {"outlier_cap": 900000, "UNATTRIBUTED": 25},
  "by_column": {"price_std": 800000, "iv": 250000},
  "protected_counts": {
    "unattributed_protected_cell_mod": 0,
    "key_mutation": 0,
    "label_mutation": 0
  },
  "rates": {
    "unattributed_rate": 0.00002,
    "row_drop_rate": 0.07695,
    "row_add_rate": 0.0,
    "schema_drop_rate": 0.0
  },
  "numeric_delta_stats": {
    "price_std": {
      "n": 800000,
      "max_abs_delta": 12.5,
      "p99_abs_delta": 0.22,
      "p99_abs_pct": 0.004
    }
  },
  "samples": {}
}
```

Rollups must be created by streaming the JSONL ledger. Do not load a large ledger
into memory only to summarize it.

## Hard Fail Gates

A run fails immediately if any condition is true:

### Structural

- Ledger is malformed JSONL.
- More than `0` records are missing `change_type`.
- More than `0` records are missing both `stage_from` and `stage_to`.
- Ledger size exceeds dashboard inline limit and no summary/paged API exists.

### Protected Columns

- Any identity/key column has `change_type == "cell_mod"`.
- Any label/target column has `change_type == "cell_mod"` without an explicit,
  versioned migration reason.
- Any protected canonical market data column has `reason == "UNATTRIBUTED"`.
- Any `schema_drop` removes a protected column.

### Attribution

- Any `row_drop` has no `reason`.
- Any `row_add` appears outside an approved restatement/vintage-diff context.
- Any `cell_mod` has `reason == "UNATTRIBUTED"` and `column` is protected.

### Stage Policy

- Any stage mutates columns outside its allowlist and the column is protected.
- Any post-validator stage mutates training inputs, labels, keys, or canonical
  market data.

## Default Statistical Budgets

These budgets apply after hard gates.

### Unattributed Changes

| Metric | Pass | Warn | Fail |
| --- | --- | --- | --- |
| protected unattributed count | `0` | not allowed | `>0` |
| non-protected unattributed count | `0` | `1..50` and rate `<=0.01%` | `>50` or rate `>0.01%` |
| unattributed by any single column | `0` | `1..10` | `>10` or column rate `>0.1%` |

Rationale:

- For protected data, one unexplained mutation is enough to fail.
- For non-protected metadata/diagnostic fields, a tiny count can be triaged, but
  it must not be normalized into routine noise.

### Row Adds And Drops

Use denominator `max(rows_before, 1)` when available. If row denominator is not
available, use `total_records` and mark confidence as degraded.

| Metric | Pass | Warn | Fail |
| --- | --- | --- | --- |
| unexplained row_drop | `0` | not allowed | `>0` |
| explained row_drop rate | `<= expected_filter_rate + 2pp` | `<= expected_filter_rate + 10pp` | `> expected_filter_rate + 10pp` |
| row_add rate in stage diff | `0` | `<=0.01%` if reasoned | `>0.01%` or unreasoned |

Default `expected_filter_rate`:

- equity/futures: `1%`
- options/futures-options: configured `option_universe.expected_drop_rate`, else
  `20%` for wide option-chain filtering
- validators-only row drop: `5%`

Reason: options chains legitimately filter more rows than daily bars, but the
budget must be explicit.

### Schema Changes

| Metric | Pass | Warn | Fail |
| --- | --- | --- | --- |
| schema_add allowlisted derived fields | any count | none | none |
| schema_add unknown fields | `0` | `1..10` | `>10` |
| schema_drop allowlisted fields | `0` | `1..3` | `>3` |
| schema_drop protected fields | `0` | not allowed | `>0` |

Schema additions must be classified:

- expected derived: pass
- diagnostic/flag: pass if naming begins with `_` or is listed
- unknown public column: warn/fail depending on count and stage

### Numeric Delta Materiality

For numeric `cell_mod`, compute:

- `max_abs_delta`
- `p95_abs_delta`
- `p99_abs_delta`
- `max_abs_pct` where denominator is nonzero
- `p99_abs_pct`
- count above materiality threshold

Default materiality thresholds:

| Column family | Warn threshold | Fail threshold |
| --- | --- | --- |
| price-like (`price`, `price_std`, `close`, `settlement`) | `p99_abs_pct > 1%` | `p99_abs_pct > 5%` without approved reason |
| return-like (`return_std`, `return_raw`) | `p99_abs_delta > 1pp` | `p99_abs_delta > 5pp` without approved reason |
| IV-like (`iv`, `iv_provided`, `iv_solved`) | `p99_abs_delta > 0.05` vol points | `p99_abs_delta > 0.20` vol points without approved reason |
| delta-like (`delta`) | `p99_abs_delta > 0.05` | `p99_abs_delta > 0.20` |
| greeks other than delta | warn only unless protected by config | fail if unreasoned protected mutation |
| dates / labels / keys | any delta | any mutation |

These materiality thresholds do not excuse unattributed protected mutations. They
only decide whether attributed high-magnitude changes need review.

## Baseline Anomaly Rules

When at least 10 prior comparable runs exist for the same `family` and
instrument class, compare current rates to historical baselines.

Comparable run key:

```text
family + provider + adapter version + stage + instrument class
```

For each rate:

- `unattributed_rate`
- `row_drop_rate`
- `cell_mod_rate`
- protected mutation rate
- top-column mutation rates

Compute:

```text
median_baseline = median(prior_rates)
mad_baseline = median(abs(prior_rates - median_baseline))
robust_z = 0.6745 * (current_rate - median_baseline) / max(mad_baseline, epsilon)
```

Decision:

| Condition | Status |
| --- | --- |
| hard gate violated | fail |
| `robust_z >= 8` and current rate is material | fail |
| `robust_z >= 5` | warn |
| current rate > `3x` median and absolute increase > 1pp | warn |

Use `epsilon = 1e-9`.

Rationale:

- Median/MAD is robust to previous outlier runs.
- Absolute increase avoids alerting on tiny relative changes from near-zero.

## Wilson Confidence Rule For Sampled Review

Some checks require human judgment. For large attributed buckets, reviewers
sample records instead of reading all rows.

Use a deterministic stratified sample and record:

- sample size `n`
- unjustified defects `x`
- defect rate `x / n`
- 95% Wilson upper confidence bound

If no defects are found, the approximate 95% upper bound is:

```text
upper_bound ~= 3 / n
```

Sampling sizes:

| Desired confidence when zero defects found | Required sample size |
| --- | --- |
| defect rate < `1%` | `n = 300` |
| defect rate < `0.5%` | `n = 600` |
| defect rate < `0.1%` | `n = 3000` |

Default sampling:

- critical protected buckets: inspect all records; if too many, fail until a
  specific migration policy exists.
- high-risk attributed buckets: sample `min(bucket_n, 600)`.
- ordinary attributed buckets: sample `min(bucket_n, 300)`.
- unknown schema additions: inspect all.
- row drops/adds: sample `min(bucket_n, 300)` per reason.

Sample failure rule:

- If any sampled protected or high-risk record is unjustified: fail.
- If ordinary bucket sample defect rate has Wilson upper bound > `1%`: warn.
- If ordinary bucket sample defect rate point estimate > `1%`: fail.

## Required Samples

Every diff summary should include sample evidence, selected deterministically:

1. all protected-column mutations;
2. all schema drops;
3. top 50 `UNATTRIBUTED` records by severity and absolute delta;
4. top 50 numeric absolute deltas per protected column family;
5. top 20 row drops per reason;
6. top 20 row adds;
7. stratified random samples by `stage + change_type + reason + column`.

Sampling must be deterministic from:

```text
sha256(run_id + stage + change_type + reason + column + key)
```

Reason: reviewers must be able to reproduce the same evidence set.

## Severity Mapping

| Condition | Severity |
| --- | --- |
| key/identity mutation | critical |
| label/target mutation | critical |
| protected `UNATTRIBUTED` cell mutation | high |
| unknown schema drop | high |
| protected schema drop | critical |
| unexplained row drop | high |
| row add in within-run stage diff | medium/high depending on stage |
| non-protected unattributed small count | medium |
| attributed materiality anomaly | medium |
| expected derived schema add | info |

Critical and high findings should create or preserve break-ledger entries.

## Default Policy YAML

Implementation may encode this policy as:

```yaml
diff_review_policy:
  version: 1
  inline_diff_max_bytes: 10485760
  large_ledger_requires_summary: true

  hard_gates:
    malformed_jsonl: fail
    missing_stage_or_change_type: degraded
    key_mutation_count: 0
    label_mutation_count: 0
    protected_unattributed_count: 0
    unexplained_row_drop_count: 0
    protected_schema_drop_count: 0

  budgets:
    non_protected_unattributed:
      warn_count: 1
      fail_count: 50
      fail_rate: 0.0001
    unknown_schema_add:
      warn_count: 1
      fail_count: 10
    row_add_stage_diff:
      pass_rate: 0.0
      fail_rate: 0.0001

  expected_filter_rate:
    equity: 0.01
    futures: 0.01
    equity_options: 0.20
    futures_options: 0.20
    validators: 0.05

  materiality:
    price_like:
      warn_p99_abs_pct: 0.01
      fail_p99_abs_pct: 0.05
    return_like:
      warn_p99_abs_delta: 0.01
      fail_p99_abs_delta: 0.05
    iv_like:
      warn_p99_abs_delta: 0.05
      fail_p99_abs_delta: 0.20
    delta_like:
      warn_p99_abs_delta: 0.05
      fail_p99_abs_delta: 0.20

  baseline_anomaly:
    min_prior_runs: 10
    warn_robust_z: 5
    fail_robust_z: 8
    warn_relative_multiplier: 3
    warn_absolute_increase: 0.01

  sampling:
    high_risk_bucket_n: 600
    ordinary_bucket_n: 300
    sample_fail_point_rate: 0.01
    sample_warn_wilson_upper: 0.01
```

## Human Review Workflow

Humans review the summary, not the raw ledger.

Required sequence:

1. Read final status: `pass`, `warn`, `fail`, or `degraded`.
2. If `fail`, inspect hard-gate findings first.
3. Inspect protected-column findings.
4. Inspect top stage/type/reason rollups.
5. Inspect materiality outliers.
6. Inspect deterministic samples.
7. Decide:
   - close as expected,
   - acknowledge known data issue,
   - escalate as pipeline bug,
   - request policy/config update,
   - request data provisioning fix.

Reviewers must not close a high/critical break only because the raw ledger is too
large to inspect manually.

## Output Contract For `diff_summary.json`

Future summary artifacts should follow this shape:

```json
{
  "policy_version": 1,
  "run_id": "wti_2025",
  "status": "warn",
  "generated_at": "2026-06-22T00:00:00Z",
  "ledger": {
    "path": "outputs/diff/wti_2025_changes.jsonl",
    "bytes": 178000000,
    "total_records": 1234567,
    "malformed_lines": 0
  },
  "rollups": {
    "by_stage": {},
    "by_change_type": {},
    "by_reason": {},
    "by_column": {}
  },
  "protected": {
    "key_mutations": [],
    "label_mutations": [],
    "protected_unattributed": []
  },
  "budgets": {
    "unattributed": {"status": "pass", "count": 0, "rate": 0.0},
    "row_drops": {"status": "warn", "count": 1000, "rate": 0.021}
  },
  "baseline": {
    "available": true,
    "comparable_runs": 12,
    "checks": []
  },
  "samples": {
    "top_unattributed": [],
    "top_numeric_delta": [],
    "row_drops_by_reason": [],
    "stratified": []
  },
  "findings": [
    {
      "severity": "medium",
      "code": "ROW_DROP_RATE_WARN",
      "message": "Explained row_drop rate exceeds expected budget by 3.1pp"
    }
  ]
}
```

## Agent Implementation Rules

1. Never ask a human to inspect a large JSONL ledger line by line.
2. Always stream large ledgers.
3. Produce rollups before detailed samples.
4. Apply hard gates before statistical budgets.
5. Treat protected-column unattributed changes as fail even if the total rate is
   tiny.
6. Keep thresholds configurable, but record the active threshold in the summary.
7. Sampling must be deterministic and reproducible.
8. If the summary cannot be produced, mark the run `degraded`, not `pass`.
9. Dashboard should show status, rollups, and samples; raw download is only for
   deep forensic work.

## Acceptance Checklist

Before accepting a run with a large diff ledger:

- `diff_summary.json` exists or equivalent API summary is available.
- The ledger parsed without malformed lines.
- Hard gates are all green.
- Protected-column counts are zero.
- Unattributed rates are inside policy budgets.
- Row add/drop rates are inside expected budgets or have explicit waiver.
- Schema changes match allowlist.
- Numeric materiality outliers are attributed and sampled.
- Baseline anomaly checks are pass or explained.
- Human-reviewed samples are recorded for every warning/high-risk bucket.
