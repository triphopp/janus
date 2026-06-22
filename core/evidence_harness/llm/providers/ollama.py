"""Ollama LLM provider — calls the local Ollama HTTP API.

Ollama exposes an OpenAI-compatible /api/chat endpoint.
Default base_url: http://localhost:11434

Requires httpx. Import-guarded — only loaded when llm_enabled=True.
"""

from __future__ import annotations

import json

from ..client import LLMJsonError, LLMRateLimitError, LLMUnavailableError
from .openai_compat import _parse_json, _inject_json_instruction


class OllamaClient:
    provider_name: str = "ollama"

    def __init__(
        self,
        *,
        base_url: str = "http://localhost:11434",
        model: str = "llama3.2",
        timeout_sec: int = 120,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout_sec = timeout_sec

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
        import httpx
        payload = {
            "model": self._model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        try:
            with httpx.Client(timeout=self._timeout_sec) as client:
                resp = client.post(f"{self._base_url}/api/chat", json=payload)
        except httpx.TimeoutException as e:
            raise LLMUnavailableError(f"Ollama request timed out: {e}") from e
        except httpx.RequestError as e:
            raise LLMUnavailableError(f"Ollama request failed — is Ollama running? {e}") from e

        if resp.status_code >= 400:
            raise LLMUnavailableError(f"Ollama error {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        try:
            return data["message"]["content"]
        except (KeyError, TypeError) as e:
            raise LLMJsonError(f"Unexpected Ollama response shape: {e}") from e

    def complete_json(
        self,
        messages: list[dict],
        schema: dict,
        *,
        max_tokens: int = 1000,
        temperature: float = 0.0,
    ) -> dict:
        import httpx
        patched = _inject_json_instruction(messages)
        payload = {
            "model": self._model,
            "messages": patched,
            "stream": False,
            "format": "json",   # Ollama native JSON mode — forces JSON output
            "think": False,     # disable thinking tokens — they consume budget before output
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        try:
            with httpx.Client(timeout=self._timeout_sec) as client:
                resp = client.post(f"{self._base_url}/api/chat", json=payload)
        except httpx.TimeoutException as e:
            raise LLMUnavailableError(f"Ollama request timed out: {e}") from e
        except httpx.RequestError as e:
            raise LLMUnavailableError(f"Ollama request failed — is Ollama running? {e}") from e

        if resp.status_code >= 400:
            raise LLMUnavailableError(f"Ollama error {resp.status_code}: {resp.text[:200]}")

        data = resp.json()
        try:
            raw = data["message"]["content"]
        except (KeyError, TypeError) as e:
            raise LLMJsonError(f"Unexpected Ollama response shape: {e}") from e
        return _parse_json(raw)
