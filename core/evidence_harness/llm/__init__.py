"""Evidence harness LLM sub-package.

Only import from this package when config.llm_enabled is True.
The controller gates all imports behind that flag.

Public interface:
    build_llm_client(config) -> LLMClient
    run_claim_extraction(document, case, registry, client) -> list[EvidenceClaim]
    run_query_expansion(case, queries, budget, date_start, date_end, client) -> list[str]
    run_evidence_summary(case, verdict, confidence, claims, checks, registry, client) -> dict
"""

from .router import build_llm_client, run_claim_extraction, run_query_expansion, run_evidence_summary
from .client import LLMClient, LLMJsonError, LLMRateLimitError, LLMUnavailableError
from .prompts import PROMPT_VERSION

__all__ = [
    "build_llm_client",
    "run_claim_extraction",
    "run_query_expansion",
    "run_evidence_summary",
    "LLMClient",
    "LLMJsonError",
    "LLMRateLimitError",
    "LLMUnavailableError",
    "PROMPT_VERSION",
]
