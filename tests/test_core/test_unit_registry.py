"""Unit registry + IV scaling guards (issue 002)."""

import numpy as np
import pandas as pd
import pytest

from core import unit_registry as ur
from core.unit_registry import UnknownUnitError


def test_iv_percent_raw_converts_to_decimal():
    out = ur.normalize_iv(pd.Series([58.26110, 29.14]), "percent")
    assert out["scale_factor"] == 0.01
    assert out["canonical_unit"] == "decimal"
    np.testing.assert_allclose(out["canonical"].to_numpy(), [0.582611, 0.2914])
    assert out["smoke"]["status"] == "pass"


def test_decimal_unit_is_identity():
    out = ur.normalize_iv(pd.Series([0.30, 0.45]), "decimal")
    assert out["scale_factor"] == 1.0
    np.testing.assert_allclose(out["canonical"].to_numpy(), [0.30, 0.45])


def test_unknown_iv_unit_raises():
    with pytest.raises(UnknownUnitError):
        ur.iv_scale_factor("furlongs")
    with pytest.raises(UnknownUnitError):
        ur.iv_scale_factor(None)


def test_percent_iv_treated_as_decimal_blocks():
    """58.26 left as 'decimal' → canonical ~58 → smoke fail (percent-as-decimal)."""
    out = ur.normalize_iv(pd.Series([58.26, 29.14, 41.0]), "decimal")
    assert out["smoke"]["status"] == "fail"
    assert "percent" in out["smoke"]["reason"]


def test_decimal_iv_divided_twice_blocks():
    """0.30 decimal divided by 100 again → 0.003 → smoke fail (divided twice)."""
    out = ur.normalize_iv(pd.Series([0.0030, 0.0045, 0.0028]), "decimal")
    assert out["smoke"]["status"] == "fail"
    assert "divided" in out["smoke"]["reason"]


def test_unit_assumption_record_for_manifest():
    rec = ur.iv_unit_assumption("percent", raw_iv=pd.Series([30.0, 45.0]))
    assert rec["known"] is True
    assert rec["raw_unit"] == "percent"
    assert rec["canonical_unit"] == "decimal"
    assert rec["scale_factor"] == 0.01
    assert rec["smoke"]["status"] == "pass"


def test_unit_assumption_record_unknown_unit_is_not_known():
    rec = ur.iv_unit_assumption("furlongs")
    assert rec["known"] is False
    assert rec["scale_factor"] is None
