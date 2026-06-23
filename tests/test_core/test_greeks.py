"""Greeks tests — closed-form vs bump, net greeks for spreads (section 12)."""

from math import erf

import numpy as np
import pytest
from core.greeks import (
    single_leg_greeks, net_greeks, bump_greeks, Leg,
    batch_greeks, _resolve_greeks_backend, _cupy_available, _cuda_device_count,
)
from core.pricing import price

_CUDA_AVAILABLE = _cupy_available()
_skip_no_gpu = pytest.mark.skipif(not _CUDA_AVAILABLE, reason="No GPU / CuPy not installed")


# ---------------------------------------------------------------------------
# Shared deterministic grid for batch tests
# ---------------------------------------------------------------------------

def _make_grid(model="black76") -> dict:
    """Deterministic grid: 2 rights × 3 moneyness × 2 tenors × 2 vols = 24 rows."""
    rights = ["C", "P"]
    strikes = [70.0, 80.0, 90.0]       # OTM-put, ATM, OTM-call
    tenors = [0.25, 1.0]
    vols = [0.2, 0.4]
    r = 0.05
    q = 0.02 if model in ("bs", "bsm") else 0.0
    F = 80.0

    rows = [
        {"S_or_F": F, "K": K, "T": T, "r": r, "sigma": s, "right": ri, "q": q}
        for ri in rights
        for K in strikes
        for T in tenors
        for s in vols
    ]
    return {"rows": rows, "model": model}


def _grid_arrays(grid: dict, dtype="float64") -> tuple:
    rows = grid["rows"]
    keys = ("S_or_F", "K", "T", "r", "sigma")
    arrs = {k: np.array([r[k] for r in rows], dtype=dtype) for k in keys}
    right = np.array([r["right"] for r in rows])
    q = rows[0]["q"]
    return arrs["S_or_F"], arrs["K"], arrs["T"], arrs["r"], arrs["sigma"], right, q


class TestBatchGreeksLevel1:
    """Level 1: vectorized batch Greeks parity against single_leg_greeks."""

    def test_batch_black76_matches_single_leg_grid(self):
        grid = _make_grid("black76")
        S, K, T, r, sigma, right, q = _grid_arrays(grid)
        result = batch_greeks("black76", S, K, T, r, sigma, right, q=q, backend="numpy")

        for i, row in enumerate(grid["rows"]):
            expected = single_leg_greeks(
                "black76", row["S_or_F"], row["K"], row["T"], row["r"], row["sigma"], row["right"], q=row["q"]
            )
            for g in ("delta", "gamma", "vega", "theta", "rho"):
                assert result[g][i] == pytest.approx(expected[g], rel=1e-10, abs=1e-12), (
                    f"black76 {g} mismatch at row {i}: {row}"
                )

    def test_batch_bsm_matches_single_leg_grid(self):
        grid = _make_grid("bsm")
        S, K, T, r, sigma, right, q = _grid_arrays(grid)
        result = batch_greeks("bsm", S, K, T, r, sigma, right, q=q, backend="numpy")

        for i, row in enumerate(grid["rows"]):
            expected = single_leg_greeks(
                "bsm", row["S_or_F"], row["K"], row["T"], row["r"], row["sigma"], row["right"], q=row["q"]
            )
            for g in ("delta", "gamma", "vega", "theta", "rho"):
                assert result[g][i] == pytest.approx(expected[g], rel=1e-10, abs=1e-12), (
                    f"bsm {g} mismatch at row {i}: {row}"
                )

    def test_batch_greeks_invalid_rows_return_nan(self):
        invalid_cases = [
            # T <= 0
            (80.0, 80.0, 0.0, 0.05, 0.2, "C"),
            (80.0, 80.0, -1.0, 0.05, 0.2, "P"),
            # missing T
            (80.0, 80.0, np.nan, 0.05, 0.2, "C"),
            # sigma <= 0
            (80.0, 80.0, 0.5, 0.05, 0.0, "C"),
            (80.0, 80.0, 0.5, 0.05, -0.1, "P"),
            # missing sigma
            (80.0, 80.0, 0.5, 0.05, np.nan, "C"),
            # K <= 0
            (80.0, 0.0, 0.5, 0.05, 0.2, "C"),
            (80.0, -1.0, 0.5, 0.05, 0.2, "P"),
            # S_or_F <= 0
            (0.0, 80.0, 0.5, 0.05, 0.2, "C"),
            (-5.0, 80.0, 0.5, 0.05, 0.2, "P"),
            # invalid right
            (80.0, 80.0, 0.5, 0.05, 0.2, "X"),
            (80.0, 80.0, 0.5, 0.05, 0.2, ""),
        ]
        S = np.array([c[0] for c in invalid_cases])
        K = np.array([c[1] for c in invalid_cases])
        T = np.array([c[2] for c in invalid_cases])
        r = np.full(len(invalid_cases), 0.05)
        sigma = np.array([c[4] for c in invalid_cases])
        right = np.array([c[5] for c in invalid_cases])

        result = batch_greeks("black76", S, K, T, r, sigma, right, backend="numpy")
        for g in ("delta", "gamma", "vega", "theta", "rho"):
            assert np.all(np.isnan(result[g])), f"{g} should be NaN for all invalid rows"

    def test_batch_greeks_unknown_model_raises(self):
        with pytest.raises(ValueError, match="Unknown pricing model"):
            batch_greeks("unknown_model", [80.0], [80.0], [0.5], [0.05], [0.2], ["C"])

    def test_batch_greeks_row_order_invariant(self):
        grid = _make_grid("black76")
        S, K, T, r, sigma, right, q = _grid_arrays(grid)
        baseline = batch_greeks("black76", S, K, T, r, sigma, right, q=q)

        rng = np.random.default_rng(42)
        perm = rng.permutation(len(S))
        shuffled = batch_greeks("black76", S[perm], K[perm], T[perm], r[perm], sigma[perm], right[perm], q=q)

        inv_perm = np.argsort(perm)
        for g in ("delta", "gamma", "vega", "theta", "rho"):
            np.testing.assert_allclose(shuffled[g][inv_perm], baseline[g], rtol=1e-12, atol=1e-14,
                                        err_msg=f"{g} row-order invariant failed")

    def test_batch_greeks_single_row_perturbation_is_local(self):
        """Changing one row must only affect that row's Greeks."""
        grid = _make_grid("black76")
        S, K, T, r, sigma, right, q = _grid_arrays(grid)
        baseline = batch_greeks("black76", S, K, T, r, sigma, right, q=q)

        S2 = S.copy()
        S2[0] = S2[0] * 1.05  # perturb row 0 underlying by 5%

        perturbed = batch_greeks("black76", S2, K, T, r, sigma, right, q=q)

        for g in ("delta", "gamma", "vega", "theta", "rho"):
            # Row 0 must change
            assert perturbed[g][0] != pytest.approx(baseline[g][0], rel=1e-6), (
                f"{g} row 0 did not change after perturbation"
            )
            # All other rows must be identical
            np.testing.assert_array_equal(
                perturbed[g][1:], baseline[g][1:],
                err_msg=f"{g}: non-perturbed rows changed (cross-row leakage)"
            )


class TestBatchGreeksLevel2:
    """Level 2: backend dispatch, chunking, config."""

    def test_loop_backend_matches_numpy_backend(self):
        grid = _make_grid("black76")
        S, K, T, r, sigma, right, q = _grid_arrays(grid)
        res_np = batch_greeks("black76", S, K, T, r, sigma, right, q=q, backend="numpy")
        res_lp = batch_greeks("black76", S, K, T, r, sigma, right, q=q, backend="loop")

        for g in ("delta", "gamma", "vega", "theta", "rho"):
            np.testing.assert_allclose(res_lp[g], res_np[g], rtol=1e-10, atol=1e-12,
                                        err_msg=f"{g}: loop vs numpy mismatch")

    def test_auto_backend_resolves_to_numpy_without_cuda(self):
        grid = _make_grid("black76")
        S, K, T, r, sigma, right, q = _grid_arrays(grid)
        res_auto = batch_greeks("black76", S, K, T, r, sigma, right, q=q, backend="auto")
        res_np = batch_greeks("black76", S, K, T, r, sigma, right, q=q, backend="numpy")

        for g in ("delta", "gamma", "vega", "theta", "rho"):
            np.testing.assert_array_equal(res_auto[g], res_np[g])

    def test_batch_size_chunking_matches_unchunked(self):
        grid = _make_grid("black76")
        S, K, T, r, sigma, right, q = _grid_arrays(grid)
        unchunked = batch_greeks("black76", S, K, T, r, sigma, right, q=q)
        chunked = batch_greeks("black76", S, K, T, r, sigma, right, q=q, batch_size=2)

        for g in ("delta", "gamma", "vega", "theta", "rho"):
            np.testing.assert_allclose(chunked[g], unchunked[g], rtol=1e-14, atol=1e-15,
                                        err_msg=f"{g}: chunked vs unchunked mismatch")

    def test_cuda_backend_not_available_before_cuda_level(self):
        with pytest.raises((RuntimeError, ImportError)):
            batch_greeks("black76", [80.0], [80.0], [0.5], [0.05], [0.2], ["C"], backend="cuda")

    def test_resolve_greeks_backend_loop(self):
        assert _resolve_greeks_backend("loop", 100) == "loop"

    def test_resolve_greeks_backend_numpy(self):
        assert _resolve_greeks_backend("numpy", 100) == "numpy"

    def test_resolve_greeks_backend_auto_returns_numpy(self):
        assert _resolve_greeks_backend("auto", 100) == "numpy"

    def test_resolve_greeks_backend_unknown_raises(self):
        with pytest.raises(ValueError, match="Unknown greeks backend"):
            _resolve_greeks_backend("magic", 100)

    def test_backend_selection_does_not_change_outputs(self):
        grid = _make_grid("black76")
        S, K, T, r, sigma, right, q = _grid_arrays(grid)
        results = {
            b: batch_greeks("black76", S, K, T, r, sigma, right, q=q, backend=b)
            for b in ("loop", "numpy", "auto")
        }
        ref = results["numpy"]
        for b in ("loop", "auto"):
            for g in ("delta", "gamma", "vega", "theta", "rho"):
                np.testing.assert_allclose(results[b][g], ref[g], rtol=1e-10, atol=1e-12,
                                            err_msg=f"{g}: backend={b!r} differs from numpy")


class TestClosedFormGreeks:
    """Closed-form Greek calculations."""

    def test_delta_call_vs_put(self):
        """Call delta + |Put delta| ≈ e^(-rT) for ATM (Black-76)."""
        g_call = single_leg_greeks("black76", 80, 80, 0.5, 0.05, 0.3, "C")
        g_put = single_leg_greeks("black76", 80, 80, 0.5, 0.05, 0.3, "P")
        # delta_C - delta_P ≈ e^(-rT)
        total = g_call["delta"] + abs(g_put["delta"])
        expected = np.exp(-0.05 * 0.5)
        assert total == pytest.approx(expected, rel=1e-6)

    def test_gamma_same_for_call_and_put(self):
        """Gamma must be identical for calls and puts at same strike."""
        g_call = single_leg_greeks("black76", 80, 80, 0.5, 0.05, 0.3, "C")
        g_put = single_leg_greeks("black76", 80, 80, 0.5, 0.05, 0.3, "P")
        assert g_call["gamma"] == pytest.approx(g_put["gamma"], rel=1e-10)

    def test_vega_positive(self):
        """Vega is always positive for both calls and puts."""
        g = single_leg_greeks("black76", 80, 80, 0.5, 0.05, 0.3, "C")
        assert g["vega"] > 0

    def test_theta_negative_for_long(self):
        """Theta is negative for long options (decay)."""
        g = single_leg_greeks("black76", 80, 80, 0.5, 0.05, 0.3, "C")
        assert g["theta"] < 0  # long option loses value over time

    def test_black76_vs_bsm_delta_differs(self):
        """Black-76 delta must use futures-options d1 and discounting."""
        F, K, T, r, sigma = 80, 80, 0.5, 0.05, 0.3
        g_76 = single_leg_greeks("black76", F, K, T, r, sigma, "C")
        g_bs = single_leg_greeks("bs", F, K, T, r, sigma, "C")

        d1_black76 = (np.log(F / K) + 0.5 * sigma ** 2 * T) / (sigma * np.sqrt(T))
        expected_black76 = np.exp(-r * T) * 0.5 * (1 + erf(d1_black76 / np.sqrt(2)))

        assert g_76["delta"] == pytest.approx(expected_black76, rel=1e-10)
        assert g_76["delta"] != pytest.approx(g_bs["delta"], abs=1e-3)


class TestBumpVsAnalytic:
    """Numerical bump must match closed-form within tolerance."""

    def test_delta_bump_match(self):
        """Finite diff delta ≈ analytic delta."""
        F, K, T, r, sigma = 80, 80, 0.5, 0.05, 0.3
        analytic = single_leg_greeks("black76", F, K, T, r, sigma, "C")
        bump = bump_greeks("black76", price, F, K, T, r, sigma, "C")
        assert analytic["delta"] == pytest.approx(bump["delta"], abs=1e-4)

    def test_vega_bump_match(self):
        F, K, T, r, sigma = 80, 80, 0.5, 0.05, 0.3
        analytic = single_leg_greeks("black76", F, K, T, r, sigma, "C")
        bump = bump_greeks("black76", price, F, K, T, r, sigma, "C")
        assert analytic["vega"] == pytest.approx(bump["vega"], abs=1e-4)

    def test_gamma_bump_match(self):
        F, K, T, r, sigma = 80, 80, 0.5, 0.05, 0.3
        analytic = single_leg_greeks("black76", F, K, T, r, sigma, "C")
        bump = bump_greeks("black76", price, F, K, T, r, sigma, "C")
        assert analytic["gamma"] == pytest.approx(bump["gamma"], abs=1e-4)


class TestNetGreeksSpread:
    """Net Greeks for multi-leg spreads."""

    def test_net_zero_for_opposite_legs(self):
        """Long call + short call at same K, T → net Greeks = 0."""
        leg1 = Leg(qty=+1, right="C", K=80, expiry=None, F_at_t=80, iv_at_t=0.3, T_at_t=0.5)
        leg2 = Leg(qty=-1, right="C", K=80, expiry=None, F_at_t=80, iv_at_t=0.3, T_at_t=0.5)
        cfg = {"pricing_model": "black76", "vega_bucket_cutoff": 60, "vega_beta": 0.7}
        ng = net_greeks([leg1, leg2], cfg)
        for k in ["delta", "gamma", "theta"]:
            assert ng[k] == pytest.approx(0.0, abs=1e-12)

    def test_calendar_spread_vega_term_risk(self):
        """Calendar spread: vega_total may be 0 but vega_term_risk ≠ 0."""
        leg_short = Leg(qty=-1, right="C", K=80, expiry=None, F_at_t=80, iv_at_t=0.3, T_at_t=30/365)  # 30 DTE
        leg_long  = Leg(qty=+1, right="C", K=80, expiry=None, F_at_t=80, iv_at_t=0.25, T_at_t=90/365)  # 90 DTE
        cfg = {"pricing_model": "black76", "vega_bucket_cutoff": 60, "vega_beta": 0.7}
        ng = net_greeks([leg_short, leg_long], cfg)
        # vega_term_risk must differ from vega_total
        # vega_total ≈ 0 (offsetting), but term risk ≠ 0 (non-parallel)
        # Since short-term IV moves more (beta=0.7), term risk captures this
        assert ng["vega_short_term"] != 0.0
        assert ng["vega_long_term"] != 0.0
        # Vega buckets should be different
        assert abs(ng["vega_short_term"]) > 0

    def test_calendar_vega_buckets(self):
        """Short DTE → short_term bucket; Long DTE → long_term bucket."""
        leg_short = Leg(qty=+1, right="P", K=80, expiry=None, F_at_t=80, iv_at_t=0.3, T_at_t=10/365)
        leg_long  = Leg(qty=+1, right="P", K=80, expiry=None, F_at_t=80, iv_at_t=0.3, T_at_t=120/365)
        cfg = {"pricing_model": "black76", "vega_bucket_cutoff": 60, "vega_beta": 0.7}
        ng = net_greeks([leg_short, leg_long], cfg)
        # short vega in short_term, long vega in long_term
        assert ng["vega_short_term"] > 0
        assert ng["vega_long_term"] > 0


class TestBatchGreeksLevel3:
    """Level 3: CUDA backend — CPU-safe monkeypatch tests + optional GPU tests."""

    # ------------------------------------------------------------------
    # CPU-safe tests (run on any machine)
    # ------------------------------------------------------------------

    def test_cuda_backend_missing_dependency_has_clear_error(self, monkeypatch):
        """backend='cuda' raises RuntimeError with actionable message when CuPy absent."""
        monkeypatch.setattr("core.greeks._cupy_available", lambda: False)
        with pytest.raises(RuntimeError, match="CuPy"):
            batch_greeks("black76", [80.0], [80.0], [0.5], [0.05], [0.2], ["C"], backend="cuda")

    def test_auto_backend_falls_back_to_numpy_when_cuda_unavailable(self, monkeypatch):
        """backend='auto' produces numpy-identical output when CUDA is unavailable."""
        monkeypatch.setattr("core.greeks._cupy_available", lambda: False)
        grid = _make_grid("black76")
        S, K, T, r, sigma, right, q = _grid_arrays(grid)
        res_auto = batch_greeks("black76", S, K, T, r, sigma, right, q=q, backend="auto")
        res_np = batch_greeks("black76", S, K, T, r, sigma, right, q=q, backend="numpy")
        for g in ("delta", "gamma", "vega", "theta", "rho"):
            np.testing.assert_array_equal(res_auto[g], res_np[g])

    def test_auto_backend_uses_numpy_below_cuda_min_rows(self, monkeypatch):
        """backend='auto' stays on numpy when n_rows < cuda_min_rows even if GPU exists."""
        monkeypatch.setattr("core.greeks._cupy_available", lambda: True)
        # 1 row, min_rows=1_000_000 → must resolve to numpy
        resolved = _resolve_greeks_backend("auto", n_rows=1, cuda_min_rows=1_000_000)
        assert resolved == "numpy"

    def test_auto_backend_selects_cuda_above_min_rows(self, monkeypatch):
        """backend='auto' resolves to cuda when GPU available and n_rows >= cuda_min_rows."""
        monkeypatch.setattr("core.greeks._cupy_available", lambda: True)
        resolved = _resolve_greeks_backend("auto", n_rows=1_000_000, cuda_min_rows=1_000_000)
        assert resolved == "cuda"

    def test_cuda_backend_explicit_resolves_when_available(self, monkeypatch):
        """backend='cuda' resolves to 'cuda' when CuPy is available."""
        monkeypatch.setattr("core.greeks._cupy_available", lambda: True)
        resolved = _resolve_greeks_backend("cuda", n_rows=100)
        assert resolved == "cuda"

    def test_cuda_device_count_returns_int(self):
        """_cuda_device_count() always returns an int (0 when no GPU)."""
        count = _cuda_device_count()
        assert isinstance(count, int)
        assert count >= 0

    # ------------------------------------------------------------------
    # Optional GPU tests (skipped when CuPy / GPU not present)
    # ------------------------------------------------------------------

    @_skip_no_gpu
    def test_cuda_backend_matches_numpy_backend(self):
        grid = _make_grid("black76")
        S, K, T, r, sigma, right, q = _grid_arrays(grid)
        res_cuda = batch_greeks("black76", S, K, T, r, sigma, right, q=q, backend="cuda")
        res_np   = batch_greeks("black76", S, K, T, r, sigma, right, q=q, backend="numpy")
        for g in ("delta", "gamma", "vega", "theta", "rho"):
            np.testing.assert_allclose(res_cuda[g], res_np[g], rtol=1e-8, atol=1e-10,
                                        err_msg=f"{g}: cuda vs numpy mismatch")

    @_skip_no_gpu
    def test_cuda_chunking_matches_numpy(self):
        grid = _make_grid("black76")
        S, K, T, r, sigma, right, q = _grid_arrays(grid)
        res_cuda = batch_greeks("black76", S, K, T, r, sigma, right, q=q,
                                backend="cuda", batch_size=4)
        res_np   = batch_greeks("black76", S, K, T, r, sigma, right, q=q, backend="numpy")
        for g in ("delta", "gamma", "vega", "theta", "rho"):
            np.testing.assert_allclose(res_cuda[g], res_np[g], rtol=1e-8, atol=1e-10,
                                        err_msg=f"{g}: cuda chunked vs numpy mismatch")

    @_skip_no_gpu
    def test_cuda_preserves_invalid_row_nan_behavior(self):
        """CUDA backend returns NaN in same positions as numpy for invalid rows."""
        S     = np.array([80.0, 0.0, 80.0, 80.0])
        K     = np.array([80.0, 80.0, 80.0, -1.0])
        T     = np.array([0.5, 0.5, -1.0, 0.5])
        r     = np.full(4, 0.05)
        sigma = np.array([0.2, 0.2, 0.2, 0.2])
        right = np.array(["C", "C", "C", "C"])

        res_cuda = batch_greeks("black76", S, K, T, r, sigma, right, backend="cuda")
        res_np   = batch_greeks("black76", S, K, T, r, sigma, right, backend="numpy")

        for g in ("delta", "gamma", "vega", "theta", "rho"):
            cuda_nan = np.isnan(res_cuda[g])
            np_nan   = np.isnan(res_np[g])
            np.testing.assert_array_equal(cuda_nan, np_nan,
                                           err_msg=f"{g}: NaN positions differ between cuda and numpy")
