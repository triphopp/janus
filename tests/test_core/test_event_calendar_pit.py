"""Event-calendar point-in-time normalization (issue 015 / Phase 4)."""

import pandas as pd
import pytest

from core.event_calendar import (
    assess_event_availability,
    load_event_calendar,
    pit_event_mask,
)


def _write_events(path, rows, header="date,event,impact"):
    path.write_text(header + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return str(path)


# ── Ingestion produces available_at ───────────────────────────────────────────

def test_event_csv_ingestion_produces_available_at_from_policy(tmp_path):
    f = _write_events(tmp_path / "eia.csv",
                      ["2024-01-03,EIA Weekly,high", "2024-01-10,EIA Weekly,high"])
    cfg = {"available_at_lag": {"eia": "P5D"}}
    events = load_event_calendar(f, cfg)
    assert "available_at" in events.columns
    assert (events["availability_source"] == "inferred").all()
    # P5D lag → available 5 days after the event date.
    assert events["available_at"].iloc[0] == pd.Timestamp("2024-01-08T00:00:00Z")


def test_event_csv_provided_available_at_is_used(tmp_path):
    f = _write_events(
        tmp_path / "eia.csv",
        ["2024-01-03,EIA Weekly,high,2024-01-03T15:30:00Z"],
        header="date,event,impact,available_at",
    )
    events = load_event_calendar(f, {})
    assert events["availability_source"].iloc[0] == "provided"
    assert events["available_at"].iloc[0] == pd.Timestamp("2024-01-03T15:30:00Z")


def test_missing_event_availability_is_unknown_not_midnight(tmp_path):
    """No release policy → available_at is NaT (unknown), never midnight (would leak)."""
    f = _write_events(tmp_path / "mystery.csv", ["2024-01-03,Mystery,high"])
    events = load_event_calendar(f, {})  # no lag policy for 'mystery'
    assert events["availability_source"].iloc[0] == "unknown"
    assert events["available_at"].isna().all()


# ── Run-level availability status ─────────────────────────────────────────────

def test_missing_event_availability_is_not_checked(tmp_path):
    f = _write_events(tmp_path / "mystery.csv", ["2024-01-03,Mystery,high"])
    status = assess_event_availability({"event_calendars": [f]})
    assert status["status"] == "not_checked"
    assert status["files"][f]["availability"] == "not_checked"


def test_event_availability_checked_with_policy(tmp_path):
    f = _write_events(tmp_path / "eia.csv", ["2024-01-03,EIA Weekly,high"])
    status = assess_event_availability({"event_calendars": [f],
                                        "available_at_lag": {"eia": "P5D"}})
    assert status["status"] == "checked"


def test_no_event_calendars_is_not_applicable():
    assert assess_event_availability({})["status"] == "not_applicable"


def test_missing_event_file_is_not_checked():
    status = assess_event_availability({"event_calendars": ["/no/such/file.csv"]})
    assert status["status"] == "not_checked"


# ── PIT join invariant ────────────────────────────────────────────────────────

def test_event_feature_join_requires_available_at_before_decision(tmp_path):
    f = _write_events(tmp_path / "eia.csv", ["2024-01-03,EIA Weekly,high"])
    cfg = {"available_at_lag": {"eia": "P5D"}}      # available 2024-01-08
    events = load_event_calendar(f, cfg)

    row_dates = pd.Series([pd.Timestamp("2024-01-03").date(),
                           pd.Timestamp("2024-01-03").date()])
    # decision before vs after the event becomes available
    decision = pd.Series([pd.Timestamp("2024-01-03T12:00:00Z"),   # before P5D release
                          pd.Timestamp("2024-01-09T12:00:00Z")])  # after release
    mask = pit_event_mask(row_dates, decision, events)
    assert mask.tolist() == [False, True]


def test_event_released_after_decision_is_rejected(tmp_path):
    """An event whose release time is after the decision must not flag the row."""
    f = _write_events(
        tmp_path / "eia.csv",
        ["2024-01-03,EIA Weekly,high,2024-01-03T15:30:00Z"],
        header="date,event,impact,available_at",
    )
    events = load_event_calendar(f, {})
    row_dates = pd.Series([pd.Timestamp("2024-01-03").date()])
    decision = pd.Series([pd.Timestamp("2024-01-03T09:00:00Z")])  # before 15:30 release
    assert pit_event_mask(row_dates, decision, events).tolist() == [False]


def test_unknown_availability_never_flags(tmp_path):
    f = _write_events(tmp_path / "mystery.csv", ["2024-01-03,Mystery,high"])
    events = load_event_calendar(f, {})
    row_dates = pd.Series([pd.Timestamp("2024-01-03").date()])
    decision = pd.Series([pd.Timestamp("2024-06-01T00:00:00Z")])
    # Even a much-later decision cannot use an event with unknown availability.
    assert pit_event_mask(row_dates, decision, events).tolist() == [False]
