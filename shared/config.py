"""
shared/config.py — Centralized Configuration Loader

HOW IT WORKS:
─────────────
1. Loads .env file using python-dotenv
2. Reads each variable with os.getenv() + sensible defaults
3. For sensitive secrets (API keys, webhook secrets), prefers AWS Secrets
   Manager via shared/secrets.py — falls back to env vars for local dev
4. Validates required secrets exist at startup (fail-fast)
5. Exposes a single `settings` object the entire app imports

COMMUNICATION:
─────────────
Every module does: `from shared.config import settings`
Then uses: `settings.GITHUB_APP_ID`, `settings.GROQ_API_KEY`, etc.

This is the SINGLE SOURCE OF TRUTH for all configuration.
No module reads os.environ directly — always go through settings.

SECRETS MANAGER MIGRATION (Production):
─────────────
For these 4 sensitive values, the loader will:
  - Use AWS Secrets Manager if `<NAME>_SECRET_ARN` env var is set
  - Otherwise fall back to plain `<NAME>` env var (local dev)

  • GITHUB_WEBHOOK_SECRET ← GITHUB_WEBHOOK_SECRET_ARN
  • GROQ_API_KEY          ← GROQ_API_KEY_SECRET_ARN
  • OPENAI_API_KEY        ← OPENAI_API_KEY_SECRET_ARN
  • QDRANT_API_KEY        ← QDRANT_API_KEY_SECRET_ARN   ★ NEW

See shared/secrets.py for implementation details.
"""

import os
import sys
from pathlib import Path
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv


# ──────────────────────────────────────────────
# Load .env from project root
# ──────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_env_path = _PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=_env_path)


# ──────────────────────────────────────────────
# 🔐 Secrets Manager helpers (production path)
# ──────────────────────────────────────────────
# These fall back to plain env vars when *_SECRET_ARN is not set,
# so local development continues to work with a .env file.
from shared.secrets import (
    get_github_webhook_secret,
    get_groq_api_key,
    get_openai_api_key,
    get_qdrant_api_key,   # ★ NEW
)


@dataclass
class Settings:
    """
    Immutable application settings loaded from environment variables.

    Each field maps to an env var. Required fields raise on missing.
    Optional fields have defaults.
    """

    # ── AWS ──
    AWS_REGION: str = ""
    AWS_ACCOUNT_ID: str = ""
    S3_SAM_BUCKET: str = ""
    S3_DATA_BUCKET: str = ""

    # ── GitHub App ──
    GITHUB_APP_ID: str = ""
    GITHUB_INSTALLATION_ID: str = ""
    GITHUB_PRIVATE_KEY_PATH: str = ""
    GITHUB_WEBHOOK_SECRET: str = ""  # 🔐 loaded via Secrets Manager in prod

    # ── Groq LLM ──
    GROQ_API_KEY: str = ""  # 🔐 loaded via Secrets Manager in prod

    # ── Azure OpenAI ──
    AZURE_OPENAI_ENDPOINT: str = ""
    AZURE_OPENAI_API_KEY: str = ""
    AZURE_OPENAI_API_VERSION: str = "2024-02-01"
    AZURE_OPENAI_DEPLOYMENT_NAME: str = "gpt-4o"

    # ── Azure Storage (replaces S3 for hackathon demo) ──
    AZURE_STORAGE_CONNECTION_STRING: str = ""
    AZURE_STORAGE_CONTAINER: str = "repomind-events"

    # ── Azure Service Bus (replaces SQS for hackathon demo) ──
    AZURE_SERVICE_BUS_CONNECTION_STRING: str = ""
    AZURE_SERVICE_BUS_QUEUE: str = "repomind-events"

    # ── OpenAI Embeddings ──
    OPENAI_API_KEY: str = ""  # 🔐 loaded via Secrets Manager in prod

    # ── Email ──
    GMAIL_ADDRESS: str = ""
    GMAIL_APP_PASSWORD: str = ""
    NOTIFICATION_EMAILS: List[str] = field(default_factory=list)

    # ──────────────────────────────────────────
    # 🧠 Qdrant (Vector DB for RAG)
    # ──────────────────────────────────────────
    # Connection params come from plain env vars (set by template.yaml).
    # API key is loaded via Secrets Manager in production, falls back to
    # plain env var for local dev. See shared/secrets.py.
    QDRANT_HOST: str = "localhost"
    QDRANT_PORT: int = 6333
    QDRANT_HTTPS: bool = False                # ★ NEW — True for Qdrant Cloud
    QDRANT_API_KEY: str = ""                  # ★ NEW — 🔐 via Secrets Manager
    QDRANT_TIMEOUT: int = 5                   # ★ NEW — request timeout (seconds)
    QDRANT_CIRCUIT_BREAKER_TTL: int = 60      # ★ NEW — back-off TTL (seconds)

    # ── Target Repo ──
    TARGET_REPO: str = ""

    # ── Observability (Step 11) ──
    PUSHGATEWAY_URL: str = ""
    METRICS_ENABLED: str = "false"
    KILL_SWITCH_PARAM: str = "/repomind/kill_switch"

    # ── LLM Observability (Tier 2) ──
    LLM_JUDGE_ENABLED: str = "true"

    # ── Verifier (Step 10) ──
    VERIFICATION_ENABLED: str = "true"
    MAX_ROLLBACKS_PER_HOUR: str = "3"

    # ── App ──
    LOG_LEVEL: str = "INFO"
    ENVIRONMENT: str = "development"

    @classmethod
    def from_env(cls) -> "Settings":
        """
        Factory method: reads every field from os.environ.
        Splits comma-separated NOTIFICATION_EMAILS into a list.

        🔐 SECRETS: Sensitive values (GITHUB_WEBHOOK_SECRET, GROQ_API_KEY,
        OPENAI_API_KEY, QDRANT_API_KEY) are loaded via shared/secrets.py
        helpers, which prefer AWS Secrets Manager (when *_SECRET_ARN env
        var is set) and fall back to plain env vars otherwise.
        """
        notification_emails_raw = os.getenv("NOTIFICATION_EMAILS", "")
        notification_emails = [
            e.strip() for e in notification_emails_raw.split(",") if e.strip()
        ]

        return cls(
            AWS_REGION=os.getenv("AWS_REGION", "ap-south-1"),
            AWS_ACCOUNT_ID=os.getenv("AWS_ACCOUNT_ID", ""),
            S3_SAM_BUCKET=os.getenv("S3_SAM_BUCKET", "repomind-sam-deployments"),
            S3_DATA_BUCKET=os.getenv("S3_DATA_BUCKET", "repomind-data"),
            GITHUB_APP_ID=os.getenv("GITHUB_APP_ID", ""),
            GITHUB_INSTALLATION_ID=os.getenv("GITHUB_INSTALLATION_ID", ""),
            GITHUB_PRIVATE_KEY_PATH=os.getenv("GITHUB_PRIVATE_KEY_PATH", "private-key.pem"),
            GITHUB_WEBHOOK_SECRET=get_github_webhook_secret(),  # 🔐 Secrets Manager or env
            GROQ_API_KEY=get_groq_api_key(),                    # 🔐 Secrets Manager or env
            OPENAI_API_KEY=get_openai_api_key(),                # 🔐 Secrets Manager or env
            AZURE_OPENAI_ENDPOINT=os.getenv("AZURE_OPENAI_ENDPOINT", ""),
            AZURE_OPENAI_API_KEY=os.getenv("AZURE_OPENAI_API_KEY", ""),
            AZURE_OPENAI_API_VERSION=os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01"),
            AZURE_OPENAI_DEPLOYMENT_NAME=os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o"),
            AZURE_STORAGE_CONNECTION_STRING=os.getenv("AZURE_STORAGE_CONNECTION_STRING", ""),
            AZURE_STORAGE_CONTAINER=os.getenv("AZURE_STORAGE_CONTAINER", "repomind-events"),
            AZURE_SERVICE_BUS_CONNECTION_STRING=os.getenv("AZURE_SERVICE_BUS_CONNECTION_STRING", ""),
            AZURE_SERVICE_BUS_QUEUE=os.getenv("AZURE_SERVICE_BUS_QUEUE", "repomind-events"),
            GMAIL_ADDRESS=os.getenv("GMAIL_ADDRESS", ""),
            GMAIL_APP_PASSWORD=os.getenv("GMAIL_APP_PASSWORD", ""),
            NOTIFICATION_EMAILS=notification_emails,
            # ── 🧠 Qdrant ──
            QDRANT_HOST=os.getenv("QDRANT_HOST", "localhost"),
            QDRANT_PORT=int(os.getenv("QDRANT_PORT", "6333")),
            QDRANT_HTTPS=os.getenv("QDRANT_HTTPS", "false").lower() == "true",  # ★ NEW
            QDRANT_API_KEY=get_qdrant_api_key(),                                # ★ NEW (🔐)
            QDRANT_TIMEOUT=int(os.getenv("QDRANT_TIMEOUT", "5")),               # ★ NEW
            QDRANT_CIRCUIT_BREAKER_TTL=int(os.getenv("QDRANT_CIRCUIT_BREAKER_TTL", "60")),  # ★ NEW
            TARGET_REPO=os.getenv("TARGET_REPO", ""),
            PUSHGATEWAY_URL=os.getenv("PUSHGATEWAY_URL", ""),
            METRICS_ENABLED=os.getenv("METRICS_ENABLED", "false"),
            KILL_SWITCH_PARAM=os.getenv("KILL_SWITCH_PARAM", "/repomind/kill_switch"),
            LLM_JUDGE_ENABLED=os.getenv("LLM_JUDGE_ENABLED", "true"),
            VERIFICATION_ENABLED=os.getenv("VERIFICATION_ENABLED", "true"),
            MAX_ROLLBACKS_PER_HOUR=os.getenv("MAX_ROLLBACKS_PER_HOUR", "3"),
            LOG_LEVEL=os.getenv("LOG_LEVEL", "INFO"),
            ENVIRONMENT=os.getenv("ENVIRONMENT", "development"),
        )

    def validate_required(self, keys: List[str]) -> None:
        """
        Fail fast if any required key is empty.
        Called at service startup to catch misconfig early.

        Usage:
            settings.validate_required(["GITHUB_APP_ID", "GITHUB_WEBHOOK_SECRET"])
        """
        missing = [k for k in keys if not getattr(self, k, "")]
        if missing:
            raise EnvironmentError(
                f"Missing required environment variables: {', '.join(missing)}"
            )


# ──────────────────────────────────────────────
# Singleton — import this everywhere
# ──────────────────────────────────────────────
settings = Settings.from_env()