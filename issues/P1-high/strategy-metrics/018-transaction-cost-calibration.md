# Transaction Cost Calibration

Urgency: `P1-high`

Status: `draft`

Source plan:

- `docs/reports/implementation_status_v1_4.md`

## Summary

Calibrate transaction-cost models from real or fixture bid/ask, spread, volume,
and liquidity data before using costs for trusted strategy metrics.

## Why It Matters

Placeholder cost and market-impact formulas can materially change net PnL and
strategy conclusions.

## Scope

In scope:

- Add bid/ask and volume fixtures.
- Calibrate Level 2 spread curves from data or explicit assumptions.
- Mark Level 3 market impact as placeholder until calibrated.
- Show active transaction-cost policy in reports.

Out of scope:

- Full broker/exchange execution simulator.

## Acceptance Criteria

- [ ] Cost assumptions are recorded in manifest or summary.
- [ ] Tests cover spread and volume-driven cost behavior.
- [ ] Placeholder market impact cannot be presented as calibrated.
- [ ] Net PnL reports disclose transaction-cost input.

## Evidence Required

- cost calibration fixture
- transaction-cost summary
- net PnL sensitivity artifact

## Related Checks

- Gate: `G7 Dashboard Status`

