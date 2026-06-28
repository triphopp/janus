"""Date-window parsing tests for the user-facing CLI."""

import pytest

from cli.dates import parse_window, resolve_window, WindowError


# ── parse_window ──────────────────────────────────────────────────────────────

def test_parse_year():
    assert parse_window("2024") == ("2024-01-01", "2024-12-31")


def test_parse_month():
    assert parse_window("2024-09") == ("2024-09-01", "2024-09-30")


def test_parse_month_february_leap_year():
    assert parse_window("2024-02") == ("2024-02-01", "2024-02-29")


def test_parse_month_february_non_leap_year():
    assert parse_window("2023-02") == ("2023-02-01", "2023-02-28")


@pytest.mark.parametrize(
    "token,expected",
    [
        ("2024Q1", ("2024-01-01", "2024-03-31")),
        ("2024Q2", ("2024-04-01", "2024-06-30")),
        ("2024Q3", ("2024-07-01", "2024-09-30")),
        ("2024Q4", ("2024-10-01", "2024-12-31")),
        ("2024q4", ("2024-10-01", "2024-12-31")),
    ],
)
def test_parse_quarter(token, expected):
    assert parse_window(token) == expected


@pytest.mark.parametrize("bad", ["2024-13", "abc", "24Q4", "2024Q5", "2024-00", ""])
def test_parse_window_rejects_garbage(bad):
    with pytest.raises(WindowError):
        parse_window(bad)


# ── resolve_window ────────────────────────────────────────────────────────────

def test_resolve_explicit_from_to():
    assert resolve_window(from_="2024-09-25", to="2024-12-31") == (
        "2024-09-25",
        "2024-12-31",
    )


def test_resolve_start_end_aliases():
    assert resolve_window(start="2024-01-01", end="2024-06-30") == (
        "2024-01-01",
        "2024-06-30",
    )


def test_resolve_window_token():
    assert resolve_window(window="2024Q4") == ("2024-10-01", "2024-12-31")


def test_window_and_explicit_dates_conflict():
    with pytest.raises(WindowError, match="cannot be combined"):
        resolve_window(window="2024Q4", from_="2024-09-25")


def test_no_dates_at_all_is_error():
    with pytest.raises(WindowError, match="no date range"):
        resolve_window()


def test_incomplete_range_is_error():
    with pytest.raises(WindowError, match="missing"):
        resolve_window(from_="2024-01-01")


def test_inverted_range_is_error():
    with pytest.raises(WindowError, match="after end"):
        resolve_window(from_="2024-12-31", to="2024-01-01")


def test_bad_iso_date_is_error():
    with pytest.raises(WindowError, match="not a real date"):
        resolve_window(from_="2024-02-30", to="2024-03-01")
