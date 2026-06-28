# Exchange Calendar Coverage

Urgency: `P0-critical`

Status: `draft`

Source plan:

- `docs/design/data_test_measurement_criteria.md`
- `core/coverage.py`

## Summary

Replace generic weekday coverage expectations with instrument/provider trading
calendars where available.

## Why It Matters

The current coverage gate counts expected days using generic business days. This
can overstate or understate missing data when exchange holidays, special closes,
or provider-specific schedules apply.

## Scope

In scope:

- Add calendar source selection per family/instrument.
- Record the calendar used in coverage artifacts.
- Keep generic business days as fallback with visible `calendar=generic`.
- Update coverage dashboard label to show which calendar was used.

Out of scope:

- Real-time holiday maintenance service.

## Acceptance Criteria

- [ ] Coverage report records `calendar_id`.
- [ ] Futures/options coverage can use exchange/provider calendar.
- [ ] Generic business-day fallback is visible and not silently treated as exchange truth.
- [ ] Tests cover a date range where exchange calendar differs from generic weekdays.

## Evidence Required

- coverage report
- calendar fixture
- dashboard status snapshot

## Related Checks

- Gate: `G5 Domain Market Checks`
- Metric: `missing_market_day_rate`
