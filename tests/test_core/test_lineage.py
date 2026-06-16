"""P3 tests — lineage graph: impact analysis, auto-purge, coverage gate."""

from core import lineage as lin


GRAPH = {
    "underlying_price": {"inputs": ["price"], "op": "x", "tier": "silver", "lookback_bars": 0},
    "return_std": {"inputs": ["underlying_price"], "op": "pct_change", "tier": "silver", "lookback_bars": 1},
    "vol_regime": {"inputs": ["return_std"], "op": "causal_rank", "tier": "gold", "lookback_bars": 21},
    "vrp": {"inputs": ["iv", "return_std"], "op": "diff", "tier": "gold", "lookback_bars": 21},
}


def test_impact_of_walks_downstream():
    # price contaminates everything derived from it
    impact = lin.impact_of(GRAPH, "price")
    assert "underlying_price" in impact
    assert "return_std" in impact
    assert "vol_regime" in impact
    assert "vrp" in impact


def test_impact_of_partial():
    impact = lin.impact_of(GRAPH, "iv")
    assert impact == ["vrp"]  # iv only feeds vrp


def test_upstream_inputs_provenance():
    up = lin.upstream_inputs(GRAPH, "vol_regime")
    assert "price" in up and "return_std" in up and "underlying_price" in up


def test_max_lookback_global_and_targeted():
    assert lin.max_lookback(GRAPH) == 21
    assert lin.max_lookback(GRAPH, target="return_std") == 1  # its provenance has no 21-bar node


def test_validate_coverage_flags_undeclared_derived_col():
    cols = ["price", "underlying_price", "return_std", "vol_regime", "mystery_feature", "_outlier_flag"]
    roots = {"price"}
    rep = lin.validate_coverage(GRAPH, cols, roots)
    assert rep["ok"] is False
    assert "mystery_feature" in rep["missing"]
    assert "_outlier_flag" not in rep["missing"]  # underscore-prefixed audit flag exempt
    assert "price" not in rep["missing"]           # root exempt


def test_load_lineage_futures_options():
    graph = lin.load_lineage("futures_options")
    assert "vol_regime" in graph
    assert graph["vol_regime"]["lookback_bars"] == 21
    # purge window derived from the real shipped graph
    assert lin.max_lookback(graph) >= 21
