"""Preset, override, resolve, and plan-assembly tests."""

import pytest

from cli import presets, plan, registry, resolve


WTI_HEADER = (
    "TRADE DATE|HUB|PRODUCT|STRIP|CONTRACT|CONTRACT TYPE|STRIKE|"
    "SETTLEMENT PRICE|NET CHANGE|EXPIRATION DATE|PRODUCT_ID|"
    "OPTION_VOLATILITY|DELTA_FACTOR"
)


def _write_wti(path):
    path.write_text(
        WTI_HEADER + "\n"
        "9/25/2024|WTI|WTI Crude Futures|11/1/2024|T|C|35.0|34.69|-1.87|10/17/2024|425|58.26|1.0\n",
        encoding="utf-8",
    )
    return path


# ── presets ──────────────────────────────────────────────────────────────────

def test_official_preset_requires_pinned():
    cfg = presets.apply_run_preset({"family": "futures_options"}, "official")
    assert cfg["require_fixed_data_version"] is True
    assert cfg["reproducible"] is True
    assert cfg["preset"] == "official"


def test_diagnostic_preset_allows_unpinned():
    cfg = presets.apply_run_preset({"family": "equity"}, "diagnostic")
    assert cfg["require_fixed_data_version"] is False
    assert cfg["reproducible"] is False


def test_export_preset_enables_greeks():
    cfg = presets.apply_run_preset({"family": "futures_options"}, "export")
    assert cfg["compute_greeks"] is True
    assert cfg["pricing"]["compute_greeks"] is True


def test_unknown_preset_errors():
    with pytest.raises(presets.PresetError, match="unknown preset"):
        presets.apply_run_preset({}, "ultra")


def test_universe_near_term_sets_max_dte():
    cfg = presets.apply_universe_preset({}, "near-term")
    assert cfg["option_universe"]["max_dte_days"] == 90
    assert cfg["universe"] == "near-term"


def test_universe_liquid_sets_delta_band():
    cfg = presets.apply_universe_preset({}, "liquid")
    assert cfg["option_universe"]["delta_band"] == {
        "min_abs_delta": 0.10,
        "max_abs_delta": 0.90,
    }


def test_unknown_universe_errors():
    with pytest.raises(presets.PresetError, match="unknown universe"):
        presets.apply_universe_preset({}, "spicy")


def test_custom_universe_from_config():
    cfg = {
        "universe_presets": {
            "myband": {"option_universe": {"max_dte_days": 45}}
        }
    }
    out = presets.apply_universe_preset(cfg, "custom:myband")
    assert out["option_universe"]["max_dte_days"] == 45


# ── overrides ────────────────────────────────────────────────────────────────

def test_parse_override_coerces_types():
    assert presets.parse_override("pricing.compute_greeks=true") == (
        "pricing.compute_greeks",
        True,
    )
    assert presets.parse_override("cv.n_folds=4") == ("cv.n_folds", 4)
    assert presets.parse_override("x.y=0.5") == ("x.y", 0.5)
    assert presets.parse_override("a.b=hello") == ("a.b", "hello")


def test_apply_overrides_sets_nested_and_records():
    cfg, recorded = presets.apply_overrides({}, ["pricing.compute_greeks=true"])
    assert cfg["pricing"]["compute_greeks"] is True
    assert cfg["compute_greeks"] is True
    assert recorded == {"pricing.compute_greeks": True}
    assert cfg["advanced_overrides"] == {"pricing.compute_greeks": True}


def test_apply_overrides_syncs_pricing_backend_aliases():
    cfg, recorded = presets.apply_overrides(
        {"compute_greeks": False, "greeks_backend": "numpy"},
        ["pricing.compute_greeks=true", "pricing.greeks_backend=cuda"],
    )

    assert cfg["pricing"]["compute_greeks"] is True
    assert cfg["compute_greeks"] is True
    assert cfg["pricing"]["greeks_backend"] == "cuda"
    assert cfg["greeks_backend"] == "cuda"
    assert recorded == {
        "pricing.compute_greeks": True,
        "pricing.greeks_backend": "cuda",
    }


def test_apply_override_bad_token():
    with pytest.raises(presets.PresetError):
        presets.apply_overrides({}, ["noequalsign"])


# ── resolve ──────────────────────────────────────────────────────────────────

def test_resolve_unknown_symbol_synthesizes_equity():
    cfg = resolve.resolve_profile("ZZZZ")
    assert cfg["family"] == "equity"
    assert cfg["symbol"]["ticker"] == "ZZZZ"
    assert resolve.is_file_backed(cfg) is False


# ── plan ─────────────────────────────────────────────────────────────────────

def test_plan_equity_official_fails_unreproducible(tmp_path):
    reg = tmp_path / "r.yaml"
    p = plan.build_plan(
        "NVDA", start="2024-01-01", end="2024-06-30",
        preset="official", registry_path=reg,
    )
    assert p.file_backed is False
    assert p.ready is False
    g = p.guards[0]
    assert g["status"] == "fail"
    assert "diagnostic" in (g["next_action"] or "")


def test_plan_equity_diagnostic_is_ready(tmp_path):
    reg = tmp_path / "r.yaml"
    p = plan.build_plan(
        "NVDA", start="2024-01-01", end="2024-06-30",
        preset="diagnostic", registry_path=reg,
    )
    assert p.ready is True
    assert p.reproducible is False


def test_plan_futures_official_without_source_fails(tmp_path):
    reg = tmp_path / "r.yaml"
    p = plan.build_plan(
        "bz", start="2024-01-01", end="2024-06-30",
        preset="official", registry_path=reg,
    )
    assert p.file_backed is True
    assert p.ready is False
    assert "import" in (p.guards[0]["next_action"] or "")


def test_plan_futures_official_with_pinned_source_ready(tmp_path):
    data = _write_wti(tmp_path / "BZ.csv")
    reg = tmp_path / "r.yaml"
    registry.import_source("BZ", data, registry_path=reg)

    p = plan.build_plan(
        "bz", start="2024-01-01", end="2024-06-30",
        preset="official", universe="near-term", registry_path=reg,
    )
    assert p.ready is True
    assert p.reproducible is True
    assert p.cfg["data_file"] == str(data.resolve())
    assert p.cfg["data_file_sha256"] == registry.sha256_file(data)
    assert p.cfg["option_universe"]["max_dte_days"] == 90


def test_plan_detects_hash_drift(tmp_path):
    data = _write_wti(tmp_path / "BZ.csv")
    reg = tmp_path / "r.yaml"
    registry.import_source("BZ", data, registry_path=reg)
    data.write_text("TRADE DATE|HUB\n9/25/2024|WTI\n", encoding="utf-8")  # mutate

    p = plan.build_plan(
        "bz", start="2024-01-01", end="2024-06-30",
        preset="official", registry_path=reg,
    )
    assert p.ready is False
    assert "changed" in p.guards[0]["detail"]
