"""
tests/test_comment_poster.py — Dry-run + status comment tests (V2)
"""

from unittest.mock import patch, MagicMock

from pr_creator.comment_poster import CommentPoster


class TestDryRunComment:
    def test_post_dry_run_on_commit_when_no_pr(self):
        poster = CommentPoster()
        with patch("pr_creator.comment_poster.get_github_client") as mock_gh:
            commit_mock = MagicMock()
            commit_mock.get_pulls.return_value = []
            comment_mock = MagicMock()
            comment_mock.html_url = "https://github.com/o/r/commit/abc#x"
            commit_mock.create_comment.return_value = comment_mock

            repo_mock = MagicMock()
            repo_mock.get_commit.return_value = commit_mock
            mock_gh.return_value.get_repo.return_value = repo_mock

            result = poster.post_dry_run(
                repo="o/r",
                head_sha="abc",
                triage={"failure_type": "dependency_error", "confidence": 0.85},
                plan={"description": "install foo"},
                event_id="evt-1",
            )
        assert result["status"] == "posted"
        assert result["target"] == "commit"

    def test_post_dry_run_on_pr_when_commit_belongs_to_one(self):
        poster = CommentPoster()
        with patch("pr_creator.comment_poster.get_github_client") as mock_gh:
            pr_mock = MagicMock()
            pr_mock.number = 7
            comment_mock = MagicMock()
            comment_mock.html_url = "https://github.com/o/r/pull/7#issuecomment-1"
            pr_mock.create_issue_comment.return_value = comment_mock

            commit_mock = MagicMock()
            commit_mock.get_pulls.return_value = [pr_mock]
            repo_mock = MagicMock()
            repo_mock.get_commit.return_value = commit_mock
            mock_gh.return_value.get_repo.return_value = repo_mock

            result = poster.post_dry_run(
                repo="o/r",
                head_sha="abc",
                triage={"failure_type": "import_error", "confidence": 0.9},
                plan={"description": "fix import"},
                event_id="evt-2",
            )
        assert result["status"] == "posted"
        assert result["target"] == "pr"
        assert result["pr_number"] == 7

    def test_post_dry_run_handles_github_failure(self):
        poster = CommentPoster()
        with patch("pr_creator.comment_poster.get_github_client") as mock_gh:
            mock_gh.side_effect = RuntimeError("api down")
            result = poster.post_dry_run(
                repo="o/r",
                head_sha="abc",
                triage={"failure_type": "dependency_error"},
                plan={},
                event_id="evt-3",
            )
        assert result["status"] == "failed"
        assert "api down" in result["reason"]


class TestStatusComment:
    def test_status_pr_opened(self):
        poster = CommentPoster()
        with patch.object(CommentPoster, "_post_comment") as mock_post:
            mock_post.return_value = {"status": "posted", "url": "x"}
            poster.post_status(
                repo="o/r",
                head_sha="abc",
                triage={"failure_type": "dependency_error", "confidence": 0.9},
                policy={"decision": "allow"},
                action_taken="pr_opened",
                event_id="evt-x",
                pr_url="https://github.com/o/r/pull/1",
            )
            body = mock_post.call_args.args[2]
            assert "I opened a fix PR" in body

    def test_status_policy_denied(self):
        poster = CommentPoster()
        with patch.object(CommentPoster, "_post_comment") as mock_post:
            mock_post.return_value = {"status": "posted", "url": "x"}
            poster.post_status(
                repo="o/r",
                head_sha="abc",
                triage={"failure_type": "secrets_error", "confidence": 0.5},
                policy={"decision": "deny", "reason": "Secrets unsafe"},
                action_taken="policy_denied",
                event_id="evt-y",
            )
            body = mock_post.call_args.args[2]
            assert "Policy denied" in body
            assert "Secrets unsafe" in body

    def test_status_mode_disabled(self):
        poster = CommentPoster()
        with patch.object(CommentPoster, "_post_comment") as mock_post:
            mock_post.return_value = {"status": "posted", "url": "x"}
            poster.post_status(
                repo="o/r",
                head_sha="abc",
                triage={"failure_type": "test_failure", "confidence": 0.5},
                policy={},
                action_taken="mode_disabled",
                event_id="evt-z",
            )
            body = mock_post.call_args.args[2]
            assert "disabled" in body.lower()


class TestApologyComment:
    def test_post_apology_calls_underlying_post(self):
        poster = CommentPoster()
        with patch.object(CommentPoster, "_post_comment") as mock_post:
            mock_post.return_value = {"status": "posted", "url": "x"}
            result = poster.post_apology(
                repo="o/r",
                head_sha="abc",
                event_id="evt-q",
                reason="Reviewer said no.",
            )
        assert result["status"] == "posted"
        body = mock_post.call_args.args[2]
        assert "rejected" in body.lower()
        assert "Reviewer said no." in body
