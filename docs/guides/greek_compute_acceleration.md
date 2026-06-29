# Greek Computation Acceleration

## Overview

`core/greeks.py` provides two paths for computing closed-form option Greeks:

| Path | Function | Use case |
|------|----------|----------|
| Scalar | `single_leg_greeks()` | One row at a time; legacy callers, tests, `net_greeks()` |
| Batch | `batch_greeks()` | DataFrames of any size; default production path |

`OptionsBase.compute_greeks()` uses `batch_greeks()` internally — no `iterrows()`.

---

## `batch_greeks()` API

```python
from core.greeks import batch_greeks

result = batch_greeks(
    model="black76",   # "black76" | "bs" | "bsm"
    S_or_F=S,          # array-like: underlying or futures price
    K=K,               # array-like: strike
    T=T,               # array-like: time to expiry (years)
    r=r,               # array-like: risk-free rate
    sigma=sigma,       # array-like: implied vol
    right=right,       # array-like: "C" or "P"
    q=0.0,             # scalar: dividend yield (bsm only)
    backend="numpy",   # "numpy" | "loop" | "auto"
    batch_size=None,   # int | None: chunk size for memory control
    dtype="float64",   # output dtype
)
# result: {"delta": np.ndarray, "gamma": ..., "vega": ..., "theta": ..., "rho": ...}
```

### Invalid rows → NaN

Rows that fail any condition below receive `NaN` in **all** Greek outputs:

- `T <= 0` or `T` is NaN / inf
- `S_or_F <= 0` or non-finite
- `K <= 0` or non-finite
- `sigma <= 0` or non-finite
- `r` is non-finite
- `right` is not `"C"` or `"P"`

This matches the adapter's existing behavior: non-option rows and expired/missing-IV rows stay `NaN`.

---

## Backends

| Backend | Speed | Dependency | Notes |
|---------|-------|------------|-------|
| `numpy` | fast (vectorized) | NumPy + SciPy | default; uses `scipy.special.ndtr` for CDF |
| `loop` | slow (scalar loop) | none | emergency fallback; calls `single_leg_greeks()` per row |
| `auto` | numpy, or cuda for large arrays | none (CuPy optional) | switches to CUDA when CuPy+device exist **and** `n_rows >= cuda_min_rows` |
| `cuda` | fastest above breakeven | CuPy + GPU | computes on GPU, or raises actionable `RuntimeError` if CuPy/device absent |

### Choosing a backend

```python
# Default — fastest on CPU, no GPU required
batch_greeks(..., backend="numpy")

# Scalar loop — useful for debugging or comparing against scalar results
batch_greeks(..., backend="loop")

# Auto — safe choice; will upgrade to CUDA automatically when Level 3 lands
batch_greeks(..., backend="auto")
```

### Chunking large arrays

Use `batch_size` to limit peak memory when processing millions of rows:

```python
batch_greeks(..., batch_size=250_000)
```

Chunked and unchunked results are identical within floating-point tolerance.

---

## Adapter config

`OptionsBase.compute_greeks()` reads backend settings from `cfg`:

```yaml
pricing:
  compute_greeks: true          # false → return NaN columns immediately
  greeks_backend: numpy         # numpy | loop | auto
  greeks_batch_size: 250000     # chunk size (null = no chunking)
  greeks_dtype: float64
```

Root-level aliases also work for backwards compatibility:

```yaml
compute_greeks: true
greeks_backend: numpy
greeks_batch_size: 250000
greeks_dtype: float64
```

---

## Performance

Measured on CPU (single thread, `black76`, random inputs):

| Rows | Scalar `iterrows()` | `backend="numpy"` | Speedup |
|-----:|--------------------:|------------------:|--------:|
| 1 000 | 11 ms | 0.3 ms | ~40× |
| 10 000 | 108 ms | 2 ms | ~53× |
| 100 000 | ~1 100 ms | 29 ms | ~38× |
| 500 000 | ~5 500 ms | 147 ms | ~37× |

CUDA (`backend="cuda"`) requires a local GPU + CuPy. Below the breakeven row count,
host<->device transfer dominates and NumPy wins; above it, GPU compute amortizes the copy.
Generate a fresh, hardware-specific benchmark (never hand-edited) with:

```bash
python tools/benchmark_greeks.py
# → docs/benchmarks/greek_backend_benchmark.md
```

Measured on an RTX 3080 (best-of-5, `black76`): CUDA breakeven ≈ **100k rows**
(numpy 13.0 ms vs cuda 6.2 ms); at 5M rows cuda is ~3.2× faster than numpy.
See `docs/benchmarks/greek_backend_benchmark.md` for the full sweep.

---

## Correctness guarantees

Every Greek output from `batch_greeks(..., backend="numpy")` matches `single_leg_greeks()` within:

- `rtol=1e-10`, `atol=1e-12` for delta, gamma, vega, rho
- `atol=1e-14` for theta (machine-epsilon rounding only)

Verified by `tests/test_core/test_greeks.py::TestBatchGreeksLevel1`.

### Leakage guards

Greek computation is **row-wise only**. No cross-row dependency, no lookahead.
Tests that enforce this:

| Test | Property proved |
|------|----------------|
| `test_batch_greeks_single_row_perturbation_is_local` | Changing one row changes only that row's output |
| `test_batch_greeks_row_order_invariant` | Shuffling input rows → same outputs after reordering |
| `test_compute_greeks_does_not_use_future_context_rows` | Future/non-option rows cannot influence option Greeks |
| `test_compute_greeks_does_not_use_later_option_rows` | Later `as_of_date` rows cannot influence earlier Greeks |

---

## Formulas

### Black-76 (futures options)

```
d1 = (ln(F/K) + 0.5·σ²·T) / (σ·√T)
d2 = d1 − σ·√T
disc = e^(−r·T)

delta  = disc · Φ(d1)          [call]   / −disc · Φ(−d1)         [put]
gamma  = disc · φ(d1) / (F·σ·√T)
vega   = disc · F · φ(d1) · √T
theta  = −F·φ(d1)·σ/(2√T) − r·K·disc·Φ(d2) + r·F·disc·Φ(d1)    [call]
rho    = −T·disc·(F·Φ(d1) − K·Φ(d2))                             [call]
```

### BSM / BS (equity options)

```
d1 = (ln(S/K) + (r − q + 0.5·σ²)·T) / (σ·√T)
d2 = d1 − σ·√T
disc_r = e^(−r·T),  disc_q = e^(−q·T)

delta  = disc_q · Φ(d1)        [call]   / −disc_q · Φ(−d1)       [put]
gamma  = disc_q · φ(d1) / (S·σ·√T)
vega   = disc_q · S · φ(d1) · √T
theta  = −S·φ(d1)·σ·disc_q/(2√T) − r·K·disc_r·Φ(d2) + q·S·disc_q·Φ(d1)  [call]
rho    = K·T·disc_r·Φ(d2)                                         [call]
```

Φ = standard normal CDF (`scipy.special.ndtr`), φ = standard normal PDF.

---

## Level 3 — CUDA (implemented, verified on RTX 3080)

`_batch_greeks_cuda()`:

1. Builds the validity mask on CPU (avoids GPU string ops), transfers only valid rows
2. Computes using CuPy equivalents of all NumPy operations (`cupyx.scipy.special.ndtr`,
   erfc fallback)
3. Transfers result back to CPU as NumPy arrays; invalid rows stay NaN
4. Chunks at 250k rows/chunk by default to bound VRAM

Correctness is verified against both the NumPy backend and an **independent** scipy/QuantLib
reference (`tests/test_core/test_greek_external_reference.py::TestCudaVsReference`).

Install separately — CuPy is **not** a base dependency:

```text
# requirements-cuda.txt
cupy-cuda12x    # match your local CUDA runtime
```

`backend="auto"` will switch to CUDA only when:
- CuPy imports successfully and a device exists
- `n_rows >= greeks_cuda_min_rows` (code default `100_000`, set from the RTX 3080
  breakeven; raise it via config for slower GPUs or fast many-core CPUs)

Non-GPU machines will continue to use `numpy` with no config change needed.
