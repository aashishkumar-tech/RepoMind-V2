"""
tests/test_github_auth_multitenant.py — Multi-Tenancy Tests for GitHub App Auth

WHAT THIS TESTS:
─────────────
The core multi-tenancy fix in shared/github_auth.py. A single GitHub App
can be installed on many accounts (each with its own installation_id).
This test suite verifies that:

  1. When `installation_id` is passed explicitly → it wins (webhook path).
  2. When `installation_id` is NOT passed → env var fallback works (local dev).
  3. Different installs get different tokens (no cross-tenant leakage).
  4. Tokens are cached per-install (no thundering herd).
  5. When neither source has a value → we raise (no silent default).

WHY:
    Before this fix, the GitHub App could only ever act on ONE installation
    (the one baked into the env var at deploy time). Installing on a second
    account always 403'd with "Resource not accessible by integration".
"""

from unittest.mock import MagicMock, patch

import pytest

from shared import github_auth


@pytest.fixture(autouse=True)
def _reset_cache():
    """Make sure every test starts with a clean token cache."""
    github_auth.reset_token_cache()
    yield
    github_auth.reset_token_cache()


# ──────────────────────────────────────────────
# _resolve_installation_id
# ──────────────────────────────────────────────
class TestResolveInstallationId:
    def test_explicit_id_wins(self, monkeypatch):
        """When caller passes an ID, it should be used (not the env var)."""
        monkeypatch.setattr(github_auth.settings, "GITHUB_INSTALLATION_ID", "111")
        assert github_auth._resolve_installation_id(999) == 999

    def test_env_fallback_when_none(self, monkeypatch):
        """When caller passes None, fall back to env var."""
        monkeypatch.setattr(github_auth.settings, "GITHUB_INSTALLATION_ID", "222")
        assert github_auth._resolve_installation_id(None) == 222

    def test_env_fallback_when_zero(self, monkeypatch):
        """When caller passes 0 (no install in webhook payload), fall back."""
        monkeypatch.setattr(github_auth.settings, "GITHUB_INSTALLATION_ID", "333")
        assert github_auth._resolve_installation_id(0) == 333

    def test_raises_when_neither(self, monkeypatch):
        """When neither source has a value, raise ValueError."""
        monkeypatch.setattr(github_auth.settings, "GITHUB_INSTALLATION_ID", "")
        with pytest.raises(ValueError, match="No GitHub App installation_id"):
            github_auth._resolve_installation_id(None)

    def test_string_env_var_is_coerced_to_int(self, monkeypatch):
        """Env var is a string ('123'); resolver should int() it."""
        monkeypatch.setattr(github_auth.settings, "GITHUB_INSTALLATION_ID", "456")
        result = github_auth._resolve_installation_id(None)
        assert result == 456
        assert isinstance(result, int)


# ──────────────────────────────────────────────
# get_installation_token — token cache isolation
# ──────────────────────────────────────────────
class TestTokenCacheIsolation:
    """The bug we are fixing: different installs MUST get different tokens."""

    @patch("shared.github_auth.GithubIntegration")
    @patch("shared.github_auth._read_private_key")
    def test_different_installs_get_different_tokens(self, mock_pem, mock_integration):
        """
        Install A and install B must each get their OWN token from GitHub.
        This is the regression test for the 403 'Resource not accessible by
        integration' bug.
        """
        mock_pem.return_value = "fake-private-key"

        # GithubIntegration().get_access_token returns a Mock with .token attr
        def make_token(install_id):
            tok = MagicMock()
            tok.token = f"token-for-{install_id}"
            return tok

        integration_instance = MagicMock()
        integration_instance.get_access_token.side_effect = make_token
        mock_integration.return_value = integration_instance

        with patch.object(github_auth.settings, "GITHUB_APP_ID", "999"):
            token_a = github_auth.get_installation_token(installation_id=111)
            token_b = github_auth.get_installation_token(installation_id=222)

        assert token_a == "token-for-111"
        assert token_b == "token-for-222"
        assert token_a != token_b
        # Two distinct API calls — no caching across installs
        assert integration_instance.get_access_token.call_count == 2

    @patch("shared.github_auth.GithubIntegration")
    @patch("shared.github_auth._read_private_key")
    def test_same_install_reuses_cached_token(self, mock_pem, mock_integration):
        """Within an install, the second call should hit the cache."""
        mock_pem.return_value = "fake-private-key"

        tok = MagicMock()
        tok.token = "cached-token"
        integration_instance = MagicMock()
        integration_instance.get_access_token.return_value = tok
        mock_integration.return_value = integration_instance

        with patch.object(github_auth.settings, "GITHUB_APP_ID", "999"):
            t1 = github_auth.get_installation_token(installation_id=555)
            t2 = github_auth.get_installation_token(installation_id=555)

        assert t1 == t2 == "cached-token"
        # ONE API call total — second was served from cache
        assert integration_instance.get_access_token.call_count == 1

    @patch("shared.github_auth.GithubIntegration")
    @patch("shared.github_auth._read_private_key")
    def test_env_fallback_path_caches_separately(
        self, mock_pem, mock_integration, monkeypatch
    ):
        """Env-var fallback shouldn't collide with an explicit different ID."""
        mock_pem.return_value = "fake-private-key"

        def make_token(install_id):
            tok = MagicMock()
            tok.token = f"token-{install_id}"
            return tok

        integration_instance = MagicMock()
        integration_instance.get_access_token.side_effect = make_token
        mock_integration.return_value = integration_instance

        monkeypatch.setattr(github_auth.settings, "GITHUB_APP_ID", "999")
        monkeypatch.setattr(github_auth.settings, "GITHUB_INSTALLATION_ID", "888")

        token_env = github_auth.get_installation_token()  # falls back to 888
        token_explicit = github_auth.get_installation_token(installation_id=777)

        assert token_env == "token-888"
        assert token_explicit == "token-777"


# ──────────────────────────────────────────────
# get_github_client — public API
# ──────────────────────────────────────────────
class TestGetGithubClient:
    @patch("shared.github_auth.Github")
    @patch("shared.github_auth.get_installation_token")
    def test_passes_installation_id_through(self, mock_get_token, mock_github_cls):
        """The PyGithub client must be built with the resolved token."""
        mock_get_token.return_value = "the-token"
        client = github_auth.get_github_client(installation_id=42)

        mock_get_token.assert_called_once_with(42)
        mock_github_cls.assert_called_once_with("the-token")
        assert client == mock_github_cls.return_value

    @patch("shared.github_auth.Github")
    @patch("shared.github_auth.get_installation_token")
    def test_no_install_id_uses_fallback(self, mock_get_token, mock_github_cls):
        """Calling without an arg should still work (env-var path)."""
        mock_get_token.return_value = "fallback-token"
        github_auth.get_github_client()
        mock_get_token.assert_called_once_with(None)