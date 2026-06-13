"""
AWS Secrets Manager helper for RepoMind.

PATTERN (mirrors shared/notifier.py SMTP logic):
  1. Look for `<NAME>_SECRET_ARN` env var
  2. If set → fetch from Secrets Manager, parse JSON, cache in-process
  3. If not set → fall back to plain `<NAME>` env var (legacy / local dev)

The secret is cached for the lifetime of the Lambda container (warm starts)
so we only call Secrets Manager once per cold start (~150ms).

STORAGE FORMATS SUPPORTED:
  • JSON (preferred for multi-field):  {"api_key": "sk-..."}
  • Plain text (single-value):         sk-...
  Both formats work — helper auto-detects and falls back gracefully.

USAGE:
    from shared.secrets import (
        get_openai_api_key,
        get_groq_api_key,
        get_github_webhook_secret,
        get_qdrant_api_key,        # 🧠 RAG vector search
    )

    openai_key = get_openai_api_key()
    groq_key   = get_groq_api_key()
    webhook    = get_github_webhook_secret()
    qdrant_key = get_qdrant_api_key()
"""
from __future__ import annotations

import json
import os
import threading
from typing import Optional

import boto3
import structlog

logger = structlog.get_logger(__name__)

# Module-level secret cache (survives Lambda warm starts)
_SECRET_CACHE: dict = {}
_LOCK = threading.Lock()


def _fetch_from_secrets_manager(secret_arn: str) -> dict:
    """Fetch a JSON secret from AWS Secrets Manager. Cached per process."""
    if secret_arn in _SECRET_CACHE:
        return _SECRET_CACHE[secret_arn]

    with _LOCK:
        # Double-check after acquiring lock
        if secret_arn in _SECRET_CACHE:
            return _SECRET_CACHE[secret_arn]

        # Region is embedded in ARN: arn:aws:secretsmanager:<region>:...
        region = secret_arn.split(":")[3] if ":" in secret_arn else "ap-south-1"
        client = boto3.client("secretsmanager", region_name=region)

        try:
            resp = client.get_secret_value(SecretId=secret_arn)
            raw = resp.get("SecretString", "")

            # Try to parse as JSON; fall back to raw string wrapped in dict
            try:
                parsed = json.loads(raw)
                if not isinstance(parsed, dict):
                    parsed = {"value": raw}
            except (json.JSONDecodeError, TypeError):
                parsed = {"value": raw}

            _SECRET_CACHE[secret_arn] = parsed

            logger.info(
                "secret_loaded",
                source="secrets_manager",
                arn_tail=secret_arn[-8:],
                fields=list(parsed.keys()),
            )
            return parsed

        except Exception as exc:
            logger.error(
                "secret_fetch_failed",
                error=str(exc),
                arn_tail=secret_arn[-8:] if secret_arn else "n/a",
            )
            raise


def _resolve(arn_env: str, plain_env: str, json_field: str) -> str:
    """
    Generic resolver with 3-tier lookup:
      1a. If `<arn_env>` is set → fetch from SM, try `parsed[json_field]`
      1b. Fall back to `parsed["value"]` (set when secret is stored as plain string)
      2.  Else fall back to `<plain_env>` env var
      3.  Else return empty string (caller decides what to do)

    Supports both AWS Secrets Manager storage formats:
      • JSON:        {"api_key": "sk-..."}     ← preferred for multi-field secrets
      • Plain text:  sk-...                     ← simpler for single-value secrets
    """
    secret_arn = os.environ.get(arn_env, "").strip()
    if secret_arn:
        try:
            parsed = _fetch_from_secrets_manager(secret_arn)

            # Tier 1a: Try the expected JSON field name
            value = parsed.get(json_field, "")
            if value:
                return value

            # Tier 1b: Fall back to "value" key (set when secret is a plain string)
            value = parsed.get("value", "")
            if value:
                logger.info(
                    "secret_used_plain_string",
                    arn_env=arn_env,
                    note="stored as plain text, not JSON",
                )
                return value

            logger.warning(
                "secret_field_missing",
                arn_env=arn_env,
                field=json_field,
                available=list(parsed.keys()),
            )
        except Exception:
            # Fall through to plain env var
            pass

    # Legacy fallback
    return os.environ.get(plain_env, "").strip()


# ---------------------------------------------------------------------------
# Public accessors — one per RepoMind secret
# ---------------------------------------------------------------------------

def get_openai_api_key() -> str:
    """OpenAI API key (for RAG embeddings). Returns "" if not configured."""
    return _resolve(
        arn_env="OPENAI_API_KEY_SECRET_ARN",
        plain_env="OPENAI_API_KEY",
        json_field="api_key",
    )


def get_groq_api_key() -> str:
    """Groq API key (for triage/planner/validator/judge LLMs)."""
    return _resolve(
        arn_env="GROQ_API_KEY_SECRET_ARN",
        plain_env="GROQ_API_KEY",
        json_field="api_key",
    )


def get_github_webhook_secret() -> str:
    """GitHub App webhook HMAC secret (for signature verification)."""
    return _resolve(
        arn_env="GITHUB_WEBHOOK_SECRET_ARN",
        plain_env="GITHUB_WEBHOOK_SECRET",
        json_field="webhook_secret",
    )


def get_qdrant_api_key() -> str:
    """
    🧠 Qdrant Cloud API key (for RAG vector search).

    The secret is typically stored as a plain JWT string (not JSON) when
    created by CloudFormation via `SecretString: !Ref QdrantApiKeyValue`.
    `_resolve` handles this transparently via its Tier 1b "value" path
    and logs `secret_used_plain_string` (informational, not an error).

    Returns "" if neither `QDRANT_API_KEY_SECRET_ARN` nor `QDRANT_API_KEY`
    is set. The Retriever's circuit breaker then disables RAG cleanly
    instead of crashing the pipeline.

    JSON storage format (optional, also supported):
        {"api_key": "eyJhbGc..."}
    """
    return _resolve(
        arn_env="QDRANT_API_KEY_SECRET_ARN",
        plain_env="QDRANT_API_KEY",
        json_field="api_key",
    )