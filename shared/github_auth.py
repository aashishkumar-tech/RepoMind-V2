"""
shared/github_auth.py — GitHub App Authentication (Multi-Tenant)

HOW IT WORKS:
─────────────
GitHub Apps authenticate using JWT (JSON Web Tokens):
  1. Read the .pem private key from disk
  2. Build a JWT signed with the private key (RS256)
  3. Exchange the JWT for an Installation Access Token via GitHub API
  4. Use the token for all GitHub API calls (expires in 1 hour)

MULTI-TENANCY (V2):
    A single GitHub App can be installed on MANY accounts/orgs. Each
    install gets its own `installation_id`. A token minted for install
    A CANNOT be used to act on install B's repos (GitHub returns 403
    "Resource not accessible by integration").

    So every callable here accepts an optional `installation_id`:
      - If provided   → mint a token for THAT install (preferred path)
      - If omitted    → fall back to `settings.GITHUB_INSTALLATION_ID`
                        (used for local dev / single-tenant deployments)

    Tokens are cached per-install (dict keyed by installation_id) so
    different tenants don't trample each other's tokens.
"""

import time
import jwt
from pathlib import Path
from typing import Dict, Optional, Tuple

from github import Github, GithubIntegration

from shared.config import settings
from shared.logger import get_logger

logger = get_logger("shared.github_auth")

# ──────────────────────────────────────────────
# Per-installation token cache
#   key = installation_id (int)
#   val = (token: str, expires_at: float)
# ──────────────────────────────────────────────
_token_cache: Dict[int, Tuple[str, float]] = {}

# Token lifetime is ~1 hour. Refresh when fewer than this many seconds left.
_TOKEN_REFRESH_THRESHOLD = 300  # 5 minutes


def _read_private_key() -> str:
    """
    Read the GitHub App private key from the .pem file.
    Path comes from settings.GITHUB_PRIVATE_KEY_PATH.
    """
    key_path = Path(settings.GITHUB_PRIVATE_KEY_PATH)
    if not key_path.is_absolute():
        # Resolve relative to project root
        key_path = Path(__file__).resolve().parent.parent / key_path

    if not key_path.exists():
        raise FileNotFoundError(
            f"GitHub App private key not found at: {key_path}\n"
            f"Download it from GitHub App settings → Private keys"
        )

    return key_path.read_text(encoding="utf-8")


def _generate_jwt() -> str:
    """
    Generate a JWT for GitHub App authentication.

    The JWT is signed with the App's private key (RS256).
    Valid for 10 minutes (GitHub's maximum).
    """
    private_key = _read_private_key()
    now = int(time.time())

    payload = {
        "iss": settings.GITHUB_APP_ID,
        "iat": now - 60,  # 60 seconds in the past for clock drift
        "exp": now + (10 * 60),  # 10 minutes
    }

    token = jwt.encode(payload, private_key, algorithm="RS256")
    logger.info("jwt_generated", app_id=settings.GITHUB_APP_ID)
    return token


def _resolve_installation_id(installation_id: Optional[int]) -> int:
    """
    Pick which install ID to use. Preference order:
      1. The explicit `installation_id` arg (from the webhook payload)
      2. `settings.GITHUB_INSTALLATION_ID` (env var fallback)

    Raises ValueError if neither is set.
    """
    if installation_id and installation_id > 0:
        return int(installation_id)

    env_id = settings.GITHUB_INSTALLATION_ID
    if env_id:
        return int(env_id)

    raise ValueError(
        "No GitHub App installation_id available. "
        "Either pass installation_id=... explicitly (preferred — comes from "
        "the webhook payload) or set the GITHUB_INSTALLATION_ID env var."
    )


def get_installation_token(installation_id: Optional[int] = None) -> str:
    """
    Get an Installation Access Token for a specific GitHub App installation.

    Args:
        installation_id: The numeric install ID. If None, falls back to
                         settings.GITHUB_INSTALLATION_ID (single-tenant/dev).
    """
    resolved_id = _resolve_installation_id(installation_id)

    # Return cached token if still valid
    cached = _token_cache.get(resolved_id)
    if cached:
        token, expires_at = cached
        if time.time() < (expires_at - _TOKEN_REFRESH_THRESHOLD):
            return token

    integration = GithubIntegration(
        integration_id=int(settings.GITHUB_APP_ID),
        private_key=_read_private_key(),
    )

    token_obj = integration.get_access_token(resolved_id)
    token = token_obj.token

    # GitHub tokens expire in 1 hour
    _token_cache[resolved_id] = (token, time.time() + 3600)

    logger.info(
        "installation_token_acquired",
        installation_id=resolved_id,
        source="webhook" if installation_id else "env_fallback",
        expires_in="~1 hour",
    )
    return token


def get_github_client(installation_id: Optional[int] = None) -> Github:
    """
    Get an authenticated PyGithub client for a specific installation.

    Args:
        installation_id: The numeric install ID. If None, falls back to
                         settings.GITHUB_INSTALLATION_ID.
    """
    token = get_installation_token(installation_id)
    return Github(token)


def reset_token_cache() -> None:
    """Clear all cached tokens (used by tests)."""
    _token_cache.clear()