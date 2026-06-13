"""
tests/test_welcome_pr.py — Welcome PR generator tests (V2)
"""

from unittest.mock import patch, MagicMock

from pr_creator.welcome_pr import WelcomePRCreator, WELCOME_PR_BODY, WELCOME_README


class TestWelcomePR:
    def test_skips_if_yml_already_exists(self):
        creator = WelcomePRCreator()
        with patch("pr_creator.welcome_pr.get_github_client") as mock_gh:
            repo_mock = MagicMock()
            repo_mock.default_branch = "main"
            # get_contents on .repomind.yml succeeds → skip
            repo_mock.get_contents.return_value = MagicMock()
            mock_gh.return_value.get_repo.return_value = repo_mock

            result = creator.create_welcome_pr("owner/repo")

        assert result["status"] == "skipped"
        assert ".repomind.yml already exists" in result["reason"]

    def test_skips_if_branch_already_exists(self):
        creator = WelcomePRCreator()
        with patch("pr_creator.welcome_pr.get_github_client") as mock_gh:
            repo_mock = MagicMock()
            repo_mock.default_branch = "main"
            # No yml → branch check
            repo_mock.get_contents.side_effect = Exception("Not found")
            # Branch exists
            repo_mock.get_branch.return_value = MagicMock()
            mock_gh.return_value.get_repo.return_value = repo_mock

            result = creator.create_welcome_pr("owner/repo")

        assert result["status"] == "skipped"
        assert "already exists" in result["reason"]

    def test_creates_pr_when_repo_is_clean(self):
        creator = WelcomePRCreator()
        with patch("pr_creator.welcome_pr.get_github_client") as mock_gh:
            repo_mock = MagicMock()
            repo_mock.default_branch = "main"
            # No yml, no branch
            repo_mock.get_contents.side_effect = Exception("Not found")
            repo_mock.get_branch.side_effect = Exception("Not found")
            # ref + commit setup
            base_ref_mock = MagicMock()
            base_ref_mock.object.sha = "deadbeef"
            repo_mock.get_git_ref.return_value = base_ref_mock
            # PR creation
            pr_mock = MagicMock()
            pr_mock.html_url = "https://github.com/owner/repo/pull/1"
            pr_mock.number = 1
            repo_mock.create_pull.return_value = pr_mock
            mock_gh.return_value.get_repo.return_value = repo_mock

            result = creator.create_welcome_pr("owner/repo")

        assert result["status"] == "created"
        assert result["url"] == "https://github.com/owner/repo/pull/1"
        assert result["number"] == 1
        # Two file creations (yml + readme)
        assert repo_mock.create_file.call_count == 2

    def test_pr_body_mentions_safe_defaults(self):
        assert "dry_run" in WELCOME_PR_BODY
        assert "hitl_required" in WELCOME_PR_BODY
        assert "auto-merge" in WELCOME_PR_BODY.lower()

    def test_readme_documents_three_modes(self):
        assert "disabled" in WELCOME_README
        assert "dry_run" in WELCOME_README
        assert "auto_fix" in WELCOME_README

    def test_failed_when_github_raises(self):
        creator = WelcomePRCreator()
        with patch("pr_creator.welcome_pr.get_github_client") as mock_gh:
            mock_gh.side_effect = RuntimeError("boom")
            result = creator.create_welcome_pr("owner/repo")
        assert result["status"] == "failed"
        assert "boom" in result["reason"]
