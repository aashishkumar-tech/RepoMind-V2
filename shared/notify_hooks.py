"""
shared/notify_hooks.py — High-level notification helpers for pipeline integration

WHY THIS EXISTS:
────────────────
The Notifier class needs a `context` dict with specific keys per event type.
Repeating that mapping at every call site is error-prone and noisy.

This module provides one function per lifecycle event, each of which:
    1. Extracts relevant fields from your pipeline `state` dict
    2. Builds the right context for the email template
    3. Calls Notifier.send_event() — never raises (safe to ignore return)

USAGE:
    from shared.notify_hooks import notify_ci_failed, notify_pr_review_needed

    # In worker/main.py, after detecting a CI failure:
    notify_ci_failed(state, repo_config=cfg)

    # After opening a PR:
    notify_pr_review_needed(state, repo_config=cfg)

SAFETY:
    All functions catch their own exceptions. A failed notification will
    NEVER crash your pipeline — it's logged and ignored.

STATE DICT CONVENTIONS:
    These helpers look for common keys with fallbacks. Pass any subset:
        repo, branch, event_id, run_id, commit_sha, author,
        pr_number, pr_url, reviewer, merge_sha, confidence,
        playbook_id, error_excerpt, diff_preview, ...
    Missing keys render as "—" in the email (no crash).
"""

from typing import Optional

from shared.logger import get_logger
from shared.notifier import NotificationEvent, Notifier

logger = get_logger("shared.notify_hooks")

# ─────────────────────────────────────────────────────────────────────────
# Singleton: reuse one Notifier across the Lambda warm container
# (avoids re-fetching the SMTP secret on every event)
# ─────────────────────────────────────────────────────────────────────────
_notifier: Optional[Notifier] = None


def _get_notifier() -> Notifier:
    global _notifier
    if _notifier is None:
        _notifier = Notifier()
    return _notifier


def _safe_send(event: NotificationEvent, ctx: dict,
               repo_config: Optional[dict]) -> bool:
    """Wraps send_event so notification failures never crash the pipeline."""
    try:
        return _get_notifier().send_event(event, ctx, repo_config)
    except Exception as e:
        logger.error(
            "notify_hook_crashed",
            notification_event=event.value,
            error=str(e),
            error_type=type(e).__name__,
        )
        return False


def _pick(state: dict, *keys, default=None):
    """Return the first non-empty value found in state for any of the keys."""
    for k in keys:
        v = state.get(k)
        if v not in (None, ""):
            return v
    return default


# ─────────────────────────────────────────────────────────────────────────
# EVENT HOOKS — call these from your pipeline code
# ─────────────────────────────────────────────────────────────────────────

def notify_ci_failed(state: dict,
                     repo_config: Optional[dict] = None) -> bool:
    """
    Fire when a CI failure is detected (entry point of the pipeline).

    Recommended call site: top of worker/main.py handler,
    right after parsing the webhook payload.
    """
    ctx = {
        "repo":           _pick(state, "repo", "repo_full_name", "full_name"),
        "branch":         _pick(state, "branch", "head_branch", default="main"),
        "event_id":       _pick(state, "event_id", "id"),
        "run_id":         _pick(state, "run_id", "workflow_run_id"),
        "commit_sha":     _pick(state, "commit_sha", "head_sha", "sha"),
        "commit_message": _pick(state, "commit_message", "head_commit_message"),
        "author":         _pick(state, "author", "sender", "actor"),
        "workflow_name":  _pick(state, "workflow_name", "name"),
        "failed_step":    _pick(state, "failed_step", "job_name"),
        "triggered_by":   _pick(state, "triggered_by", "event_type", "event"),
        "error_excerpt":  _pick(state, "error_excerpt", "excerpt", "log_excerpt"),
    }
    return _safe_send(NotificationEvent.CI_FAILED, ctx, repo_config)


def notify_pr_review_needed(state: dict,
                            repo_config: Optional[dict] = None) -> bool:
    """
    Fire after a fix-PR is opened and is awaiting human review.

    Recommended call site: right after pr_creator.create_pr() returns.
    """
    ctx = {
        "repo":           _pick(state, "repo", "repo_full_name"),
        "branch":         _pick(state, "branch", "head_branch"),
        "event_id":       _pick(state, "event_id"),
        "pr_number":      _pick(state, "pr_number", "number"),
        "pr_url":         _pick(state, "pr_url", "html_url"),
        "failure_type":   _pick(state, "failure_type", "classification",
                                "classifier_label"),
        "confidence":     _pick(state, "confidence", "classifier_confidence"),
        "playbook_id":    _pick(state, "playbook_id", "playbook"),
        "risk_level":     _pick(state, "risk_level", "risk"),
        "files_changed":  _pick(state, "files_changed", "changed_files"),
        "lines_added":    _pick(state, "lines_added", "additions"),
        "lines_deleted":  _pick(state, "lines_deleted", "deletions"),
        "diff_preview":   _pick(state, "diff_preview", "diff"),
    }
    return _safe_send(NotificationEvent.PR_REVIEW_NEEDED, ctx, repo_config)


def notify_pr_merged(state: dict,
                     repo_config: Optional[dict] = None) -> bool:
    """
    Fire after a fix-PR is approved and merged.

    Recommended call site: hitl_nodes.py merge handler,
    after github_client.merge_pr() succeeds.
    """
    ctx = {
        "repo":                 _pick(state, "repo", "repo_full_name"),
        "event_id":             _pick(state, "event_id"),
        "pr_number":            _pick(state, "pr_number", "number"),
        "pr_url":               _pick(state, "pr_url", "html_url"),
        "reviewer":             _pick(state, "reviewer", "approver",
                                      "merged_by"),
        "merge_sha":            _pick(state, "merge_sha",
                                      "merge_commit_sha", "sha"),
        "time_to_merge":        _pick(state, "time_to_merge"),
        "files_changed_count":  _pick(state, "files_changed_count"),
    }
    return _safe_send(NotificationEvent.PR_MERGED, ctx, repo_config)


def notify_pr_rejected(state: dict,
                       repo_config: Optional[dict] = None) -> bool:
    """
    Fire after a fix-PR is rejected/closed without merge.

    Recommended call site: hitl_nodes.py rejection handler,
    after github_client.close_pr() succeeds.
    """
    ctx = {
        "repo":      _pick(state, "repo", "repo_full_name"),
        "event_id":  _pick(state, "event_id"),
        "pr_number": _pick(state, "pr_number", "number"),
        "pr_url":    _pick(state, "pr_url", "html_url"),
        "reviewer":  _pick(state, "reviewer", "rejecter", "closed_by"),
        "reason":    _pick(state, "reason", "rejection_reason",
                           default="No reason provided"),
    }
    return _safe_send(NotificationEvent.PR_REJECTED, ctx, repo_config)


def notify_rollback(state: dict,
                    repo_config: Optional[dict] = None) -> bool:
    """
    Fire when a rollback is triggered after a failed verification.

    Recommended call site: rollback handler in worker/main.py
    or wherever your safety-net branch deletion runs.
    """
    ctx = {
        "repo":     _pick(state, "repo", "repo_full_name"),
        "branch":   _pick(state, "branch", "head_branch"),
        "event_id": _pick(state, "event_id"),
        "attempts": _pick(state, "attempts", "retry_count", default="1"),
        "reason":   _pick(state, "reason", "rollback_reason",
                          default="Verification CI run failed"),
    }
    return _safe_send(NotificationEvent.ROLLBACK, ctx, repo_config)


def notify_pipeline_error(state: dict, error: Exception,
                          stage: Optional[str] = None,
                          repo_config: Optional[dict] = None) -> bool:
    """
    Fire from the global pipeline exception handler.

    Recommended call site: outer `except Exception as e` in worker/main.py.
    """
    ctx = {
        "repo":       _pick(state, "repo", "repo_full_name",
                            default="unknown/repo"),
        "event_id":   _pick(state, "event_id"),
        "stage":      stage or _pick(state, "current_stage", "stage"),
        "error_type": type(error).__name__,
        "error":      str(error),
    }
    return _safe_send(NotificationEvent.PIPELINE_ERROR, ctx, repo_config)