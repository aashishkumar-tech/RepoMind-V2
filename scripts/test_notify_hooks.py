"""Smoke test the high-level notify_hooks layer (uses Secrets Manager)."""
import os
import sys

# ─── EDIT THESE ───────────────────────────────────────
GMAIL_USER  = "aashishkumar.tech@gmail.com"
RECIPIENT   = "a76059142@gmail.com"
SECRET_ARN  = (
    "arn:aws:secretsmanager:ap-south-1:572638913939:"
    "secret:repomind/smtp-password-Hq1Jcq"   # ← your real ARN
)
# ──────────────────────────────────────────────────────

os.environ.update({
    "SMTP_HOST": "smtp.gmail.com", "SMTP_PORT": "587",
    "SMTP_USERNAME": GMAIL_USER, "SMTP_FROM": GMAIL_USER,
    "SMTP_FROM_NAME": "RepoMind", "SMTP_USE_TLS": "true",
    "NOTIFICATION_EMAILS": RECIPIENT, "NOTIFICATIONS_ENABLED": "true",
    "SMTP_PASSWORD_SECRET_ARN": SECRET_ARN,
})
os.environ.pop("SMTP_PASSWORD", None)

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from shared.notify_hooks import (
    notify_ci_failed,
    notify_pr_review_needed,
    notify_pipeline_error,
)

# Simulate a real pipeline state dict
state = {
    "event_id":      "evt-hooks-test-001",
    "repo":          "akashkumar-tech7/repomind-test",
    "branch":        "main",
    "run_id":        99887766,
    "commit_sha":    "deadbeef12345678",
    "author":        "aashishkumar-tech",
    "workflow_name": "Python CI",
    "error_excerpt": "ModuleNotFoundError: No module named 'tensorflow'",
}

print("📨 Test 1: notify_ci_failed...")
print("   →", "✅" if notify_ci_failed(state) else "❌")

state.update({
    "pr_number":    101,
    "pr_url":       "https://github.com/akashkumar-tech7/repomind-test/pull/101",
    "failure_type": "missing_dependency",
    "confidence":   0.88,
    "playbook_id":  "add_to_requirements",
    "risk_level":   "low",
    "files_changed": ["requirements.txt"],
    "lines_added":   1, "lines_deleted": 0,
})
print("📨 Test 2: notify_pr_review_needed...")
print("   →", "✅" if notify_pr_review_needed(state) else "❌")

print("📨 Test 3: notify_pipeline_error...")
try:
    raise TimeoutError("Simulated GitHub API timeout after 60s")
except TimeoutError as e:
    print("   →", "✅" if notify_pipeline_error(state, e, stage="log_fetcher") else "❌")

print("\n✅ Done — check inbox for 3 emails using the hooks layer!")