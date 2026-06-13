"""
tests/test_pr_creator_modes.py — PR creator mode dispatch tests (V2)
"""

from unittest.mock import patch, MagicMock
from pr_creator.pr_creator import PRCreator


class TestModeDispatch:
    """The `mode` parameter steers create_pr() to one of three behaviours."""

    def test_mode_disabled_returns_skipped_without_github_call(self):
        creator = PRCreator()
        # Should NOT call GitHub at all
        with patch("shared.github_auth.get_github_client") as mock_gh:
            result = creator.create_pr(
                repo="owner/repo",
                triage={"failure_type": "dependency_error", "confidence": 0.9},
                plan={"risk_level": "low", "code_changes": [{"file": "x.py"}]},
                event_id="evt-disabled-1",
                mode="disabled",
            )
            assert result["status"] == "skipped"
            assert result["mode"] == "disabled"
            assert result["url"] is None
            mock_gh.assert_not_called()

    def test_mode_dry_run_calls_comment_poster_not_github(self):
        creator = PRCreator()
        with (
            patch("shared.github_auth.get_github_client") as mock_gh,
            patch("pr_creator.comment_poster.CommentPoster.post_dry_run") as mock_post,
        ):
            mock_post.return_value = {
                "status": "posted",
                "url": "https://github.com/owner/repo/commit/abc#commitcomment-1",
            }
            result = creator.create_pr(
                repo="owner/repo",
                triage={"failure_type": "dependency_error", "confidence": 0.9},
                plan={"risk_level": "low", "code_changes": [{"file": "x.py"}]},
                event_id="evt-dry-1",
                head_sha="abc123",
                mode="dry_run",
            )
            assert result["mode"] == "dry_run"
            assert result["status"] == "comment_posted"
            assert result["url"] is not None
            # The dry-run path must NOT call GitHub directly via PyGithub —
            # only the comment_poster does that.
            mock_gh.assert_not_called()
            mock_post.assert_called_once()

    def test_mode_auto_fix_uses_default_when_unspecified(self):
        creator = PRCreator()
        with patch("pr_creator.pr_creator.get_github_client") as mock_gh:
            mock_gh.return_value.get_repo.side_effect = RuntimeError("stop here")
            result = creator.create_pr(
                repo="owner/repo",
                triage={"failure_type": "dependency_error", "confidence": 0.9},
                plan={"risk_level": "low", "code_changes": [{"file": "x.py"}]},
                event_id="evt-auto-1",
                # mode omitted — defaults to auto_fix
            )
            # We expect failure (mocked), but the path was auto_fix
            assert result["mode"] == "auto_fix"
            assert result["status"] == "failed"
            mock_gh.assert_called_once()
