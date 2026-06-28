# Settlement Availability Anchor Is One Day Too Early

Urgency: `P0-critical`

Status: `draft`

Source plan:

- `docs/design/data_test_measurement_criteria.md`
- `docs/design/csv_storage_bounded_context_redesign.md`

## Summary

Fix settlement `available_at` inference so it anchors to the market settlement
release time, not midnight of `as_of_date`. The current behavior can make an
end-of-day settlement row appear knowable almost a full day too early.

## Why It Matters

If `available_at` is too early, the pipeline can use a settlement value before a
strategy could have known it. That creates point-in-time leakage and can make
before/after comparisons appear shifted by one day.

## Current Failure Mode

The generic lag logic effectively does:

```text
available_at = as_of_date at 00:00 + settlement_lag
```

For a settlement row with `as_of_date = 2024-09-25` and lag `3h`, this yields:

```text
2024-09-25T03:00:00Z
```

For US markets, that timestamp is still the prior local evening and is not a
valid time to know the 2024-09-25 end-of-day settlement.

## Scope

In scope:

- Add settlement-specific availability logic.
- Anchor settlement availability to exchange/session close or configured
  settlement release time.
- Record product-specific settlement timing source references:
  - NYMEX WTI (`CL`): daily settlement period ends at `14:30 America/New_York`
    for normal active-month settlement calculation; CME also publishes daily
    settlement reports later, so same-day availability before 14:30 ET is never
    allowed and official export should use either an explicit real-time settlement
    feed policy or next-trading-session policy.
  - ICE Brent futures: daily settlement uses a two-minute period starting
    `19:28 Europe/London`, so the settlement-period end is `19:30 Europe/London`.
  - Platts Dated Brent is a different benchmark/assessment, with assessment time
    `16:30 Europe/London`; do not apply this to ICE Brent futures/options unless
    the dataset contract explicitly identifies the source as Platts Dated Brent.
- Store the calendar/time-zone assumption in manifest or run summary.
- Add tests for local-date and UTC-date boundaries.
- Block official runs when settlement availability policy is missing.

Out of scope:

- Building a full exchange calendar service.
- Vendor-specific release-time integration for every provider.

## Public-Safe Notes

- Use synthetic dates and prices in tests.
- Do not include licensed raw vendor rows.

## Acceptance Criteria

- [ ] Settlement `available_at` is never inferred from midnight unless explicitly
      approved by a data contract.
- [ ] Settlement availability is based on configured exchange timezone and
      release/settlement time.
- [ ] WTI settlement policy source records `14:30 America/New_York` as the
      settlement-period end, not a midnight anchor.
- [ ] ICE Brent futures policy source records `19:30 Europe/London` as the
      settlement-period end.
- [ ] Platts Dated Brent `16:30 Europe/London` is not reused for ICE Brent
      futures/options unless the dataset contract explicitly says the source is
      Platts Dated Brent.
- [ ] A US settlement for date `t` cannot be available before the local session
      for date `t` closes.
- [ ] UTC conversion is tested around date boundaries.
- [ ] Dashboard/run summary shows the settlement availability policy used.
- [ ] Existing one-day shift regression fails before the fix and passes after.

## Evidence Required

- availability inference tests
- PIT boundary fixture
- run summary field showing settlement availability policy
- product timing policy with source URL/reference per market

## Related Checks

- Gate: `G6 PIT + Reproducibility`
- Metric: `available_at_le_decision_time_rate`
- Metric: `settlement_available_after_session_close`
