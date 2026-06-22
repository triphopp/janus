"""OpenAI-compatible LLM provider.

Works with:
  - OpenAI API (base_url="https://api.openai.com/v1")
  - Anthropic via the OpenAI-compat layer
  - Local inference servers that expose the OpenAI chat-completions endpoint

Requires httpx. Import-guarded — only loaded when llm_enabled=True.
"""

from __future__ import annotations

import json

from ..client import LLMClient, LLMJsonError, LLMRateLimitError, LLMUnavailableError


class OpenAICompatClient:
    provider_name: str = "openai_compat"

    def __init__(
        self,
        *,
        base_url: str = "https://api.openai.com/v1",
        api_key: str | None = None,
        model: str = "gpt-4o-mini",
        timeout_sec: int = 60,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key or ""
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
        response = self._call(messages, max_tokens=max_tokens, temperature=temperature)
        try:
            return response["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as e:
            raise LLMJsonError(f"Unexpected response shape: {e}") from e

    def complete_json(
        self,
        messages: list[dict],
        schema: dict,
        *,
        max_tokens: int = 1000,
        temperature: float = 0.0,
    ) -> dict:
        # Inject JSON instruction into the last user message
        patched = _inject_json_instruction(messages)
        raw = self.complete(patched, max_tokens=max_tokens, temperature=temperature)
        return _parse_json(raw)

    def _call(self, messages: list[dict], *, max_tokens: int, temperature: float) -> dict:
        import httpx
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        try:
            with httpx.Client(timeout=self._timeout_sec) as client:
                resp = client.post(
                    f"{self._base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
        except httpx.TimeoutException as e:
            raise LLMUnavailableError(f"LLM request timed out: {e}") from e
        except httpx.RequestError as e:
            raise LLMUnavailableError(f"LLM request failed: {e}") from e

        if resp.status_code == 429:
            raise LLMRateLimitError(f"Rate limited by {self._base_url}")
        if resp.status_code >= 400:
            raise LLMUnavailableError(f"LLM API error {resp.status_code}: {resp.text[:200]}")

        return resp.json()


def _inject_json_instruction(messages: list[dict]) -> list[dict]:
    patched = list(messages)
    if patched and patched[-1]["role"] == "user":
        patched[-1] = dict(patched[-1])
        patched[-1]["content"] = (
            patched[-1]["content"]
            + "\n\nRespond with valid JSON only. No markdown fences or explanation."
        )
    return patched


def _parse_json(text: str) -> dict:
    text = text.strip()
    # Strip markdown fences if the model added them despite instructions
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(
            ln for ln in lines if not ln.startswith("```")
        )
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise LLMJsonError(f"LLM did not return valid JSON: {e}\nRaw: {text[:400]}") from e
