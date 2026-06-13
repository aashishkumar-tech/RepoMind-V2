"""
review/review_handler.py — Resume Paused Pipelines on PR Review (V2)

HOW IT WORKS:
─────────────
1. GitHub fires `pull_request_review` when someone approves / requests
   changes on a PR.
2. webhook/webhook_handler.py recognizes it, builds a ReviewMessage with
   message_type="review", and pushes it to SQS.
3. worker/main.py routes message_type="review" to handle_review_message().
4. We translate the GitHub review state to our internal HumanApproval verdict.
5. We look up the original event_id (= LangGraph thread_id) from the
   event_id mapping we stored in S3 when the PR was opened.
6. We call agents.graph.resume_pipeline(event_id, verdict) — LangGraph
   restores state from the checkpoint and runs merge_decision → merge/cleanup.

EVENT_ID ↔ PR MAPPING:
    When pr_creator_node opens a PR, the worker writes a small index file:
        events/<repo-slug>/by-pr/<repo>/<pr_number>.json → {"event_id": "..."}
    This is how a review (which only knows repo + pr_number) can find the
    original event.

NOTIFICATIONS (★ NEW in V2):
    Two lifecycle events trigger emails via shared/notify_hooks:
        pr_merged    — Fires after verdict="approved" + merge succeeds
        pr_rejected  — Fires after verdict="rejected" + cleanup runs
        pipeline_error — Fires if resume_pipeline() crashes

    Repo config is reloaded from the original artifacts.json so per-repo
    `notifications:` overrides from .repomind.yml are honored.

GRACEFUL FAILURE:
    If we can't find a matching event_id (e.g. the PR wasn't opened by
    RepoMind), we IGNORE the review. We never raise — random reviews on
    unrelated PRs must not poison the pipeline.
"""

from datetime import datetime, timezone
from typing import Dict, Any, Optional

from shared.config import settings
from shared.event_id import extract_repo_slug
from shared.storage import get_storage
from shared.logger import get_logger
# ★ NOTIFY — import the three hooks this module fires
from shared.notify_hooks import (
    notify_pr_merged,
    notify_pr_rejected,
    notify_pipeline_error,
)

from review.models import ReviewMessage

logger = get_logger("review.review_handler")


# ──────────────────────────────────────────────
# Index helpers
# ──────────────────────────────────────────────
def _pr_index_key(repo: str, pr_number: int) -> str:
    """S3 key where we store the event_id mapping for a PR."""
    repo_slug = repo.replace("/", "-")
    return f"indexes/by-pr/{repo_slug}/{pr_number}.json"


def store_pr_event_mapping(repo: str, pr_number: int, event_id: str) -> None:
    """
    Write the PR → event_id mapping. Called by the worker right after
    a PR is opened. Idempotent.
    """
    if not (repo and pr_number and event_id):
        return
    try:
        storage = get_storage()
        storage.put_json(
            _pr_index_key(repo, pr_number),
            {
                "event_id": event_id,
                "repo": repo,
                "pr_number": pr_number,
            },
        )
        logger.info(
            "pr_event_mapping_stored",
            repo=repo,
            pr_number=pr_number,
            event_id=event_id,
        )
    except Exception as e:
        logger.warning(
            "pr_event_mapping_store_failed",
            repo=repo,
            pr_number=pr_number,
            error=str(e),
        )


def lookup_event_id_for_pr(repo: str, pr_number: int) -> Optional[str]:
    """Reverse-lookup: given a PR, find the event_id that opened it."""
    if not (repo and pr_number):
        return None
    try:
        storage = get_storage()
        blob = storage.get_json(_pr_index_key(repo, pr_number))
        if not blob:
            return None
        return blob.get("event_id")
    except Exception as e:
        logger.warning(
            "pr_event_mapping_lookup_failed",
            repo=repo,
            pr_number=pr_number,
            error=str(e),
        )
        return None


# ──────────────────────────────────────────────
# Main handler
# ──────────────────────────────────────────────
class ReviewHandler:
    """Resumes a paused pipeline based on a human PR review."""

    def handle(self, message: ReviewMessage) -> Dict[str, Any]:
        """
        Process a single review message. Returns a small status dict.

        Side effects:
            - Resumes the LangGraph pipeline (merge or cleanup runs).
            - Stores the resume result in S3 alongside the original event.
            - ★ NOTIFY — sends a pr_merged or pr_rejected email.
        """
        logger.info(
            "review_handler_start",
            repo=message.repo,
            pr_number=message.pr_number,
            review_state=message.review_state,
            reviewer=message.reviewer,
        )

        # ── 1. Find the event_id for this PR ──
        event_id = message.event_id or lookup_event_id_for_pr(
            message.repo, message.pr_number
        )
        if not event_id:
            logger.info(
                "review_ignored_unknown_pr",
                repo=message.repo,
                pr_number=message.pr_number,
                reason="No event_id mapping found — PR not opened by RepoMind",
            )
            return {
                "status": "ignored",
                "reason": "PR was not opened by RepoMind",
            }

        # ── 2. Translate GitHub review state to HITL verdict ──
        verdict = message.to_human_approval()
        if verdict == "pending":
            # commented / dismissed / pending — don't touch the graph, don't notify
            logger.info(
                "review_non_actionable",
                event_id=event_id,
                review_state=message.review_state,
            )
            return {
                "status": "ignored",
                "reason": f"Review state '{message.review_state}' is non-actionable",
                "event_id": event_id,
            }

        # ★ NOTIFY — Load original artifacts ONCE for repo_config + triage context
        # This is what powers per-repo notification overrides and lets us
        # include the original failure_type / files_changed in the email.
        original_artifacts = self._load_original_artifacts(event_id)
        repo_config = original_artifacts.get("repomind_config") if original_artifacts else None

        # ── 3. Resume the paused graph ──
        try:
            from agents.graph import resume_pipeline
            review_payload = {
                "review_id": message.review_id,
                "review_state": message.review_state,
                "body": message.review_body,
                "reviewer": message.reviewer,
                "pr_number": message.pr_number,
                "pr_url": message.pr_url,
            }
            final_state = resume_pipeline(
                event_id=event_id,
                human_approval=verdict,
                review_data=review_payload,
            )
        except Exception as e:
            logger.error(
                "resume_pipeline_failed",
                event_id=event_id,
                error=str(e),
            )
            # ─────────────────────────────────────────────────────────────
            # ★★★ NOTIFY: LangGraph crashed during HITL resume
            # ─────────────────────────────────────────────────────────────
            notify_pipeline_error(
                state={
                    "event_id":  event_id,
                    "repo":      message.repo,
                    "pr_number": message.pr_number,
                    "pr_url":    message.pr_url,
                },
                error=e,
                stage=f"resume_pipeline_{verdict}",
                repo_config=repo_config,
            )
            return {
                "status": "failed",
                "error": str(e),
                "event_id": event_id,
            }

        # ── 4. Persist the post-review artifacts ──
        try:
            self._persist_resume_artifacts(event_id, message, verdict, final_state)
        except Exception as e:
            logger.warning(
                "review_persist_failed",
                event_id=event_id,
                error=str(e),
            )

        # ── 5. Metrics ──
        try:
            from observability.metrics import metrics, push_metrics
            metrics.events_total.labels(
                repo=message.repo,
                status=f"review_{verdict}",
            ).inc()
            push_metrics(job="repomind-review")
        except Exception:
            pass

        # ─────────────────────────────────────────────────────────────────
        # ★★★ NOTIFY: PR merged or PR rejected
        # Fires AFTER persistence + metrics so even if email fails, state is saved.
        # _build_review_state() merges:
        #   - the review message (reviewer, pr_number, pr_url, body)
        #   - the resumed graph's final_state (merge_result, cleanup_result)
        #   - the original artifacts (triage, plan_summary, files_changed)
        # ─────────────────────────────────────────────────────────────────
        notify_state = self._build_review_state(
            event_id=event_id,
            message=message,
            verdict=verdict,
            final_state=final_state,
            original_artifacts=original_artifacts,
        )
        if verdict == "approved":
            merge_result = (final_state.get("merge_result") or {})
            merge_status = (merge_result.get("status") or "").lower()
            # Only fire pr_merged if the merge actually went through.
            # If the merge failed (e.g. conflicts), treat it as a pipeline error.
            if merge_status in ("merged", "success", "completed"):
                notify_pr_merged(notify_state, repo_config=repo_config)
            else:
                notify_pipeline_error(
                    state=notify_state,
                    error=Exception(
                        f"Merge attempt did not succeed "
                        f"(status={merge_status or 'unknown'})"
                    ),
                    stage="auto_merge",
                    repo_config=repo_config,
                )
        elif verdict == "rejected":
            notify_pr_rejected(notify_state, repo_config=repo_config)

        logger.info(
            "review_handler_complete",
            event_id=event_id,
            verdict=verdict,
            final_status=final_state.get("status"),
        )

        return {
            "status": "resumed",
            "event_id": event_id,
            "verdict": verdict,
            "final_status": final_state.get("status"),
            "merge_result": final_state.get("merge_result"),
            "cleanup_result": final_state.get("cleanup_result"),
        }

    # ──────────────────────────────────────────────
    # Internal
    # ──────────────────────────────────────────────
    def _load_original_artifacts(self, event_id: str) -> Dict[str, Any]:
        """
        ★ NOTIFY — Best-effort fetch of the original artifacts.json.
        Used to grab repo_config + triage so emails are context-rich.
        Returns {} on any failure (notifications still go via env defaults).
        """
        try:
            repo_slug = extract_repo_slug(event_id)
            blob = get_storage().get_json(
                f"events/{repo_slug}/{event_id}/artifacts.json"
            )
            return blob or {}
        except Exception as e:
            logger.debug(
                "load_original_artifacts_skipped",
                event_id=event_id,
                error=str(e),
            )
            return {}

    def _compute_time_to_merge(
        self,
        event_id: str,
        original_artifacts: Dict[str, Any],
    ) -> Optional[str]:
        """
        ★ NOTIFY — Best-effort human-readable time-to-merge string.
        We try (in order):
          1. created_at field in artifacts.json (if present)
          2. ISO timestamp embedded in event_id (e.g. evt-20260612T143000Z-...)
        Returns None if neither works (template renders "—").
        """
        started_at: Optional[datetime] = None

        # 1. created_at field from artifacts
        ts_str = (
            original_artifacts.get("created_at")
            or original_artifacts.get("timestamp")
        )
        if ts_str:
            try:
                started_at = datetime.fromisoformat(
                    str(ts_str).replace("Z", "+00:00")
                )
            except Exception:
                started_at = None

        # 2. Try to parse event_id like 'evt-20260612T143000Z-<repo>-<hash>'
        if not started_at and event_id:
            for part in event_id.split("-"):
                if len(part) >= 15 and "T" in part:
                    try:
                        started_at = datetime.strptime(
                            part.rstrip("Z"), "%Y%m%dT%H%M%S"
                        ).replace(tzinfo=timezone.utc)
                        break
                    except Exception:
                        continue

        if not started_at:
            return None

        delta = datetime.now(timezone.utc) - started_at
        seconds = int(delta.total_seconds())
        if seconds < 60:
            return f"{seconds}s"
        if seconds < 3600:
            return f"{seconds // 60}m {seconds % 60}s"
        hours, rem = divmod(seconds, 3600)
        minutes = rem // 60
        return f"{hours}h {minutes}m"

    def _build_review_state(
        self,
        *,
        event_id: str,
        message: ReviewMessage,
        verdict: str,
        final_state: Dict[str, Any],
        original_artifacts: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        ★ NOTIFY — Flatten everything into one dict for notify_hooks.
        Missing keys are tolerated; templates render "—".
        """
        merge_result = (final_state.get("merge_result") or {})
        cleanup_result = (final_state.get("cleanup_result") or {})

        # Pull useful context from the original pipeline run
        triage = original_artifacts.get("triage") or {}
        plan = original_artifacts.get("plan_summary") or {}
        original_pr = original_artifacts.get("pr") or {}

        code_changes = plan.get("code_changes") or []
        files_list = [
            (c.get("file") or c.get("path"))
            for c in code_changes if c
        ]
        files_list = [f for f in files_list if f]

        state: Dict[str, Any] = {
            "event_id":   event_id,
            "repo":       message.repo,
            "pr_number":  message.pr_number,
            "pr_url":     message.pr_url or original_pr.get("url"),
            "reviewer":   message.reviewer,
            # Common across templates — best-effort additions:
            "failure_type": triage.get("failure_type"),
            "confidence":   triage.get("confidence"),
            "playbook_id":  triage.get("playbook_id") or triage.get("playbook"),
        }

        if verdict == "approved":
            # pr_merged template fields
            state["merge_sha"] = (
                merge_result.get("merge_sha")
                or merge_result.get("sha")
                or merge_result.get("commit_sha")
            )
            tt_merge = self._compute_time_to_merge(event_id, original_artifacts)
            if tt_merge:
                state["time_to_merge"] = tt_merge
            if files_list:
                state["files_changed_count"] = len(files_list)

        elif verdict == "rejected":
            # pr_rejected template fields
            state["reason"] = (
                (message.review_body or "").strip()
                or cleanup_result.get("reason")
                or "No reason provided"
            )

        return state

    def _persist_resume_artifacts(
        self,
        event_id: str,
        message: ReviewMessage,
        verdict: str,
        final_state: Dict[str, Any],
    ) -> None:
        """Save the review event + final state to the event's S3 folder."""
        repo_slug = extract_repo_slug(event_id)
        base_path = f"events/{repo_slug}/{event_id}"

        storage = get_storage()
        storage.put_json(
            f"{base_path}/review.json",
            {
                "review_id": message.review_id,
                "review_state": message.review_state,
                "review_body": message.review_body,
                "reviewer": message.reviewer,
                "pr_number": message.pr_number,
                "pr_url": message.pr_url,
                "verdict": verdict,
                "timestamp": message.timestamp,
            },
        )

        # Update the original artifacts.json with the post-review fields
        try:
            existing = storage.get_json(f"{base_path}/artifacts.json") or {}
            existing["human_approval"] = verdict
            existing["review_data"] = {
                "reviewer": message.reviewer,
                "review_state": message.review_state,
                "pr_number": message.pr_number,
            }
            if final_state.get("merge_result"):
                existing["merge_result"] = final_state["merge_result"]
            if final_state.get("cleanup_result"):
                existing["cleanup_result"] = final_state["cleanup_result"]
            storage.put_json(f"{base_path}/artifacts.json", existing)
        except Exception as e:
            logger.debug("artifacts_update_skipped", error=str(e))

        # Append to the timeline if it exists
        try:
            timeline_blob = storage.get_json(f"{base_path}/timeline.json")
            if timeline_blob:
                new_entries = list(timeline_blob) if isinstance(timeline_blob, list) else []
                ts = datetime.now(timezone.utc).isoformat()
                new_entries.append({
                    "step": 12,
                    "type": "human_review_received",
                    "summary": f"Reviewer={message.reviewer} verdict={verdict}",
                    "timestamp": ts,
                })
                if verdict == "approved":
                    merge = final_state.get("merge_result", {}) or {}
                    new_entries.append({
                        "step": 12,
                        "type": "auto_merge_attempted",
                        "summary": f"Merge result: {merge.get('status')}",
                        "timestamp": ts,
                    })
                elif verdict == "rejected":
                    cleanup = final_state.get("cleanup_result", {}) or {}
                    new_entries.append({
                        "step": 12,
                        "type": "cleanup_executed",
                        "summary": f"Cleanup result: {cleanup.get('status')}",
                        "timestamp": ts,
                    })
                storage.put_json(f"{base_path}/timeline.json", new_entries)
        except Exception as e:
            logger.debug("timeline_update_skipped", error=str(e))


# ──────────────────────────────────────────────
# SQS-friendly entry point
# ──────────────────────────────────────────────
def handle_review_message(message_dict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Entry point for the worker. Takes a raw SQS message dict, validates it,
    and runs the ReviewHandler.
    """
    try:
        msg = ReviewMessage(**message_dict)
    except Exception as e:
        logger.error("review_message_invalid", error=str(e), payload=message_dict)
        return {"status": "failed", "error": f"Invalid review message: {e}"}

    return ReviewHandler().handle(msg)