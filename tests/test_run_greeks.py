"""Tests for run_greeks.py — Phase 2 of greek_only_engine plan."""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from run_greeks import run_greek_only, _load_input, _write_output, main


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
