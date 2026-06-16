"""P5 tests — HTML diff viewer."""

from core.cdc import ChangeRecord, UNATTRIBUTED
from core.diff_report import write_diff_html


def _records():
    return [
        ChangeRecord("adapter", "validators", "cell_mod", key={"strike": 90.0},
                     column="price", before=90.0, after=88.0, delta=-2.0, pct=-0.0222,
                     reason="outlier_cap", reason_flag_col="_outlier_flag"),
        ChangeRecord("adapter", "validators", "cell_mod", key={"strike": 95.0},
                     column="iv", before=0.31, after=0.29, delta=-0.02, reason=UNATTRIBUTED),
    ]


def test_writes_self_contained_html(tmp_path):
    path = write_diff_html(_records(), [], "run1", out_dir=tmp_path)
    html = (tmp_path / "run1_diff.html").read_text(encoding="utf-8")
    assert path.endswith("run1_diff.html")
    assert "<script>" in html
    assert "outlier_cap" in html
    assert "UNATTRIBUTED" in html
    # data embedded inline (no external fetch)
    assert "const CHANGES=" in html


def test_script_injection_is_escaped(tmp_path):
    rec = ChangeRecord("a", "b", "cell_mod", key={"x": "</script><h1>xss"},
                       column="c", before=1, after=2)
    write_diff_html([rec], [], "xss", out_dir=tmp_path)
    html = (tmp_path / "xss_diff.html").read_text(encoding="utf-8")
    # the literal closing tag must not appear unescaped inside the embedded data
    assert "</script><h1>xss" not in html
    assert "\\u003c/script" in html


def test_breaks_rendered(tmp_path):
    breaks = [{"break_id": "BRK-1", "severity": "high", "type": "unattributed",
               "stage": "adapter->validators", "field": "iv", "before": 0.31,
               "after": 0.29, "status": "DETECTED"}]
    write_diff_html(_records(), breaks, "run2", out_dir=tmp_path)
    html = (tmp_path / "run2_diff.html").read_text(encoding="utf-8")
    assert "BRK-1" in html
