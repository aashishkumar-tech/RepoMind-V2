# 🚀 How to Run — RepoMind V2

## 1. Quick Start (Local Development)

### 1.1 Start the Webhook Server

```bash
# Activate virtual environment first
.\.venv\Scripts\Activate.ps1    # Windows PowerShell
# ## 9. Common Commands Reference

| Task | Command |
|------|---------|
| Install uv | `powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| Create virtual env | `uv venv --python 3.12` |
| Install dependencies | `uv pip install -r requirements.txt` |
| Activate venv (Windows) | `.\.venv\Scripts\Activate.ps1` |
| Activate venv (Linux) | `source .venv/bin/activate` |
| Start local server | `python run_local.py` |
| Run all tests | `pytest tests/ -v` |
| Run pipeline simulation | `python test_local_pipeline.py` |
| Build for AWS | `sam build` |
| Deploy to AWS | `sam deploy` |
| View AWS logs | `sam logs -n WorkerFunction --stack-name repomind --tail` |
| Check health | `curl http://localhost:8000/health` |
| Interactive API docs | Open `http://localhost:8000/docs` in browser |n/activate     # Linux/macOS

# Start the server
python run_local.py
```

**Output:**
```
============================================================
  🚀 RepoMind V2 — Local Dev Server
============================================================
  Webhook:  http://localhost:8000/webhook
  Health:   http://localhost:8000/health
  Docs:     http://localhost:8000/docs
============================================================
```

### 1.2 Available Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `http://localhost:8000/webhook` | POST | Receive GitHub webhook events |
| `http://localhost:8000/health` | GET | Health check |
| `http://localhost:8000/docs` | GET | Swagger UI (interactive API docs) |

---

## 2. Test the Health Endpoint

```bash
# Using curl
curl http://localhost:8000/health

# Using PowerShell
Invoke-RestMethod -Uri http://localhost:8000/health

# Expected response:
# {"status": "healthy", "service": "repomind-webhook"}
```

---

## 3. Simulate a Webhook Event

```bash
# Using curl (Linux/macOS)
curl -X POST http://localhost:8000/webhook \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: workflow_run" \
  -H "X-Hub-Signature-256: sha256=test" \
  -d '{
    "action": "completed",
    "workflow_run": {
      "id": 123456789,
      "name": "CI",
      "conclusion": "failure",
      "html_url": "https://github.com/test/repo/actions/runs/123456789",
      "head_branch": "main",
      "head_sha": "abc123"
    },
    "repository": {
      "full_name": "test/repo",
      "html_url": "https://github.com/test/repo"
    }
  }'
```

```powershell
# Using PowerShell
$body = @{
    action = "completed"
    workflow_run = @{
        id = 123456789
        name = "CI"
        conclusion = "failure"
        html_url = "https://github.com/test/repo/actions/runs/123456789"
        head_branch = "main"
        head_sha = "abc123"
    }
    repository = @{
        full_name = "test/repo"
        html_url = "https://github.com/test/repo"
    }
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Uri http://localhost:8000/webhook `
  -Method POST `
  -ContentType "application/json" `
  -Headers @{"X-GitHub-Event"="workflow_run"; "X-Hub-Signature-256"="sha256=test"} `
  -Body $body
```

> **Note:** In development mode, signature validation may be relaxed. In production, the HMAC-SHA256 signature must be valid.

---

## 4. Run the Full Pipeline Locally

The test pipeline simulates the entire flow with a sample failed log:

```bash
python test_local_pipeline.py
```

This runs the full **6-agent swarm**:
1. Excerpt generation from sample CI logs
2. Evidence retrieval (RAG context)
3. Triage (failure classification)
4. Plan generation
5. Solver (hybrid Tier 1 deep agent → Tier 2 direct LLM)
6. Validator (peer review)
7. Policy evaluation
8. RAG quality grade
9. LLM cost summary
10. LLM-as-Judge audit

**No AWS, GitHub, Azure, or Groq credentials required** — uses local fallbacks (deep agent gracefully degrades to Tier 2 direct LLM, which itself falls back to a heuristic if no LLM keys are present).

---

## 4.5. Run the Frontend Dashboard ✨ NEW

The Next.js dashboard provides live agent visibility for hackathon demos:

```bash
# In a separate terminal
cd frontend
npm install   # First time only
npm run dev
```

Open `http://localhost:3001` in your browser.

**Dashboard Features:**

- 6-card stats bar: Total Events · PRs Created · Policy Denied · Errors · Avg RAG Grade · Total LLM Cost
- Per-event 7-step pipeline visualization
- 📊 RAG Quality card (A–F grade + retrieval/context/generation breakdown)
- 💰 LLM Cost & Tokens card (stacked bar by agent + per-agent cost)
- 🛡️ LLM-as-Judge card (factuality, completeness, calibration, hallucination flag, verdict)

If the backend `/api/events` endpoint is unavailable, the dashboard auto-falls-back to mock data so the demo always works.

---

## 5. Run Tests

> **📖 For the comprehensive test command reference** — including individual unit-test commands, integration tests, local E2E, live E2E with HITL on GitHub, coverage, lint and smoke-test commands — see **[TESTING_GUIDE.md](./TESTING_GUIDE.md)**.

### 5.1 Run All Tests

```bash
pytest tests/ -v
```

### 5.2 Run Specific Test File

```bash
pytest tests/test_signature.py -v
pytest tests/test_triage.py -v
pytest tests/test_rag.py -v

# v1.3.0 tests:
pytest tests/test_graph.py -v             # 6-agent swarm + retry routing (5 tests)
pytest tests/test_deep_solver.py -v       # Hybrid solver fallback chain (11 tests)
pytest tests/test_llm_observability.py -v # Tracing + cost + judge (14 tests)

# V2.0 new tests:
pytest tests/test_repomind_config.py -v    # .repomind.yml parser + safe defaults
pytest tests/test_policy_user_config.py -v # User-config policy pre-filter
pytest tests/test_pr_creator_modes.py -v   # auto_fix/dry_run/disabled dispatch
pytest tests/test_comment_poster.py -v     # PR/commit comment targeting
pytest tests/test_welcome_pr.py -v         # Idempotent welcome PR creator
pytest tests/test_hitl.py -v               # 4 HITL nodes + router
pytest tests/test_review.py -v             # PR↔event mapping + ReviewHandler
```

### 5.3 Run with Coverage

```bash
pytest tests/ --cov=. --cov-report=term-missing
```

### 5.4 Run with Detailed Output

```bash
pytest tests/ -v -s --tb=long
```

---

## 6. Deploy to AWS

### 6.1 Build with SAM

```bash
sam build
```

### 6.2 Deploy (First Time — Guided)

```bash
sam deploy --guided
```

You'll be prompted for:
- Stack name: `repomind`
- Region: `ap-south-1`
- GitHubAppId, GitHubInstallationId, GitHubWebhookSecret
- **AzureOpenAIEndpoint, AzureOpenAIApiKey, AzureOpenAIDeploymentName** (recommended)
- GroqApiKey (fallback — used if Azure creds are absent)
- LLMJudgeEnabled (`true` / `false`)

### 6.3 Deploy (Subsequent — Quick)

```bash
sam deploy
```

### 6.4 Get the Webhook URL

```bash
# After deployment, find the webhook URL in outputs
sam list stack-outputs --stack-name repomind

# Output:
# WebhookUrl: https://xxxx.execute-api.ap-south-1.amazonaws.com/Prod/webhook
```

### 6.5 Configure GitHub Webhook

1. Go to your GitHub App settings
2. Set **Webhook URL** to the API Gateway URL from step 6.4
3. Events should start flowing automatically

---

## 7. View Logs

### 7.1 Local Development
Logs appear in the terminal with colored output.

### 7.2 AWS CloudWatch
```bash
# View webhook function logs
sam logs -n WebhookFunction --stack-name repomind --tail

# View worker function logs
sam logs -n WorkerFunction --stack-name repomind --tail
```

---

## 8. Development Workflow

```
 ┌─────────────────────────────────────────┐
 │  1. Edit code                           │
 │  2. Run tests:  pytest tests/ -v        │
 │  3. Start server: python run_local.py   │
 │  4. Test webhook: curl POST /webhook    │
 │  5. Check logs in terminal              │
 │  6. Deploy: sam build && sam deploy      │
 └─────────────────────────────────────────┘
```

---

## 9. Common Commands Reference

| Task | Command |
|------|---------|
| Start local server | `python run_local.py` |
| Start frontend dashboard | `cd frontend && npm run dev` |
| Run all tests | `pytest tests/ -v` |
| Run pipeline simulation | `python test_local_pipeline.py` |
| Run new v1.3.0 tests | `pytest tests/test_graph.py tests/test_deep_solver.py tests/test_llm_observability.py -v` |
| Build for AWS | `sam build` |
| Deploy to AWS | `sam deploy` |
| View AWS logs | `sam logs -n WorkerFunction --stack-name repomind --tail` |
| Check health | `curl http://localhost:8000/health` |
| Interactive API docs | Open `http://localhost:8000/docs` in browser |
| Open frontend dashboard | Open `http://localhost:3001` in browser |
| Install Python deps | `pip install -r requirements.txt` |
| Install frontend deps | `cd frontend && npm install` |
| Activate venv (Windows) | `.\.venv\Scripts\Activate.ps1` |
| Activate venv (Linux) | `source .venv/bin/activate` |
