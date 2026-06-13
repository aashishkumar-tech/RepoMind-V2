"""Smoke test: simulate an SQS event and verify notifications fire."""
import os, sys

# Configure SMTP exactly like Lambda
os.environ.update({
    "SMTP_HOST": "smtp.gmail.com", "SMTP_PORT": "587",
    "SMTP_USERNAME": "aashishkumar.tech@gmail.com",
    "SMTP_FROM":     "aashishkumar.tech@gmail.com",
    "SMTP_FROM_NAME": "RepoMind",
    "SMTP_USE_TLS":  "true",
    "NOTIFICATION_EMAILS":   "aashishkumar.tech@gmail.com",
    "NOTIFICATIONS_ENABLED": "true",
    "SMTP_PASSWORD_SECRET_ARN":
        "arn:aws:secretsmanager:ap-south-1:572638913939:"
        "secret:repomind/smtp-password-Hq1Jcq",
})
os.environ.pop("SMTP_PASSWORD", None)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Avoid hitting GitHub/AWS for this dry run — only test notify_hooks
from shared.notify_hooks import notify_ci_failed
state = {
    "event_id":      "evt-worker-smoke",
    "repo":          "akashkumar-tech7/repomind-test",
    "branch":        "main",
    "run_id":        12345,
    "commit_sha":    "abc12345",
    "author":        "aashishkumar-tech",
    "workflow_name": "Python CI",
    "error_excerpt": "ModuleNotFoundError: No module named 'tensorflow'",
}
print("→", "✅ sent" if notify_ci_failed(state) else "❌ failed")