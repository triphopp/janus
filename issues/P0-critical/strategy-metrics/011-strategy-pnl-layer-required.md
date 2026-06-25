# Strategy PnL Layer Required Before Trusting Strategy Metrics

Urgency: `P0-critical`

Status: `draft`

Source plan:

- `docs/design/audit_findings_pre_data_structure.md`
- `docs/design/data_structure_reading_map.md`
- `docs/reports/implementation_status_v1_4.md`

## Summary

Add an explicit strategy layer: signal, position, execution, and PnL. Until this
exists, risk metrics should be labelled as market diagnostics, not strategy
performance.

## Why It Matters

Current option/futures-option runs can score the underlying market return rather
than a real trading strategy. This can make Sharpe, Sortino, DSR, and fold
metrics look meaningful when no strategy PnL exists.

## Scope

In scope:

- Define `signal_df`, `position_df`, and `pnl_df` contracts.
- Require strategy metrics to consume `pnl_df` or explicitly label themselves as
  market diagnostics.
- Block strategy-performance dashboard claims when PnL is absent.
- Connect attribution output to real trade-level PnL columns.

Out of scope:

- Designing a final production execution simulator.
- Choosing a specific trading strategy.

## Public-Safe Notes

- Use synthetic strategy fixtures.
- Do not include private trade records.

## Acceptance Criteria

- [ ] Strategy metrics require `pnl_df` or equivalent net return series.
- [ ] Summary distinguishes market diagnostics from strategy performance.
- [ ] Dashboard does not show strategy-performance labels when PnL is absent.
- [ ] Attribution report has real PnL inputs or is marked `not_checked`.
- [ ] Tests prove underlying market returns cannot be silently used as strategy returns.

## Evidence Required

- strategy-layer fixture
- run summary field showing metrics input
- dashboard status snapshot
- tests for PnL-present and PnL-absent runs

## Related Checks

- Gate: `G6 PIT + Reproducibility`
- Gate: `G7 Dashboard Status`

