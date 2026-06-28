# Option Market Checks Must Affect Run Readiness

Urgency: `P0-critical`

Status: `draft`

Source plan:

- `docs/design/data_test_measurement_criteria.md`
- `docs/design/csv_storage_bounded_context_redesign.md`

## Summary

Make option-domain checks part of run readiness. IV mismatch, call/put mismatch,
premium bounds, delta sanity, and missing underlying match must not remain hidden
inside row-level technical flags.

## Why It Matters

The dashboard can currently show generic data quality while option-specific
failures are only present as technical details. This creates false confidence in
option runs.

## Scope

In scope:

- Add `option_market_checks` or equivalent run-level summary.
- Compute status using eligible universes.
- Escalate `not_checked` as visible review risk.
- Distinguish a deliberately disabled check from a clean zero-mismatch result.
- Map technical checks to domain labels.

Out of scope:

- Final dashboard redesign styling.
- Full volatility surface implementation.

## Acceptance Criteria

- [ ] Run summary includes option-market status.
- [ ] IV mismatch rate can set run status to `needs_review` or `blocked`.
- [ ] Call/put mismatch rate can set run status to `needs_review` or `blocked`.
- [ ] Missing eligible universe produces `not_checked`, not pass.
- [ ] Disabled checks, such as `check_pcp: false`, are visible as disabled or
      not-checked risk and are not reported as `0%` clean.
- [ ] Dashboard first screen shows domain labels for option-market risk.

## Evidence Required

- `market_checks_summary.csv`
- run summary JSON
- dashboard status snapshot

## Related Checks

- Gate: `G5 Domain Market Checks`
- Gate: `G7 Dashboard Status`
- Metric: `iv_provider_model_mismatch_rate`
- Metric: `pcp_mismatch_rate`
