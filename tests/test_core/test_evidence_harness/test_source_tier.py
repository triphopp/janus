"""Tests for SourceTierClassifier."""

from core.evidence_harness.source_tier import SourceTierClassifier, TIER_SCORES


class TestSourceTierClassifier:
    def setup_method(self):
        self.clf = SourceTierClassifier()

    def test_eia_is_tier1(self):
        assert self.clf.classify("https://www.eia.gov/report") == "tier1_official"

    def test_sec_is_tier1(self):
        assert self.clf.classify("https://sec.gov/filing/abc") == "tier1_official"

    def test_reuters_is_tier2(self):
        assert self.clf.classify("https://www.reuters.com/markets/oil") == "tier2_reputable"

    def test_bloomberg_is_tier2(self):
        assert self.clf.classify("https://bloomberg.com/news/article") == "tier2_reputable"

    def test_reddit_is_tier4(self):
        assert self.clf.classify("https://www.reddit.com/r/finance") == "tier4_social"

    def test_unknown_domain_is_tier3(self):
        assert self.clf.classify("https://somerandomsite.io/article") == "tier3_general"

    def test_subdomain_matches_parent(self):
        assert self.clf.classify("https://data.eia.gov/feed") == "tier1_official"

    def test_www_stripped(self):
        assert self.clf.classify("https://www.reuters.com/article") == "tier2_reputable"

    def test_score_tier1_higher_than_tier2(self):
        s1 = self.clf.score("https://eia.gov/report")
        s2 = self.clf.score("https://reuters.com/article")
        assert s1 > s2

    def test_score_tier2_higher_than_tier3(self):
        s2 = self.clf.score("https://reuters.com/article")
        s3 = self.clf.score("https://randomblog.com/post")
        assert s2 > s3

    def test_custom_tiers_override_defaults(self):
        custom = {"tier1_official": ["myexchange.com"], "tier2_reputable": []}
        clf = SourceTierClassifier(source_tiers=custom)
        assert clf.classify("https://myexchange.com/notice") == "tier1_official"
        assert clf.classify("https://reuters.com/article") == "tier3_general"
