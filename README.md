# 🤖 RepoMind V2 — Self-Serve CI Auto-Fix Agent

> An AI-powered GitHub App that detects CI failures, classifies them, generates fixes, opens pull requests, and keeps you in the loop via beautifully-designed email notifications — with Human-in-the-Loop (HITL) review and automatic rollback safety nets.

[![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/)
[![AWS Lambda](https://img.shields.io/badge/AWS-Lambda-orange.svg)](https://aws.amazon.com/lambda/)
[![LangGraph](https://img.shields.io/badge/LangGraph-1.2.4-purple.svg)](https://github.com/langchain-ai/langgraph)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

---

## ✨ What's New in V2

### 🆕 Premium Email Notification System

Six lifecycle events trigger beautifully-styled HTML emails to keep your team in the loop:

| Event | Trigger | Email Tone |
|---|---|---|
| 🔴 **CI Failed** | A workflow run fails on your repo | Alert (red header) |
| 🟡 **PR Review Needed** | RepoMind opens a fix PR for HITL review | Action required (yellow) |
| ✅ **PR Merged** | Reviewer approves OR auto-merge in HITL=false mode | Success (green) |
| ❌ **PR Rejected** | Reviewer requests changes | Info (gray) |
| 🔁 **Rollback Triggered** | Verifier detects fix did NOT resolve the issue | Warning (orange) |
| ⚠️ **Pipeline Error** | Policy deny / unhandled exception / merge failed | Error (dark red) |

### 🔐 Production-Grade Secret Management

SMTP credentials never live in plaintext env vars. The notifier supports:

- **AWS Secrets Manager** (recommended for Lambda) — fetched at runtime, cached per warm container
- **Plain env var** (`SMTP_PASSWORD`) for local dev/testing

### ⚙️ Per-Repo Notification Overrides via `.repomind.yml`

Each repo can customize who gets notified for which events:

```yaml
# .repomind.yml in your repo's root
mode: review              # or "auto" | "disabled"
hitl_required: true

notifications:
  enabled: true
  emails:
    - team-lead@company.com
    - oncall@company.com
  events:
    ci_failed: true
    pr_review_needed: true
    pr_merged: true
    pr_rejected: false       # don't spam on rejections
    rollback: true
    pipeline_error: true
```

---

## 🏗️ Architecture

```
┌──────────────────┐
│  GitHub Webhook  │  workflow_run.failed / pull_request_review
└────────┬─────────┘
         │
         ▼
┌──────────────────┐         ┌──────────────────────────┐
│  Webhook Lambda  │────────▶│  SQS Queue (FIFO)        │
│  (Step 1)        │         └──────────┬───────────────┘
└──────────────────┘                    │
                                        ▼
                          ┌──────────────────────────────┐
                          │  Worker Lambda  (Step 2-11)  │
                          │  ────────────────────────    │
                          │  ┌─────────────────────────┐ │
                          │  │ 1. Fetch + sanitize logs│ │
                          │  │ 2. Generate excerpt     │ │
                          │  │ 3. Load .repomind.yml   │ │
                          │  │ 4. 📧 notify CI failed   │ │
                          │  │ 5. LangGraph pipeline:  │ │
                          │  │    • Triage             │ │
                          │  │    • Solver (DeepAgent) │ │
                          │  │    • Policy gate        │ │
                          │  │    • PR creator         │ │
                          │  │    • HITL pause OR merge│ │
                          │  │ 6. 📧 notify PR/merge    │ │
                          │  │ 7. Verifier + rollback  │ │
                          │  │ 8. 📧 notify rollback    │ │
                          │  └─────────────────────────┘ │
                          └──────────────────────────────┘
                                        │
                            ┌───────────┴───────────┐
                            ▼                       ▼
                   ┌────────────────┐     ┌─────────────────┐
                   │  S3 (events/)  │     │  Qdrant (RAG)   │
                   └────────────────┘     └─────────────────┘
```

---

## 🚀 Quick Start

### 1. Clone & Setup Virtual Environment

```powershell
git clone https://github.com/<your-org>/repomind.git
cd repomind

# Create + activate venv (Windows PowerShell)
python -m venv venv
.\venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Copy `.env.example` to `.env` and fill in:

```bash
# ───── AWS ─────
AWS_REGION=ap-south-1
S3_BUCKET=repomind-artifacts
SQS_QUEUE_URL=https://sqs.ap-south-1.amazonaws.com/.../repomind-queue

# ───── GitHub App ─────
GITHUB_APP_ID=123456
GITHUB_PRIVATE_KEY_SECRET_ARN=arn:aws:secretsmanager:...:secret:repomind/github-key

# ───── LLM ─────
GROQ_API_KEY=gsk_...
OPENAI_API_KEY=sk-...

# ───── Vector DB ─────
QDRANT_URL=https://...qdrant.io
QDRANT_API_KEY=...

# ───── Email Notifications ─────
NOTIFICATIONS_ENABLED=true
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_USERNAME=alerts@yourcompany.com
SMTP_FROM=alerts@yourcompany.com
SMTP_FROM_NAME=RepoMind
NOTIFICATION_EMAILS=team@yourcompany.com,oncall@yourcompany.com

# Choose ONE password source:
SMTP_PASSWORD_SECRET_ARN=arn:aws:secretsmanager:...:secret:repomind/smtp-password
# OR (local dev only):
# SMTP_PASSWORD=your-app-password
```

### 3. Test Email Notifications Locally

```powershell
# Test all 6 templates → 6 emails arrive
python scripts\test_notifier.py

# Test hooks layer → 3 emails arrive
python scripts\test_notify_hooks.py

# Test review handler hooks → 2 emails arrive
python scripts\test_review_notifications.py
```

You should receive **11 emails** with premium HTML styling.

---

## 📧 Notification Events Reference

### 🔴 CI Failed

Fires the moment a CI failure is detected and parsed.

- **Subject:** `🔴 CI Failed: <repo> on <branch>`
- **Includes:** Commit SHA, author, workflow name, failed step, error excerpt
- **Action button:** "View Failed Run" → GitHub Actions page

### 🟡 PR Review Needed

Fires when RepoMind opens a fix PR and the graph pauses for HITL review.

- **Subject:** `🟡 Review Required: PR #<n> — <repo>`
- **Includes:** Failure classification, confidence %, playbook, risk, files changed, diff preview
- **Action button:** "Review Pull Request" → PR page

### ✅ PR Merged

Fires after a fix PR is merged — either by reviewer approval or auto-merge.

- **Subject:** `✅ Fix Merged: PR #<n> — <repo>`
- **Includes:** Reviewer, merge SHA, time-to-merge, files changed count

### ❌ PR Rejected

Fires when a reviewer requests changes or rejects the fix.

- **Subject:** `❌ Fix Rejected: PR #<n> — <repo>`
- **Includes:** Reviewer name, rejection reason (from PR review body)

### 🔁 Rollback Triggered

Fires when the verifier detects the fix CI also failed and rolls back the branch.

- **Subject:** `🔁 Rollback: <repo> on <branch>`
- **Includes:** Attempt count, reason, branch deleted confirmation

### ⚠️ Pipeline Error

Fires on policy denial, unhandled exceptions, or merge-after-approval failures.

- **Subject:** `⚠️ Pipeline Error: <repo>`
- **Includes:** Stage where it failed, exception type, error message

---

## 🛠️ Configuration: `.repomind.yml`

Drop this in your repo's root to customize RepoMind's behavior:

```yaml
# ─────────────────────────────────────────────
# RepoMind Configuration
# ─────────────────────────────────────────────

mode: review                  # auto | review | disabled
hitl_required: true           # require human approval before merge

# Failure types RepoMind should fix
allowed_failures:
  - missing_dependency
  - import_error
  - syntax_error
  - test_failure

# Files RepoMind is allowed to touch
allowed_paths:
  - "requirements.txt"
  - "package.json"
  - "src/**/*.py"

# Per-repo notification settings
notifications:
  enabled: true
  emails:
    - team-lead@company.com
    - oncall@company.com
  events:
    ci_failed: true
    pr_review_needed: true
    pr_merged: true
    pr_rejected: false
    rollback: true
    pipeline_error: true

# Risk threshold for auto-merge
max_risk_for_auto_merge: low  # low | medium | high
```

---

## 🧪 Local Development

```powershell
# Activate venv every session
.\venv\Scripts\Activate.ps1

# Run the worker locally with a sample event
python -m worker.main --event examples\sample_ci_failure.json

# Run tests
pytest tests\ -v

# Lint
ruff check .
```

---

## 📦 Deploying to AWS Lambda

### Package the Lambda

```powershell
.\scripts\package_lambda.ps1
```

### Deploy

```powershell
aws lambda update-function-code `
  --function-name repomind-worker `
  --zip-file fileb://dist\repomind-worker.zip `
  --region ap-south-1
```

### Required Lambda Environment Variables

| Variable | Required | Example |
|---|---|---|
| `S3_BUCKET` | ✅ | `repomind-artifacts` |
| `SQS_QUEUE_URL` | ✅ | `https://sqs.../repomind-queue` |
| `GITHUB_APP_ID` | ✅ | `123456` |
| `GITHUB_PRIVATE_KEY_SECRET_ARN` | ✅ | `arn:aws:secretsmanager:...` |
| `GROQ_API_KEY` | ✅ | `gsk_...` |
| `OPENAI_API_KEY` | ✅ | `sk-...` |
| `QDRANT_URL` | ✅ | `https://...qdrant.io` |
| `QDRANT_API_KEY` | ✅ | `...` |
| `NOTIFICATIONS_ENABLED` | ✅ | `true` |
| `SMTP_HOST` | ✅ | `smtp.gmail.com` |
| `SMTP_PORT` | ✅ | `587` |
| `SMTP_USERNAME` | ✅ | `alerts@company.com` |
| `SMTP_FROM` | ✅ | `alerts@company.com` |
| `SMTP_PASSWORD_SECRET_ARN` | ✅ | `arn:aws:secretsmanager:...` |
| `NOTIFICATION_EMAILS` | ✅ | `team@company.com` |

### IAM Permissions Required

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue"
      ],
      "Resource": [
        "arn:aws:secretsmanager:*:*:secret:repomind/*"
      ]
    },
    {
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject"
      ],
      "Resource": "arn:aws:s3:::repomind-artifacts/*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:SendMessage"
      ],
      "Resource": "arn:aws:sqs:*:*:repomind-*"
    }
  ]
}
```

---

## 📂 Project Structure

```
RepoMind-main/
├── agents/                     # LangGraph multi-agent pipeline
│   ├── graph.py                # Main pipeline + HITL resume
│   ├── triage_node.py
│   ├── planner_node.py
│   ├── policy_node.py
│   └── pr_creator_node.py
├── code_quality/               # Step 9: code quality gate
├── observability/              # Metrics + kill switch
├── pr_creator/                 # GitHub PR creation
├── review/                     # HITL review handler
│   └── review_handler.py
├── shared/                     # Shared utilities
│   ├── notifier.py             # ★ SMTP + Secrets Manager
│   ├── notify_hooks.py         # ★ High-level event hooks
│   ├── email_templates.py      # ★ 6 premium HTML templates
│   ├── repomind_config.py      # .repomind.yml loader
│   ├── storage.py
│   ├── logger.py
│   └── config.py
├── triage/                     # Failure classification
├── verifier/                   # Step 10: verify + rollback
├── webhook/                    # Step 1: webhook ingestion
├── worker/                     # Step 2-11 worker
│   └── main.py                 # ★ Pipeline orchestrator
├── scripts/                    # Test + deploy scripts
│   ├── test_notifier.py
│   ├── test_notify_hooks.py
│   └── test_review_notifications.py
├── tests/
├── requirements.txt
├── .gitignore
├── .env.example
└── README.md
```

---

## 🤝 Contributing

1. Fork the repo
2. Create a feature branch: `git checkout -b feat/awesome-thing`
3. Activate venv: `.\venv\Scripts\Activate.ps1`
4. Make changes + add tests
5. Run local smoke tests
6. Open a PR

---

## 📜 License

MIT © 2026 RepoMind contributors

---

## 🆘 Troubleshooting

### Emails not arriving?

1. Check `NOTIFICATIONS_ENABLED=true` in Lambda env
2. Verify `SMTP_PASSWORD_SECRET_ARN` IAM permission
3. CloudWatch logs: search for `notifier_` to see status
4. Gmail users: must use an **app password**, not your account password

### Pipeline running but no PRs?

1. Check `.repomind.yml` `mode` is not `disabled`
2. Check policy_node logs for `decision=deny`
3. Verify GitHub App has `pull_requests: write` permission

### Verification not rolling back?

1. Workflow run must complete on the `fix/*` branch
2. Webhook must be subscribed to `workflow_run` events
3. Check SQS DLQ for stuck messages
