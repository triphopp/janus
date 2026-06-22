import json

from fastapi.responses import FileResponse, HTMLResponse

from web import dashboard
from web import scanner


def _patch_diff_dir(monkeypatch, tmp_path):
    diff_dir = tmp_path / "diff"
    diff_dir.mkdir()
    monkeypatch.setattr(scanner, "DIFF_DIR", diff_dir)
    return diff_dir


def test_iter_jsonl_reads_valid_lines(tmp_path):
    path = tmp_path / "x.jsonl"
    path.write_text('{"a": 1}\n\n{"b": 2}\n', encoding="utf-8")

    assert list(scanner._iter_jsonl(path)) == [{"a": 1}, {"b": 2}]


def test_large_diff_html_returns_guard_page_without_inline_render(tmp_path, monkeypatch):
    diff_dir = _patch_diff_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(dashboard, "MAX_INLINE_DIFF_BYTES", 32)
    monkeypatch.setattr(dashboard, "MAX_DIFF_REGEN_LEDGER_BYTES", 32)
    (diff_dir / "big_diff.html").write_text("x" * 128, encoding="utf-8")

    response = dashboard.serve_diff("big")

    assert isinstance(response, HTMLResponse)
    body = response.body.decode("utf-8")
    assert "Diff is too large to render inline" in body
    assert "Open first 200 records as JSON" in body


def test_small_diff_html_is_streamed_as_file_response(tmp_path, monkeypatch):
    diff_dir = _patch_diff_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(dashboard, "MAX_INLINE_DIFF_BYTES", 1024)
    (diff_dir / "small_diff.html").write_text("<html>ok</html>", encoding="utf-8")

    response = dashboard.serve_diff("small")

    assert isinstance(response, FileResponse)


def test_large_ledger_without_html_does_not_generate_inline_html(tmp_path, monkeypatch):
    diff_dir = _patch_diff_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(dashboard, "MAX_INLINE_DIFF_BYTES", 32)
    monkeypatch.setattr(dashboard, "MAX_DIFF_REGEN_LEDGER_BYTES", 32)
    (diff_dir / "ledger_changes.jsonl").write_text('{"x": 1}\n' * 20, encoding="utf-8")

    response = dashboard.serve_diff("ledger")

    assert isinstance(response, HTMLResponse)
    assert not (diff_dir / "ledger_diff.html").exists()
    assert "Diff is too large to render inline" in response.body.decode("utf-8")


def test_diff_records_endpoint_returns_bounded_page(tmp_path, monkeypatch):
    diff_dir = _patch_diff_dir(monkeypatch, tmp_path)
    rows = [
        {"stage_from": "a", "stage_to": "b", "change_type": "cell_mod", "reason": "r1", "i": 1},
        {"stage_from": "a", "stage_to": "b", "change_type": "row_add", "reason": "r2", "i": 2},
        {"stage_from": "a", "stage_to": "b", "change_type": "cell_mod", "reason": "r1", "i": 3},
    ]
    (diff_dir / "run_changes.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    page = dashboard.api_diff_records("run", offset=0, limit=2, stage=None, change_type=None, reason=None)

    assert page["returned"] == 2
    assert page["has_more"] is True
    assert page["next_offset"] == 2
    assert [row["i"] for row in page["records"]] == [1, 2]


def test_diff_records_endpoint_filters_before_paging(tmp_path, monkeypatch):
    diff_dir = _patch_diff_dir(monkeypatch, tmp_path)
    rows = [
        {"stage_from": "a", "stage_to": "b", "change_type": "cell_mod", "reason": "keep", "i": 1},
        {"stage_from": "a", "stage_to": "b", "change_type": "row_add", "reason": "drop", "i": 2},
        {"stage_from": "a", "stage_to": "b", "change_type": "cell_mod", "reason": "keep", "i": 3},
    ]
    (diff_dir / "run_changes.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    page = dashboard.api_diff_records("run", offset=0, limit=10, stage=None, change_type="cell_mod", reason="keep")

    assert page["returned"] == 2
    assert page["has_more"] is False
    assert [row["i"] for row in page["records"]] == [1, 3]


def test_diff_meta_marks_large_artifacts_as_paged(tmp_path, monkeypatch):
    diff_dir = _patch_diff_dir(monkeypatch, tmp_path)
    monkeypatch.setattr(dashboard, "MAX_INLINE_DIFF_BYTES", 32)
    monkeypatch.setattr(dashboard, "MAX_DIFF_REGEN_LEDGER_BYTES", 32)
    (diff_dir / "big_changes.jsonl").write_text('{"x": 1}\n' * 20, encoding="utf-8")

    meta = dashboard.api_diff_meta("big")

    assert meta["too_large_for_inline"] is True
    assert meta["render_mode"] == "paged_required"
