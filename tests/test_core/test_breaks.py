"""P2 tests — break lifecycle: classification, signed transitions, SoD, chain."""

import pytest

from core import breaks as bk
from core.cdc import ChangeRecord, UNATTRIBUTED


def _unattributed_mod():
    return ChangeRecord("adapter", "validators", "cell_mod", key={"strike": 90.0},
                        column="price", before=90.0, after=88.0, delta=-2.0,
                        reason=UNATTRIBUTED)


def _attributed_mod():
    return ChangeRecord("adapter", "validators", "cell_mod", key={"strike": 90.0},
                        column="price", before=90.0, after=88.0, reason="outlier_cap")


def test_classify_unattributed_is_high():
    assert bk.classify(_unattributed_mod()) == ("unattributed", "high")


def test_classify_attributed_is_not_a_break():
    assert bk.classify(_attributed_mod()) is None


def test_raise_breaks_only_unattributed():
    recs = [_unattributed_mod(), _attributed_mod()]
    breaks = bk.raise_breaks(recs, "run1")
    assert len(breaks) == 1
    assert breaks[0]["severity"] == "high"
    assert breaks[0]["status"] == bk.DETECTED


def test_legal_lifecycle_to_close():
    brk = bk.raise_breaks([_unattributed_mod()], "run1")[0]
    bk.transition(brk, bk.TRIAGED, "analyst_1", "analyst")
    bk.transition(brk, bk.ACKNOWLEDGED, "analyst_1", "analyst")
    bk.transition(brk, bk.CLOSED, "analyst_1", "analyst", reason_code="vendor_confirmed")
    assert brk["status"] == bk.CLOSED
    assert brk["signed_by"] == "analyst_1"
    assert bk.verify_chain(brk) is True


def test_illegal_transition_raises():
    brk = bk.raise_breaks([_unattributed_mod()], "run1")[0]
    with pytest.raises(bk.BreakTransitionError):
        bk.transition(brk, bk.CLOSED, "analyst_1", "analyst")  # DETECTED→CLOSED not allowed


def test_segregation_of_duties_blocks_system_ack():
    brk = bk.raise_breaks([_unattributed_mod()], "run1")[0]
    bk.transition(brk, bk.TRIAGED, "analyst_1", "analyst")
    with pytest.raises(bk.BreakTransitionError):
        bk.transition(brk, bk.ACKNOWLEDGED, "cdc_engine", "system")


def test_high_severity_close_requires_reason_code():
    brk = bk.raise_breaks([_unattributed_mod()], "run1")[0]
    bk.transition(brk, bk.TRIAGED, "analyst_1", "analyst")
    bk.transition(brk, bk.ACKNOWLEDGED, "analyst_1", "analyst")
    with pytest.raises(bk.BreakTransitionError):
        bk.transition(brk, bk.CLOSED, "analyst_1", "analyst")  # no reason_code


def test_verify_chain_detects_tamper():
    brk = bk.raise_breaks([_unattributed_mod()], "run1")[0]
    bk.transition(brk, bk.TRIAGED, "analyst_1", "analyst")
    brk["history"][1]["actor_id"] = "impostor"  # rewrite after signing
    assert bk.verify_chain(brk) is False


def test_write_breaks_jsonl(tmp_path):
    breaks = bk.raise_breaks([_unattributed_mod()], "run1")
    path = bk.write_breaks(breaks, "run1", out_dir=tmp_path)
    assert path is not None
    assert (tmp_path / "run1.jsonl").exists()
    assert bk.write_breaks([], "empty", out_dir=tmp_path) is None
