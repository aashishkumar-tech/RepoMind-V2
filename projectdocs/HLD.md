# 📐 High-Level Design (HLD) — RepoMind V2

## 1. Document Information

| Field | Value |
|-------|-------|
| **Project** | RepoMind V2 |
| **Version** | 2.0.0 (Self-Serve + HITL Release) |
| **Author** | RepoMind Team |
| **Date** | June 2026 |
| **Status** | Implementation Phase — Hackathon Demo Ready |

---

## 2. System Overview

RepoMind is a **serverless, event-driven 6-agent swarm with Human-in-the-Loop merge gating** that automatically detects, diagnoses, and proposes fixes for CI/CD failures in GitHub repositories. The system uses **Azure OpenAI GPT-4o** for high-quality multi-agent reasoning (with **Groq Llama 3.3** as a free-tier fallback), an Anthropic-style **deep agent solver** with file-reading tools, and a rule-based policy engine for safety. Every LLM call is traced for cost/tokens/latency, and an independent **LLM-as-Judge** audits the swarm's output for hallucinations.

**V2 additions:** every onboarded repo owns a `.repomind.yml` self-serve config (created via a one-click "welcome PR"), and the LangGraph pipeline now **pauses before merging**, persists state to S3, and waits for a human PR review before applying any change.

### 2.1 Goals

- **Zero human intervention** for low-risk, high-confidence CI failures (when `hitl_required: false`)
- **Human-gated merge** when owners want HITL (default in V2)
- **Sub-5-minute** response time from failure detection to PR creation
- **Cost-aware** — supports free-tier (Groq, $0/mo) and Azure mode (~$40-100 per 1,000 events)
- **Fail-safe** — deny by default, conservative policy enforcement, dry-run as default mode
- **Self-serve** — repo owners control behaviour via `.repomind.yml` (no operator handshake needed)
- **Self-auditing** — LLM-as-Judge independently grades the swarm's triage quality
- **Observable** — per-call LLM tracing + Prometheus metrics + live dashboard

### 2.2 Non-Goals (Phase 1)

- Multi-cloud support (AWS primary; Azure Storage/Service Bus optional)
- Custom LLM fine-tuning
- Multi-language playbook execution

---

## 3. Major Components

### 3.1 Component Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                          EXTERNAL                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐            │
│  │ GitHub       │  │ Azure OpenAI │  │ Qdrant       │            │
│  │ (Repos/API)  │  │ (GPT-4o)     │  │ (Vector DB)  │            │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘            │
│                           │ + Groq fallback │                     │
└─────────┼─────────────────┼─────────────────┼─────────────────────┘
          │                 │                 │
┌─────────┼─────────────────┼─────────────────┼─────────────────────┐
│         │       AWS CLOUD │                 │                     │
│  ┌──────▼───────┐  ┌──────┴───────┐  ┌──────▼───────┐             │
│  │ API Gateway  │  │ Lambda       │  │ S3           │             │
│  │ (Ingress)    │  │ (Compute)    │  │ (Storage)    │             │
│  └──────┬───────┘  └──────┬───────┘  └──────────────┘             │
│         │                 │                                       │
│  ┌──────▼───────┐  ┌──────▼───────┐  ┌──────────────┐             │
│  │ SQS Queue    │  │ CloudWatch   │  │ SSM (Kill    │             │
│  │ (Messaging)  │  │ (Logs)       │  │  Switch)     │             │
│  └──────────────┘  └──────────────┘  └──────────────┘             │
└───────────────────────────────────────────────────────────────────┘
                       │
                       ▼
            ┌──────────────────────────┐
            │  Pushgateway → Prometheus│
            │  → Grafana + Next.js UI  │
            └──────────────────────────┘
```

### 3.2 Component Responsibilities

| Component | Responsibility | Technology |
|-----------|---------------|------------|
| **Webhook Handler** | Receive & validate GitHub events | FastAPI + Mangum on Lambda |
| **Message Queue** | Decouple webhook from processing | Amazon SQS + DLQ |
| **Worker** | Pre-pipeline orchestrator (logs/sanitize/excerpt) | Lambda (SQS-triggered) |
| **Log Fetcher** | Download GitHub Actions logs | httpx + tenacity |
| **Sanitizer** | Redact secrets from logs | regex patterns |
| **Excerpt Generator** | Extract relevant log sections | Heuristic |
| **LangGraph Swarm** | 6-agent orchestration with retry edge | langgraph 0.3.4 |
| **Evidence Agent** | RAG retrieval of similar past failures | Qdrant search |
| **Triage Agent** | Classify failure type with RAG context | Azure GPT-4o + traced_completion |
| **Planner Agent** | Generate fix plan with RAG context | Azure GPT-4o + traced_completion |
| **Solver Agent (Tier 1)** | Deep agent with file-reading tools | deepagents + langchain-openai |
| **Solver Agent (Tier 2)** | Direct LLM fallback | Azure GPT-4o + traced_completion |
| **Validator Agent** | Peer review with retry routing | Azure GPT-4o + traced_completion |
| **Policy Engine** | Approve/deny auto-fix | Rule-based YAML evaluation |
| **LLM-as-Judge** | Independent quality auditor (post-graph) | Azure GPT-4o |
| **LLM Observability** | Per-call tracing (tokens, cost, latency) | shared/llm_observability.py |
| **Code Quality Gate** | Validate generated code before PR | ast + ruff + black + mypy |
| **PR Creator** | Create fix branch + PR (skips on empty diff) | PyGithub API |
| **Verifier** | Check CI result on fix/* branches | PyGithub API |
| **Rollback Client** | Revert failed fix PRs automatically | PyGithub + S3 markers |
| **Metrics Registry** | Pipeline + LLM metrics (counters, histograms) | prometheus-client + Pushgateway |
| **Kill Switch** | Emergency halt of all auto-fix operations | AWS SSM Parameter Store |
| **Frontend Dashboard** | Live agent visibility (RAG + LLM cost + Judge) | Next.js 14 + React 18 |
| **Vector Indexer** | Embed & store for RAG | sentence-transformers + Qdrant |
| **Graph Orchestrator** | Coordinate analysis steps | LangGraph |
| **Storage** | Persist artifacts & logs | S3 (prod) / local filesystem (dev) |

---

## 4. Data Flow

### 4.1 Primary Pipeline Flow

```
 ① GitHub CI Fails
       │
       ▼
 ② Webhook received (Step 1)
       │ Validate HMAC signature
       │ Parse payload
       │ Generate event_id
       ▼
 ③ Message queued to SQS
       │
       ▼
 ④ Worker triggered (Step 2)
       │
       ├──▶ ⑤ Fetch CI logs from GitHub API
       ├──▶ ⑥ Sanitize logs (redact secrets)
       ├──▶ ⑦ Generate excerpt (key error lines)
       ├──▶ ⑧ Store logs + excerpt in S3
       │
       ├──▶ ⑨ Triage: classify failure type (Step 5)
       │       Input: excerpt
       │       Output: failure_type, confidence, summary
       │
       ├──▶ ⑩ Plan: generate fix actions (Step 6)
       │       Input: triage + excerpt
       │       Output: playbook_id, actions, code_changes
       │
       ├──▶ ⑪ Policy: evaluate safety (Step 7)
       │       Input: triage + plan
       │       Output: allow / deny + reason
       │
       ├──▶ ⑪.5 Code Quality Gate (Step 9)
       │       Input: code_changes from plan
       │       Output: pass/fail + check details
       │       Blocking: syntax + ruff failures prevent PR
       │
       ├──▶ ⑫ PR: create fix pull request (Step 8)
       │       Input: plan + policy (if allowed)
       │       Output: PR URL, branch, commit SHA
       │
       ├──▶ ⑬ Index: embed & store vectors (Step 3)
       │       Input: excerpt, triage, plan
       │       Output: Qdrant vectors + S3 backup
       │
       ├──▶ ⑭ Save artifacts.json to S3
       ├──▶ ⑮ Save timeline.json to S3
       ├──▶ ⑯ Push metrics to Pushgateway (Step 11)
       └──▶ ⑰ Send notification (email / PR comment)

 ⑱ Fix PR triggers CI re-run on fix/* branch
       │
       ▼
 ⑲ GitHub sends workflow_run.completed webhook
       │
       ▼
 ⑳ Worker routes to Verification (Step 10)
       │
       ├──▶ CI passed → status = "verified" ✅
       │
       └──▶ CI failed → Rollback triggered
               │ Anti-flapping check (S3 marker)
               │ Rate limit check (max/hour)
               ▼
              Create revert PR, notify, audit
```

### 4.2 Data Contracts Between Steps

| Source | Target | Data | Format |
|--------|--------|------|--------|
| GitHub | Step 1 | `workflow_run` event | JSON webhook payload |
| Step 1 | SQS | `event_id, repo, run_id, run_url, timestamp` | JSON |
| Step 2 | Step 5 | `excerpt` text | String |
| Step 5 | Step 6 | `failure_type, confidence, summary` | Dict |
| Step 6 | Step 7 | `playbook_id, actions, risk_level, confidence` | Dict |
| Step 6 | Step 9 | `code_changes` list | List[Dict] |
| Step 9 | Step 8 | `quality report (pass/fail, checks)` | Dict |
| Step 7 | Step 8 | `decision (allow/deny), reason` | Dict |
| Step 8 | S3 | `pr_url, branch, commit_sha` | Dict |
| Step 8 | Step 10 | `fix/* branch triggers CI re-run` | GitHub webhook |
| Step 10 | S3 | `rollback marker, audit record` | JSON |
| Step 10 | GitHub | `revert PR (if CI failed)` | PyGithub API |
| Step 11 | Pushgateway | `counters, histogram, gauges` | Prometheus exposition |
| Step 11 | SSM | `kill switch state read` | boto3 SSM API |

---

## 5. Deployment Topology

### 5.1 Production (AWS)

```
Region: ap-south-1 (Mumbai) — configurable

┌─ API Gateway ─────────────────┐
│  POST /webhook                │
│  GET  /health                 │
└──────────┬────────────────────┘
           ▼
┌─ Lambda: repomind-webhook ────┐
│  Memory: 256 MB               │
│  Timeout: 30s                 │
│  Runtime: Python 3.12         │
└──────────┬────────────────────┘
           ▼
┌─ SQS: repomind-events ───────┐
│  Visibility: 360s             │
│  Retention: 24h               │
│  DLQ: max 3 receives         │
└──────────┬────────────────────┘
           ▼
┌─ Lambda: repomind-worker ─────┐
│  Memory: 1024 MB              │
│  Timeout: 300s                │
│  Runtime: Python 3.12         │
│  Batch Size: 1                │
└───────────────────────────────┘
```

### 5.2 Development (Local)

```
┌─ Uvicorn (localhost:8080) ────┐
│  FastAPI app                  │
│  Local filesystem storage     │
│  In-memory queue              │
│  Swagger UI at /docs          │
└───────────────────────────────┘
```

---

## 6. Scalability Considerations

| Concern | Strategy |
|---------|----------|
| **Concurrent failures** | SQS handles queuing; Lambda auto-scales |
| **Large logs** | Heuristic excerpt reduces to ~200-300 lines |
| **LLM rate limits** | Tenacity retry with exponential backoff |
| **S3 growth** | Lifecycle policies: 30d logs, 180d artifacts |
| **Vector DB growth** | Qdrant handles millions of vectors; optional cleanup |

---

## 7. Availability & Reliability

| Feature | Mechanism |
|---------|-----------|
| **Retry on failure** | SQS retry (max 3) → DLQ |
| **Network resilience** | Tenacity exponential backoff on HTTP calls |
| **Partial failure** | Worker saves partial artifacts even on error |
| **Monitoring** | CloudWatch Logs + Prometheus + Grafana dashboards |
| **LLM unavailability** | Keyword heuristic fallback for triage |
| **Kill switch** | SSM parameter instantly halts all auto-fix operations |
| **Rollback safety** | Anti-flapping + rate limiting prevent cascading reverts |

---

## 8. Integration Points

| System | Integration Method | Purpose |
|--------|--------------------|---------|
| **GitHub** | Webhook (inbound) + REST API (outbound) | Event source + PR creation |
| **Groq** | REST API (outbound) | LLM inference for triage & planning |
| **Qdrant** | HTTP API (outbound) | Vector storage & similarity search |
| **AWS S3** | boto3 SDK | Artifact & log storage |
| **AWS SQS** | boto3 SDK | Event queuing |
| **AWS SSM** | boto3 SDK | Kill switch parameter read |
| **Gmail** | SMTP | Email notifications |
| **Prometheus Pushgateway** | HTTP push | Metrics collection from Lambda |
| **Prometheus** | HTTP scrape | Time-series storage |
| **Grafana** | HTTP UI | Metrics dashboards |
