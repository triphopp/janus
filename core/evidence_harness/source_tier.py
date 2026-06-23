"""SourceTierClassifier — assigns source tiers from versioned config domain rules."""

from __future__ import annotations

from urllib.parse import urlparse


TIER_ORDER = ["tier1_official", "tier2_reputable", "tier3_general", "tier4_social", "unknown"]

_DEFAULT_TIERS: dict[str, list[str]] = {
    "tier1_official": ["sec.gov", "eia.gov", "cmegroup.com", "nasdaqtrader.com", "federalreserve.gov"],
    "tier2_reputable": ["reuters.com", "bloomberg.com", "wsj.com", "marketwatch.com", "ft.com"],
    "tier4_social": ["reddit.com", "x.com", "twitter.com"],
}

TIER_SCORES = {
    "tier0_local": 1.0,
    "tier1_official": 0.9,
    "tier2_reputable": 0.7,
    "tier3_general": 0.4,
    "tier4_social": 0.1,
    "unknown": 0.2,
}


class SourceTierClassifier:
    """Classify a URL or domain into a source tier based on config rules."""

    def __init__(self, source_tiers: dict[str, list[str]] | None = None) -> None:
        self._tiers = source_tiers if source_tiers is not None else _DEFAULT_TIERS

    def classify(self, url: str) -> str:
        domain = _extract_domain(url)
        for tier in TIER_ORDER:
            domains = self._tiers.get(tier, [])
            if any(_domain_matches(domain, d) for d in domains):
                return tier
        return "tier3_general"

    def score(self, url: str) -> float:
        return TIER_SCORES.get(self.classify(url), 0.2)


def _extract_domain(url: str) -> str:
    try:
        netloc = urlparse(url).netloc
        return netloc.lower().lstrip("www.")
    except Exception:
        return url.lower()


def _domain_matches(domain: str, rule: str) -> bool:
    domain = domain.lstrip("www.")
    rule = rule.lstrip("www.")
    return domain == rule or domain.endswith("." + rule)
