"""
Verifies the full flow:
    1. Notifier fetches password from AWS Secrets Manager
    2. Sends a test CI_FAILED notification via Gmail SMTP

PREREQUISITES:
    - Secret created: repomind/smtp-password
    - AWS CLI configured with credentials that can GetSecretValue
    - shared/notifier.py updated with Secrets Manager support

Usage:
    python scripts\test_secrets_smtp.py
"""
import os
import sys

# ─── EDIT THESE ───────────────────────────────────────
GMAIL_USER = "aashishkumar.tech@gmail.com"
RECIPIENT = "a76059142@gmail.com"
SECRET_ARN = (
    "arn:aws:secretsmanager:ap-south-1:572638913939:"
    "secret:repomind/smtp-password-Hq1Jcq"
)
# ──────────────────────────────────────────────────────

# Configure env vars BEFORE importing notifier
os.environ["SMTP_HOST"] = "smtp.gmail.com"
os.environ["SMTP_PORT"] = "587"
os.environ["SMTP_USERNAME"] = GMAIL_USER
os.environ["SMTP_FROM"] = GMAIL_USER
os.environ["SMTP_USE_TLS"] = "true"
os.environ["NOTIFICATION_EMAILS"] = RECIPIENT
os.environ["NOTIFICATIONS_ENABLED"] = "true"
os.environ["SMTP_PASSWORD_SECRET_ARN"] = SECRET_ARN
# Intentionally NOT setting SMTP_PASSWORD — force Secrets Manager path
os.environ.pop("SMTP_PASSWORD", None)

# Make project root importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from shared.notifier import Notifier, NotificationEvent  # noqa: E402


def main():
    print(f"🔐 Testing Secrets Manager → Gmail flow...")
    print(f"   Secret ARN tail: ...{SECRET_ARN[-12:]}")
    print(f"   Recipient: {RECIPIENT}\n")

    ok = Notifier().send_event(
        NotificationEvent.CI_FAILED,
        context={
            "repo": "test/repo",
            "run_id": 99999,
            "event_id": "evt-secrets-test",
            "branch": "main",
        },
    )

    if ok:
        print("\n✅ Notification sent — check inbox!")
        return 0
    else:
        print("\n❌ Failed — check logs above for 'smtp_secret_fetch_failed' or 'notification_failed'")
        return 1


if __name__ == "__main__":
    sys.exit(main())