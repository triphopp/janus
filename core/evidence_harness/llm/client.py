"""LLMClient protocol — all LLM providers implement this interface.

The controller imports this module only when config.llm_enabled is True.
No provider-specific code lives here.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """Minimal interface that every LLM provider must satisfy."""

    @property
    def provider_name(self) -> str: ...
    @property
    def model(self) -> str: ...

    def complete(
        self,
        messages: list[dict],
        *,
        max_tokens: int = 1000,
        temperature: float = 0.0,
    ) -> str:
        """Return the assistant turn as a plain string."""
        ...

    def complete_json(
        self,
        messages: list[dict],
        schema: dict,
        *,
        max_tokens: int = 1000,
        temperature: float = 0.0,
    ) -> dict:
        """Return a parsed JSON object conforming to schema.

        The provider is responsible for requesting JSON output and parsing the
        response. Raises ``LLMJsonError`` if the response cannot be parsed or
        does not match the expected top-level keys.
        """
        ...


class LLMJsonError(ValueError):
    """Raised when the LLM response cannot be parsed as valid JSON."""


class LLMRateLimitError(RuntimeError):
    """Raised when the provider signals a rate-limit condition."""


class LLMUnavailableError(RuntimeError):
    """Raised when the provider endpoint cannot be reached."""
