"""
pr_creator/comment_poster.py — GitHub Comments for Dry-Run Mode & Status Updates (V2)

HOW IT WORKS:
─────────────
This module posts comments to GitHub instead of creating PRs.

Two responsibilities:

1. DRY-RUN COMMENT (mode=dry_run)
   When the user's `.repomind.yml` says `mode: dry_run`, we skip PR creation
   and instead post a single comment on the failed commit (or the PR that
   triggered the failure) with the proposed fix as a markdown diff. The user
   can copy-paste it themselves, or change `mode: auto_fix` if they trust it.

2. STATUS COMMENT (always)
   For EVERY CI failure RepoMind sees, we post a short status comment
   summarizing what happened — even when policy denies, mode is disabled,
   or the agent had no fix. This eliminates the "did RepoMind even see this?"
   confusion that was a top onboarding pain point in v1.3.

WHERE COMMENTS LAND:
    - If the failed run was triggered by a PR → comment on the PR.
    - Else → comment on the commit (`POST /repos/{repo}/commits/{sha}/comments`).

COMMUNICATION:
─────────────
Worker (worker) calls:
    CommentPoster().post_dry_run(repo, head_sha, triage, plan)
    CommentPoster().post_status(repo, head_sha, triage, policy, action_taken)
"""

from typing import Dict, Any, Optional, List

from shared.github_auth import get_github_client
from shared.logger import get_logger

logger = get_logger("pr_creator.comment_poster")


class CommentPoster:
    """Posts informational and dry-run comments to GitHub."""

    # ──────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────
    def post_dry_run(
        self,
        repo: str,
        head_sha: str,
        triage: Dict[str, Any],
        plan: Dict[str, Any],
        event_id: str,
        run_url: str = "",
    ) -> Dict[str, Any]:
        """
        Post a dry-run preview comment with the proposed fix.

        Used when `.repomind.yml` says `mode: dry_run`. No branch is created,
        no PR is opened — just a markdown comment showing what RepoMind WOULD
        have done.

        Returns:
            { "status": "posted" | "failed", "url": str, "reason": str }
        """
        body = self._build_dry_run_body(triage, plan, event_id, run_url)
        return self._post_comment(repo, head_sha, body, kind="dry_run")

    def post_status(
        self,
        repo: str,
        head_sha: str,
        triage: Dict[str, Any],
        policy: Dict[str, Any],
        action_taken: str,
        event_id: str,
        pr_url: Optional[str] = None,
        run_url: str = "",
    ) -> Dict[str, Any]:
        """
        Post a status comment summarizing what RepoMind did for this failure.

        ALWAYS called, regardless of outcome. Eliminates the "silent agent"
        problem identified in onboarding feedback.

        Args:
            action_taken: One of:
                "pr_opened" | "comment_only" | "policy_denied"
                | "mode_disabled" | "skipped_no_changes" | "error"
        """
        body = self._build_status_body(
            triage, policy, action_taken, event_id, pr_url, run_url
        )
        return self._post_comment(repo, head_sha, body, kind="status")

    def post_apology(
        self,
        repo: str,
        head_sha: str,
        event_id: str,
        reason: str,
    ) -> Dict[str, Any]:
        """Post a graceful 'we couldn't fix this' comment when the human rejected the PR."""
        body = (
            f"## 🤖 RepoMind — fix rejected\n\n"
            f"A human reviewer rejected the auto-fix RepoMind proposed.\n\n"
            f"**Reason:** {reason}\n\n"
            f"_Event ID: `{event_id}`_\n\n"
            f"RepoMind has closed the fix branch. No further action will be "
            f"taken on this failure unless you re-trigger CI."
        )
        return self._post_comment(repo, head_sha, body, kind="apology")

    # ──────────────────────────────────────────────
    # Internal: post helpers
    # ──────────────────────────────────────────────
    def _post_comment(
        self,
        repo: str,
        head_sha: str,
        body: str,
        kind: str,
    ) -> Dict[str, Any]:
        """
        Post a comment, preferring the PR (if the commit is part of one)
        over the bare commit comment.
        """
        try:
            gh = get_github_client()
            repository = gh.get_repo(repo)

            # Try to find a PR associated with this commit
            pr = self._find_pr_for_commit(repository, head_sha)

            if pr is not None:
                comment = pr.create_issue_comment(body)
                logger.info(
                    "comment_posted_on_pr",
                    repo=repo,
                    pr_number=pr.number,
                    kind=kind,
                )
                return {
                    "status": "posted",
                    "url": comment.html_url,
                    "target": "pr",
                    "pr_number": pr.number,
                }

            # Fall back to commit comment
            commit = repository.get_commit(head_sha)
            comment = commit.create_comment(body)
            logger.info(
                "comment_posted_on_commit",
                repo=repo,
                sha=head_sha[:8],
                kind=kind,
            )
            return {
                "status": "posted",
                "url": comment.html_url,
                "target": "commit",
            }

        except Exception as e:
            logger.warning(
                "comment_post_failed",
                repo=repo,
                sha=head_sha[:8] if head_sha else "?",
                kind=kind,
                error=str(e),
            )
            return {
                "status": "failed",
                "url": None,
                "reason": str(e),
            }

    def _find_pr_for_commit(self, repository, head_sha: str):
        """Return the PR (if any) that contains this commit, else None."""
        if not head_sha:
            return None
        try:
            commit = repository.get_commit(head_sha)
            prs = list(commit.get_pulls())
            return prs[0] if prs else None
        except Exception:
            return None

    # ──────────────────────────────────────────────
    # Body builders
    # ──────────────────────────────────────────────
    def _build_dry_run_body(
        self,
        triage: Dict[str, Any],
        plan: Dict[str, Any],
        event_id: str,
        run_url: str,
    ) -> str:
        confidence = triage.get("confidence", 0)
        bar = "🟢" if confidence >= 0.8 else "🟡" if confidence >= 0.6 else "🔴"

        body = f"""## 🤖 RepoMind — Dry-Run Preview

> Your `.repomind.yml` is set to `mode: dry_run`, so I'm only posting a preview.
> Change it to `mode: auto_fix` if you'd like me to open PRs automatically.

### 📋 What I saw

| Field | Value |
|-------|-------|
| **Failure type** | `{triage.get('failure_type', 'unknown')}` |
| **Confidence** | {bar} {confidence:.0%} |
| **Summary** | {triage.get('summary', 'N/A')} |
| **Affected file** | `{triage.get('affected_file', 'N/A')}` |

### 🔧 Proposed fix

**{plan.get('description', 'Auto-fix')}**

"""
        actions = plan.get("actions", []) or []
        if actions:
            body += "**Steps:**\n"
            for i, action in enumerate(actions, 1):
                body += f"{i}. {action}\n"
            body += "\n"

        code_changes = plan.get("code_changes", []) or []
        if code_changes:
            body += "### 📝 Diff preview\n\n"
            for ch in code_changes[:5]:  # cap at 5 to keep comment readable
                file_path = ch.get("file", "?")
                action = ch.get("action", "modify")
                body += f"<details>\n<summary><code>{action}</code> <code>{file_path}</code></summary>\n\n"
                old = ch.get("old_content", "")
                new = ch.get("new_content", "")
                body += "```diff\n"
                if old:
                    for line in old.splitlines()[:30]:
                        body += f"- {line}\n"
                if new:
                    for line in new.splitlines()[:30]:
                        body += f"+ {line}\n"
                body += "```\n\n</details>\n\n"

        if run_url:
            body += f"### 🔗 Failed run\n[{run_url}]({run_url})\n\n"

        body += (
            f"---\n"
            f"_Event ID: `{event_id}` · "
            f"Set `mode: auto_fix` in `.repomind.yml` to enable auto-PRs._"
        )
        return body

    def _build_status_body(
        self,
        triage: Dict[str, Any],
        policy: Dict[str, Any],
        action_taken: str,
        event_id: str,
        pr_url: Optional[str],
        run_url: str,
    ) -> str:
        confidence = triage.get("confidence", 0)
        bar = "🟢" if confidence >= 0.8 else "🟡" if confidence >= 0.6 else "🔴"

        action_msgs = {
            "pr_opened": f"✅ I opened a fix PR: {pr_url}",
            "comment_only": "💬 Posted a dry-run preview (see comment above).",
            "policy_denied": (
                "🚫 Policy denied this fix.\n\n"
                f"**Reason:** {policy.get('reason', 'N/A')}"
            ),
            "mode_disabled": (
                "⏸ This repo's `.repomind.yml` has `mode: disabled` — "
                "I'm sitting this one out."
            ),
            "skipped_no_changes": (
                "🤷 I couldn't generate any specific code changes for this "
                "failure. No PR opened."
            ),
            "error": "❌ Something went wrong while processing this failure.",
        }
        action_line = action_msgs.get(action_taken, f"Action: `{action_taken}`")

        body = f"""## 🤖 RepoMind — saw your CI failure

| Field | Value |
|-------|-------|
| **Failure type** | `{triage.get('failure_type', 'unknown')}` |
| **Confidence** | {bar} {confidence:.0%} |
| **Summary** | {triage.get('summary', 'N/A')} |

{action_line}

"""
        if run_url:
            body += f"_Failed run:_ [{run_url}]({run_url})\n\n"
        body += f"_Event ID: `{event_id}`_"
        return body
