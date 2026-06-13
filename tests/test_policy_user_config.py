"""
tests/test_policy_user_config.py — User .repomind.yml integration with policy (V2)
"""

from shared.repomind_config import RepoMindConfig
from policy_engine.policy import PolicyEngine


class TestUserConfigGate:
    """Tests that the user's .repomind.yml acts as a pre-filter before
    operator default rules."""

    def setup_method(self):
        self.engine = PolicyEngine(policy_path="nonexistent.yaml")

    def test_no_user_config_uses_operator_rules(self):
        triage = {"failure_type": "dependency_error", "confidence": 0.85}
        plan = {"risk_level": "low"}
        result = self.engine.evaluate(triage, plan, "owner/repo")
        # Operator rule allows this
        assert result["decision"] == "allow"

    def test_user_config_default_source_does_not_block(self):
        """When .repomind.yml is missing (source=default), operator rules apply."""
        cfg = RepoMindConfig(source="default")
        triage = {"failure_type": "dependency_error", "confidence": 0.85}
        plan = {"risk_level": "low"}
        result = self.engine.evaluate(triage, plan, "owner/repo", repomind_config=cfg)
        assert result["decision"] == "allow"

    def test_user_excludes_failure_type(self):
        """User .repomind.yml with allowlist NOT containing the failure type → deny."""
        cfg = RepoMindConfig(
            source="repo",
            allowed_failure_types=["import_error"],  # NOT dependency_error
            max_risk_level="low",
            min_confidence=0.5,
        )
        triage = {"failure_type": "dependency_error", "confidence": 0.85}
        plan = {"risk_level": "low"}
        result = self.engine.evaluate(triage, plan, "owner/repo", repomind_config=cfg)
        assert result["decision"] == "deny"
        assert "user_config_failure_type" in result["rules_triggered"]
        assert "dependency_error" in result["reason"]

    def test_user_includes_failure_type_then_operator_allows(self):
        cfg = RepoMindConfig(
            source="repo",
            allowed_failure_types=["dependency_error"],
            max_risk_level="low",
            min_confidence=0.5,
        )
        triage = {"failure_type": "dependency_error", "confidence": 0.85}
        plan = {"risk_level": "low"}
        result = self.engine.evaluate(triage, plan, "owner/repo", repomind_config=cfg)
        assert result["decision"] == "allow"

    def test_user_min_confidence_stricter_than_operator(self):
        """User wants confidence >= 0.95 — operator allows 0.85."""
        cfg = RepoMindConfig(
            source="repo",
            allowed_failure_types=["dependency_error"],
            max_risk_level="low",
            min_confidence=0.95,
        )
        triage = {"failure_type": "dependency_error", "confidence": 0.85}
        plan = {"risk_level": "low"}
        result = self.engine.evaluate(triage, plan, "owner/repo", repomind_config=cfg)
        assert result["decision"] == "deny"
        assert "user_config_min_confidence" in result["rules_triggered"]

    def test_user_max_risk_stricter_than_operator(self):
        """User wants only low risk — fix is medium risk."""
        cfg = RepoMindConfig(
            source="repo",
            allowed_failure_types=["dependency_error"],
            max_risk_level="low",
            min_confidence=0.5,
        )
        triage = {"failure_type": "dependency_error", "confidence": 0.85}
        plan = {"risk_level": "medium"}
        result = self.engine.evaluate(triage, plan, "owner/repo", repomind_config=cfg)
        assert result["decision"] == "deny"
        assert "user_config_max_risk" in result["rules_triggered"]

    def test_user_empty_allowlist_does_not_filter(self):
        """Empty allowed_failure_types means user didn't restrict — operator rules apply."""
        cfg = RepoMindConfig(
            source="repo",
            allowed_failure_types=[],
            max_risk_level="high",
            min_confidence=0.0,
        )
        triage = {"failure_type": "dependency_error", "confidence": 0.85}
        plan = {"risk_level": "low"}
        result = self.engine.evaluate(triage, plan, "owner/repo", repomind_config=cfg)
        assert result["decision"] == "allow"
