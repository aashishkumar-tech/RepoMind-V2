"""
pr_creator/pr_creator.py — GitHub Pull Request Creator

HOW IT WORKS:
─────────────
Creates a pull request on the target repository with the auto-fix changes.

Flow:
    1. Authenticate via GitHub App
    2. Get the default branch (main/master)
    3. Create a UNIQUE fix branch:
         fix/<failure-type>-<YYYYMMDD-HHMMSS>-<event-id-suffix>
       (timestamp + event-id suffix → collision-proof across retries
        and multi-event-per-day scenarios)
    4. Filter out files our App is not allowed to touch
       (e.g. `.github/workflows/*` needs `workflows: write` permission)
    5. Apply remaining code changes from the plan
    6. If NOTHING applied → write a fix-report placeholder so the PR has
       at least one commit (otherwise GitHub 422 "No commits between …")
    7. Create the PR with a descriptive title and body
    8. Return PR metadata (url, branch, status)

V2 MULTI-TENANCY:
    `create_pr()` accepts `installation_id` and threads it through
    `get_github_client(installation_id)`. This guarantees we mint a token
    for the customer's actual GitHub App install — without it, the auth
    layer falls back to the env var (Account A) and we get 403 Forbidden
    on cross-account repos.

V2 PERMISSION SAFETY:
    GitHub blocks ANY app from creating/updating files under
    `.github/workflows/` unless the App has the `workflows: write`
    permission. RepoMind's manifest doesn't (intentionally — minimal
    blast radius). We filter those paths out before applying, and log
    a `workflow_file_skipped` event so reviewers can see what was
    proposed and apply it by hand.

V2 BRANCH COLLISION HARDENING (Bug #5):
    Old naming `fix/<failure_type>-<YYYYMMDD>` collided on multiple
    failures of the same type on the same day. New naming includes:
      • UTC timestamp down to the second
      • 8-char event-id suffix
      • Auto-retry with random suffix if the (extremely unlikely) collision
        still occurs
    Result: every fix attempt lands on its own pristine branch.
    No more "pushing new commits to a stale PR branch" footguns.

COMMUNICATION:
─────────────
Worker calls (via hitl_nodes.pr_creator_node):
    PRCreator().create_pr(repo, triage, plan, event_id, head_branch,
                          installation_id=139630626)
Returns: { url, branch, commit_sha, title, status }
Stored in artifacts.json → "pr" section
"""

import re
import secrets as _stdlib_secrets   # stdlib — aliased to avoid clashing with shared/secrets.py
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timezone

from shared.github_auth import get_github_client
from shared.logger import get_logger

logger = get_logger("pr_creator.pr_creator")


# ── Paths the App is NOT allowed to write to (without extra permissions) ──
#
# GitHub returns 403 for any PUT to .github/workflows/* unless the App
# manifest declares `workflows: write`. We skip those files at apply-time
# and surface them in the PR body so a human can apply them manually.
RESTRICTED_PATH_PREFIXES = (
    ".github/workflows/",
)


# ──────────────────────────────────────────────
# Git ref name hygiene
# ──────────────────────────────────────────────
# Git refs disallow many characters (spaces, colons, ~, ^, :, ?, *, [, \, .., //).
# We collapse anything outside [A-Za-z0-9_-] to a single dash, then de-dupe dashes.
# This protects branch creation from weird triage outputs like
# "failure_type": "test: ImportError!" → would otherwise raise an invalid-ref error.
_GIT_REF_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_\-]+")


def _sanitize_for_git_ref(value: str, fallback: str = "unknown") -> str:
    """Make a string safe to embed in a Git ref name."""
    if not value:
        return fallback
    cleaned = _GIT_REF_SANITIZE_RE.sub("-", value.strip())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return cleaned or fallback


class PRCreator:
    """Creates GitHub Pull Requests with auto-fix changes."""

    def create_pr(
        self,
        repo: str,
        triage: Dict[str, Any],
        plan: Dict[str, Any],
        event_id: str,
        head_branch: str = "",
        head_sha: str = "",
        run_url: str = "",
        mode: str = "auto_fix",
        installation_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Create a pull request with the proposed fix — OR post a dry-run
        comment, OR skip entirely, depending on `mode`.

        Args:
            repo:            Repository full name (e.g. "user/mlproject")
            triage:          Triage result from Step 5
            plan:            Fix plan from Step 6
            event_id:        Unique event ID
            head_branch:     The branch that failed (target for PR)
            head_sha:        Commit SHA that triggered the failure (for comments)
            run_url:         URL of the failed CI run (for comment context)
            mode:            "auto_fix" → open PR (default, legacy behaviour)
                             "dry_run"  → post a comment, no PR
                             "disabled" → do nothing, return skipped
            installation_id: V2 multi-tenancy — GitHub App install ID to use
                             when authenticating. None falls back to env var.

        Returns:
            PR/comment metadata dict with url, branch, status, etc.
        """
        # ── V2: Honor user's `.repomind.yml` mode ──
        if mode == "disabled":
            logger.info("pr_skipped_mode_disabled", repo=repo, event_id=event_id)
            return {
                "url": None,
                "branch": None,
                "commit_sha": None,
                "title": None,
                "status": "skipped",
                "reason": "mode=disabled in .repomind.yml",
                "mode": "disabled",
            }

        if mode == "dry_run":
            logger.info("pr_dry_run", repo=repo, event_id=event_id)
            from pr_creator.comment_poster import CommentPoster
            poster = CommentPoster()
            comment_result = poster.post_dry_run(
                repo=repo,
                head_sha=head_sha,
                triage=triage,
                plan=plan,
                event_id=event_id,
                run_url=run_url,
            )
            return {
                "url": comment_result.get("url"),
                "branch": None,
                "commit_sha": None,
                "title": "[Dry-run] Proposed fix preview",
                "status": "comment_posted" if comment_result.get("status") == "posted" else "comment_failed",
                "mode": "dry_run",
                "comment": comment_result,
            }

        # mode == "auto_fix" → original PR creation flow
        try:
            # V2 multi-tenancy: use the specific install ID for this repo's
            # account; falls back to env var if None.
            g = get_github_client(installation_id)
            repository = g.get_repo(repo)

            # Determine base branch
            base_branch = head_branch or repository.default_branch

            # ── Build a unique branch name (timestamp + event-id suffix) ──
            # See _build_fix_branch_name for collision-prevention rationale.
            failure_type = triage.get("failure_type", "unknown")
            fix_branch = self._build_fix_branch_name(failure_type, event_id)

            logger.info(
                "creating_pr",
                repo=repo,
                base_branch=base_branch,
                fix_branch=fix_branch,
                installation_id=installation_id,
            )

            # Get the latest commit SHA of the base branch
            base_ref = repository.get_git_ref(f"heads/{base_branch}")
            base_sha = base_ref.object.sha

            # ── Create the fix branch, with retry-on-collision ──
            # Collisions are astronomically rare (would require same failure_type
            # + same UTC second + same 8-char event-id suffix), but if Lambda
            # warm-start clock skew or a deliberate retry races, we append a
            # random suffix and try again rather than reusing a stale branch.
            MAX_BRANCH_CREATE_ATTEMPTS = 5
            branch_created = False
            for attempt in range(1, MAX_BRANCH_CREATE_ATTEMPTS + 1):
                try:
                    repository.create_git_ref(
                        ref=f"refs/heads/{fix_branch}",
                        sha=base_sha,
                    )
                    logger.info(
                        "branch_created",
                        branch=fix_branch,
                        attempt=attempt,
                    )
                    branch_created = True
                    break
                except Exception as e:
                    if "Reference already exists" in str(e):
                        # Add 4 hex chars and retry — never reuse an existing branch
                        suffix = _stdlib_secrets.token_hex(2)
                        old = fix_branch
                        fix_branch = f"{fix_branch}-{suffix}"
                        logger.warning(
                            "branch_collision_retrying",
                            old_branch=old,
                            new_branch=fix_branch,
                            attempt=attempt,
                        )
                        continue
                    # Any other error (auth, rate limit, etc.) — propagate
                    raise

            if not branch_created:
                raise RuntimeError(
                    f"Failed to create unique fix branch after "
                    f"{MAX_BRANCH_CREATE_ATTEMPTS} attempts (last tried: {fix_branch})"
                )

            # ── V2: Filter out files we don't have permission to touch ──
            raw_changes: List[Dict[str, Any]] = plan.get("code_changes", []) or []
            applicable_changes, skipped_changes = self._split_changes_by_permission(
                raw_changes
            )

            if skipped_changes:
                for sk in skipped_changes:
                    logger.warning(
                        "workflow_file_skipped",
                        file=sk.get("file"),
                        reason="GitHub App lacks `workflows: write` permission",
                        event_id=event_id,
                    )

            # ── Apply the allowed code changes ──
            commit_sha: Optional[str] = None
            if applicable_changes:
                commit_sha = self._apply_changes(
                    repository, fix_branch, applicable_changes, event_id
                )

            # ── If nothing was applied (no changes, or all were filtered),
            #    fall back to a placeholder commit so the PR has at least
            #    one diff. Otherwise GitHub returns 422 on create_pull. ──
            if not commit_sha:
                if skipped_changes:
                    logger.info(
                        "pr_using_placeholder_commit",
                        event_id=event_id,
                        reason="All proposed changes were in restricted paths",
                        skipped_count=len(skipped_changes),
                    )
                else:
                    logger.info(
                        "pr_using_placeholder_commit",
                        event_id=event_id,
                        reason="No applicable code changes from planner",
                    )

                commit_sha = self._create_placeholder_commit(
                    repository=repository,
                    branch=fix_branch,
                    triage=triage,
                    plan=plan,
                    event_id=event_id,
                    skipped_changes=skipped_changes,
                )

            if not commit_sha:
                # Even the placeholder failed — give up cleanly.
                logger.warning(
                    "pr_skipped_no_commit",
                    event_id=event_id,
                    repo=repo,
                    reason="No commit could be created on the fix branch",
                )
                return {
                    "url": None,
                    "branch": fix_branch,
                    "commit_sha": None,
                    "title": None,
                    "status": "skipped",
                    "reason": "No actionable code changes generated",
                    "mode": "auto_fix",
                }

            # Build PR title and body
            title = self._build_pr_title(triage, plan)
            body = self._build_pr_body(
                triage, plan, event_id, skipped_changes=skipped_changes
            )

            # Create the Pull Request
            pr = repository.create_pull(
                title=title,
                body=body,
                head=fix_branch,
                base=base_branch,
            )

            # Add label
            try:
                pr.add_to_labels("repomind-auto-fix")
            except Exception:
                pass  # Label might not exist

            result = {
                "url": pr.html_url,
                "number": pr.number,
                "branch": fix_branch,
                "commit_sha": commit_sha or base_sha,
                "title": title,
                "status": "created",
                "mode": "auto_fix",
                "skipped_files": [c.get("file") for c in skipped_changes],
            }

            logger.info(
                "pr_created",
                pr_url=pr.html_url,
                pr_number=pr.number,
                installation_id=installation_id,
                skipped_files=result["skipped_files"],
            )
            return result

        except Exception as e:
            logger.error("pr_creation_failed", repo=repo, error=str(e))
            return {
                "url": None,
                "branch": None,
                "commit_sha": None,
                "title": None,
                "status": "failed",
                "error": str(e),
                "mode": "auto_fix",
            }

    # ──────────────────────────────────────────────
    # Branch naming (collision-free)
    # ──────────────────────────────────────────────
    def _build_fix_branch_name(self, failure_type: str, event_id: str) -> str:
        """
        Build a unique branch name for this fix attempt.

        Format: fix/<failure_type>-<YYYYMMDD-HHMMSS>-<short_event_id>

        Examples:
            fix/import_error-20260613-143052-a8b3c9d1
            fix/test_failure-20260613-143055-f4e2a1b8
            fix/unknown-20260613-143100-7f3e8b2a       (no event_id → random)

        Why this format guarantees uniqueness:
            • failure_type   → human-readable category (sanitized for git)
            • UTC timestamp  → two distinct CI events can't share the same
                               second; even retries-of-retries get a fresh ref
            • short_event_id → debugging breadcrumb (matches CloudWatch logs)

        Replaces the old `fix/<failure_type>-<YYYYMMDD>` scheme which
        collided whenever the same failure_type happened twice on the
        same day (Bug #5).
        """
        safe_type = _sanitize_for_git_ref(failure_type, fallback="unknown")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        short_id = (
            event_id.split("-")[-1][:8]
            if event_id
            else _stdlib_secrets.token_hex(4)
        )
        short_id = _sanitize_for_git_ref(short_id, fallback="fix")
        return f"fix/{safe_type}-{timestamp}-{short_id}"

    # ──────────────────────────────────────────────
    # Permission gating
    # ──────────────────────────────────────────────
    @staticmethod
    def _is_restricted_path(file_path: str) -> bool:
        """Return True if our App is NOT permitted to write to this path."""
        if not file_path:
            return False
        return any(file_path.startswith(p) for p in RESTRICTED_PATH_PREFIXES)

    def _split_changes_by_permission(
        self, changes: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Split `code_changes` into (applicable, skipped) lists based on
        whether the App is allowed to write the target path.
        """
        applicable: List[Dict[str, Any]] = []
        skipped: List[Dict[str, Any]] = []
        for change in changes:
            file_path = change.get("file", "")
            if self._is_restricted_path(file_path):
                skipped.append(change)
            else:
                applicable.append(change)
        return applicable, skipped

    # ──────────────────────────────────────────────
    # File mutation
    # ──────────────────────────────────────────────
    def _apply_changes(
        self,
        repository,
        branch: str,
        changes: list,
        event_id: str,
    ) -> Optional[str]:
        """
        Apply code changes to the fix branch.

        Each change can create, modify, or delete a file.
        """
        commit_sha = None

        for change in changes:
            file_path = change.get("file", "")
            action = change.get("action", "modify")
            new_content = change.get("new_content", "")
            description = change.get("description", "Auto-fix")

            if not file_path:
                continue

            # Defensive: the planner shouldn't send restricted paths after the
            # split, but if a NEW restricted prefix sneaks in, skip rather than 403.
            if self._is_restricted_path(file_path):
                logger.warning(
                    "workflow_file_skipped_inline",
                    file=file_path,
                    reason="restricted_path",
                )
                continue

            try:
                if action == "create":
                    result = repository.create_file(
                        path=file_path,
                        message=f"fix: {description} [{event_id[:20]}]",
                        content=new_content,
                        branch=branch,
                    )
                    commit_sha = result["commit"].sha

                elif action == "modify":
                    # Get current file content
                    file_obj = repository.get_contents(file_path, ref=branch)
                    current_content = file_obj.decoded_content.decode("utf-8")

                    # Apply modification
                    old_content = change.get("old_content", "")
                    if old_content and old_content in current_content:
                        updated = current_content.replace(old_content, new_content, 1)
                    else:
                        updated = new_content

                    result = repository.update_file(
                        path=file_path,
                        message=f"fix: {description} [{event_id[:20]}]",
                        content=updated,
                        sha=file_obj.sha,
                        branch=branch,
                    )
                    commit_sha = result["commit"].sha

                elif action == "delete":
                    file_obj = repository.get_contents(file_path, ref=branch)
                    result = repository.delete_file(
                        path=file_path,
                        message=f"fix: remove {file_path} [{event_id[:20]}]",
                        sha=file_obj.sha,
                        branch=branch,
                    )
                    commit_sha = result["commit"].sha

                logger.info("file_changed", file=file_path, action=action)

            except Exception as e:
                logger.error("file_change_failed", file=file_path, error=str(e))

        return commit_sha

    # ──────────────────────────────────────────────
    # Placeholder commit (used when no applicable changes succeeded)
    # ──────────────────────────────────────────────
    def _create_placeholder_commit(
        self,
        repository,
        branch: str,
        triage: Dict[str, Any],
        plan: Dict[str, Any],
        event_id: str,
        skipped_changes: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[str]:
        """
        Create a placeholder file when no specific code changes are
        applicable on this branch.

        Ensures the PR has at least one commit diff (otherwise GitHub
        rejects create_pull with 422 "No commits between main and …").
        """
        skipped_changes = skipped_changes or []
        timestamp = datetime.now(timezone.utc).isoformat()

        content = (
            f"# 🤖 RepoMind Auto-Fix Report\n\n"
            f"**Event ID:** `{event_id}`\n"
            f"**Failure Type:** `{triage.get('failure_type', 'unknown')}`\n"
            f"**Summary:** {triage.get('summary', 'N/A')}\n"
            f"**Confidence:** {triage.get('confidence', 0)}\n"
            f"**Plan:** {plan.get('description', 'N/A')}\n"
            f"**Generated:** {timestamp}\n\n"
            f"## Suggested Actions\n\n"
        )
        for i, action in enumerate(plan.get("actions", []), 1):
            content += f"{i}. {action}\n"

        if skipped_changes:
            content += (
                f"\n## ⚠️ Restricted Files (Manual Action Required)\n\n"
                f"RepoMind proposed changes to the following files, but the "
                f"GitHub App does not have permission to modify them "
                f"(this needs `workflows: write`):\n\n"
            )
            for sk in skipped_changes:
                content += f"- `{sk.get('file', '?')}` — {sk.get('description', 'change proposed')}\n"
            content += (
                "\nPlease apply these changes manually if appropriate, or grant the "
                "App `workflows: write` permission and re-run.\n"
            )

        content += (
            f"\n---\n"
            f"*This file was auto-generated by RepoMind. "
            f"Please review and apply the suggested fixes, then delete this file.*\n"
        )

        try:
            result = repository.create_file(
                path=f".repomind/{event_id[:30]}-fix-report.md",
                message=f"fix: auto-fix report for {triage.get('failure_type', 'unknown')} [{event_id[:20]}]",
                content=content,
                branch=branch,
            )
            return result["commit"].sha
        except Exception as e:
            logger.error("placeholder_commit_failed", error=str(e))
            return None

    # ──────────────────────────────────────────────
    # PR title + body
    # ──────────────────────────────────────────────
    def _build_pr_title(self, triage: Dict[str, Any], plan: Dict[str, Any]) -> str:
        """Build a descriptive PR title."""
        failure_type = triage.get("failure_type", "unknown").replace("_", " ")
        description = plan.get("description", f"Fix {failure_type}")
        return f"🤖 [RepoMind] {description}"

    def _build_pr_body(
        self,
        triage: Dict[str, Any],
        plan: Dict[str, Any],
        event_id: str,
        skipped_changes: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Build a detailed PR body with all context."""
        skipped_changes = skipped_changes or []
        confidence = triage.get("confidence", 0)
        confidence_bar = "🟢" if confidence >= 0.8 else "🟡" if confidence >= 0.6 else "🔴"

        body = f"""## 🤖 RepoMind Auto-Fix

> This pull request was automatically generated by **RepoMind V2**.

### 📋 Failure Analysis

| Field | Value |
|-------|-------|
| **Failure Type** | `{triage.get('failure_type', 'unknown')}` |
| **Confidence** | {confidence_bar} {confidence:.0%} |
| **Summary** | {triage.get('summary', 'N/A')} |
| **Affected File** | `{triage.get('affected_file', 'N/A')}` |
| **Event ID** | `{event_id}` |

### 🔧 Fix Applied

**{plan.get('description', 'Auto-fix')}**

"""
        actions = plan.get("actions", [])
        if actions:
            body += "**Steps:**\n"
            for i, action in enumerate(actions, 1):
                body += f"{i}. {action}\n"
            body += "\n"

        files = plan.get("files_to_modify", [])
        if files:
            body += "**Files Modified:**\n"
            for f in files:
                body += f"- `{f}`\n"
            body += "\n"

        if skipped_changes:
            body += (
                "### ⚠️ Restricted Files Skipped\n\n"
                "RepoMind proposed changes to files that the GitHub App is not "
                "permitted to modify (these need `workflows: write` permission). "
                "Please apply these manually if appropriate:\n\n"
            )
            for sk in skipped_changes:
                body += f"- `{sk.get('file', '?')}` — {sk.get('description', 'change proposed')}\n"
            body += "\n"

        body += f"""### ⚠️ Human-in-the-Loop Required

> **This PR will NOT be auto-merged.** RepoMind is waiting for a human review.
>
> - ✅ **Approve** the PR → RepoMind may auto-merge (if your `.repomind.yml` allows it).
> - ❌ **Request changes** → RepoMind will close the PR and post an apology comment.
> - 💬 **Comment only** → RepoMind keeps waiting.

Please review carefully before approving:
- [ ] Fix is correct and complete
- [ ] No unintended side effects
- [ ] Tests pass

### 🔗 Links
- Event ID: `{event_id}`

---
*Generated by [RepoMind](https://github.com/repomind) CI Auto-Fix Agent · V2*
"""
        return body