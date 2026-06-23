"""Tests for deterministic ID generation."""

from core.evidence_harness.ids import (
    stable_id,
    case_id,
    query_id,
    source_id,
    document_id,
    canonical_json,
)


class TestStableId:
    def test_same_input_same_output(self):
        payload = {"a": 1, "b": "x"}
        assert stable_id("pfx", payload) == stable_id("pfx", payload)

    def test_key_order_does_not_matter(self):
        a = stable_id("pfx", {"a": 1, "b": 2})
        b = stable_id("pfx", {"b": 2, "a": 1})
        assert a == b

    def test_prefix_changes_id(self):
        payload = {"x": 1}
        assert stable_id("foo", payload) != stable_id("bar", payload)

    def test_format_is_prefix_underscore_hex(self):
        id_ = stable_id("case", {"x": 1})
        assert id_.startswith("case_")
        hex_part = id_[len("case_"):]
        assert len(hex_part) == 16
        int(hex_part, 16)  # must be valid hex


class TestCaseId:
    def test_stable_across_calls(self):
        a = case_id("run_1", "return_outlier", "2024-01-25", "return_std", "equity", "TSLA")
        b = case_id("run_1", "return_outlier", "2024-01-25", "return_std", "equity", "TSLA")
        assert a == b

    def test_different_symbol_different_id(self):
        a = case_id("run_1", "return_outlier", "2024-01-25", "return_std", "equity", "TSLA")
        b = case_id("run_1", "return_outlier", "2024-01-25", "return_std", "equity", "AAPL")
        assert a != b

    def test_different_date_different_id(self):
        a = case_id("run_1", "return_outlier", "2024-01-25", "return_std")
        b = case_id("run_1", "return_outlier", "2024-01-26", "return_std")
        assert a != b

    def test_starts_with_case(self):
        cid = case_id("r", "s", "2024-01-01", "m")
        assert cid.startswith("case_")


class TestQueryId:
    def test_stable(self):
        a = query_id("case_abc", "TSLA stock move 2024-01-25", "2024-01-23", "2024-01-27", [])
        b = query_id("case_abc", "TSLA stock move 2024-01-25", "2024-01-23", "2024-01-27", [])
        assert a == b

    def test_domain_order_does_not_matter(self):
        a = query_id("c", "text", None, None, ["reuters.com", "eia.gov"])
        b = query_id("c", "text", None, None, ["eia.gov", "reuters.com"])
        assert a == b

    def test_different_text_different_id(self):
        a = query_id("c", "WTI inventory 2024", None, None, [])
        b = query_id("c", "WTI price move 2024", None, None, [])
        assert a != b


class TestSourceAndDocumentId:
    def test_source_id_stable(self):
        a = source_id("https://example.com/article", "sha256:abc123")
        b = source_id("https://example.com/article", "sha256:abc123")
        assert a == b

    def test_source_id_starts_with_src(self):
        assert source_id("https://x.com", "sha256:000").startswith("src_")

    def test_document_id_stable(self):
        a = document_id("src_abc", "sha256:def", "extract.v1")
        b = document_id("src_abc", "sha256:def", "extract.v1")
        assert a == b

    def test_document_id_starts_with_doc(self):
        assert document_id("src_x", "sha256:y", "v1").startswith("doc_")
