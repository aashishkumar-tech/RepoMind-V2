"""
shared/notifier.py — Multi-channel event notifications (Gmail SMTP)

HOW IT WORKS:
─────────────
Sends pipeline lifecycle notifications to a configurable recipient list.
Backend: SMTP (Gmail by default). HTML templates per event type with
plain-text fallback for accessibility and spam-score reduction.

GLOBAL ENV VARS:
    SMTP_HOST                  default: smtp.gmail.com
    SMTP_PORT                  default: 587
    SMTP_USERNAME              your Gmail address (required)
    SMTP_PASSWORD              Plaintext password — used if Secrets Manager ARN is not set
    SMTP_PASSWORD_SECRET_ARN   AWS Secrets Manager ARN (preferred for production)
    SMTP_FROM                  default: same as SMTP_USERNAME
    SMTP_FROM_NAME             default: "RepoMind" (displayed sender name)
    SMTP_USE_TLS               "true" / "false"  (default true)
    NOTIFICATION_EMAILS        comma-separated fallback recipients
    NOTIFICATIONS_ENABLED      master kill-switch ("true" / "false")

SECRETS HANDLING:
─────────────────
SMTP_PASSWORD is resolved in this priority order:
    1. Env var SMTP_PASSWORD                          (dev/local)
    2. AWS Secrets Manager (SMTP_PASSWORD_SECRET_ARN) (production)
    3. None → notifier disabled gracefully

The secret is cached for the lifetime of the Lambda container (warm starts)
so we only call Secrets Manager once per cold start (~150ms).

PER-REPO OVERRIDE (.repomind.yaml in user's repo):
    notifications:
      enabled: true
      emails:
        - dev@team.com
        - lead@team.com
      events:
        ci_failed: true
        pr_review_needed: true
        pr_merged: true
        pr_rejected: false     # disable specific events
        rollback: true
        pipeline_error: true

USAGE:
    from shared.notifier import Notifier, NotificationEvent

    Notifier().send_event(
        NotificationEvent.CI_FAILED,
        context={
            "repo": "user/repo",
            "run_id": 123,
            "event_id": "evt-...",
            "branch": "main",
        },
        repo_config=repomind_cfg,  # optional dict from .repomind.yaml
    )

EMAIL FORMAT:
    Sent as multipart/alternative containing:
      - text/plain  (fallback for old clients & spam filters)
      - text/html   (branded design from shared.email_templates)

NOTE ON LOGGING:
    structlog reserves the first positional arg as `event`, so we use
    `notification_event=...` as the kwarg key to avoid collision.
"""

import json
import os
import smtplib
import ssl
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from enum import Enum
from typing import Optional

from shared.email_templates import render_email
from shared.logger import get_logger

logger = get_logger("shared.notifier")

# ─────────────────────────────────────────────
# Module-level secret cache (survives warm starts)
# ─────────────────────────────────────────────
_SECRET_CACHE: dict = {}


def _resolve_smtp_password() -> Optional[str]:
    """
    Resolve SMTP password from env var or AWS Secrets Manager.
    Caches Secrets Manager result for warm-start performance.
    """
    # 1. Try plain env var first (local dev / fallback)
    pwd = os.environ.get("SMTP_PASSWORD", "").strip()
    if pwd:
        return pwd

    # 2. Try Secrets Manager
    secret_arn = os.environ.get("SMTP_PASSWORD_SECRET_ARN", "").strip()
    if not secret_arn:
        return None

    if secret_arn in _SECRET_CACHE:
        return _SECRET_CACHE[secret_arn]

    try:
        import boto3  # lazy import — boto3 is in Lambda runtime

        region = secret_arn.split(":")[3] if ":" in secret_arn else "ap-south-1"
        client = boto3.client("secretsmanager", region_name=region)
        resp = client.get_secret_value(SecretId=secret_arn)

        # Secret may be a raw string OR JSON like {"password": "..."}
        raw = resp.get("SecretString", "")
        try:
            parsed = json.loads(raw)
            value = (
                parsed.get("password")
                or parsed.get("SMTP_PASSWORD")
                or parsed.get("smtp_password")
            )
            if not value:
                value = raw  # fallback: treat as raw string
        except (json.JSONDecodeError, TypeError):
            value = raw

        value = (value or "").strip()
        _SECRET_CACHE[secret_arn] = value
        logger.info(
            "smtp_secret_loaded",
            source="secrets_manager",
            arn_tail=secret_arn[-8:],
        )
        return value

    except Exception as e:
        logger.error(
            "smtp_secret_fetch_failed",
            error=str(e),
            error_type=type(e).__name__,
            arn_tail=secret_arn[-8:] if secret_arn else "n/a",
        )
        return None


class NotificationEvent(str, Enum):
    """All lifecycle events the pipeline can emit notifications for."""
    CI_FAILED = "ci_failed"
    PR_REVIEW_NEEDED = "pr_review_needed"
    PR_MERGED = "pr_merged"
    PR_REJECTED = "pr_rejected"
    ROLLBACK = "rollback"
    PIPELINE_ERROR = "pipeline_error"


@dataclass
class SMTPConfig:
    host: str
    port: int
    username: str
    password: str
    from_addr: str
    from_name: str
    use_tls: bool

    @classmethod
    def from_env(cls) -> Optional["SMTPConfig"]:
        username = os.environ.get("SMTP_USERNAME", "").strip()
        password = _resolve_smtp_password()
        if not username or not password:
            return None
        return cls(
            host=os.environ.get("SMTP_HOST", "smtp.gmail.com"),
            port=int(os.environ.get("SMTP_PORT", "587")),
            username=username,
            password=password,
            from_addr=os.environ.get("SMTP_FROM", username),
            from_name=os.environ.get("SMTP_FROM_NAME", "RepoMind"),
            use_tls=os.environ.get("SMTP_USE_TLS", "true").lower() == "true",
        )


class Notifier:
    """Sends event-based HTML notifications via SMTP with plain-text fallback."""

    def __init__(self):
        self.smtp = SMTPConfig.from_env()
        self.master_enabled = (
            os.environ.get("NOTIFICATIONS_ENABLED", "true").lower() == "true"
        )

    # ───────── public API ─────────

    def send_event(
        self,
        event: NotificationEvent,
        context: dict,
        repo_config: Optional[dict] = None,
    ) -> bool:
        """
        Render a templated event email (HTML + text) and deliver it.

        Args:
            event:       The lifecycle event (NotificationEvent enum)
            context:     dict of values referenced by the template
                         (e.g. repo, run_id, pr_url, etc.)
            repo_config: optional dict parsed from .repomind.yaml's `notifications`
                         section — overrides global settings per-repo.

        Returns:
            True if email was sent successfully, False otherwise.
        """
        if not self._event_enabled(event, repo_config):
            logger.info(
                "notification_skipped",
                notification_event=event.value,
                reason="disabled",
            )
            return False

        recipients = self._resolve_recipients(repo_config)
        if not recipients:
            logger.warning(
                "no_recipients",
                notification_event=event.value,
                msg="No emails configured (set NOTIFICATION_EMAILS or .repomind.yaml)",
            )
            return False

        if not self.smtp:
            logger.warning(
                "smtp_not_configured",
                notification_event=event.value,
                msg="Missing SMTP_USERNAME or password (env/Secrets Manager)",
            )
            return False

        # Render HTML + text from shared templates
        try:
            subject, html_body, text_body = render_email(event.value, context)
        except Exception as e:
            logger.error(
                "template_render_failed",
                notification_event=event.value,
                error=str(e),
                error_type=type(e).__name__,
            )
            return False

        return self._send_smtp(recipients, subject, html_body, text_body, event)

    def send(
        self,
        subject: str,
        body: str,
        recipients: Optional[list] = None,
    ) -> bool:
        """
        Back-compat shim for legacy `notifier.send(subject, body)` callers.
        Sends `body` as both the plain-text and HTML alternative.
        """
        if not self.master_enabled:
            return False
        recipients = recipients or self._resolve_recipients(None)
        if not recipients:
            logger.warning(
                "no_recipients",
                msg="No notification emails configured",
            )
            return False
        if not self.smtp:
            logger.warning("smtp_not_configured")
            return False
        # Wrap plain body in minimal HTML for legacy callers
        html = (
            f"<html><body style='font-family:sans-serif;color:#111;'>"
            f"<pre style='white-space:pre-wrap;'>{body}</pre>"
            f"</body></html>"
        )
        return self._send_smtp(
            recipients, subject, html, body, NotificationEvent.PIPELINE_ERROR
        )

    # ───────── internals ─────────

    def _resolve_recipients(self, repo_config: Optional[dict]) -> list:
        """Per-repo emails win, otherwise fall back to NOTIFICATION_EMAILS env var."""
        if repo_config:
            notif = (repo_config or {}).get("notifications", {}) or {}
            emails = notif.get("emails") or []
            if emails:
                return [e.strip() for e in emails if e and e.strip()]
        env_emails = os.environ.get("NOTIFICATION_EMAILS", "")
        return [e.strip() for e in env_emails.split(",") if e.strip()]

    def _event_enabled(
        self, event: NotificationEvent, repo_config: Optional[dict]
    ) -> bool:
        """Check master switch + per-repo enable flag + per-event override."""
        if not self.master_enabled:
            return False
        if repo_config:
            notif = (repo_config or {}).get("notifications", {}) or {}
            if notif.get("enabled", True) is False:
                return False
            events = notif.get("events") or {}
            if event.value in events:
                return bool(events[event.value])
        return True

    def _send_smtp(
        self,
        recipients: list,
        subject: str,
        html_body: str,
        text_body: str,
        event: NotificationEvent,
    ) -> bool:
        """
        Send a multipart/alternative email with both HTML and plain-text parts.

        Per RFC 2046, email clients should prefer the LAST alternative they can
        render — so we attach plain text first, HTML last.
        """
        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = f"{self.smtp.from_name} <{self.smtp.from_addr}>"
            msg["To"] = ", ".join(recipients)
            msg["Subject"] = subject

            # Order matters: text first (lowest priority), HTML last (highest)
            msg.attach(MIMEText(text_body, "plain", "utf-8"))
            msg.attach(MIMEText(html_body, "html", "utf-8"))

            ctx = ssl.create_default_context()
            with smtplib.SMTP(self.smtp.host, self.smtp.port, timeout=15) as server:
                if self.smtp.use_tls:
                    server.starttls(context=ctx)
                server.login(self.smtp.username, self.smtp.password)
                server.send_message(msg)

            logger.info(
                "notification_sent",
                notification_event=event.value,
                recipients=len(recipients),
                subject=subject,
                format="multipart/alternative",
            )
            return True
        except Exception as e:
            logger.error(
                "notification_failed",
                notification_event=event.value,
                error=str(e),
                error_type=type(e).__name__,
            )
            return False