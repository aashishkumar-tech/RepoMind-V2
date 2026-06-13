# RepoMind — Testing Guide (V2)

> **One-stop reference for testing RepoMind**: unit tests, integration tests, the full local end-to-end run, and the live GitHub end-to-end flow including the new V2 self-serve config, dry-run mode, welcome PR, and **human-in-the-loop merge approval**.
>
> Last updated: **June 2026** · RepoMind **v2.0.0**

---

## Table of Contents

1. [Quick Start (TL;DR)](#1-quick-start-tldr)
2. [Prerequisites](#2-prerequisites)
3. [Test Pyramid Overview](#3-test-pyramid-overview)
4. [Unit Tests — Per File](#4-unit-tests--per-file)
5. [Integration Tests](#5-integration-tests)
6. [Local End-to-End Run (no GitHub)](#6-local-end-to-end-run-no-github)
7. [Live End-to-End on GitHub (full flow + HITL)](#7-live-end-to-end-on-github-full-flow--hitl)
8. [Coverage Report](#8-coverage-report)
9. [Linting & Static Checks](#9-linting--static-checks)
10. [Smoke Tests After Deploy](#10-smoke-tests-after-deploy)
11. [Troubleshooting Failed Tests](#11-troubleshooting-failed-tests)

---

## 1. Quick Start (TL;DR)

```powershell
# 1. Install everything
pip install -r requirements.txt -r requirements-dev.txt

# 2. Run ALL tests (unit + integration)
pytest -v

# 3. Run only V2 new tests
pytest tests/test_repomind_config.py tests/test_policy_user_config.py tests/test_hitl.py tests/test_review.py tests/test_welcome_pr.py tests/test_comment_poster.py tests/test_pr_creator_modes.py -v

# 4. Run with coverage
pytest --cov=. --cov-report=term-missing --cov-report=html

# 5. Local end-to-end (no real GitHub call)
python run_local.py

# 6. Local pipeline smoke test
python test_local_pipeline.py
```

**Expected:** ~165 tests pass (~145 existing + ~70 new V2 tests).

---

## 2. Prerequisites

### Required packages

```powershell
pip install -r requirements.txt          # production deps
pip install -r requirements-dev.txt      # pytest, pytest-cov, pytest-asyncio, ruff
```

### Required env vars (`.env` at repo root)

For **unit tests** — none required (everything is mocked).

For **integration/E2E tests** — copy `.env.example` to `.env` and fill in:

```env
# Minimum for local pipeline
GITHUB_APP_ID=<your app id>
GITHUB_INSTALLATION_ID=<installation id>
GITHUB_PRIVATE_KEY_PATH=private-key.pem
GITHUB_WEBHOOK_SECRET=<secret>

# LLM (one of)
AZURE_OPENAI_ENDPOINT=https://...
AZURE_OPENAI_API_KEY=...
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o
# OR fallback
GROQ_API_KEY=gsk_...

# Embeddings (for Step 3)
OPENAI_API_KEY=sk-...

# Optional
QDRANT_HOST=localhost
QDRANT_PORT=6333
ENVIRONMENT=development
```

### Optional services for full E2E

| Service       | Purpose                          | How to start                                  |
|---------------|----------------------------------|-----------------------------------------------|
| Qdrant        | Vector DB for RAG (Step 3)       | `docker run -p 6333:6333 qdrant/qdrant`       |
| Prometheus    | Metrics (Step 11)                | `cd monitoring; docker-compose up`            |
| ngrok         | Tunnel for live webhooks         | `ngrok http 8000`                             |

---

## 3. Test Pyramid Overview

```
                  ┌─────────────────────┐
                  │   Live E2E on       │  ← Section 7 (real GitHub PRs)
                  │   GitHub + HITL     │
                  └─────────────────────┘
                ┌─────────────────────────┐
                │   Local E2E             │  ← Section 6 (synthetic webhook)
                │  (run_local.py)         │
                └─────────────────────────┘
            ┌─────────────────────────────────┐
            │   Integration Tests             │  ← Section 5 (webhook flow)
            │  (test_webhook, test_local)     │
            └─────────────────────────────────┘
        ┌───────────────────────────────────────────┐
        │   Unit Tests — ~165 individual tests      │  ← Section 4
        │   (one file per module)                   │
        └───────────────────────────────────────────┘
```

---

## 4. Unit Tests — Per File

Run **all** unit tests:

```powershell
pytest tests/ -v
```

Run a **single file**:

```powershell
pytest tests/test_repomind_config.py -v
```

Run a **single test**:

```powershell
pytest tests/test_hitl.py::TestPRCreatorNode::test_auto_fix_with_hitl_pauses -v
```

### Test file → Module under test → What it verifies

| Test file                          | Module(s) under test                  | Verifies                                                        |
|------------------------------------|---------------------------------------|-----------------------------------------------------------------|
| `test_signature.py`                | `webhook/signature.py`                  | HMAC-SHA256 webhook signature validation                        |
| `test_webhook.py`                  | `webhook/webhook_handler.py`            | FastAPI endpoint flow (workflow_run, ping)                      |
| `test_sanitizer.py`                | `worker/sanitizer.py`                  | Secret redaction (API keys, tokens, emails)                     |
| `test_excerpt.py`                  | `worker/excerpt.py`                    | Log excerpting (errors, tracebacks, length cap)                 |
| `test_rag.py`                    | `rag/embedder.py`, `retriever.py`   | Embedding generation + Qdrant search                            |
| `test_rag_metrics.py`              | `rag/rag_metrics.py`                | Retrieval/context/generation scoring + grade                    |
| `test_triage.py`                   | `triage/triage.py`                     | Failure classification via LLM                                  |
| `test_policy.py`                   | `policy_engine/policy.py`                     | Operator default rules (allow/deny)                             |
| **`test_policy_user_config.py`** ⭐ | `policy_engine/policy.py` + `.repomind.yml`   | **V2** — user config gate (stricter than operator)            |
| **`test_repomind_config.py`** ⭐    | `shared/repomind_config.py`           | **V2** — YAML parsing, safe defaults, sample yml              |
| **`test_pr_creator_modes.py`** ⭐   | `pr_creator/pr_creator.py`                 | **V2** — mode=disabled/dry_run/auto_fix dispatch              |
| **`test_comment_poster.py`** ⭐     | `pr_creator/comment_poster.py`             | **V2** — dry-run + status + apology comments                  |
| **`test_welcome_pr.py`** ⭐         | `pr_creator/welcome_pr.py`                 | **V2** — welcome PR on app install, idempotency               |
| **`test_hitl.py`** ⭐               | `agents/hitl_nodes.py`                 | **V2** — pr_creator/merge_decision/merge/cleanup nodes        |
| **`test_review.py`** ⭐             | `review/review_handler.py`            | **V2** — PR↔event mapping, review→resume_pipeline             |
| `test_code_quality.py`                    | `code_quality/code_checker.py`               | Code quality gate                                               |
| `test_verifier.py`                   | `verifier/verifier.py`, `rollback.py`   | Fix verification + rollback                                     |
| `test_observability.py`                   | `observability/killswitch.py`, `metrics.py`  | Kill switch, Prometheus metrics                                 |

⭐ = **New in V2**

### Run only the V2 test suite

```powershell
pytest tests/test_repomind_config.py `
       tests/test_policy_user_config.py `
       tests/test_pr_creator_modes.py `
       tests/test_comment_poster.py `
       tests/test_welcome_pr.py `
       tests/test_hitl.py `
       tests/test_review.py `
       -v
```

### Run only one step's tests by glob

```powershell
pytest -k "pr_creator or comment_poster or welcome" -v
pytest -k "hitl or or review" -v
pytest -k "repomind_config or policy_user" -v
```

---

## 5. Integration Tests

These exercise multiple modules together (without real GitHub).

### 5a. Webhook → SQS flow

```powershell
pytest tests/test_webhook.py -v
```

**Covers:**
- Valid `workflow_run` failure → 202 Accepted, message published to local queue
- Invalid signature → 403
- Non-failure event → 200 ignored
- Ping → pong
- **V2:** `installation` event → welcome PR queued (mocked)
- **V2:** `pull_request_review` event → review queued for (mocked)

### 5b. Local pipeline smoke test (no real APIs)

```powershell
python test_local_pipeline.py
```

This runs the full pipeline (Steps 1-12) against a fabricated log file. Useful to confirm wiring after a refactor.

### 5c. LangGraph node-by-node

```powershell
pytest tests/test_hitl.py -v
```

Runs each HITL node in isolation with mocked GitHub & LLM, confirming routing logic.

---

## 6. Local End-to-End Run (no GitHub)

This simulates a CI failure entering the pipeline **without touching real GitHub**. Great for demoing or debugging.

### 6a. Start a local webhook server

```powershell
# Terminal 1
uvicorn webhook.webhook_handler:app --reload --host 0.0.0.0 --port 8000
```

### 6b. Send a synthetic webhook

```powershell
# Terminal 2
python run_local.py
```

`run_local.py` builds a sample `workflow_run` failure payload, signs it with `GITHUB_WEBHOOK_SECRET`, and POSTs to `http://localhost:8000/webhook`. The worker consumes it from the local in-process queue and runs the full pipeline.

### 6c. What you should see

In **Terminal 1** logs:

```
INFO  webhook_received                   event_type=workflow_run
INFO  webhook_accepted                   event_id=evt-...
INFO  pipeline_started                   event_id=evt-...
INFO  repomind_config_resolved           mode=dry_run hitl_required=True source=default
INFO  pipeline_start_langgraph           event_id=evt-... mode=dry_run hitl_required=True
INFO  evidence_node_start                ...
INFO  triage_node_complete               failure_type=dependency_error confidence=0.85
INFO  planner_node_complete              ...
INFO  solver_node_complete               ...
INFO  validator_node_complete            approved=True
INFO  policy_evaluating                  ... user_config_source=default
INFO  policy_decision                    decision=allow rule=allow_low_risk_dependency_fix
INFO  pr_creator_node_start              mode=dry_run hitl_required=True
INFO  pr_dry_run                         repo=... event_id=...
INFO  comment_posted_on_commit           ...
INFO  pipeline_complete_langgraph        status=completed
```

The pipeline output (artifacts + timeline JSON) lands in `./data/events/<slug>/<event-id>/`.

### 6d. Verify artifacts

```powershell
ls data/events/
# Pick the latest event folder
cat data/events/<slug>/<event-id>/artifacts.json | ConvertFrom-Json | Format-List
cat data/events/<slug>/<event-id>/timeline.json  | ConvertFrom-Json | Format-Table
```

You should see `repomind_config`, `triage`, `plan_summary`, `policy`, and `pr` (with `mode: dry_run` and `status: comment_posted`).

---

## 7. Live End-to-End on GitHub (full flow + HITL)

This is the **real-world test**: a GitHub Action fails → RepoMind opens a PR → a human reviews → graph resumes → PR is merged or closed.

### 7a. Prereqs

1. GitHub App installed on a sandbox repo (see `projectdocs/ONBOARDING.md`).
2. Webhook URL pointing at your local ngrok or deployed API Gateway.
3. `.env` populated with all required secrets.
4. Qdrant running (so Step 3 RAG works).

### 7b. Walkthrough

| Step | Action                                       | Expected outcome                                                 |
|------|----------------------------------------------|------------------------------------------------------------------|
| 1    | Install RepoMind GitHub App on a sandbox repo | Welcome PR opens on `repomind/welcome` branch                    |
| 2    | Review & merge the welcome PR                 | `.repomind.yml` + `.repomind/README.md` added to default branch  |
| 3    | Edit `.repomind.yml`: change `mode` to `auto_fix` | RepoMind will open PRs (still HITL-gated)                       |
| 4    | Break CI deliberately (e.g. add a missing import) | GitHub Action fails → workflow_run webhook fires                |
| 5    | Watch RepoMind logs (Lambda / uvicorn)        | Triage → Plan → Solver → Validator → Policy → PR opened          |
| 6    | A `fix/...` PR appears on the repo            | PR body says "Human-in-the-Loop Required"                        |
| 7    | Status comment appears on the failed commit   | "I opened a fix PR" with link                                    |
| 8    | **Pipeline is paused** (checkpointed to S3)   | `artifacts.json` shows `status: awaiting_review`                 |
| 9a   | **Approve** the PR in GitHub UI               | `review` module fires → graph resumes → `merge_node` auto-merges PR       |
| 9b   | **Request changes** on the PR                 | `review` module fires → graph resumes → `cleanup_node` closes PR + apology|
| 10   | Verify Step 10 runs on the fix branch CI      | If merged, Step 10 checks the fix worked → rollback if not       |

### 7c. Commands to drive each phase

```powershell
# Trigger an installation (manually click in GitHub UI, then check logs)
# tail your local server:
uvicorn webhook.webhook_handler:app --port 8000 --log-level info

# Force a CI failure on the sandbox repo
cd <sandbox-repo>
git checkout -b break-ci-test
echo "import nonexistent_module" >> src/foo.py
git commit -am "test: trigger RepoMind"
git push origin break-ci-test

# Now wait — RepoMind should see the workflow_run failure within ~10s
# and open a fix PR within 1-2 min (LLM latency)
```

### 7d. Inspect checkpointer state (HITL pause)

While the graph is paused waiting for review:

```powershell
# Local dev (LocalStorage)
ls data/checkpoints/<event_id>/
cat data/checkpoints/<event_id>/latest.txt
cat data/checkpoints/<event_id>/<checkpoint_id>.json | ConvertFrom-Json | Format-List

# Production (S3)
aws s3 ls s3://repomind-data/checkpoints/<event_id>/
aws s3 cp s3://repomind-data/checkpoints/<event_id>/latest.txt -
```

### 7e. PR↔event mapping (for HITL resume)

```powershell
# Local
cat data/indexes/by-pr/<owner>-<repo>/<pr_number>.json

# Production
aws s3 cp s3://repomind-data/indexes/by-pr/<owner>-<repo>/<pr_number>.json -
```

---

## 8. Coverage Report

```powershell
pytest --cov=shared --cov=webhook --cov=worker --cov=rag --cov=agents `
       --cov=triage --cov=planner --cov=policy_engine --cov=pr_creator --cov=code_quality `
       --cov=verifier --cov=observability --cov=review `
       --cov-report=term-missing --cov-report=html
```

Open `htmlcov/index.html` in a browser.

**Target coverage** (V2):
- `shared/repomind_config.py` ≥ 90 %
- `agents/hitl_nodes.py`       ≥ 85 %
- `pr_creator/comment_poster.py`   ≥ 85 %
- `pr_creator/welcome_pr.py`       ≥ 80 %
- `review/review_handler.py`  ≥ 80 %
- Overall                     ≥ 70 %

---

## 9. Linting & Static Checks

```powershell
# Ruff (configured in pyproject.toml)
ruff check .

# Auto-fix
ruff check . --fix

# Format
ruff format .
```

Ruff is configured for Python 3.12, line length 120, with `E/W/F/I/N/UP/B/SIM/TCH/RUF` rule sets enabled.

---

## 10. Smoke Tests After Deploy

After deploying to AWS Lambda via SAM:

```powershell
sam deploy --guided
```

### 10a. Health check

```powershell
$apiUrl = aws cloudformation describe-stacks --stack-name repomind `
          --query "Stacks[0].Outputs[?OutputKey=='WebhookApi'].OutputValue" --output text
curl "$apiUrl/health"
# Expected: {"status":"healthy", "service":"repomind-webhook", "environment":"production"}
```

### 10b. Ping test

```powershell
curl -X POST "$apiUrl/webhook/ping" -H "Content-Type: application/json" -d '{}'
# Expected: {"status":"pong"}
```

### 10c. Synthetic webhook (signed)

Use the `_check_setup.py` helper or `run_local.py` with `TARGET_REPO` and `WEBHOOK_URL` env vars set to remote.

---

## 11. Troubleshooting Failed Tests

| Error                                                | Cause                              | Fix                                                                              |
|------------------------------------------------------|------------------------------------|----------------------------------------------------------------------------------|
| `ModuleNotFoundError: No module named 'fastapi'`     | requirements not installed         | `pip install -r requirements.txt -r requirements-dev.txt`                        |
| `ModuleNotFoundError: No module named 'langgraph'`   | LangGraph optional dep missing     | `pip install langgraph==0.3.4`                                                   |
| `qdrant_client.http.exceptions.ResponseHandlingException` | Qdrant not running             | `docker run -p 6333:6333 qdrant/qdrant`                                          |
| `EnvironmentError: Missing GITHUB_APP_ID`            | `.env` not loaded                  | Confirm `.env` is in repo root, run from repo root                               |
| `AssertionError` in `test_hitl.py`                   | LangGraph version mismatch         | Pin `langgraph==0.3.4` per requirements.txt                                      |
| `ImportError: cannot import name 'CheckpointTuple'`  | Older LangGraph                    | Upgrade: `pip install langgraph==0.3.4 --upgrade`                                |
| `test_repomind_config` failures                      | Bad YAML in test fixture           | Re-run; tests use inline strings, no external files                              |
| `test_review.py` resume failures                     | S3 / LocalStorage misconfigured    | Set `ENVIRONMENT=development` so `LocalStorage` is used; ensure `./data/` writable |
| HITL test hangs                                      | Real GitHub call snuck in          | Check that all `get_github_client` calls are mocked in the test                  |
| Coverage < target                                    | New code without tests             | Add tests in `tests/test_<module>.py` mirroring naming                           |

---

## Appendix A — Test Count by Phase

| Phase          | Test files                                                                                          | Approx tests |
|----------------|-----------------------------------------------------------------------------------------------------|--------------|
| Steps 1-11 (v1.3) | `test_signature, test_webhook, test_sanitizer, test_excerpt, test_rag, test_rag_metrics, test_triage, test_policy, test_code_quality, test_verifier, test_observability` | ~145 |
| **V2**       | `test_repomind_config, test_policy_user_config, test_pr_creator_modes, test_comment_poster, test_welcome_pr, test_hitl, test_review` | **~70** |
| **Total**      | 18 files                                                                                            | **~215**     |

---

## Appendix B — One-Liner Cheat-Sheet

```powershell
# Everything in one shot
pip install -r requirements.txt -r requirements-dev.txt; pytest -v --cov=. --cov-report=html; python run_local.py

# Just the new V2 stuff
pytest tests/test_repomind_config.py tests/test_policy_user_config.py tests/test_pr_creator_modes.py tests/test_comment_poster.py tests/test_welcome_pr.py tests/test_hitl.py tests/test_review.py -v

# Watch mode (re-run on file change — needs pytest-watch)
pip install pytest-watch
ptw -- -v

# Stop at first failure
pytest -x -v

# Re-run only failing tests
pytest --lf -v
```

---

**Done!** If everything above passes, RepoMind V2 is ready to demo end-to-end.

For onboarding a new repo, see [`ONBOARDING.md`](./ONBOARDING.md).
For HITL architecture details, see [`LANGGRAPH_PIPELINE.md`](./LANGGRAPH_PIPELINE.md).
For deployment, see [`DEPLOYMENT.md`](./DEPLOYMENT.md).
