"""Event calendar point-in-time normalization (issue 015 / Phase 4).

Events (EIA inventory, OPEC reports, earnings, …) leak future knowledge if joined
by event date alone. Each event must be knowable — carry an ``available_at`` — before
a decision can use it, and a feature join must assert ``available_at <= decision_time``.

This module centralizes:

- ``load_event_calendar`` — read an event CSV and attach ``available_at`` plus an
  ``availability_source`` (``provided`` column, ``inferred`` from a release policy,
  or ``unknown`` when no policy resolves);
- ``assess_event_availability`` — a run-level status (``checked`` /
  ``not_checked`` / ``blocked``) so a missing event-availability policy is visible
  in the dashboard instead of silently passing.

Pure-ish: ``load_event_calendar`` reads one CSV; the rest is config logic. Reuses
``ingestion.versioned_cache.infer_available_at`` for anchoring.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from ingestion.versioned_cache import infer_available_at


def _release_policy_known(event_file: str, cfg: dict) -> Optional[str]:
    """Return the lag-map key that gives this event file a release policy, else None.

    A policy is "known" when cfg declares a release lag for the file's stem (or a
    ``*_inventory`` / ``*_report`` variant) or a generic ``event`` lag.
    """
    stem = Path(event_file).stem.lower()
    lag_map = (cfg or {}).get("available_at_lag", {}) or {}
    for candidate in (stem, f"{stem}_inventory", f"{stem}_report", "event"):
        if candidate in lag_map:
            return candidate
    return None


def load_event_calendar(event_file: str, cfg: dict) -> pd.DataFrame:
    """Load one event CSV with an ``available_at`` column and availability provenance.

    Returns a frame with at least ``date``, ``available_at`` (UTC, may be NaT when
    unknown), and ``availability_source`` in {``provided``, ``inferred``, ``unknown``}.
    Raises FileNotFoundError if the file is absent (caller decides whether to skip).
    """
    events = pd.read_csv(event_file, parse_dates=["date"], comment="#")
    if "date" not in events.columns:
        raise ValueError(f"event file {event_file} missing 'date' column")
    events = events.dropna(subset=["date"]).copy()

    if "available_at" in events.columns:
        events["available_at"] = pd.to_datetime(events["available_at"], utc=True)
        events["availability_source"] = "provided"
        return events

    policy_key = _release_policy_known(event_file, cfg)
    if policy_key is not None:
        events["available_at"] = infer_available_at(events["date"], policy_key, cfg)
        events["availability_source"] = "inferred"
    else:
        # No release policy → availability is genuinely unknown. Do NOT default to
        # midnight (that would leak); leave NaT and let the gate mark not_checked.
        events["available_at"] = pd.NaT
        events["availability_source"] = "unknown"
    return events


def assess_event_availability(cfg: dict) -> dict:
    """Run-level event-availability status (issue 015).

    ``not_checked`` (never silent pass) when an event calendar has no resolvable
    availability policy; ``checked`` when every configured event file resolves a
    policy or supplies ``available_at``; ``not_applicable`` when no event calendars.
    """
    event_files = (cfg or {}).get("event_calendars", []) or []
    if not event_files:
        return {"status": "not_applicable", "files": {}}

    files: dict = {}
    worst = "checked"
    for event_file in event_files:
        try:
            events = load_event_calendar(event_file, cfg)
        except FileNotFoundError:
            files[event_file] = {"availability": "not_checked", "reason": "file_not_found"}
            worst = "not_checked"
            continue
        except Exception as exc:  # malformed file → visible, not silent
            files[event_file] = {"availability": "not_checked", "reason": str(exc)}
            worst = "not_checked"
            continue

        source = events["availability_source"].iloc[0] if len(events) else "unknown"
        if source == "unknown" or events["available_at"].isna().any():
            files[event_file] = {"availability": "not_checked",
                                 "reason": "no release-time policy; available_at unknown",
                                 "source": source}
            worst = "not_checked"
        else:
            files[event_file] = {"availability": "checked", "source": source,
                                 "rows": int(len(events))}

    return {"status": worst, "files": files}


def pit_event_mask(
    row_dates: pd.Series,
    decision_time: pd.Series,
    events: pd.DataFrame,
) -> pd.Series:
    """Boolean mask: event known on the row's date by its decision_time.

    Implements the PIT invariant ``available_at <= decision_time`` for an event
    feature join. Rows whose same-date event was not yet available (released after
    the decision) are False — the event cannot inform that decision.
    """
    if events.empty or events["available_at"].isna().all():
        return pd.Series(False, index=row_dates.index)
    available_by_date = (
        events.assign(_event_date=events["date"].dt.date)
        .dropna(subset=["available_at"])
        .groupby("_event_date")["available_at"]
        .min()
    )
    row_event_available = row_dates.map(available_by_date)
    dt = pd.to_datetime(decision_time, utc=True)
    return (row_event_available.notna() & (row_event_available <= dt)).fillna(False)
