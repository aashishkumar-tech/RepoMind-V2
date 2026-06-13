"""
shared/repomind_config.py — User Repo Self-Serve Configuration (V2)

HOW IT WORKS:
─────────────
Each repo that uses RepoMind can drop a `.repomind.yml` file at its root
to control how RepoMind behaves for that repo. This shifts control from the
operator (us) to the repo owner (the user) — no more begging for policy
changes via email.

WHY (V2 ONBOARDING SIMPLIFICATION):
    Before V2 → every policy change required the operator to edit
                  policy/default.yaml, redeploy, hand-edit a YAML allowlist.
                  HUGE friction.
    After V2 → user drops a 5-line `.repomind.yml` in their own repo,
                 commits, done. RepoMind reads it from the GitHub Contents API.

CONFIG SCHEMA (.repomind.yml):
    mode: auto_fix          # auto_fix | dry_run | disabled
    hitl_required: true     # human approval required before merge?
    policy:
      allowed_failure_types:
        - dependency_error
        - import_error
        - syntax_error
      max_risk_level: low   # low | medium | high
      min_confidence: 0.7
    notifications:
      slack_webhook: ""     # optional
      email: ""             # optional

DEFAULTS (when .repomind.yml is missing or invalid):
    mode: dry_run           # SAFE default — never opens PRs without consent
    hitl_required: true     # ALWAYS ask humans before merge
    policy: (operator defaults)

COMMUNICATION:
─────────────
- Worker (worker) calls load_repomind_config(repo) FIRST, before triage.
- Result is attached to PipelineContext.repomind_config.
- Policy engine (policy_engine) reads it to merge with operator rules.
- PR creator (pr_creator) reads `mode` to decide PR vs comment vs skip.
- HITL nodes (agents) read `hitl_required` to decide interrupt vs auto-merge.
"""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field

import yaml

from shared.logger import get_logger

logger = get_logger("shared.repomind_config")


# ──────────────────────────────────────────────
# Defaults (used when .repomind.yml is missing)
# ──────────────────────────────────────────────
SAFE_DEFAULT_CONFIG: Dict[str, Any] = {
    "mode": "dry_run",          # SAFE default — comment, don't auto-PR
    "hitl_required": True,      # ALWAYS require human approval
    "policy": {
        "allowed_failure_types": [
            "dependency_error",
            "import_error",
            "syntax_error",
        ],
        "max_risk_level": "low",
        "min_confidence": 0.7,
    },
    "notifications": {
        "slack_webhook": "",
        "email": "",
    },
    # Internal: was this loaded from the repo or are we using defaults?
    "_source": "default",
}

VALID_MODES = ["auto_fix", "dry_run", "disabled"]
VALID_RISK_LEVELS = ["low", "medium", "high"]


@dataclass
class RepoMindConfig:
    """
    Typed wrapper around the raw .repomind.yml dict.

    Provides convenience accessors and validation.
    """
    mode: str = "dry_run"
    hitl_required: bool = True
    allowed_failure_types: List[str] = field(default_factory=list)
    max_risk_level: str = "low"
    min_confidence: float = 0.7
    slack_webhook: str = ""
    email: str = ""
    source: str = "default"  # "repo" | "default" | "fallback"
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def is_disabled(self) -> bool:
        return self.mode == "disabled"

    @property
    def is_dry_run(self) -> bool:
        return self.mode == "dry_run"

    @property
    def is_auto_fix(self) -> bool:
        return self.mode == "auto_fix"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "hitl_required": self.hitl_required,
            "policy": {
                "allowed_failure_types": self.allowed_failure_types,
                "max_risk_level": self.max_risk_level,
                "min_confidence": self.min_confidence,
            },
            "notifications": {
                "slack_webhook": self.slack_webhook,
                "email": self.email,
            },
            "_source": self.source,
        }


# ──────────────────────────────────────────────
# Parsing & validation
# ──────────────────────────────────────────────
def parse_config(raw: Dict[str, Any], source: str = "repo") -> RepoMindConfig:
    """
    Parse a raw dict (from yaml.safe_load) into a validated RepoMindConfig.

    Invalid values fall back to safe defaults rather than raising,
    so a malformed `.repomind.yml` never crashes the pipeline.
    """
    if not isinstance(raw, dict):
        logger.warning("repomind_config_invalid_root", got=type(raw).__name__)
        return RepoMindConfig(source="fallback", raw={})

    # Mode
    mode = str(raw.get("mode", "dry_run")).strip().lower()
    if mode not in VALID_MODES:
        logger.warning("repomind_config_invalid_mode", got=mode, allowed=VALID_MODES)
        mode = "dry_run"

    # HITL
    hitl_required = bool(raw.get("hitl_required", True))

    # Policy block
    policy = raw.get("policy") or {}
    if not isinstance(policy, dict):
        policy = {}

    allowed_types = policy.get("allowed_failure_types") or []
    if not isinstance(allowed_types, list):
        allowed_types = []
    allowed_types = [str(t).strip() for t in allowed_types if str(t).strip()]

    max_risk = str(policy.get("max_risk_level", "low")).strip().lower()
    if max_risk not in VALID_RISK_LEVELS:
        max_risk = "low"

    try:
        min_confidence = float(policy.get("min_confidence", 0.7))
    except (TypeError, ValueError):
        min_confidence = 0.7
    min_confidence = max(0.0, min(1.0, min_confidence))

    # Notifications
    notifications = raw.get("notifications") or {}
    if not isinstance(notifications, dict):
        notifications = {}

    slack_webhook = str(notifications.get("slack_webhook", "")).strip()
    email = str(notifications.get("email", "")).strip()

    return RepoMindConfig(
        mode=mode,
        hitl_required=hitl_required,
        allowed_failure_types=allowed_types,
        max_risk_level=max_risk,
        min_confidence=min_confidence,
        slack_webhook=slack_webhook,
        email=email,
        source=source,
        raw=raw,
    )


def parse_yaml_text(yaml_text: str) -> RepoMindConfig:
    """Parse `.repomind.yml` content text into a config. Invalid YAML → defaults."""
    try:
        raw = yaml.safe_load(yaml_text) or {}
        return parse_config(raw, source="repo")
    except yaml.YAMLError as e:
        logger.warning("repomind_config_yaml_parse_failed", error=str(e))
        return RepoMindConfig(source="fallback", raw={})


# ──────────────────────────────────────────────
# Loading from GitHub
# ──────────────────────────────────────────────
def load_repomind_config(
    repo: str,
    ref: Optional[str] = None,
) -> RepoMindConfig:
    """
    Load `.repomind.yml` from the user's repo via GitHub Contents API.

    If the file is missing, malformed, or the API call fails, we return
    SAFE defaults (dry_run + hitl_required) so the pipeline keeps working
    but never takes destructive action without explicit consent.

    Args:
        repo: Full repo name "owner/repo".
        ref:  Optional branch/SHA to read from. Defaults to the repo's
              default branch.

    Returns:
        RepoMindConfig — never None, always safe to use.
    """
    try:
        from shared.github_auth import get_github_client
        gh = get_github_client()
        repository = gh.get_repo(repo)

        kwargs = {"path": ".repomind.yml"}
        if ref:
            kwargs["ref"] = ref

        try:
            content_obj = repository.get_contents(**kwargs)
        except Exception as e:
            # 404 — no .repomind.yml in repo. Use SAFE defaults.
            logger.info(
                "repomind_config_not_found",
                repo=repo,
                msg="No .repomind.yml; using safe defaults (dry_run + hitl)",
            )
            return RepoMindConfig(source="default")

        # GitHub Contents API may return a list when the path is a directory.
        if isinstance(content_obj, list):
            logger.warning("repomind_config_path_is_directory", repo=repo)
            return RepoMindConfig(source="fallback")

        try:
            yaml_text = content_obj.decoded_content.decode("utf-8")
        except Exception as e:
            logger.warning(
                "repomind_config_decode_failed", repo=repo, error=str(e)
            )
            return RepoMindConfig(source="fallback")

        cfg = parse_yaml_text(yaml_text)
        logger.info(
            "repomind_config_loaded",
            repo=repo,
            mode=cfg.mode,
            hitl_required=cfg.hitl_required,
            allowed_types=cfg.allowed_failure_types,
        )
        return cfg

    except Exception as e:
        # Any unexpected error → safe defaults. NEVER let config loading
        # break the pipeline.
        logger.warning(
            "repomind_config_load_failed",
            repo=repo,
            error=str(e),
            msg="Falling back to safe defaults",
        )
        return RepoMindConfig(source="fallback")


# ──────────────────────────────────────────────
# Sample / template generator (used by welcome PR)
# ──────────────────────────────────────────────
SAMPLE_REPOMIND_YML = """\
# .repomind.yml — RepoMind configuration for this repository
# Docs: https://github.com/repomind/repomind/blob/main/projectdocs/ONBOARDING.md
#
# RepoMind is an autonomous CI auto-fix agent. This file controls how it
# behaves for THIS repo.

# ─── Mode ───────────────────────────────────────────────────────────────
# auto_fix : RepoMind opens PRs with proposed fixes (still requires human merge)
# dry_run  : RepoMind only posts comments with proposed fixes (no PRs) ← SAFE
# disabled : RepoMind ignores this repo entirely
mode: dry_run

# ─── Human-in-the-Loop ──────────────────────────────────────────────────
# When true, RepoMind will NEVER auto-merge a PR. A human review is required.
# Recommended: keep this true.
hitl_required: true

# ─── Policy ─────────────────────────────────────────────────────────────
# What kinds of failures should RepoMind try to fix?
policy:
  allowed_failure_types:
    - dependency_error    # Missing pip packages, version conflicts
    - import_error        # Wrong import paths
    - syntax_error        # Indentation, missing colons, etc.
    # - test_failure      # Uncomment to allow test fixes (riskier)
    # - lint_error        # Uncomment to allow lint fixes
  max_risk_level: low     # low | medium | high
  min_confidence: 0.7     # Reject fixes the agent isn't confident about

# ─── Notifications (optional) ───────────────────────────────────────────
notifications:
  slack_webhook: ""       # e.g. "https://hooks.slack.com/services/..."
  email: ""               # e.g. "team@example.com"
"""


def generate_sample_yml() -> str:
    """Return the template `.repomind.yml` content for a welcome PR."""
    return SAMPLE_REPOMIND_YML
