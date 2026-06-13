"""
worker/main.py — Core Pipeline Orchestrator

HOW IT WORKS:
─────────────
This is the BRAIN of the pipeline. It receives an SQS message from Step 1
and orchestrates the ENTIRE fix pipeline:

    1. Fetch CI logs from GitHub        (log_fetcher.py)
    2. Sanitize logs                    (sanitizer.py)
    3. Generate excerpt                 (excerpt.py)
    4. Store logs + excerpt in S3       (shared/storage.py)
    5. Run triage (classify failure)    (triage/triage.py)
    6. Generate fix plan                (planner/planner.py)
    7. Evaluate policy                  (policy_engine/policy.py)
    8. Create PR with fix               (pr_creator/pr_creator.py)
    9. Code quality gate                (code_quality/code_checker.py)
    10. Verify fix + rollback           (verifier/verifier.py)
    11. Metrics + kill switch           (observability/metrics.py, observability/killswitch.py)

ROUTING:
    message_type == "ci_failure"    → full pipeline (Steps 2-9)
    message_type == "verification"  → Step 10 only (verify + rollback)
    message_type == "installation"  → Welcome PR (V2)
    message_type == "review"        → Resume paused HITL graph (V2)

MULTI-TENANT (V2):
    Every SQS message includes installation_id. We thread it through every
    GitHub API call so each repo's actions use that repo's app install token.

NOTIFICATIONS (★ NEW in V2):
    Six lifecycle events trigger emails via shared/notify_hooks:
        ci_failed         — Fires after excerpt + repomind config are loaded
        pr_review_needed  — Fires when graph pauses for HITL review
        pr_merged         — Fires when HITL=false branch auto-merges
        rollback          — Fires in _handle_verification on rollback
        pipeline_error    — Fires on policy deny OR finalize-with-errors

    All hooks pass repo_config so .repomind.yml `notifications:` block
    overrides global env vars per-repo.

    ★ BUG FIX (2026-06-13) — Rollback email no longer fires when the
       RollbackClient skipped due to anti-flap. We check the inner
       rollback status, not just the `rollback_triggered` boolean.

ERROR HANDLING:
    - Each step is wrapped in try/except
    - Errors are recorded in the timeline
    - On critical failure → pipeline stops, error notification sent
    - Notification failures NEVER crash the pipeline (see notify_hooks._safe_send)
"""

import json
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from worker.log_fetcher import LogFetcher
from worker.sanitizer import Sanitizer
from worker.excerpt import ExcerptGenerator
from shared.config import settings
from shared.event_id import extract_repo_slug
from shared.storage import get_storage
from shared.timeline import Timeline
# ★ NOTIFY — replaced legacy `from shared.notifier import Notifier`
from shared.notify_hooks import (
    notify_ci_failed,
    notify_pr_review_needed,
    notify_pr_merged,
    notify_rollback,
    notify_pipeline_error,
)
from shared.logger import get_logger

logger = get_logger("worker.main")


@dataclass
class PipelineContext:
    """
    Carries all data through the pipeline.

    Each step reads from and writes to this context.
    At the end, the worker serializes it to artifacts.json.
    """
    event_id: str
    repo: str
    workflow_run_id: int
    run_url: str
    head_branch: str = ""
    head_sha: str = ""
    installation_id: int = 0  # V2: per-install GitHub auth (multi-tenant)

    # Populated by pipeline steps
    raw_logs: Optional[str] = None
    sanitized_logs: Optional[str] = None
    excerpt: Optional[str] = None

    triage: Optional[Dict[str, Any]] = None
    plan_summary: Optional[Dict[str, Any]] = None
    policy: Optional[Dict[str, Any]] = None
    code_quality: Optional[Dict[str, Any]] = None
    pr: Optional[Dict[str, Any]] = None
    verification: Optional[Dict[str, Any]] = None

    # V2: User config
    repomind_config: Optional[Dict[str, Any]] = None

    errors: list = field(default_factory=list)


class Worker:
    """
    Core pipeline orchestrator.

    Runs the entire CI auto-fix pipeline for a single event.
    """

    def __init__(self):
        self.storage = get_storage()
        self.log_fetcher = LogFetcher()
        self.sanitizer = Sanitizer()
        self.excerpt_generator = ExcerptGenerator()
        # ★ NOTIFY — removed: self.notifier = Notifier()
        # Notifications now use the singleton in shared.notify_hooks,
        # which only fetches the SMTP secret ONCE per warm Lambda container.

    # ──────────────────────────────────────────────
    # ★ NOTIFY: Helper — flatten PipelineContext into a state dict for hooks
    # ──────────────────────────────────────────────
    def _ctx_to_state(self, ctx: PipelineContext) -> Dict[str, Any]:
        """
        Build a flat state dict from PipelineContext for notify_hooks.

        Hooks tolerate missing keys (templates render "—"), so this is
        intentionally generous — pass whatever we have, hooks pick what
        they need per event.
        """
        state: Dict[str, Any] = {
            "event_id":        ctx.event_id,
            "repo":            ctx.repo,
            "branch":          ctx.head_branch or "main",
            "head_branch":     ctx.head_branch,
            "run_id":          ctx.workflow_run_id,
            "workflow_run_id": ctx.workflow_run_id,
            "commit_sha":      ctx.head_sha,
            "head_sha":        ctx.head_sha,
            "error_excerpt":   ctx.excerpt,
        }
        if ctx.triage:
            state["failure_type"] = ctx.triage.get("failure_type")
            state["confidence"]   = ctx.triage.get("confidence")
            state["playbook_id"]  = (
                ctx.triage.get("playbook_id")
                or ctx.triage.get("playbook")
            )
            if ctx.triage.get("risk_level"):
                state["risk_level"] = ctx.triage["risk_level"]
        if ctx.plan_summary:
            code_changes = ctx.plan_summary.get("code_changes") or []
            file_list = [
                (c.get("file") or c.get("path"))
                for c in code_changes if c
            ]
            file_list = [f for f in file_list if f]
            if file_list:
                state["files_changed"] = file_list
                state["files_changed_count"] = len(file_list)
        if ctx.pr:
            state["pr_number"] = ctx.pr.get("number")
            state["pr_url"]    = ctx.pr.get("url")
        return state

    def process_event(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process a single CI failure event end-to-end.

        Routes messages based on message_type:
            "ci_failure"    → full pipeline (default)
            "verification"  → Step 10 verification only
            "installation"  → Welcome PR (V2)
            "review"        → Resume paused HITL graph (V2)
        """
        message_type = message.get("message_type", "ci_failure")

        # ── V2: Route installation events ──
        if message_type == "installation":
            return self._handle_installation(message)

        # ── V2: Route review events (HITL resume) ──
        if message_type == "review":
            return self._handle_review(message)

        # ── Route verification messages to Step 10 ──
        if message_type == "verification":
            return self._handle_verification(message)

        # ── Kill Switch Check (Step 11) ──
        try:
            from observability.killswitch import is_kill_switch_enabled
            if is_kill_switch_enabled():
                logger.warning(
                    "kill_switch_active",
                    event_id=message.get("event_id"),
                    repo=message.get("repo"),
                )
                return {
                    "status": "halted",
                    "reason": "Kill switch is ON — pipeline halted",
                    "event_id": message.get("event_id"),
                }
        except Exception as e:
            logger.warning("kill_switch_check_failed", error=str(e))

        # ── Record pipeline start metric ──
        try:
            from observability.metrics import metrics
            metrics.events_total.labels(
                repo=message.get("repo", "unknown"),
                status="started",
            ).inc()
        except Exception:
            pass

        # ── Initialize context and timeline ──
        ctx = PipelineContext(
            event_id=message["event_id"],
            repo=message["repo"],
            workflow_run_id=message["workflow_run_id"],
            run_url=message["run_url"],
            head_branch=message.get("head_branch", ""),
            head_sha=message.get("head_sha", ""),
            installation_id=int(message.get("installation_id") or 0),
        )
        timeline = Timeline(event_id=ctx.event_id)
        repo_slug = extract_repo_slug(ctx.event_id)
        base_path = f"events/{repo_slug}/{ctx.event_id}"

        logger.info(
            "pipeline_started",
            event_id=ctx.event_id,
            repo=ctx.repo,
            run_id=ctx.workflow_run_id,
            installation_id=ctx.installation_id,
        )

        timeline.record(
            step=1,
            event_type="event_received",
            summary=f"Processing workflow run {ctx.workflow_run_id} for {ctx.repo}",
        )

        # ── Step 2a: Fetch Logs ──
        try:
            timeline.start_step(2)
            ctx.raw_logs = self.log_fetcher.fetch_logs(
                ctx.repo,
                ctx.workflow_run_id,
                installation_id=ctx.installation_id or None,
            )

            if ctx.raw_logs:
                timeline.record(
                    step=2,
                    event_type="logs_downloaded",
                    summary=f"Downloaded {len(ctx.raw_logs)} bytes of logs",
                )
            else:
                timeline.record_error(step=2, error="Failed to download logs")
                self._finalize(ctx, timeline, base_path)
                return self._build_artifacts(ctx)

        except Exception as e:
            self._handle_error(ctx, timeline, 2, "log_fetch_failed", e)
            self._finalize(ctx, timeline, base_path)
            return self._build_artifacts(ctx)

        # ── Step 2b: Sanitize ──
        try:
            ctx.sanitized_logs = self.sanitizer.sanitize(ctx.raw_logs)
            self.storage.put_text(f"{base_path}/logs/full_logs.txt", ctx.sanitized_logs)
        except Exception as e:
            self._handle_error(ctx, timeline, 2, "sanitization_failed", e)

        # ── Step 2c: Generate Excerpt ──
        try:
            logs_to_excerpt = ctx.sanitized_logs or ctx.raw_logs
            ctx.excerpt = self.excerpt_generator.generate(logs_to_excerpt)
            self.storage.put_text(f"{base_path}/logs/excerpt.txt", ctx.excerpt)

            timeline.record(
                step=2,
                event_type="excerpt_generated",
                summary=f"Excerpt: {len(ctx.excerpt.splitlines())} lines",
            )
        except Exception as e:
            self._handle_error(ctx, timeline, 2, "excerpt_failed", e)
            self._finalize(ctx, timeline, base_path)
            return self._build_artifacts(ctx)

        # ── Step 3a: Load .repomind.yml from user's repo (V2) ──
        repomind_cfg = None
        try:
            from shared.repomind_config import load_repomind_config
            repomind_cfg = load_repomind_config(ctx.repo, ref=ctx.head_branch or None)
            ctx.repomind_config = repomind_cfg.to_dict()
            timeline.record(
                step=2,
                event_type="repomind_config_loaded",
                summary=(
                    f"mode={repomind_cfg.mode} "
                    f"hitl={repomind_cfg.hitl_required} "
                    f"source={repomind_cfg.source}"
                ),
            )
            logger.info(
                "repomind_config_resolved",
                event_id=ctx.event_id,
                mode=repomind_cfg.mode,
                hitl_required=repomind_cfg.hitl_required,
                source=repomind_cfg.source,
            )
        except Exception as e:
            logger.warning(
                "repomind_config_load_error",
                event_id=ctx.event_id,
                error=str(e),
            )
            from shared.repomind_config import RepoMindConfig
            repomind_cfg = RepoMindConfig(source="fallback")
            ctx.repomind_config = repomind_cfg.to_dict()

        # ── Short-circuit: mode=disabled means we drop everything ──
        if repomind_cfg.is_disabled:
            logger.info(
                "pipeline_skipped_mode_disabled",
                event_id=ctx.event_id,
                repo=ctx.repo,
            )
            timeline.record(
                step=2,
                event_type="pipeline_skipped",
                summary="mode=disabled in .repomind.yml — RepoMind ignored this failure",
            )
            ctx.policy = {
                "decision": "deny",
                "reason": "mode=disabled in .repomind.yml",
                "rules_triggered": ["user_config_disabled"],
            }
            # ★ NOTIFY — skip notifications when user has explicitly disabled
            self._finalize(ctx, timeline, base_path, send_failure_notify=False)
            return self._build_artifacts(ctx)

        # ─────────────────────────────────────────────────────────────────
        # ★★★ NOTIFY #1: CI failure detected
        # Fires after we have excerpt + config so the email is RICH.
        # Won't fire if repo set notifications.enabled=false in .repomind.yml.
        # ─────────────────────────────────────────────────────────────────
        notify_ci_failed(
            self._ctx_to_state(ctx),
            repo_config=ctx.repomind_config,
        )

        # ── Steps 3-8: LangGraph Multi-Agent Pipeline ──
        try:
            from agents.graph import run_pipeline
            timeline.start_step(4)

            pipeline_result = run_pipeline(
                event_id=ctx.event_id,
                repo=ctx.repo,
                workflow_run_id=ctx.workflow_run_id,
                run_url=ctx.run_url,
                excerpt=ctx.excerpt,
                head_branch=ctx.head_branch,
                head_sha=ctx.head_sha,
                repomind_config=ctx.repomind_config,
                mode=repomind_cfg.mode,
                hitl_required=repomind_cfg.hitl_required,
                installation_id=ctx.installation_id or None,
            )

            ctx.triage = pipeline_result.get("triage", {})
            ctx.plan_summary = pipeline_result.get("plan_summary", {})
            ctx.policy = pipeline_result.get("policy", {})
            ctx.pr = pipeline_result.get("pr", {})
            graph_status = pipeline_result.get("status", "")
            pr_number = int(pipeline_result.get("pr_number") or 0)

            timeline.record(
                step=4,
                event_type="langgraph_pipeline_completed",
                summary=(
                    f"Agents completed: "
                    f"triage={ctx.triage.get('failure_type')} "
                    f"policy={ctx.policy.get('decision')} "
                    f"status={graph_status}"
                ),
            )

            # ─────────────────────────────────────────────────────────────
            # ★★★ NOTIFY (policy deny): treat as a pipeline error event
            # Maps to PIPELINE_ERROR template — user sees why pipeline stopped.
            # ─────────────────────────────────────────────────────────────
            if ctx.policy.get("decision") == "deny":
                reason = ctx.policy.get("reason", "Policy denied")
                logger.info("policy_denied", event_id=ctx.event_id, reason=reason)
                notify_pipeline_error(
                    self._ctx_to_state(ctx),
                    error=Exception(f"Policy denied: {reason}"),
                    stage="policy_evaluation",
                    repo_config=ctx.repomind_config,
                )

            # ── V2: If a PR was opened, store the PR ↔ event mapping ──
            if pr_number and ctx.pr.get("status") == "created":
                try:
                    from review.review_handler import store_pr_event_mapping
                    store_pr_event_mapping(ctx.repo, pr_number, ctx.event_id)
                    timeline.record(
                        step=8,
                        event_type="pr_event_mapping_stored",
                        summary=f"PR #{pr_number} ↔ event {ctx.event_id}",
                    )
                except Exception as e:
                    logger.warning(
                        "pr_mapping_store_failed",
                        event_id=ctx.event_id,
                        error=str(e),
                    )

            # ─────────────────────────────────────────────────────────────
            # ★★★ NOTIFY #2: HITL — graph paused awaiting human review
            # ─────────────────────────────────────────────────────────────
            if graph_status == "awaiting_review":
                logger.info(
                    "pipeline_awaiting_review",
                    event_id=ctx.event_id,
                    pr_url=pipeline_result.get("pr_url"),
                )
                # Make sure pr_url ends up in state even if ctx.pr didn't have it
                state = self._ctx_to_state(ctx)
                if pipeline_result.get("pr_url") and not state.get("pr_url"):
                    state["pr_url"] = pipeline_result.get("pr_url")
                notify_pr_review_needed(
                    state,
                    repo_config=ctx.repomind_config,
                )

                # Persist partial artifacts; review will append later.
                self._finalize(ctx, timeline, base_path, send_failure_notify=False)
                artifacts = self._build_artifacts(ctx)
                artifacts["status"] = "awaiting_review"
                artifacts["pr_url"] = pipeline_result.get("pr_url")
                return artifacts

            # ─────────────────────────────────────────────────────────────
            # ★★★ NOTIFY #3: PR auto-merged (HITL=false flow)
            # When HITL is disabled, the graph opens AND merges the PR
            # immediately. The verifier still runs after CI completes.
            # ─────────────────────────────────────────────────────────────
            if ctx.pr.get("status") == "created" and ctx.pr.get("url"):
                state = self._ctx_to_state(ctx)
                state["reviewer"] = "RepoMind (auto-merge)"
                state["time_to_merge"] = "Immediate (HITL disabled)"
                notify_pr_merged(
                    state,
                    repo_config=ctx.repomind_config,
                )

        except Exception as e:
            self._handle_error(ctx, timeline, 4, "langgraph_pipeline_failed", e)
            self._finalize(ctx, timeline, base_path)
            return self._build_artifacts(ctx)

        # ── Step 9: Code Quality Gate (post-graph, advisory only in V2) ──
        try:
            timeline.start_step(9)
            from code_quality.code_checker import CodeChecker
            checker = CodeChecker()
            code_changes = ctx.plan_summary.get("code_changes", []) if ctx.plan_summary else []
            ctx.code_quality = checker.check(code_changes)

            timeline.record(
                step=9,
                event_type="code_quality_checked",
                summary=(
                    f"Quality: {'PASSED' if ctx.code_quality['passed'] else 'WARNING'} "
                    f"— {ctx.code_quality['summary']}"
                ),
            )

            if not ctx.code_quality["passed"]:
                logger.warning(
                    "code_quality_warning_post_pr",
                    event_id=ctx.event_id,
                    blocking_failures=ctx.code_quality["blocking_failures"],
                    summary=ctx.code_quality["summary"],
                )

        except Exception as e:
            self._handle_error(ctx, timeline, 9, "code_quality_check_failed", e)

        # ── Step 3: Index to Vector DB (non-blocking) ──
        try:
            timeline.start_step(3)
            from rag.indexer import Indexer
            indexer = Indexer()
            count = indexer.index_event(
                event_id=ctx.event_id,
                repo=ctx.repo,
                excerpt=ctx.excerpt,
                triage=ctx.triage,
                plan=ctx.plan_summary,
                verification=ctx.verification,
            )
            timeline.record(
                step=3,
                event_type="vectors_indexed",
                summary=f"Indexed {count} vectors to Qdrant",
            )
        except Exception as e:
            self._handle_error(ctx, timeline, 3, "indexing_failed", e)

        # ── Finalize ──
        self._finalize(ctx, timeline, base_path)
        return self._build_artifacts(ctx)

    # ──────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────
    def _handle_error(
        self,
        ctx: PipelineContext,
        timeline: Timeline,
        step: int,
        error_type: str,
        exception: Exception,
    ) -> None:
        """Record an error in context, timeline, and logs."""
        error_msg = f"{error_type}: {str(exception)}"
        ctx.errors.append({"step": step, "error": error_msg, "exc_type": type(exception).__name__})
        timeline.record_error(step=step, error=error_msg)
        logger.error(
            error_type,
            event_id=ctx.event_id,
            step=step,
            error=str(exception),
            traceback=traceback.format_exc(),
        )

    def _build_artifacts(self, ctx: PipelineContext) -> Dict[str, Any]:
        """Build the final artifacts.json from the pipeline context."""
        artifacts = {}
        if ctx.repomind_config:
            artifacts["repomind_config"] = ctx.repomind_config
        if ctx.triage:
            artifacts["triage"] = ctx.triage
        if ctx.plan_summary:
            artifacts["plan_summary"] = ctx.plan_summary
        if ctx.policy:
            artifacts["policy"] = ctx.policy
        if ctx.code_quality:
            artifacts["code_quality"] = ctx.code_quality
        if ctx.pr:
            artifacts["pr"] = ctx.pr
        if ctx.verification:
            artifacts["verification"] = ctx.verification
        if ctx.errors:
            artifacts["errors"] = ctx.errors
        return artifacts

    def _finalize(
        self,
        ctx: PipelineContext,
        timeline: Timeline,
        base_path: str,
        send_failure_notify: bool = True,   # ★ NOTIFY (new param)
    ) -> None:
        """
        Save artifacts and timeline to storage. Push metrics.

        send_failure_notify=False is used by:
          - mode=disabled short-circuit (user wants silence)
          - awaiting_review path (already notified)
        """
        try:
            artifacts = self._build_artifacts(ctx)
            self.storage.put_json(f"{base_path}/artifacts.json", artifacts)
            self.storage.put_json(f"{base_path}/timeline.json", timeline.to_dict())

            logger.info(
                "pipeline_finalized",
                event_id=ctx.event_id,
                has_errors=bool(ctx.errors),
                steps_completed=len(timeline),
            )
        except Exception as e:
            logger.error(
                "finalize_failed",
                event_id=ctx.event_id,
                error=str(e),
            )

        # Record final status metric
        try:
            from observability.metrics import metrics
            status = "error" if ctx.errors else "completed"
            if ctx.policy and ctx.policy.get("decision") == "deny":
                status = "denied"
            if ctx.code_quality and not ctx.code_quality.get("passed", True):
                status = "quality_blocked"
            metrics.events_total.labels(repo=ctx.repo, status=status).inc()
        except Exception:
            pass

        # Push all metrics to Pushgateway (non-blocking)
        try:
            from observability.metrics import push_metrics
            push_metrics(job="repomind-worker")
        except Exception as e:
            logger.debug("metrics_push_error", error=str(e))

        # ─────────────────────────────────────────────────────────────────
        # ★★★ NOTIFY #4: Pipeline error — fires on any unrecovered error
        # Builds an Exception from the last logged error message so the
        # template can render type + message in the dark code block.
        # ─────────────────────────────────────────────────────────────────
        if ctx.errors and send_failure_notify:
            last = ctx.errors[-1]
            err_msg = last.get("error", "Unknown error")
            exc_type = last.get("exc_type", "RuntimeError")
            # Re-construct a synthetic exception just for type naming
            try:
                exc = type(exc_type, (Exception,), {})(err_msg)
            except Exception:
                exc = Exception(err_msg)
            notify_pipeline_error(
                self._ctx_to_state(ctx),
                error=exc,
                stage=f"step_{last.get('step', 'unknown')}",
                repo_config=ctx.repomind_config,
            )

    def _handle_verification(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle a verification message (Step 10).

        Called when a CI run completes on a fix/* branch.
        Verifies if the fix worked and triggers rollback if not.

        ★ BUG FIX (2026-06-13) — Two issues fixed here:
            1. Anti-flap suppression: when RollbackClient skips because we
               already opened a revert PR for this branch, the verifier
               returns rollback_triggered=True (correctly — a rollback
               state exists) BUT no NEW rollback work was done. We must
               NOT send another email in that case.
            2. Repo config: we now LOAD .repomind.yml here so per-repo
               notification overrides apply to rollback emails too.
        """
        logger.info(
            "verification_started",
            event_id=message.get("event_id"),
            repo=message.get("repo"),
            branch=message.get("head_branch"),
            conclusion=message.get("conclusion"),
        )

        try:
            from verifier.verifier import Verifier
            verifier = Verifier()
            result = verifier.verify(
                repo=message["repo"],
                workflow_run_id=message["workflow_run_id"],
                branch=message.get("head_branch", ""),
                conclusion=message.get("conclusion", ""),
                head_sha=message.get("head_sha", ""),
                run_url=message.get("run_url", ""),
            )

            # Store verification result
            from shared.event_id import extract_repo_slug
            repo_slug = extract_repo_slug(message["event_id"])
            base_path = f"events/{repo_slug}/{message['event_id']}"
            self.storage.put_json(
                f"{base_path}/verification.json",
                result.to_dict(),
            )

            logger.info(
                "verification_completed",
                event_id=message.get("event_id"),
                status=result.status,
                rollback_triggered=result.rollback_triggered,
            )

            # ─────────────────────────────────────────────────────────────
            # ★★★ NOTIFY #5: Rollback triggered
            # Verifier decided the fix didn't work and reverted the branch.
            #
            # ★ BUG FIX — Check the INNER rollback status. The RollbackClient
            #   exposes one of: "completed", "skipped", "failed", "rate_limited".
            #   Only "completed" (i.e., a NEW revert PR was actually opened)
            #   should trigger an email — otherwise we spam the user with one
            #   rollback email per re-fired CI on the same fix branch.
            # ─────────────────────────────────────────────────────────────
            if getattr(result, "rollback_triggered", False):
                # Resolve the inner rollback status from any of the shapes
                # the Verifier might expose.
                rollback_status = (
                    getattr(result, "rollback_status", None)
                    or (getattr(result, "rollback_result", None) or {}).get("status")
                    or (result.to_dict().get("rollback") or {}).get("status")
                )

                # Anti-flap / rate-limited / failed → skip email
                if rollback_status in ("skipped", "rate_limited"):
                    logger.info(
                        "rollback_notify_suppressed",
                        event_id=message.get("event_id"),
                        repo=message.get("repo"),
                        branch=message.get("head_branch"),
                        rollback_status=rollback_status,
                        reason="No new revert PR was created (anti-flap or rate-limit)",
                    )
                else:
                    # Real rollback occurred — load repo_config so .repomind.yml
                    # notification overrides apply, then send the email.
                    repo_cfg_dict: Optional[Dict[str, Any]] = None
                    try:
                        from shared.repomind_config import load_repomind_config
                        repo_cfg = load_repomind_config(
                            message["repo"],
                            ref=message.get("head_branch") or None,
                        )
                        repo_cfg_dict = repo_cfg.to_dict()
                    except Exception as cfg_err:
                        logger.warning(
                            "rollback_repomind_config_load_failed",
                            event_id=message.get("event_id"),
                            error=str(cfg_err),
                        )

                    state = {
                        "event_id":           message.get("event_id"),
                        "repo":               message.get("repo"),
                        "branch":             message.get("head_branch"),
                        "head_branch":        message.get("head_branch"),
                        "commit_sha":         message.get("head_sha"),
                        "head_sha":           message.get("head_sha"),
                        "run_id":             message.get("workflow_run_id"),
                        "workflow_run_id":    message.get("workflow_run_id"),
                        "attempts":           getattr(result, "attempts", 1),
                        "reason":             (
                            getattr(result, "rollback_reason", None)
                            or "Verification CI failed — fix did not resolve the issue"
                        ),
                        "rollback_status":    rollback_status or "completed",
                        "revert_pr_url":      getattr(result, "revert_pr_url", None),
                        "revert_pr_number":   getattr(result, "revert_pr_number", None),
                        "original_pr_number": getattr(result, "original_pr_number", None),
                        "fix_branch":         message.get("head_branch"),
                    }
                    notify_rollback(state, repo_config=repo_cfg_dict)

            # Push metrics
            try:
                from observability.metrics import push_metrics
                push_metrics(job="repomind-verifier")
            except Exception:
                pass

            return result.to_dict()

        except Exception as e:
            logger.error(
                "verification_failed",
                event_id=message.get("event_id"),
                error=str(e),
                traceback=traceback.format_exc(),
            )
            # ─────────────────────────────────────────────────────────────
            # ★★★ NOTIFY #6: Verification crashed
            # ─────────────────────────────────────────────────────────────
            notify_pipeline_error(
                state={
                    "event_id": message.get("event_id"),
                    "repo":     message.get("repo", "unknown/repo"),
                    "branch":   message.get("head_branch"),
                },
                error=e,
                stage="verification",
                repo_config=None,
            )
            return {
                "status": "error",
                "error": str(e),
                "event_id": message.get("event_id"),
            }

    # ──────────────────────────────────────────────
    # V2: Installation handler — open welcome PR
    # ──────────────────────────────────────────────
    def _handle_installation(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle an installation event by opening a welcome PR on each
        newly-installed repo.

        No notification fires here — the welcome PR IS the notification.
        """
        repos = message.get("repos_added") or []
        if not repos and message.get("repo"):
            repos = [message["repo"]]

        installation_id = int(message.get("installation_id") or 0)
        logger.info(
            "installation_handler_start",
            installation_id=installation_id,
            repos=repos,
        )

        results = []
        try:
            from pr_creator.welcome_pr import WelcomePRCreator
            creator = WelcomePRCreator()
            for repo_name in repos:
                result = creator.create_welcome_pr(
                    repo_name,
                    installation_id=installation_id or None,
                )
                result["repo"] = repo_name
                results.append(result)
        except Exception as e:
            logger.error("installation_handler_failed", error=str(e))
            return {
                "status": "failed",
                "error": str(e),
                "installation_id": installation_id,
            }

        # Metrics
        try:
            from observability.metrics import metrics, push_metrics
            for r in results:
                metrics.events_total.labels(
                    repo=r.get("repo", "unknown"),
                    status=f"welcome_{r.get('status', 'unknown')}",
                ).inc()
            push_metrics(job="repomind-installation")
        except Exception:
            pass

        return {
            "status": "completed",
            "installation_id": installation_id,
            "results": results,
        }

    # ──────────────────────────────────────────────
    # V2: Review handler — resume paused HITL graph (Step 12)
    # ──────────────────────────────────────────────
    def _handle_review(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle a pull_request_review event by resuming the paused graph.
        Delegates to review/review_handler.

        ★ NOTIFY — notify_pr_merged / notify_pr_rejected belong INSIDE
           review/review_handler.py because that's where the merge/close
           decision actually happens. Share that file and we'll wire it.
        """
        logger.info(
            "review_handler_dispatch",
            repo=message.get("repo"),
            pr_number=message.get("pr_number"),
            review_state=message.get("review_state"),
        )
        try:
            from review.review_handler import handle_review_message
            return handle_review_message(message)
        except Exception as e:
            logger.error("review_handler_dispatch_failed", error=str(e))
            notify_pipeline_error(
                state={
                    "event_id": message.get("event_id"),
                    "repo":     message.get("repo", "unknown/repo"),
                },
                error=e,
                stage="review_dispatch",
                repo_config=None,
            )
            return {
                "status": "failed",
                "error": str(e),
                "repo": message.get("repo"),
                "pr_number": message.get("pr_number"),
            }


# ──────────────────────────────────────────────
# Lambda handler for SQS trigger
# ──────────────────────────────────────────────
def lambda_handler(event, context):
    """
    AWS Lambda entry point.
    Triggered by SQS. Processes each record (message).
    """
    worker = Worker()

    for record in event.get("Records", []):
        try:
            message = json.loads(record["body"])
            logger.info("sqs_message_received", event_id=message.get("event_id"))
            worker.process_event(message)
        except Exception as e:
            logger.error("sqs_processing_failed", error=str(e))
            raise  # Let Lambda retry via SQS visibility timeout