"""
Preview every event type with REALISTIC, rich context data.
Run: python scripts\test_email_designs.py
"""
import os
import sys
import time

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
from shared.notifier import Notifier, NotificationEvent

REPO = "akashkumar-tech7/repomind-test"

SAMPLES = [
    (NotificationEvent.CI_FAILED, {
        "repo": REPO,
        "branch": "main",
        "run_id": 12345678,
        "event_id": "evt-abc123de",
        "commit_sha": "a3f9c1d8e2b45678",
        "commit_message": "Add TensorFlow model training pipeline",
        "author": "aashishkumar-tech",
        "workflow_name": "Python CI",
        "failed_step": "Run pytest",
        "triggered_by": "push",
        "error_excerpt": (
            "Traceback (most recent call last):\n"
            '  File "train.py", line 12, in <module>\n'
            "    import tensorflow as tf\n"
            "ModuleNotFoundError: No module named 'tensorflow'\n"
            "\n"
            "ERROR: Process completed with exit code 1."
        ),
    }),
    (NotificationEvent.PR_REVIEW_NEEDED, {
        "repo": REPO,
        "branch": "repomind/fix-evt-abc123de",
        "pr_number": 42,
        "pr_url": f"https://github.com/{REPO}/pull/42",
        "event_id": "evt-abc123de",
        "failure_type": "missing_dependency",
        "confidence": 0.92,
        "playbook_id": "add_to_requirements",
        "risk_level": "low",
        "files_changed": ["requirements.txt", "setup.py"],
        "lines_added": 1,
        "lines_deleted": 0,
        "diff_preview": (
            "diff --git a/requirements.txt b/requirements.txt\n"
            "@@ -3,3 +3,4 @@ numpy>=1.24.0\n"
            " pandas>=2.0.0\n"
            " scikit-learn>=1.3.0\n"
            "+tensorflow>=2.15.0\n"
        ),
    }),
    (NotificationEvent.PR_MERGED, {
        "repo": REPO,
        "pr_number": 42,
        "pr_url": f"https://github.com/{REPO}/pull/42",
        "event_id": "evt-abc123de",
        "reviewer": "aashishkumar-tech",
        "merge_sha": "b7e2f4a91c5d8302",
        "time_to_merge": "4 minutes 23 seconds",
        "files_changed_count": "2",
    }),
    (NotificationEvent.PR_REJECTED, {
        "repo": REPO,
        "pr_number": 43,
        "pr_url": f"https://github.com/{REPO}/pull/43",
        "event_id": "evt-xyz789ab",
        "reviewer": "aashishkumar-tech",
        "reason": "Proposed change conflicts with internal style guide",
    }),
    (NotificationEvent.ROLLBACK, {
        "repo": REPO,
        "branch": "repomind/fix-evt-rollback01",
        "event_id": "evt-rollback01",
        "attempts": "3",
        "reason": "Verification CI run still failing — original error persists",
    }),
    (NotificationEvent.PIPELINE_ERROR, {
        "repo": REPO,
        "event_id": "evt-error-99",
        "stage": "log_fetcher",
        "error_type": "TimeoutError",
        "error": (
            "TimeoutError: GitHub API request timed out after 60 seconds\n"
            "  at LogFetcher.fetch_logs (worker/log_fetcher.py:72)\n"
            "  while downloading run 12345678 from akashkumar-tech7/repomind-test"
        ),
    }),
]

n = Notifier()
for event, ctx in SAMPLES:
    print(f"📨 Sending {event.value}...")
    ok = n.send_event(event, ctx)
    print(f"   {'✅ sent' if ok else '❌ failed'}")
    time.sleep(2)

print("\n✅ Done — check your inbox for 6 richly designed emails!")