"""
tests/test_hitl.py — Human-in-the-Loop nodes + checkpointer tests (V2)
"""

from unittest.mock import patch, MagicMock

from agents.hitl_nodes import (
    pr_creator_node,
    merge_decision_node,
    merge_node,
    cleanup_node,
    route_after_merge_decision,
)
from review.models import ReviewMessage


class TestPRCreatorNode:
    def test_policy_denied_short_circuits(self):
        state = {
            "event_id": "evt-1",
            "repo": "owner/repo",
            "policy": {"decision": "deny", "reason": "test deny"},
            "mode": "auto_fix",
            "hitl_required": True,
        }
        result = pr_creator_node(state)
        assert result["pr"]["status"] == "skipped"
        assert result["human_approval"] == "skipped"
        assert result["status"] == "denied"

    def test_disabled_mode_short_circuits(self):
        state = {
            "event_id": "evt-2",
            "repo": "owner/repo",
            "policy": {"decision": "allow"},
            "mode": "disabled",
            "hitl_required": True,
        }
        result = pr_creator_node(state)
        assert result["pr"]["mode"] == "disabled"
        assert result["human_approval"] == "skipped"

    def test_auto_fix_with_hitl_pauses(self):
        state = {
            "event_id": "evt-3",
            "repo": "owner/repo",
            "policy": {"decision": "allow"},
            "triage": {"failure_type": "dependency_error", "confidence": 0.9},
            "plan_summary": {"risk_level": "low", "code_changes": [{"file": "x.py"}]},
            "head_branch": "main",
            "head_sha": "abc",
            "run_url": "",
            "mode": "auto_fix",
            "hitl_required": True,
        }
        with (
            patch("pr_creator.pr_creator.PRCreator.create_pr") as mock_create,
            patch("pr_creator.comment_poster.CommentPoster.post_status") as mock_status,
        ):
            mock_create.return_value = {
                "status": "created",
                "url": "https://github.com/owner/repo/pull/42",
                "number": 42,
                "branch": "fix/dep-1",
                "mode": "auto_fix",
            }
            mock_status.return_value = {"status": "posted"}
            result = pr_creator_node(state)
        assert result["pr"]["status"] == "created"
        assert result["pr_url"] == "https://github.com/owner/repo/pull/42"
        assert result["pr_number"] == 42
        assert result["human_approval"] == "pending"
        assert result["status"] == "awaiting_review"

    def test_auto_fix_without_hitl_auto_approves(self):
        state = {
            "event_id": "evt-4",
            "repo": "owner/repo",
            "policy": {"decision": "allow"},
            "triage": {"failure_type": "dependency_error", "confidence": 0.9},
            "plan_summary": {"risk_level": "low", "code_changes": [{"file": "x.py"}]},
            "head_branch": "main",
            "head_sha": "abc",
            "run_url": "",
            "mode": "auto_fix",
            "hitl_required": False,
        }
        with (
            patch("pr_creator.pr_creator.PRCreator.create_pr") as mock_create,
            patch("pr_creator.comment_poster.CommentPoster.post_status"),
        ):
            mock_create.return_value = {
                "status": "created",
                "url": "https://github.com/owner/repo/pull/43",
                "number": 43,
                "branch": "fix/dep-1",
                "mode": "auto_fix",
            }
            result = pr_creator_node(state)
        assert result["human_approval"] == "approved"
        assert result["status"] == "auto_merge_pending"


class TestRouter:
    def test_approved_routes_to_merge(self):
        assert route_after_merge_decision({"human_approval": "approved"}) == "merge"

    def test_rejected_routes_to_cleanup(self):
        assert route_after_merge_decision({"human_approval": "rejected"}) == "cleanup"

    def test_skipped_routes_to_end(self):
        assert route_after_merge_decision({"human_approval": "skipped"}) == "end"

    def test_pending_routes_to_end(self):
        assert route_after_merge_decision({"human_approval": "pending"}) == "end"


class TestMergeNode:
    def test_no_pr_number_skipped(self):
        state = {"event_id": "evt-1", "repo": "owner/repo", "pr_number": 0}
        result = merge_node(state)
        assert result["merge_result"]["status"] == "skipped"

    def test_already_merged_returns_idempotent(self):
        state = {
            "event_id": "evt-1",
            "repo": "owner/repo",
            "pr_number": 7,
        }
        with patch("shared.github_auth.get_github_client") as mock_gh:
            mock_pr = MagicMock()
            mock_pr.merged = True
            mock_pr.merged_at = None
            mock_pr.merge_commit_sha = "deadbeef"
            mock_gh.return_value.get_repo.return_value.get_pull.return_value = mock_pr
            result = merge_node(state)
        assert result["merge_result"]["status"] == "already_merged"

    def test_successful_merge(self):
        state = {
            "event_id": "evt-1",
            "repo": "owner/repo",
            "pr_number": 8,
        }
        with patch("shared.github_auth.get_github_client") as mock_gh:
            mock_pr = MagicMock()
            mock_pr.merged = False
            mock_pr.mergeable = True
            mock_merge = MagicMock()
            mock_merge.sha = "facefeed"
            mock_pr.merge.return_value = mock_merge
            mock_gh.return_value.get_repo.return_value.get_pull.return_value = mock_pr
            result = merge_node(state)
        assert result["merge_result"]["status"] == "merged"
        assert result["merge_result"]["merge_commit_sha"] == "facefeed"


class TestCleanupNode:
    def test_closes_pr_and_deletes_branch(self):
        state = {
            "event_id": "evt-cleanup-1",
            "repo": "owner/repo",
            "pr_number": 10,
            "pr": {"branch": "fix/dep-1"},
            "head_sha": "abc",
            "review_data": {"body": "Looks wrong"},
        }
        with (
            patch("shared.github_auth.get_github_client") as mock_gh,
            patch("pr_creator.comment_poster.CommentPoster.post_apology") as mock_apology,
        ):
            mock_pr = MagicMock()
            mock_pr.state = "open"
            mock_repo = MagicMock()
            mock_repo.get_pull.return_value = mock_pr
            mock_ref = MagicMock()
            mock_repo.get_git_ref.return_value = mock_ref
            mock_gh.return_value.get_repo.return_value = mock_repo
            mock_apology.return_value = {"status": "posted"}

            result = cleanup_node(state)

        assert result["cleanup_result"]["status"] == "rejected"
        assert result["cleanup_result"]["pr_closed"] is True
        assert result["cleanup_result"]["branch_deleted"] is True
        assert result["cleanup_result"]["apology_posted"] is True
        mock_pr.edit.assert_called_with(state="closed")
        mock_ref.delete.assert_called_once()


class TestReviewMessage:
    def test_to_human_approval_approved(self):
        msg = ReviewMessage(repo="owner/repo", pr_number=1, review_state="approved")
        assert msg.to_human_approval() == "approved"

    def test_to_human_approval_changes_requested(self):
        msg = ReviewMessage(
            repo="owner/repo", pr_number=1, review_state="changes_requested"
        )
        assert msg.to_human_approval() == "rejected"

    def test_to_human_approval_commented_is_pending(self):
        msg = ReviewMessage(repo="owner/repo", pr_number=1, review_state="commented")
        assert msg.to_human_approval() == "pending"

    def test_to_human_approval_dismissed_is_pending(self):
        msg = ReviewMessage(repo="owner/repo", pr_number=1, review_state="dismissed")
        assert msg.to_human_approval() == "pending"
