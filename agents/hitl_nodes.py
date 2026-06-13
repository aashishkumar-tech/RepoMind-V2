"""
agents/hitl_nodes.py — Human-in-the-Loop LangGraph Nodes (V2)

NODES IN THIS FILE:
─────────────────────
1. pr_creator_node      — opens the PR (or posts dry-run comment, or skips)
2. merge_decision_node  — reads state["human_approval"] and routes
3. merge_node           — auto-merges the PR (only if human approved)
4. cleanup_node         — closes PR + posts apology (if human rejected)

GRAPH TOPOLOGY (with these nodes):

    policy_node
        │
        ▼
    pr_creator_node     ← opens PR if mode=auto_fix, else comment / skip
        │
        ▼
    [INTERRUPT BEFORE]  ← graph PAUSES here when hitl_required=true
        │
        ▼
    merge_decision_node ← runs only after review resumes the graph
        │
        ├── approved → merge_node → END
        ├── rejected → cleanup_node → END
        └── skipped  → END           (mode=dry_run / disabled / failed)

INTERRUPT MECHANICS:
─────────────────────
LangGraph's `interrupt_before=["merge_decision_node"]` causes the graph
to STOP and persist its state to the checkpointer right before this node
would run. The Lambda returns. Hours/days later, when GitHub fires
`pull_request_review`, review/review_handler loads the state, sets
state["human_approval"], and resumes the graph. The graph picks up at
merge_decision_node which now sees the verdict.

V2 MULTI-TENANCY:
─────────────────
Every node reads `state["installation_id"]` and passes it to:
    - PRCreator.create_pr(...)       (pr_creator_node)
    - get_github_client(...)          (merge_node, cleanup_node)
This ensures we mint a token for the *right* GitHub App install
instead of falling back to the env var (which only knows about
Account A → 403 on cross-account repos).
"""

from typing import Dict, Any, Optional

from shared.logger import get_logger

logger = get_logger("agents.hitl_nodes")


def _install_id(state: Dict[str, Any]) -> Optional[int]:
    """
    Pull `installation_id` off the graph state.

    Returns an `int` (>0) if present, else `None` — which lets the auth
    layer fall back to the env var (useful for local dev, fatal for
    cross-account prod).
    """
    raw = state.get("installation_id") or 0
    try:
        val = int(raw)
    except (TypeError, ValueError):
        val = 0
    return val if val > 0 else None


# ──────────────────────────────────────────────
# 1. pr_creator_node
# ──────────────────────────────────────────────
def pr_creator_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Open the PR (or post dry-run comment, or skip) based on user's mode.

    Reads:  triage, plan_summary, policy, repomind_config, mode, repo,
            installation_id (V2 multi-tenancy)
    Writes: pr, pr_url, pr_number, status (if skipped/denied), human_approval
    """
    event_id = state.get("event_id", "")
    repo = state.get("repo", "")
    policy = state.get("policy") or {}
    mode = state.get("mode", "auto_fix")
    hitl_required = bool(state.get("hitl_required", True))
    installation_id = _install_id(state)

    logger.info(
        "pr_creator_node_start",
        event_id=event_id,
        repo=repo,
        mode=mode,
        hitl_required=hitl_required,
        installation_id=installation_id,
        policy_decision=policy.get("decision"),
    )

    # ── Pre-flight: respect policy denial ──
    if policy.get("decision") == "deny":
        logger.info("pr_creator_node_policy_denied", event_id=event_id)
        return {
            "pr": {
                "url": None,
                "status": "skipped",
                "reason": f"Policy denied: {policy.get('reason', 'N/A')}",
                "mode": mode,
            },
            "pr_url": "",
            "pr_number": 0,
            "human_approval": "skipped",
            "status": "denied",
        }

    # ── Disabled mode: short-circuit ──
    if mode == "disabled":
        logger.info("pr_creator_node_disabled", event_id=event_id)
        return {
            "pr": {
                "url": None,
                "status": "skipped",
                "reason": "mode=disabled in .repomind.yml",
                "mode": "disabled",
            },
            "pr_url": "",
            "pr_number": 0,
            "human_approval": "skipped",
            "status": "completed",
        }

    # ── Delegate to PRCreator (handles auto_fix and dry_run internally) ──
    try:
        from pr_creator.pr_creator import PRCreator
        creator = PRCreator()
        pr_result = creator.create_pr(
            repo=repo,
            triage=state.get("triage") or {},
            plan=state.get("plan_summary") or {},
            event_id=event_id,
            head_branch=state.get("head_branch", ""),
            head_sha=state.get("head_sha", ""),
            run_url=state.get("run_url", ""),
            mode=mode,
            installation_id=installation_id,
        )
    except Exception as e:
        logger.error("pr_creator_node_failed", event_id=event_id, error=str(e))
        return {
            "pr": {"status": "failed", "error": str(e), "mode": mode},
            "pr_url": "",
            "pr_number": 0,
            "human_approval": "skipped",
            "status": "failed",
            "error": str(e),
        }

    pr_status = pr_result.get("status", "unknown")

    # ── Post the always-on status comment (unless we already posted a dry-run) ──
    if pr_status != "comment_posted":
        try:
            from pr_creator.comment_poster import CommentPoster
            poster = CommentPoster()
            action_taken = {
                "created": "pr_opened",
                "skipped": "skipped_no_changes",
                "comment_posted": "comment_only",
                "failed": "error",
            }.get(pr_status, pr_status)
            poster.post_status(
                repo=repo,
                head_sha=state.get("head_sha", ""),
                triage=state.get("triage") or {},
                policy=policy,
                action_taken=action_taken,
                event_id=event_id,
                pr_url=pr_result.get("url"),
                run_url=state.get("run_url", ""),
            )
        except Exception as e:
            logger.warning(
                "status_comment_failed", event_id=event_id, error=str(e)
            )

    # ── Decide HITL next-state ──
    if pr_status == "created" and hitl_required:
        # PR opened, hitl required → state will be checkpointed and graph
        # will pause at the next node (merge_decision_node).
        human_approval = "pending"
        status = "awaiting_review"
    elif pr_status == "created" and not hitl_required:
        # PR opened but user opted out of HITL → auto-approve immediately.
        human_approval = "approved"
        status = "auto_merge_pending"
    else:
        # dry_run, disabled, skipped, failed — no PR exists, no HITL needed.
        human_approval = "skipped"
        status = "completed"

    return {
        "pr": pr_result,
        "pr_url": pr_result.get("url") or "",
        "pr_number": pr_result.get("number") or 0,
        "human_approval": human_approval,
        "status": status,
    }


# ──────────────────────────────────────────────
# 2. merge_decision_node
# ──────────────────────────────────────────────
def merge_decision_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Read state["human_approval"] and decide what to do next.

    This node runs AFTER the graph resumes from interrupt. By the time it
    runs, review/review_handler has set state["human_approval"] to one of:
        "approved" | "rejected" | "timeout" | "skipped"

    The conditional edge after this node uses the same value to pick
    merge_node, cleanup_node, or END.
    """
    event_id = state.get("event_id", "")
    approval = state.get("human_approval", "pending")

    logger.info(
        "merge_decision_node",
        event_id=event_id,
        approval=approval,
        pr_url=state.get("pr_url"),
    )

    # No-op node — just logs the decision. Routing happens in graph.py.
    return {"human_approval": approval}


# ──────────────────────────────────────────────
# 3. merge_node
# ──────────────────────────────────────────────
def merge_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Auto-merge the approved PR.

    Only reached when state["human_approval"] == "approved".
    """
    event_id = state.get("event_id", "")
    repo = state.get("repo", "")
    pr_number = int(state.get("pr_number") or 0)
    installation_id = _install_id(state)

    if not pr_number:
        logger.warning("merge_node_no_pr_number", event_id=event_id)
        return {
            "merge_result": {"status": "skipped", "reason": "No PR number in state"},
            "status": "completed",
        }

    try:
        from shared.github_auth import get_github_client
        # V2 multi-tenancy: use the right install token for this repo
        gh = get_github_client(installation_id)
        repository = gh.get_repo(repo)
        pr = repository.get_pull(pr_number)

        if pr.merged:
            logger.info("merge_node_already_merged", pr_number=pr_number)
            return {
                "merge_result": {
                    "status": "already_merged",
                    "merged_at": str(pr.merged_at) if pr.merged_at else None,
                    "merge_commit_sha": pr.merge_commit_sha,
                },
                "status": "completed",
            }

        if not pr.mergeable:
            logger.warning(
                "merge_node_not_mergeable",
                pr_number=pr_number,
                state=pr.mergeable_state,
            )
            return {
                "merge_result": {
                    "status": "blocked",
                    "reason": f"PR not mergeable (state={pr.mergeable_state})",
                },
                "status": "blocked",
            }

        merge = pr.merge(
            commit_title=f"🤖 RepoMind auto-merge: {pr.title}",
            commit_message=f"Approved by reviewer. Event: {event_id}",
            merge_method="squash",  # Squash to keep history clean
        )

        logger.info(
            "merge_node_succeeded",
            pr_number=pr_number,
            merge_sha=merge.sha,
            installation_id=installation_id,
        )
        return {
            "merge_result": {
                "status": "merged",
                "merge_commit_sha": merge.sha,
                "merged_at": str(merge.merged) if hasattr(merge, "merged") else None,
            },
            "status": "completed",
        }

    except Exception as e:
        logger.error("merge_node_failed", event_id=event_id, error=str(e))
        return {
            "merge_result": {"status": "failed", "error": str(e)},
            "status": "failed",
            "error": str(e),
        }


# ──────────────────────────────────────────────
# 4. cleanup_node
# ──────────────────────────────────────────────
def cleanup_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Clean up after a rejected PR: close it, delete the fix branch, post apology.

    Only reached when state["human_approval"] == "rejected".
    """
    event_id = state.get("event_id", "")
    repo = state.get("repo", "")
    pr_number = int(state.get("pr_number") or 0)
    pr_data = state.get("pr") or {}
    fix_branch = pr_data.get("branch", "")
    head_sha = state.get("head_sha", "")
    review = state.get("review_data") or {}
    rejection_reason = review.get("body") or "Reviewer requested changes."
    installation_id = _install_id(state)

    logger.info(
        "cleanup_node_start",
        event_id=event_id,
        pr_number=pr_number,
        branch=fix_branch,
        installation_id=installation_id,
    )

    closed = False
    branch_deleted = False
    apology_posted = False

    try:
        from shared.github_auth import get_github_client
        # V2 multi-tenancy: use the right install token for this repo
        gh = get_github_client(installation_id)
        repository = gh.get_repo(repo)

        # ── Close the PR ──
        if pr_number:
            try:
                pr = repository.get_pull(pr_number)
                if pr.state == "open":
                    pr.edit(state="closed")
                    closed = True
                    logger.info("cleanup_pr_closed", pr_number=pr_number)
            except Exception as e:
                logger.warning("cleanup_close_failed", error=str(e))

        # ── Delete the fix branch ──
        if fix_branch:
            try:
                ref = repository.get_git_ref(f"heads/{fix_branch}")
                ref.delete()
                branch_deleted = True
                logger.info("cleanup_branch_deleted", branch=fix_branch)
            except Exception as e:
                logger.warning(
                    "cleanup_branch_delete_failed",
                    branch=fix_branch,
                    error=str(e),
                )

        # ── Post apology comment on the original failed commit ──
        if head_sha:
            try:
                from pr_creator.comment_poster import CommentPoster
                poster = CommentPoster()
                result = poster.post_apology(
                    repo=repo,
                    head_sha=head_sha,
                    event_id=event_id,
                    reason=rejection_reason,
                )
                apology_posted = result.get("status") == "posted"
            except Exception as e:
                logger.warning("cleanup_apology_failed", error=str(e))

    except Exception as e:
        logger.error("cleanup_node_failed", event_id=event_id, error=str(e))
        return {
            "cleanup_result": {"status": "failed", "error": str(e)},
            "status": "completed",  # Still considered completed — we tried.
        }

    return {
        "cleanup_result": {
            "status": "rejected",
            "pr_closed": closed,
            "branch_deleted": branch_deleted,
            "apology_posted": apology_posted,
            "rejection_reason": rejection_reason,
        },
        "status": "completed",
    }


# ──────────────────────────────────────────────
# Conditional edge router
# ──────────────────────────────────────────────
def route_after_merge_decision(state: Dict[str, Any]) -> str:
    """
    Pick the next node after merge_decision_node based on human verdict.

    Returns the NAME of the next node ("merge", "cleanup", "end").
    Wired into graph.py via add_conditional_edges.
    """
    approval = state.get("human_approval", "pending")
    if approval == "approved":
        return "merge"
    if approval == "rejected":
        return "cleanup"
    # "skipped", "timeout", "pending" — graph just ends.
    return "end"