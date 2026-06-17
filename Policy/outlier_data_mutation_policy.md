# Janus Outlier Data Mutation Policy

## Purpose

This policy defines how Janus agents must handle outliers, data corrections, and
return-series transformations. The goal is to preserve auditability and avoid
silently turning real market events into cleaner but misleading data.

## Core Principles

1. Raw observations are immutable.
   - Never overwrite vendor-provided or reconstructed raw values.
   - Keep `return_raw` as the first-class evidence column.

2. Tag-only is the default action for statistical outliers.
   - A value that is unusual is not automatically wrong.
   - Statistical detection may add flags, thresholds, and reasons, but must not
     change the canonical return by default.

3. Derived series must be explicit.
   - Clipped, winsorized, corrected, or model-safe values must live in clearly
     named derived columns such as `return_winsorized` or `return_model_safe`.
   - Reports and dashboards must label which series is used for each metric.

4. No silent mutation.
   - Any changed value must be reproducible, reason-coded, and visible in CDC or
     diff reports.
   - Agents must not make diffs look "clean" by replacing large moves without a
     documented policy and evidence trail.

5. Market events are data, not noise.
   - If an independent provider agrees, or a corporate/news event explains the
     move, keep the raw return and tag it as a genuine event.

6. Performance metrics must disclose their input.
   - Metrics computed from raw market returns are diagnostics, not strategy
     performance.
   - Metrics computed from clipped or winsorized returns must disclose the
     derived input column and should be accompanied by raw-vs-derived
     sensitivity where practical.

## Definitions

- `raw`: The original observation or return created directly from vendor prices.
- `canonical`: The default PIT-safe standardized series used for audit and
  diagnostics. For equity returns this should usually be the same value as raw,
  after schema normalization, not statistical clipping.
- `derived`: A transformed series created for modeling or sensitivity analysis.
- `mutation`: Any operation that changes a value in-place rather than creating a
  new derived field.
- `outlier tag`: Metadata that marks a value as unusual without changing it.
- `quarantine`: A state where a row is excluded from default analysis because it
  is structurally invalid or has strong evidence of being erroneous.

## Required Default Behavior

For equity return outliers, the default policy is:

```yaml
outlier_policy:
  return_action: tag_only
```

Under `tag_only`:

- `return_raw` must remain unchanged.
- `return_std` must remain the canonical PIT-safe return and must not be clipped
  only because it is statistically extreme.
- Outlier metadata should be added.
- CDC/diff output should show added flags or diagnostics, not a value
  replacement for `return_std`.

## Allowed Actions

| Situation | Default action | Allowed derived action | Notes |
| --- | --- | --- | --- |
| Statistical return outlier only | Tag only | Optional `return_winsorized` | Do not overwrite `return_std`. |
| Independent provider confirms the move | Keep raw, tag genuine event | Optional sensitivity series | Treat as real market information. |
| Corporate/news event explains the move | Keep raw, tag genuine event | Optional sensitivity series | Evidence should be linked or summarized. |
| Provider conflict, no resolution | Tag `needs_review` | Optional model-safe series | Avoid declaring the new value correct. |
| Structurally impossible value | Quarantine | Corrected derived value if evidence exists | Example: negative adjusted close. |
| Proven bad tick or vendor error | Preserve raw, create corrected derived value | `return_corrected` or `return_model_safe` | Requires evidence and reason code. |

## Required Columns

Agents implementing outlier handling should prefer these names:

- `return_raw`: raw return evidence.
- `return_std`: canonical PIT-safe return, not statistically clipped by default.
- `return_winsorized`: optional clipped/winsorized derived return.
- `return_model_safe`: optional derived return selected for modeling.
- `_return_outlier_flag`: boolean outlier marker.
- `_return_outlier_reason`: reason code, such as `pit_mad_outlier`.
- `_return_outlier_policy`: applied policy, such as `tag_only`.
- `_return_outlier_evidence`: short evidence note or reference.
- `_return_clip_lower`: lower threshold used by the detector.
- `_return_clip_upper`: upper threshold used by the detector.
- `_return_validation_status`: `unreviewed`, `provider_confirmed`,
  `event_confirmed`, `needs_review`, or `quarantined`.

Existing project names may be retained during migration, but reports should map
them into this vocabulary.

## Config Contract

Agents may implement these policy modes:

```yaml
outlier_policy:
  return_action: tag_only
  derived_return_col: return_winsorized
  metrics_return_col: return_std
```

Allowed `return_action` values:

- `tag_only`: detect and tag outliers without changing canonical returns.
- `derive_winsorized`: keep canonical returns unchanged and create a derived
  winsorized column.
- `mutate_after_validation`: allowed only for non-raw, non-canonical derived
  columns after provider/event validation. This mode must not overwrite
  `return_raw`.

`metrics_return_col` must be shown in final reports and dashboards. If a metric
uses a derived column, the report should say so clearly.

## Agent Implementation Rules

1. Do not overwrite `return_raw`.
2. Do not overwrite `return_std` for statistical outliers under the default
   policy.
3. If asked to "clip returns", implement it as a derived column unless the user
   explicitly approves a different policy.
4. If changing a value, preserve the before value, after value, reason, policy,
   thresholds, and evidence.
5. Update tests so they assert raw preservation and correct tagging.
6. Dashboard/report changes must show the active policy and the return column
   used for metrics.
7. Diffs should help reviewers decide whether the new value is justified; a diff
   alone is not evidence that the replacement is correct.

## Migration Notes For Current Janus

Current equity preparation already has useful building blocks:

- `return_raw` exists.
- outlier flags and CDC stages exist.
- cross-provider validation exists.
- report/dashboard surfaces already expose metric input context.

The main policy gap is that the current return clipping path can overwrite
`return_std` with clipped values. That behavior should be migrated to:

1. Detect thresholds and tag rows.
2. Keep `return_raw` and `return_std` unchanged under `tag_only`.
3. Optionally create `return_winsorized` under `derive_winsorized`.
4. Let metrics select `metrics_return_col` explicitly.
5. Show raw-vs-derived differences as sensitivity, not as hidden correction.

## Review Checklist

Before accepting any outlier-handling change, verify:

- Raw values remain present and unchanged.
- Canonical values are not silently clipped.
- Derived columns have explicit names.
- Reason codes and policy modes are recorded.
- Reports and dashboards disclose the return column used.
- Tests cover both tag-only and derived-series behavior.
- Large moves that match provider or event evidence are kept as real market
  events.
