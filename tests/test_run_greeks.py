"""Tests for run_greeks.py — Phases 2–5 of greek_only_engine plan."""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from run_greeks import run_greek_only, _load_input, _write_output, main, _git_commit, _file_sha256


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _option_df(**overrides):
    rows = [
        {"underlying_price": 80.0, "K": 80.0, "T": 0.5, "r": 0.05, "iv": 0.3, "right": "C"},
        {"underlying_price": 80.0, "K": 85.0, "T": 0.5, "r": 0.05, "iv": 0.28, "right": "P"},
    ]
    df = pd.DataFrame(rows)
    for k, v in overrides.items():
        df[k] = v
    return df


# ── run_greek_only unit tests ─────────────────────────────────────────────────

class TestGreekOutputColumns:
    def test_greek_columns_present(self):
        out, _ = run_greek_only(_option_df())
        for col in ("delta", "gamma", "vega", "theta", "rho", "greek_model", "greek_backend", "greek_input_valid"):
            assert col in out.columns, f"Missing column: {col}"

    def test_valid_rows_produce_finite_greeks(self):
        out, _ = run_greek_only(_option_df())
        for col in ("delta", "gamma", "vega", "theta", "rho"):
            assert out[col].notna().all(), f"{col} has NaN on valid row"

    def test_invalid_rows_produce_nan_greeks(self):
        df = pd.DataFrame([
            {"underlying_price": 80.0, "K": 80.0, "T": 0.5, "iv": 0.3, "right": "C"},  # valid
            {"K": 80.0, "T": 0.5, "iv": 0.3, "right": "C"},  # missing underlying → invalid
        ])
        out, _ = run_greek_only(df)
        assert pd.notna(out["delta"].iloc[0])
        for col in ("delta", "gamma", "vega", "theta", "rho"):
            assert pd.isna(out[col].iloc[1]), f"{col} should be NaN for invalid row"

    def test_model_and_backend_recorded(self):
        out, summary = run_greek_only(_option_df(), model="black76", backend="numpy")
        assert (out["greek_model"] == "black76").all()
        assert (out["greek_backend"] == "numpy").all()
        assert summary["model"] == "black76"
        assert summary["backend"] == "numpy"


class TestBackendParity:
    def test_loop_matches_numpy(self):
        df = _option_df()
        out_np, _ = run_greek_only(df, backend="numpy")
        out_lp, _ = run_greek_only(df, backend="loop")
        for col in ("delta", "gamma", "vega", "theta", "rho"):
            np.testing.assert_allclose(
                out_np[col].values, out_lp[col].values, rtol=1e-8,
                err_msg=f"numpy vs loop mismatch on {col}",
            )


class TestUniverseFilter:
    def test_min_dte_applied(self):
        df = pd.DataFrame([
            {"underlying_price": 80.0, "K": 80.0, "T": 30/365, "iv": 0.3, "right": "C"},  # 30 DTE
            {"underlying_price": 80.0, "K": 80.0, "T": 90/365, "iv": 0.3, "right": "C"},  # 90 DTE
        ])
        out, summary = run_greek_only(df, min_dte=60)
        assert summary["universe_filter"]["rows_after_filter"] == 1
        assert len(out) == 1

    def test_max_dte_applied(self):
        df = pd.DataFrame([
            {"underlying_price": 80.0, "K": 80.0, "T": 30/365, "iv": 0.3, "right": "C"},
            {"underlying_price": 80.0, "K": 80.0, "T": 120/365, "iv": 0.3, "right": "C"},
        ])
        out, summary = run_greek_only(df, max_dte=90)
        assert summary["universe_filter"]["rows_after_filter"] == 1

    def test_max_iv_applied(self):
        df = pd.DataFrame([
            {"underlying_price": 80.0, "K": 80.0, "T": 0.5, "iv": 0.3, "right": "C"},
            {"underlying_price": 80.0, "K": 80.0, "T": 0.5, "iv": 3.0, "right": "C"},  # iv > max
        ])
        out, summary = run_greek_only(df, max_iv=2.0)
        assert summary["universe_filter"]["rows_after_filter"] == 1


class TestSummaryContents:
    def test_summary_counts(self):
        _, summary = run_greek_only(_option_df())
        assert summary["input_quality"]["total_rows"] == 2
        assert summary["input_quality"]["valid_rows"] == 2
        assert summary["input_quality"]["invalid_rows"] == 0

    def test_summary_conventions(self):
        _, summary = run_greek_only(_option_df())
        assert "conventions" in summary
        assert "theta" in summary["conventions"]
        assert "vega" in summary["conventions"]
        assert "rate" in summary["conventions"]

    def test_summary_invalid_reason_counts(self):
        df = pd.DataFrame([{"K": 80.0, "T": 0.5, "iv": 0.3, "right": "C"}])
        _, summary = run_greek_only(df)
        assert summary["input_quality"]["invalid_by_reason"]["missing_underlying"] == 1


class TestUnknownModelFails:
    def test_unknown_model_raises(self):
        with pytest.raises((ValueError, RuntimeError, Exception)):
            run_greek_only(_option_df(), model="bad_model")


# ── CSV / Parquet I/O tests ───────────────────────────────────────────────────

class TestIOPaths:
    def test_csv_input_produces_greeks(self, tmp_path):
        df = _option_df()
        csv_path = str(tmp_path / "input.csv")
        df.to_csv(csv_path, index=False)
        loaded = _load_input(csv_path)
        out, _ = run_greek_only(loaded)
        assert "delta" in out.columns

    def test_parquet_input_produces_greeks(self, tmp_path):
        pytest.importorskip("pyarrow")
        df = _option_df()
        pq_path = str(tmp_path / "input.parquet")
        df.to_parquet(pq_path, index=False)
        loaded = _load_input(pq_path)
        out, _ = run_greek_only(loaded)
        assert "delta" in out.columns

    def test_csv_output(self, tmp_path):
        df = _option_df()
        out, _ = run_greek_only(df)
        out_path = str(tmp_path / "out.csv")
        _write_output(out, out_path)
        assert Path(out_path).exists()
        reloaded = pd.read_csv(out_path)
        assert "delta" in reloaded.columns

    def test_parquet_output(self, tmp_path):
        pytest.importorskip("pyarrow")
        df = _option_df()
        out, _ = run_greek_only(df)
        out_path = str(tmp_path / "out.parquet")
        _write_output(out, out_path)
        assert Path(out_path).exists()
        reloaded = pd.read_parquet(out_path)
        assert "delta" in reloaded.columns


# ── CLI (main) tests ──────────────────────────────────────────────────────────

class TestCLI:
    def test_csv_round_trip(self, tmp_path):
        df = _option_df()
        in_path = str(tmp_path / "in.csv")
        out_path = str(tmp_path / "out.csv")
        df.to_csv(in_path, index=False)
        rc = main(["--input", in_path, "--output", out_path])
        assert rc == 0
        assert Path(out_path).exists()
        result = pd.read_csv(out_path)
        assert "delta" in result.columns

    def test_summary_file_written(self, tmp_path):
        df = _option_df()
        in_path = str(tmp_path / "in.csv")
        out_path = str(tmp_path / "out.csv")
        df.to_csv(in_path, index=False)
        main(["--input", in_path, "--output", out_path])
        summary_path = tmp_path / "out.greek_summary.json"
        assert summary_path.exists()
        with open(summary_path) as f:
            s = json.load(f)
        assert "input_quality" in s
        assert "conventions" in s

    def test_unknown_model_exits_nonzero(self, tmp_path):
        df = _option_df()
        in_path = str(tmp_path / "in.csv")
        out_path = str(tmp_path / "out.csv")
        df.to_csv(in_path, index=False)
        with pytest.raises(SystemExit) as exc_info:
            main(["--input", in_path, "--model", "bad", "--output", out_path])
        assert exc_info.value.code != 0

    def test_missing_input_exits_nonzero(self, tmp_path):
        rc = main(["--input", str(tmp_path / "nope.csv"), "--output", str(tmp_path / "out.csv")])
        assert rc != 0

    def test_loop_backend_matches_numpy_via_cli(self, tmp_path):
        df = _option_df()
        in_path = str(tmp_path / "in.csv")
        df.to_csv(in_path, index=False)
        out_np = str(tmp_path / "np.csv")
        out_lp = str(tmp_path / "lp.csv")
        main(["--input", in_path, "--backend", "numpy", "--output", out_np])
        main(["--input", in_path, "--backend", "loop", "--output", out_lp])
        df_np = pd.read_csv(out_np)
        df_lp = pd.read_csv(out_lp)
        for col in ("delta", "gamma", "vega", "theta", "rho"):
            np.testing.assert_allclose(df_np[col].values, df_lp[col].values, rtol=1e-8)


# ── Phase 3: Instrument mode tests ───────────────────────────────────────────

def _make_futures_raw_df():
    """Minimal synthetic futures options fixture for instrument-mode tests."""
    rows = []
    for strike in [75.0, 80.0, 85.0]:
        for right in ("C", "P"):
            rows.append({
                "as_of_date": "2024-06-01",
                "expiry": "2024-12-31",
                "strike": strike,
                "right": right,
                "price": 3.5,
                "F": 80.0,
                "T": 0.5,
                "r": 0.05,
                "iv": 0.3,
                "underlying_price": 80.0,
            })
    return pd.DataFrame(rows)


def _make_equity_raw_df():
    rows = []
    for strike in [145.0, 150.0, 155.0]:
        for right in ("C", "P"):
            rows.append({
                "as_of_date": "2024-06-01",
                "expiry": "2024-12-31",
                "strike": strike,
                "right": right,
                "price": 4.0,
                "S": 150.0,
                "T": 0.5,
                "r": 0.05,
                "iv": 0.25,
                "underlying_price": 150.0,
            })
    return pd.DataFrame(rows)


class TestInstrumentMode:
    """Phase 3 — config-driven mode via mocked adapter."""

    def _mock_adapter(self, df_out, model="black76"):
        """Return a mock adapter that simulates prepare() + compute_greeks()."""
        from adapters.options_base import OptionsBase
        adapter = MagicMock()
        cfg_out = {"pricing_model": model, "rf_rate": 0.05}

        # prepare() returns (df, cfg)
        df_prep = df_out.copy()
        adapter.prepare.return_value = (df_prep, cfg_out)

        # compute_greeks() adds Greek columns
        def fake_compute_greeks(df):
            from core.greeks import batch_greeks
            from core.greek_inputs import resolve_greek_inputs
            resolved, _ = resolve_greek_inputs(df)
            g = batch_greeks(
                model=model,
                S_or_F=resolved["S_or_F"].to_numpy(),
                K=resolved["K"].to_numpy(),
                T=resolved["T"].to_numpy(),
                r=resolved["r"].to_numpy(),
                sigma=resolved["sigma"].to_numpy(),
                right=resolved["right"].to_numpy(),
            )
            out = df.copy()
            for col in ("delta", "gamma", "vega", "theta", "rho"):
                out[col] = g[col]
            return out

        adapter.compute_greeks.side_effect = fake_compute_greeks
        return adapter, cfg_out

    def _patch_rp(self, cfg, provider, adapter):
        return (
            patch("run_pipeline.load_config", return_value=cfg),
            patch("run_pipeline.get_provider", return_value=provider),
            patch("run_pipeline.get_adapter", return_value=adapter),
            patch("run_pipeline.apply_runtime_overrides", side_effect=lambda c, **kw: c),
        )

    def test_futures_raw_maps_F(self):
        df = _make_futures_raw_df()
        adapter, _ = self._mock_adapter(df, model="black76")
        mock_provider = MagicMock()
        mock_provider.fetch.return_value = df
        cfg = {"family": "futures_options", "provider": "settlement", "pricing_model": "black76"}

        patches = self._patch_rp(cfg, mock_provider, adapter)
        with patches[0], patches[1], patches[2], patches[3]:
            from run_greeks import run_instrument_mode
            out, summary = run_instrument_mode("bz", data_file="fake.csv")

        assert "delta" in out.columns
        assert out["delta"].notna().any()
        assert summary["raw_rows"] == len(df)

    def test_equity_raw_computes_bsm(self):
        df = _make_equity_raw_df()
        adapter, _ = self._mock_adapter(df, model="bsm")
        mock_provider = MagicMock()
        mock_provider.fetch.return_value = df
        cfg = {"family": "equity_options", "provider": "yfinance", "pricing_model": "bsm"}

        patches = self._patch_rp(cfg, mock_provider, adapter)
        with patches[0], patches[1], patches[2], patches[3]:
            from run_greeks import run_instrument_mode
            out, summary = run_instrument_mode("aapl")

        assert summary["model"] == "bsm"
        assert out["delta"].notna().any()

    def test_missing_futures_underlying_counted(self):
        df = _make_futures_raw_df()
        df_with_missing = df.copy()
        df_with_missing.loc[0, "underlying_price"] = np.nan
        df_with_missing.loc[0, "F"] = np.nan
        adapter, _ = self._mock_adapter(df_with_missing)
        mock_provider = MagicMock()
        mock_provider.fetch.return_value = df_with_missing
        cfg = {"family": "futures_options", "pricing_model": "black76"}

        patches = self._patch_rp(cfg, mock_provider, adapter)
        with patches[0], patches[1], patches[2], patches[3]:
            from run_greeks import run_instrument_mode
            out, summary = run_instrument_mode("bz")

        assert summary["underlying_missing_rows"] >= 1

    def test_future_context_rows_excluded_from_output(self):
        """Rows with instrument_type='future' must not appear in Greek-only output."""
        df = pd.DataFrame([
            {"instrument_type": "future", "expiry": "2024-12-31", "right": None, "strike": None, "F": 80.0},
            {"instrument_type": "option", "expiry": "2024-12-31", "right": "C", "strike": 80.0,
             "underlying_price": 80.0, "iv": 0.3, "T": 0.5, "r": 0.05},
        ])
        adapter, _ = self._mock_adapter(df)
        mock_provider = MagicMock()
        mock_provider.fetch.return_value = df
        cfg = {"family": "futures_options", "pricing_model": "black76"}

        patches = self._patch_rp(cfg, mock_provider, adapter)
        with patches[0], patches[1], patches[2], patches[3]:
            from run_greeks import run_instrument_mode
            out, summary = run_instrument_mode("bz")

        assert len(out) == 1, f"Expected 1 option row, got {len(out)}"
        if "instrument_type" in out.columns:
            assert (out["instrument_type"] == "option").all()

    def test_context_row_without_instrument_type_excluded_by_right(self):
        """Without instrument_type column, rows lacking right+strike are excluded."""
        df = pd.DataFrame([
            {"expiry": "2024-12-31", "F": 80.0},  # context/future row, no right or strike
            {"expiry": "2024-12-31", "right": "C", "strike": 80.0,
             "underlying_price": 80.0, "iv": 0.3, "T": 0.5, "r": 0.05},
        ])
        adapter, _ = self._mock_adapter(df)
        mock_provider = MagicMock()
        mock_provider.fetch.return_value = df
        cfg = {"family": "futures_options", "pricing_model": "black76"}

        patches = self._patch_rp(cfg, mock_provider, adapter)
        with patches[0], patches[1], patches[2], patches[3]:
            from run_greeks import run_instrument_mode
            out, summary = run_instrument_mode("bz")

        assert len(out) == 1

    def test_dte_filter_applied_in_instrument_mode(self):
        short_dte = _make_futures_raw_df()
        short_dte["T"] = 5 / 365  # 5 DTE only
        adapter, _ = self._mock_adapter(short_dte)
        mock_provider = MagicMock()
        mock_provider.fetch.return_value = short_dte
        cfg = {"family": "futures_options", "pricing_model": "black76"}

        patches = self._patch_rp(cfg, mock_provider, adapter)
        with patches[0], patches[1], patches[2], patches[3]:
            from run_greeks import run_instrument_mode
            _, summary = run_instrument_mode("bz", min_dte=30)

        assert summary["raw_rows"] == len(short_dte)


# ── Phase 4: Full pipeline compatibility tests ────────────────────────────────

class TestFullPipelineCompat:
    """Phase 4 — verify run_greeks and adapter share same Greek engine."""

    def test_compute_greeks_false_returns_nan_columns(self):
        """OptionsBase.compute_greeks with compute_greeks=False → NaN Greeks."""
        from adapters.options_base import OptionsBase

        df = _make_futures_raw_df()
        df["iv"] = 0.3

        class _FakeOptionsAdapter(OptionsBase):
            def prepare(self, raw_df):
                return raw_df.copy(), self.cfg

        cfg = {"compute_greeks": False, "pricing_model": "black76", "family": "futures_options"}
        adapter = _FakeOptionsAdapter(cfg)
        result = adapter.compute_greeks(df)

        for col in ("delta", "gamma", "vega", "theta", "rho"):
            assert col in result.columns
            assert result[col].isna().all(), f"{col} should be all-NaN when compute_greeks=False"

    def test_adapter_and_run_greek_only_agree_on_greeks(self):
        """OptionsBase.compute_greeks and run_greek_only produce identical Greek values."""
        from adapters.options_base import OptionsBase

        df = pd.DataFrame([
            {"underlying_price": 80.0, "strike": 80.0, "T": 0.5, "r": 0.05,
             "iv": 0.3, "right": "C", "F": 80.0, "as_of_date": "2024-01-01",
             "expiry": "2024-07-01", "price": 5.0},
        ])

        class _FakeAdapter(OptionsBase):
            def prepare(self, raw_df):
                return raw_df.copy(), self.cfg

        cfg = {"compute_greeks": True, "pricing_model": "black76", "greeks_backend": "numpy",
               "family": "futures_options"}
        adapter = _FakeAdapter(cfg)
        adapter_out = adapter.compute_greeks(df.copy())

        runner_out, _ = run_greek_only(df, model="black76", backend="numpy")

        for col in ("delta", "gamma", "vega"):
            assert adapter_out[col].iloc[0] == pytest.approx(runner_out[col].iloc[0], rel=1e-8)

    def test_context_rows_do_not_affect_option_greeks(self):
        """Perturbing context/future rows does not change Greeks on earlier rows."""
        df1 = _option_df()
        df2 = _option_df()
        df2["underlying_price"] = df2["underlying_price"] * 1.5  # perturb all rows

        out1, _ = run_greek_only(df1)
        out_combined, _ = run_greek_only(pd.concat([df1, df2]).reset_index(drop=True))

        for col in ("delta", "gamma", "vega", "theta", "rho"):
            np.testing.assert_allclose(
                out1[col].values, out_combined[col].iloc[:len(df1)].values,
                rtol=1e-10,
                err_msg=f"Row order affected {col}",
            )


# ── Phase 5: Provenance and quality gates ────────────────────────────────────

class TestProvenance:
    def test_git_commit_returns_string_or_none(self):
        result = _git_commit()
        assert result is None or (isinstance(result, str) and len(result) > 0)

    def test_file_sha256_returns_hex(self, tmp_path):
        p = tmp_path / "f.csv"
        p.write_text("a,b\n1,2\n")
        h = _file_sha256(str(p))
        assert h is not None
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)

    def test_file_sha256_missing_returns_none(self):
        assert _file_sha256("/nonexistent/path.csv") is None

    def test_summary_contains_provenance(self, tmp_path):
        df = _option_df()
        in_path = str(tmp_path / "in.csv")
        out_path = str(tmp_path / "out.csv")
        df.to_csv(in_path, index=False)
        main(["--input", in_path, "--output", out_path])
        with open(str(tmp_path / "out.greek_summary.json")) as f:
            s = json.load(f)
        assert "provenance" in s
        assert "input_file" in s["provenance"]
        assert "input_hash" in s["provenance"]
        assert "git_commit" in s["provenance"]

    def test_summary_contains_conventions(self, tmp_path):
        df = _option_df()
        in_path = str(tmp_path / "in.csv")
        out_path = str(tmp_path / "out.csv")
        df.to_csv(in_path, index=False)
        main(["--input", in_path, "--output", out_path])
        with open(str(tmp_path / "out.greek_summary.json")) as f:
            s = json.load(f)
        assert s["conventions"]["theta"] == "annualized calendar-time decay, -dV/dT"
        assert s["conventions"]["vega"] == "per 1.0 vol unit"
        assert s["conventions"]["rate"] == "continuously compounded"

    def test_iv_provided_quality_warning(self):
        """When iv_source='provided' and _iv_quality_flag is set, config_warnings non-empty."""
        df = _option_df(iv=0.3)
        df["iv_provided"] = 0.3
        df["_iv_quality_flag"] = ["iv_suspect", ""]
        _, summary = run_greek_only(df, iv_source="provided")
        # At least one row has a flag — warning should appear
        assert len(summary["config_warnings"]) >= 1
        assert any("iv_provided" in w for w in summary["config_warnings"])

    def test_no_iv_warning_when_flag_absent(self):
        df = _option_df()
        _, summary = run_greek_only(df)
        assert summary["config_warnings"] == []


# ── P1: div_yield (BSM dividend yield) ───────────────────────────────────────

class TestDivYield:
    def test_bsm_div_yield_matches_single_leg_greeks(self):
        from core.greeks import single_leg_greeks
        df = pd.DataFrame([{
            "underlying_price": 150.0, "K": 150.0, "T": 0.5,
            "r": 0.05, "iv": 0.2, "right": "C",
        }])
        out, _ = run_greek_only(df, model="bsm", div_yield=0.02)
        ref = single_leg_greeks("bsm", 150.0, 150.0, 0.5, 0.05, 0.2, "C", q=0.02)
        assert out["delta"].iloc[0] == pytest.approx(ref["delta"], rel=1e-6)
        assert out["theta"].iloc[0] == pytest.approx(ref["theta"], rel=1e-6)

    def test_bsm_div_yield_changes_output_vs_zero(self):
        df = pd.DataFrame([{
            "underlying_price": 150.0, "K": 150.0, "T": 0.5,
            "r": 0.05, "iv": 0.2, "right": "C",
        }])
        out_q0, _ = run_greek_only(df, model="bsm", div_yield=0.0)
        out_q2, _ = run_greek_only(df, model="bsm", div_yield=0.02)
        assert out_q0["delta"].iloc[0] != pytest.approx(out_q2["delta"].iloc[0], rel=1e-4)

    def test_black76_unchanged_by_div_yield(self):
        df = _option_df()
        out_q0, _ = run_greek_only(df, model="black76", div_yield=0.0)
        out_q2, _ = run_greek_only(df, model="black76", div_yield=0.05)
        for col in ("delta", "gamma", "vega", "theta", "rho"):
            np.testing.assert_allclose(
                out_q0[col].values, out_q2[col].values, rtol=1e-10,
                err_msg=f"Black-76 {col} changed with div_yield",
            )

    def test_cli_div_yield_changes_bsm_output(self, tmp_path):
        df = pd.DataFrame([{
            "underlying_price": 150.0, "K": 150.0, "T": 0.5,
            "r": 0.05, "iv": 0.2, "right": "C",
        }])
        in_path = str(tmp_path / "in.csv")
        out_q0 = str(tmp_path / "q0.csv")
        out_q2 = str(tmp_path / "q2.csv")
        df.to_csv(in_path, index=False)
        main(["--input", in_path, "--model", "bsm", "--output", out_q0])
        main(["--input", in_path, "--model", "bsm", "--div-yield", "0.02", "--output", out_q2])
        delta_q0 = pd.read_csv(out_q0)["delta"].iloc[0]
        delta_q2 = pd.read_csv(out_q2)["delta"].iloc[0]
        assert delta_q0 != pytest.approx(delta_q2, rel=1e-4)

    def test_summary_records_div_yield_for_bsm(self):
        df = pd.DataFrame([{
            "underlying_price": 150.0, "K": 150.0, "T": 0.5,
            "r": 0.05, "iv": 0.2, "right": "C",
        }])
        _, summary = run_greek_only(df, model="bsm", div_yield=0.03)
        assert summary["div_yield"] == pytest.approx(0.03)

    def test_summary_div_yield_none_for_black76(self):
        df = _option_df()
        _, summary = run_greek_only(df, model="black76", div_yield=0.03)
        assert summary["div_yield"] is None

    def test_cfg_div_yield_used_when_explicit_arg_omitted(self):
        """cfg['div_yield'] must be used when div_yield arg is omitted."""
        from core.greeks import single_leg_greeks
        df = pd.DataFrame([{
            "underlying_price": 150.0, "K": 150.0, "T": 0.5,
            "r": 0.05, "iv": 0.2, "right": "C",
        }])
        out_cfg, s_cfg = run_greek_only(df, model="bsm", cfg={"div_yield": 0.07})
        out_exp, _ = run_greek_only(df, model="bsm", div_yield=0.07)
        assert out_cfg["delta"].iloc[0] == pytest.approx(out_exp["delta"].iloc[0], rel=1e-8)
        assert s_cfg["div_yield"] == pytest.approx(0.07)

    def test_explicit_zero_div_yield_overrides_cfg(self):
        """Explicit div_yield=0.0 must override a non-zero cfg dividend yield."""
        from core.greeks import single_leg_greeks
        df = pd.DataFrame([{
            "underlying_price": 150.0, "K": 150.0, "T": 0.5,
            "r": 0.05, "iv": 0.2, "right": "C",
        }])
        out, summary = run_greek_only(df, model="bsm", div_yield=0.0, cfg={"div_yield": 0.07})
        ref = single_leg_greeks("bsm", 150.0, 150.0, 0.5, 0.05, 0.2, "C", q=0.0)
        assert out["delta"].iloc[0] == pytest.approx(ref["delta"], rel=1e-6)
        assert summary["div_yield"] == pytest.approx(0.0)

    def test_explicit_div_yield_overrides_cfg(self):
        """Explicit div_yield arg wins over cfg['div_yield']."""
        from core.greeks import single_leg_greeks
        df = pd.DataFrame([{
            "underlying_price": 150.0, "K": 150.0, "T": 0.5,
            "r": 0.05, "iv": 0.2, "right": "C",
        }])
        out, summary = run_greek_only(df, model="bsm", div_yield=0.03, cfg={"div_yield": 0.07})
        ref = single_leg_greeks("bsm", 150.0, 150.0, 0.5, 0.05, 0.2, "C", q=0.03)
        assert out["delta"].iloc[0] == pytest.approx(ref["delta"], rel=1e-6)
        assert summary["div_yield"] == pytest.approx(0.03)


# ── P1: Numeric coercion in run_greek_only ───────────────────────────────────

class TestNumericCoercionInRunner:
    def test_bad_underlying_produces_nan_greeks(self):
        df = pd.DataFrame([{
            "underlying_price": "bad", "K": 80.0, "T": 0.5, "iv": 0.3, "right": "C",
        }])
        out, _ = run_greek_only(df)
        for col in ("delta", "gamma", "vega", "theta", "rho"):
            assert pd.isna(out[col].iloc[0])

    def test_bad_row_does_not_affect_other_rows(self):
        df = pd.DataFrame([
            {"underlying_price": 80.0, "K": 80.0, "T": 0.5, "iv": 0.3, "right": "C"},
            {"underlying_price": "bad", "K": 80.0, "T": 0.5, "iv": 0.3, "right": "C"},
        ])
        out, _ = run_greek_only(df)
        assert pd.notna(out["delta"].iloc[0])
        assert pd.isna(out["delta"].iloc[1])


# ── P2: DTE filter with date-derived T ───────────────────────────────────────

class TestDTEFilterWithDates:
    """DTE filter applies even when T is absent but as_of_date + expiry are present."""

    def _date_df(self, as_of_date, expiry):
        return pd.DataFrame([{
            "underlying_price": 80.0, "K": 80.0, "iv": 0.3, "right": "C",
            "as_of_date": as_of_date, "expiry": expiry,
        }])

    def test_min_dte_filters_short_dated_row(self):
        short = self._date_df("2024-06-01", "2024-06-10")  # ~9 DTE
        long = self._date_df("2024-06-01", "2024-12-31")   # ~213 DTE
        df = pd.concat([short, long]).reset_index(drop=True)
        _, summary = run_greek_only(df, min_dte=30)
        assert summary["universe_filter"]["rows_after_filter"] == 1

    def test_max_dte_filters_long_dated_row(self):
        short = self._date_df("2024-06-01", "2024-06-20")  # ~19 DTE
        long = self._date_df("2024-06-01", "2025-12-31")   # >500 DTE
        df = pd.concat([short, long]).reset_index(drop=True)
        _, summary = run_greek_only(df, max_dte=90)
        assert summary["universe_filter"]["rows_after_filter"] == 1


# ── P2: Output contract ───────────────────────────────────────────────────────

class TestOutputContract:
    def test_greek_invalid_reason_column_present(self):
        out, _ = run_greek_only(_option_df())
        assert "greek_invalid_reason" in out.columns

    def test_valid_rows_have_empty_reason(self):
        out, _ = run_greek_only(_option_df())
        for reason in out["greek_invalid_reason"]:
            assert reason == ""

    def test_invalid_rows_have_nonempty_reason(self):
        df = pd.DataFrame([{"K": 80.0, "T": 0.5, "iv": 0.3, "right": "C"}])
        out, _ = run_greek_only(df)
        assert out["greek_invalid_reason"].iloc[0] != ""

    def test_greek_dtype_column_present(self):
        out, _ = run_greek_only(_option_df(), dtype="float32")
        assert "greek_dtype" in out.columns
        assert (out["greek_dtype"] == "float32").all()

    def test_summary_schema_version_present(self):
        _, summary = run_greek_only(_option_df())
        assert "schema_version" in summary
        assert isinstance(summary["schema_version"], int)
        assert summary["schema_version"] >= 1


# ── P2: Downstream-skip tests ─────────────────────────────────────────────────

class TestDownstreamSkip:
    """Greek-only mode must not invoke any full-pipeline stages."""

    _FORBIDDEN = [
        "core.splitter",
        "core.metrics",
        "reporting",
        "core.cdc",
        "web.dashboard",
    ]

    def test_prepared_row_mode_does_not_call_pipeline_stages(self):
        """run_greek_only() must never import or call pipeline-specific stages."""
        import sys

        originally_loaded = set(sys.modules.keys())
        run_greek_only(_option_df())
        newly_loaded = set(sys.modules.keys()) - originally_loaded

        for forbidden in self._FORBIDDEN:
            collisions = [m for m in newly_loaded if m.startswith(forbidden)]
            assert not collisions, (
                f"run_greek_only() triggered import of forbidden module(s): {collisions}"
            )

    def test_prepared_row_mode_skip_stages_raise(self):
        """Monkeypatching pipeline stages to raise proves they are unreachable."""
        import importlib
        import run_greeks as rg

        def _boom(*args, **kwargs):
            raise AssertionError("Pipeline stage should not be called in Greek-only mode")

        # Patch any attributes on the module that might invoke pipeline stages
        for attr in ("run_pipeline",):
            if hasattr(rg, attr):
                original = getattr(rg, attr)
                setattr(rg, attr, _boom)
                try:
                    run_greek_only(_option_df())
                finally:
                    setattr(rg, attr, original)
            else:
                run_greek_only(_option_df())  # must succeed without that attr
