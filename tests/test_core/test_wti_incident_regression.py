"""WTI incident regression suite (issue 001 / Phase 0).

Proves the pipeline cannot again present a WTI-style option run as normal when the
option-market checks are severely unreliable, using a public-safe synthetic fixture.
"""

import numpy as np
import pandas as pd
import pytest

from core import options_quality as opt_quality
from core.run_readiness import assess_option_market_readiness
from core.row_reconciliation import (
    DOMAIN_RECONCILIATION_KEYS,
    reconcile_options_to_underlying,
    reconciliation_summary,
)
from tests.fixtures.wti_incident_fixture import (
    build_wti_incident_frame,
    incident_pipeline_cfg,
)


# ── Fixture shape ─────────────────────────────────────────────────────────────

def test_wti_fixture_contains_futures_and_option_rows():
    df = build_wti_incident_frame()
    types = df["instrument_type"].value_counts()
    assert types.get("future", 0) >= 1
    assert types.get("option", 0) >= 1
    # Many option rows per trade date, one future per date → grain separation.
    per_date = df.groupby(["as_of_date", "instrument_type"]).size().unstack(fill_value=0)
    assert (per_date["future"] == 1).all()
    assert (per_date["option"] > 1).all()


def test_wti_fixture_is_raw_schema_compliant():
    from ingestion.base import RAW_SCHEMA, validate_schema

    df = build_wti_incident_frame()
    # Should not raise — fixture is a valid ingestion → adapter input.
    validate_schema(df, RAW_SCHEMA)


# ── Row reconciliation: domain keys, never row index ──────────────────────────

def test_row_reconciliation_rejects_row_index_join():
    df = build_wti_incident_frame()
    for bad_key in ("row_index", "__index__", "line_number", "position"):
        with pytest.raises(ValueError, match="domain keys, not row index"):
            reconcile_options_to_underlying(df, keys=[bad_key])


def test_reconciliation_maps_each_option_to_its_own_session_future():
    """Domain-key join is robust to row order; a positional join would not be."""
    df = build_wti_incident_frame().sample(frac=1.0, random_state=7).reset_index(drop=True)
    recon = reconcile_options_to_underlying(df, keys=DOMAIN_RECONCILIATION_KEYS)

    assert (recon["match_status"] == "matched").all()
    # Each option's underlying equals the future settlement of its own trade date.
    expected = {pd.Timestamp("2024-09-24"): 70.00, pd.Timestamp("2024-09-25"): 71.00}
    for as_of, price in expected.items():
        rows = recon[recon["as_of_date"] == as_of]
        assert (rows["underlying_settlement_price"] == price).all()

    summary = reconciliation_summary(recon)
    assert summary["match_rate"] == 1.0
    assert summary["join"] == "domain_keys"


def test_reconciliation_flags_ambiguous_underlying():
    """Two distinct futures settlements for one identity must not silently match."""
    df = build_wti_incident_frame()
    dup = df[(df["instrument_type"] == "future")
             & (df["as_of_date"] == pd.Timestamp("2024-09-24"))].copy()
    dup["price"] = 999.0
    df = pd.concat([df, dup], ignore_index=True)

    recon = reconcile_options_to_underlying(df)
    day1 = recon[recon["as_of_date"] == pd.Timestamp("2024-09-24")]
    assert (day1["match_status"] == "ambiguous_underlying").all()


# ── Run readiness: IV/PCP mismatch must change run status ──────────────────────

def _prepare_incident():
    from adapters.futures_options_adapter import FuturesOptionsAdapter

    cfg = incident_pipeline_cfg()
    df, prepared_cfg = FuturesOptionsAdapter(cfg).prepare(build_wti_incident_frame())
    summary = opt_quality.summarize(df, prepared_cfg, prepared_cfg.get("option_quality"))
    return df, summary


def test_wti_incident_sets_run_readiness_review_or_blocked():
    _, summary = _prepare_incident()
    readiness = assess_option_market_readiness(summary)
    assert readiness["status"] in ("needs_review", "blocked"), readiness
    # The two injected failures must be the drivers.
    assert readiness["checks"]["iv_provider_model_mismatch"]["status"] != "ready"
    assert readiness["checks"]["pcp_mismatch"]["status"] != "ready"


def test_incident_iv_and_pcp_mismatches_are_present():
    _, summary = _prepare_incident()
    assert (summary["iv"]["flag_rate"] or 0) > 0
    assert (summary["pcp"]["flag_rate"] or 0) > 0


# ── Run readiness: pure-function ladder ───────────────────────────────────────

def _clean_summary(**overrides):
    summary = {
        "option_rows": 100,
        "iv": {"flag_rate": 0.0},
        "pcp": {"flag_rate": 0.0},
        "delta": {"bad_sign_count": 0},
        "premium": {"flag_rate": 0.0},
        "underlying_map": {"drop_rate": 0.0},
    }
    summary.update(overrides)
    return summary


def test_readiness_ready_when_rates_clean():
    assert assess_option_market_readiness(_clean_summary())["status"] == "ready"


def test_readiness_needs_review_band():
    # iv flag_rate between review (0.05) and block (0.20)
    summary = _clean_summary(iv={"flag_rate": 0.10})
    assert assess_option_market_readiness(summary)["status"] == "needs_review"


def test_readiness_blocked_band():
    summary = _clean_summary(iv={"flag_rate": 0.50})
    assert assess_option_market_readiness(summary)["status"] == "blocked"


def test_readiness_not_checked_is_not_pass():
    """Missing eligible universe / unrun checks surface as review, never pass."""
    no_rows = {"option_rows": 0, "iv": {}, "pcp": {}, "delta": {}}
    assert assess_option_market_readiness(no_rows)["status"] == "needs_review"

    unrun = {
        "option_rows": 100,
        "iv": {"flag_rate": None},
        "pcp": {"flag_rate": None},
        "delta": {"bad_sign_count": None},
    }
    out = assess_option_market_readiness(unrun)
    assert out["status"] == "needs_review"
    assert any("not_checked" in r for r in out["reasons"])


def test_readiness_non_option_run_is_ready():
    assert assess_option_market_readiness({})["status"] == "ready"
    assert assess_option_market_readiness(None)["status"] == "ready"
