"""Date-window parsing for the user-facing CLI.

Users should not have to compute calendar boundaries by hand. ``--window``
accepts a year, a year-month, or a year-quarter and expands to inclusive
``YYYY-MM-DD`` start/end dates. Explicit ``--from/--to`` (with ``--start/--end``
aliases) remain first-class for custom ranges.

This module is dependency-light (stdlib only) so it can be imported and tested
without pulling in the full pipeline.
"""

from __future__ import annotations

import calendar
import re
from datetime import date

__all__ = ["parse_window", "resolve_window", "WindowError"]


class WindowError(ValueError):
    """Raised when a date window is malformed or over-specified."""


_YEAR_RE = re.compile(r"^(\d{4})$")
_MONTH_RE = re.compile(r"^(\d{4})-(\d{2})$")
_QUARTER_RE = re.compile(r"^(\d{4})[Qq]([1-4])$")

_QUARTER_BOUNDS = {
    1: ((1, 1), (3, 31)),
    2: ((4, 1), (6, 30)),
    3: ((7, 1), (9, 30)),
    4: ((10, 1), (12, 31)),
}


def _iso(y: int, m: int, d: int) -> str:
    return date(y, m, d).isoformat()


def parse_window(window: str) -> tuple[str, str]:
    """Expand a window token to inclusive ``(start, end)`` ISO dates.

    Accepts:
      - ``YYYY``        -> Jan 1 .. Dec 31
      - ``YYYY-MM``     -> first .. last day of that month
      - ``YYYYQ1..Q4``  -> calendar-quarter bounds (case-insensitive ``q``)
    """
    if window is None:
        raise WindowError("window is required")
    token = window.strip()

    m = _YEAR_RE.match(token)
    if m:
        y = int(m.group(1))
        return _iso(y, 1, 1), _iso(y, 12, 31)

    m = _MONTH_RE.match(token)
    if m:
        y, mo = int(m.group(1)), int(m.group(2))
        if not 1 <= mo <= 12:
            raise WindowError(f"invalid month in window {window!r}: {mo:02d}")
        last = calendar.monthrange(y, mo)[1]
        return _iso(y, mo, 1), _iso(y, mo, last)

    m = _QUARTER_RE.match(token)
    if m:
        y, q = int(m.group(1)), int(m.group(2))
        (sm, sd), (em, ed) = _QUARTER_BOUNDS[q]
        return _iso(y, sm, sd), _iso(y, em, ed)

    raise WindowError(
        f"unrecognized --window {window!r}. "
        "Use YYYY (2024), YYYY-MM (2024-09), or YYYYQn (2024Q4), "
        "or pass explicit --from / --to dates."
    )


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _validate_iso(label: str, value: str) -> str:
    value = value.strip()
    if not _DATE_RE.match(value):
        raise WindowError(f"--{label} must be YYYY-MM-DD, got {value!r}")
    y, mo, d = (int(p) for p in value.split("-"))
    try:
        date(y, mo, d)
    except ValueError as exc:
        raise WindowError(f"--{label} is not a real date: {value!r} ({exc})")
    return value


def resolve_window(
    *,
    from_: str | None = None,
    to: str | None = None,
    start: str | None = None,
    end: str | None = None,
    window: str | None = None,
) -> tuple[str, str]:
    """Resolve a final ``(start, end)`` from CLI date inputs.

    Precedence/rules:
      - ``--start/--end`` are compatibility aliases for ``--from/--to``.
      - ``--window`` cannot be combined with any explicit date.
      - Explicit dates require both ends; a real range with start <= end.
    """
    lo = from_ if from_ is not None else start
    hi = to if to is not None else end
    has_explicit = lo is not None or hi is not None

    if window is not None:
        if has_explicit:
            raise WindowError(
                "--window cannot be combined with --from/--to (or --start/--end). "
                "Pick one: a named window OR an explicit date range."
            )
        return parse_window(window)

    if not has_explicit:
        raise WindowError(
            "no date range given. Provide --window (e.g. 2024Q4) "
            "or --from/--to (e.g. --from 2024-09-25 --to 2024-12-31)."
        )
    if lo is None or hi is None:
        missing = "--from/--start" if lo is None else "--to/--end"
        raise WindowError(f"incomplete date range: {missing} is missing")

    lo = _validate_iso("from", lo)
    hi = _validate_iso("to", hi)
    if lo > hi:
        raise WindowError(f"start {lo} is after end {hi}")
    return lo, hi
