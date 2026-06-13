"""
tests/test_repomind_config.py — `.repomind.yml` parser tests (V2)
"""

from shared.repomind_config import (
    RepoMindConfig,
    SAFE_DEFAULT_CONFIG,
    parse_config,
    parse_yaml_text,
    generate_sample_yml,
)


class TestParseConfig:
    def test_empty_dict_returns_safe_defaults(self):
        cfg = parse_config({})
        assert cfg.mode == "dry_run"
        assert cfg.hitl_required is True
        assert cfg.max_risk_level == "low"

    def test_invalid_root_returns_fallback(self):
        cfg = parse_config("not a dict")  # type: ignore[arg-type]
        assert cfg.source == "fallback"

    def test_full_config_parsed(self):
        raw = {
            "mode": "auto_fix",
            "hitl_required": False,
            "policy": {
                "allowed_failure_types": ["dependency_error", "test_failure"],
                "max_risk_level": "medium",
                "min_confidence": 0.85,
            },
            "notifications": {
                "slack_webhook": "https://hooks.slack.com/x",
                "email": "team@example.com",
            },
        }
        cfg = parse_config(raw)
        assert cfg.mode == "auto_fix"
        assert cfg.hitl_required is False
        assert "dependency_error" in cfg.allowed_failure_types
        assert "test_failure" in cfg.allowed_failure_types
        assert cfg.max_risk_level == "medium"
        assert cfg.min_confidence == 0.85
        assert cfg.slack_webhook == "https://hooks.slack.com/x"
        assert cfg.email == "team@example.com"

    def test_invalid_mode_falls_back_to_dry_run(self):
        cfg = parse_config({"mode": "yolo"})
        assert cfg.mode == "dry_run"

    def test_invalid_risk_level_falls_back_to_low(self):
        cfg = parse_config({"policy": {"max_risk_level": "extreme"}})
        assert cfg.max_risk_level == "low"

    def test_min_confidence_clamped_to_unit_interval(self):
        cfg = parse_config({"policy": {"min_confidence": 5.0}})
        assert cfg.min_confidence == 1.0

        cfg2 = parse_config({"policy": {"min_confidence": -1.0}})
        assert cfg2.min_confidence == 0.0

    def test_min_confidence_non_numeric_default(self):
        cfg = parse_config({"policy": {"min_confidence": "abc"}})
        assert cfg.min_confidence == 0.7

    def test_disabled_helpers(self):
        cfg = parse_config({"mode": "disabled"})
        assert cfg.is_disabled is True
        assert cfg.is_dry_run is False
        assert cfg.is_auto_fix is False

    def test_to_dict_round_trip(self):
        raw = {"mode": "auto_fix", "hitl_required": False}
        cfg = parse_config(raw)
        d = cfg.to_dict()
        assert d["mode"] == "auto_fix"
        assert d["hitl_required"] is False
        assert "policy" in d
        assert "_source" in d


class TestParseYamlText:
    def test_valid_yaml(self):
        text = """
mode: auto_fix
hitl_required: true
policy:
  allowed_failure_types:
    - dependency_error
  max_risk_level: low
  min_confidence: 0.8
"""
        cfg = parse_yaml_text(text)
        assert cfg.mode == "auto_fix"
        assert cfg.allowed_failure_types == ["dependency_error"]

    def test_malformed_yaml_returns_fallback(self):
        cfg = parse_yaml_text("mode: : invalid: yaml: ::")
        assert cfg.source == "fallback"
        # Defaults still safe
        assert cfg.mode == "dry_run"

    def test_empty_yaml_returns_defaults(self):
        cfg = parse_yaml_text("")
        assert cfg.mode == "dry_run"
        assert cfg.hitl_required is True


class TestSampleYml:
    def test_sample_is_valid_yaml(self):
        sample = generate_sample_yml()
        cfg = parse_yaml_text(sample)
        # Sample defaults must be SAFE
        assert cfg.mode == "dry_run"
        assert cfg.hitl_required is True

    def test_sample_contains_expected_sections(self):
        sample = generate_sample_yml()
        assert "mode:" in sample
        assert "hitl_required:" in sample
        assert "policy:" in sample
        assert "allowed_failure_types:" in sample


class TestSafeDefaults:
    def test_safe_default_constants_match_dataclass_defaults(self):
        assert SAFE_DEFAULT_CONFIG["mode"] == "dry_run"
        assert SAFE_DEFAULT_CONFIG["hitl_required"] is True
        cfg = RepoMindConfig()
        assert cfg.mode == SAFE_DEFAULT_CONFIG["mode"]
        assert cfg.hitl_required == SAFE_DEFAULT_CONFIG["hitl_required"]
