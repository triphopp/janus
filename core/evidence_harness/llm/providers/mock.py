"""Mock LLM provider — deterministic responses for tests.

Returns pre-scripted JSON based on which task is detected from the messages.
Never calls any external service.
"""

from __future__ import annotations

import hashlib
import json


class MockLLMClient:
    provider_name: str = "mock"

    def __init__(self, model: str = "mock-v1") -> None:
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    def complete(
        self,
        messages: list[dict],
        *,
        max_tokens: int = 1000,
        temperature: float = 0.0,
    ) -> str:
        task = _detect_task(messages)
        return json.dumps(_mock_response(task, messages))

    def complete_json(
        self,
        messages: list[dict],
        schema: dict,
        *,
        max_tokens: int = 1000,
        temperature: float = 0.0,
    ) -> dict:
        task = _detect_task(messages)
        return _mock_response(task, messages)


def _detect_task(messages: list[dict]) -> str:
    for msg in messages:
        content = msg.get("content", "")
        if "Extract factual claims" in content:
            return "claim_extraction"
        if "Propose additional web search" in content:
            return "query_expansion"
        if "Write a concise evidence summary" in content:
            return "evidence_summary"
    return "unknown"


def _extract_doc_id(messages: list[dict]) -> str:
    for msg in messages:
        content = msg.get("content", "")
        if "Document ID of this document" in content:
            lines = content.splitlines()
            for i, line in enumerate(lines):
                if "Document ID of this document" in line and i + 1 < len(lines):
                    return lines[i + 1].strip()
    return "doc_mock"


def _mock_response(task: str, messages: list[dict]) -> dict:
    if task == "claim_extraction":
        doc_id = _extract_doc_id(messages)
        return {
            "claims": [
                {
                    "claim_text": "Mock claim: market event detected by LLM.",
                    "claim_type": "market_event",
                    "support_score": 0.75,
                    "contradiction_score": 0.05,
                    "confidence": "medium",
                    "document_ids": [doc_id] if doc_id else [],
                    "event_type": "macro_event",
                }
            ]
        }

    if task == "query_expansion":
        return {
            "queries": [
                {
                    "text": "mock expansion query for LLM test",
                    "rationale": "Mock rationale.",
                }
            ]
        }

    if task == "evidence_summary":
        return {
            "summary": "Mock LLM summary: evidence reviewed. Verdict is consistent with available data.",
            "key_findings": ["Key finding 1 (mock)."],
            "limitations": ["Mock limitation: LLM in test mode."],
            "cited_document_ids": [],
        }

    return {"error": "unknown task", "task": task}
