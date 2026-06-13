# 🔧 Tech Stack Document — RepoMind V2

## 1. Overview

RepoMind is an **agent swarm with Human-in-the-Loop merge gating** built on a **hybrid stack**:

- **Compute:** AWS Lambda (free tier) — pay-per-execution serverless
- **Primary LLM:** Azure OpenAI (GPT-4o) — high-quality reasoning + tool use
- **Fallback LLM:** Groq Cloud (Llama 3.3) — free, ultra-fast inference for budget mode
- **Orchestration:** LangGraph 0.3.4 — stateful 6-agent swarm with conditional retry edges + **HITL `interrupt_before` (V2)**
- **HITL State Persistence:** Custom `S3CheckpointSaver(BaseCheckpointSaver)` — paused-graph durable storage for multi-day reviews (V2)
- **Deep Reasoning:** `deepagents` (Anthropic-style) — multi-step tool use for the Solver agent
- **Per-Repo Config:** `.repomind.yml` parsed via PyYAML (V2) — self-serve owner control
- **Vector DB:** Qdrant — RAG retrieval for similar past failures
- **Observability:** Prometheus + Grafana + structured tracing via `shared/llm_observability.py`
- **Frontend:** Next.js 14 dashboard for live agent visibility

> Optional Azure credentials enable GPT-4o; without them the system **automatically falls back to Groq** for $0/month operation.
> V2 adds **per-repo `.repomind.yml`** and a **LangGraph `interrupt_before` pause-point** that requires a real GitHub PR review before any merge.

---

## 2. Core Language & Tooling

| Technology | Version | Purpose |
|-----------|---------|---------|
| **Python** | 3.12+ | Primary language for all backend components |
| **TypeScript** | 5.x | Frontend dashboard (`frontend/`) |
| **Node.js** | 18+ | Frontend runtime (Next.js dev server) |
| **uv** | Latest | Ultra-fast Python package & project manager (replaces pip/venv) |

> **Why uv?** Written in Rust, 10–100x faster than pip. Handles virtual environments, dependency resolution, and package installation in a single tool. See [docs.astral.sh/uv](https://docs.astral.sh/uv/).

---

## 3. Web & API Framework

| Package | Version | Purpose |
|---------|---------|---------|
| `fastapi` | 0.115.12 | REST API framework for webhook handler |
| `uvicorn` | 0.34.2 | ASGI server for local development |
| `mangum` | 0.19.0 | FastAPI → AWS Lambda adapter |
| `pydantic` | 2.11.3 | Data validation, serialization, type safety |

---

## 4. AWS Services (Free Tier)

| Service | Purpose | Free Tier Limit |
|---------|---------|-----------------|
| **API Gateway** | Webhook ingress endpoint | 1M API calls/month |
| **AWS Lambda** | Compute (webhook + worker) | 1M requests + 400K GB-sec/month |
| **Amazon SQS** | Event message queue + DLQ | 1M requests/month |
| **Amazon S3** | Artifact & log storage | 5 GB storage |
| **CloudWatch** | Logging & monitoring | 5 GB log data/month |
| **SSM Parameter Store** | Kill switch state (Standard tier) | Free (standard params) |

---

## 5. GitHub Integration

| Package | Version | Purpose |
|---------|---------|---------|
| `PyGithub` | 2.6.0 | GitHub REST API client (repos, PRs, files) |
| `PyJWT` | 2.10.1 | JWT generation for GitHub App auth |
| `cryptography` | 44.0.3 | RSA key handling for JWT signing |

**GitHub Services Used:**

- GitHub App (free) — authentication
- GitHub Actions — CI trigger source
- GitHub API — log downloads, PR creation

---

## 6. HTTP & Resilience

| Package | Version | Purpose |
|---------|---------|---------|
| `httpx` | 0.28.1 | Async/sync HTTP client for API calls |
| `tenacity` | 9.1.2 | Retry with exponential backoff |

---

## 7. LLM & AI

### 7.1 LLM Providers (Hybrid)

| Provider | Model | Tier | Used For |
|----------|-------|------|----------|
| **Azure OpenAI** | `gpt-4o` (default) | Paid | **Primary** — Triage, Planner, Solver, Validator, Judge |
| **Groq Cloud** | `llama-3.3-70b-versatile` | Free | **Fallback** — when Azure creds are absent |

### 7.2 LLM Packages

| Package | Version | Purpose |
|---------|---------|---------|
| `openai` | 1.82.0 | Azure OpenAI SDK (chat completions, embeddings) |
| `azure-identity` | 1.19.0 | Azure AD authentication (managed identity) |
| `langchain-openai` | 0.2.14 | LangChain bridge for `deepagents` (uses `AzureChatOpenAI`) |
| `groq` | 0.25.0 | Groq Python SDK (fallback) |

### 7.3 LLM Usage by Agent

| Agent | LLM Call | Mode | Temp | Tokens |
|-------|----------|------|------|--------|
| Step 5 — **Triage** | Failure classification | JSON object | 0.1 | ≤ 1024 |
| Step 6 — **Planner** | Fix plan generation | JSON object | 0.2 | ≤ 1500 |
| Step 4 — **Solver** (Tier 1) | Deep agent (multi-step) | Tool calls | 0.2 | ≤ 4000 |
| Step 4 — **Solver** (Tier 2) | Direct fallback | JSON object | 0.2 | ≤ 2000 |
| Step 4 — **Validator** | Peer review | JSON object | 0.1 | ≤ 1500 |
| Step 4 — **Judge** | Quality audit | JSON object | 0.0 | ≤ 1000 |

> **All 4 active call sites** use `traced_completion()` from `shared/llm_observability.py` for full token + cost + latency tracking.

### 7.4 Hybrid Deep Agent Solver (Tier 1 + Tier 2)

| Component | Purpose |
|-----------|---------|
| **`deepagents`** 0.6.8 | Anthropic-style harness with tool use + sub-agents (Tier 1) |
| **`langchain-openai`** 0.2.14 | `AzureChatOpenAI` adapter that `deepagents` consumes |
| **3 read-only tools** | `read_repo_file`, `list_repo_directory`, `search_repo_code` |
| **2 sub-agents** | `code-reader` (file inspection), `diff-writer` (diff generation) |
| **Tool budget** | 8 reads max, 50 KB per file, 45 s timeout |
| **Tier 2 fallback** | Direct Azure GPT-4o call when deep agent times out / errors / returns empty |

---

## 8. Embeddings & Vector Search

| Package / Service | Version | Purpose |
|-------------------|---------|---------|
| **Azure OpenAI Embeddings** | `text-embedding-3-small` | 1536-dim embeddings for RAG (primary) |
| `qdrant-client` | 1.14.2 | Vector database client |
| `sentence-transformers` | 4.1.0 *(legacy)* | Local fallback (`all-MiniLM-L6-v2`, 384-dim) |

**Vector DB:** Qdrant Cloud free tier or self-hosted on EC2 t2.micro  
**Active model:** `text-embedding-3-small` (Azure) when configured, else local MiniLM.

---

## 9. Pipeline Orchestration

| Package | Version | Purpose |
|---------|---------|---------|
| `langgraph` | **0.3.4** | Stateful directed-graph workflow orchestration |
| `deepagents` | 0.6.8 | Sub-graph harness for the Solver agent |
| `langchain-openai` | 0.2.14 | LangChain LLM wrapper used by deep agent |

**Active Graph Flow (6-agent swarm):**

```
evidence → triage → planner → solver → validator → policy
                                  ↑___________│ (max 2 retries on rejection)
```

**Fallback:** Sequential execution if LangGraph fails (`agents/graph.py::_sequential_run`).

---

## 10. Configuration & Utilities

| Package | Version | Purpose |
|---------|---------|---------|
| `python-dotenv` | 1.1.0 | Load .env files for local development |
| `pyyaml` | 6.0.2 | Parse policy & config YAML files |
| `boto3` | 1.38.24 | AWS SDK for Python (S3, SQS) |

---

## 11. Logging & Observability

| Package | Version | Purpose |
|---------|---------|---------|
| `structlog` | 25.1.0 | Structured JSON logging |
| `prometheus-client` | 0.21.1 | Prometheus metrics |

**Production:** JSON output for CloudWatch parsing  
**Development:** Colored console output for readability

---

## 12. LLM Observability Layer (`shared/llm_observability.py`)

| Capability | Implementation |
|------------|----------------|
| **Per-call tracing** | `traced_completion()` wrapper records tokens, latency, cost |
| **Cost engine** | `estimate_cost_usd()` with Azure pricing table (GPT-4o, GPT-4o-mini, GPT-4-turbo, GPT-3.5, Llama models @ $0) |
| **Prompt hashing** | 12-char SHA-256 prefix for cache analysis |
| **Per-event aggregation** | `summarize_traces()` builds `total_cost_usd`, `total_tokens`, per-agent breakdown |
| **State propagation** | `attach_trace(state, trace)` appends to `state["llm_traces"]` |
| **LLM-as-Judge** | `agents/llm_judge.py` grades triage on factuality + completeness + calibration + hallucination |

**Six new Prometheus metrics:**

- `repomind_llm_calls_total{agent, model, status}` (Counter)
- `repomind_llm_tokens_total{agent, model, type}` (Counter, type=prompt|completion)
- `repomind_llm_latency_seconds{agent, model}` (Histogram, buckets 0.1–90 s)
- `repomind_llm_cost_usd_total{agent, model, repo}` (Counter)
- `repomind_llm_judge_score{agent, judged_agent, metric}` (Gauge)
- `repomind_llm_hallucinations_total{judged_agent, model}` (Counter)

---

## 13. Frontend Dashboard (`frontend/`)

| Package | Version | Purpose |
|---------|---------|---------|
| `next` | 14.2.3 | React framework with App Router |
| `react` | 18.x | UI library |
| `react-dom` | 18.x | React DOM renderer |
| `typescript` | 5.x | Type system |

**Features:**

- 5-second polling of `/api/events` with mock fallback
- 6-card stats bar: Total Events · PRs Created · Policy Denied · Errors · Avg RAG Grade · Total LLM Cost
- 7-step agent pipeline visualization per event
- 📊 RAG Quality card (A–F grade + retrieval/context/generation breakdown)
- 💰 LLM Cost & Tokens card (stacked bar + per-agent cost)
- 🛡️ LLM-as-Judge card (factuality, completeness, calibration, hallucination flag, verdict)

---

## 14. Testing

| Package | Version | Purpose |
|---------|---------|---------|
| `pytest` | 8.3.5 | Test framework |
| `pytest-asyncio` | 0.26.0 | Async test support |
| `pytest-cov` | 6.1.1 | Test coverage reporting |

**Test files added in v1.3.0:**

- `tests/test_graph.py` — LangGraph pipeline + retry routing (5 tests)
- `tests/test_deep_solver.py` — Hybrid solver helpers + fallback chain (11 tests)
- `tests/test_llm_observability.py` — Tracing, cost engine, LLM-as-judge (14 tests)

---

## 15. Infrastructure-as-Code

| Technology | Purpose |
|-----------|---------|
| **AWS SAM** | Serverless Application Model — `template.yaml` |
| **CloudFormation** | Underlying IaC engine |

---

## 16. Code Quality Gate (Step 9)

| Package | Version | Purpose |
|---------|---------|---------|
| `ruff` | 0.8.6 | Ultra-fast Python linter (blocking check) |
| `black` | 24.10.0 | Python code formatter (warning check) |
| `mypy` | 1.14.1 | Static type checker (warning check) |
| `coverage[toml]` | 7.6.10 | Code coverage measurement |

**Built-in:** `ast.parse` (Python stdlib) — syntax validation (blocking check)  
**Config:** `pyproject.toml` — unified configuration for all tools  
**CI:** `.github/workflows/ci.yml` — GitHub Actions pipeline

---

## 17. Verification & Rollback (Step 10)

| Package | Version | Purpose |
|---------|---------|---------|
| `PyGithub` | 2.6.0 | Revert PR creation via GitHub API |
| `boto3` | 1.38.24 | S3 rollback markers + rate limiting counters |

**Anti-Flapping:** S3 marker prevents rolling back the same event twice  
**Rate Limiting:** Max N rollbacks per hour (configurable via `MAX_ROLLBACKS_PER_HOUR`)  
**Audit:** Rollback records stored in S3 for post-mortem analysis

---

## 18. Observability & Kill Switch (Step 11)

**Pipeline metrics:**

- `repomind_events_total` — Webhook events received
- `repomind_pipeline_duration_seconds` — End-to-end latency histogram
- `repomind_prs_created_total` — PRs opened
- `repomind_verification_total` — CI verification outcomes
- `repomind_rollbacks_total` — Rollbacks triggered
- `repomind_errors_total` — Errors by step

**LLM metrics:** see Section 12.

**Kill Switch:** AWS SSM Parameter Store (`/repomind/kill_switch`)  
**Push Model:** Metrics pushed to Pushgateway at pipeline end (Lambda-compatible)  
**Fail-Safe:** SSM unreachable → assume kill switch ON (halt pipeline)  
**Cache:** 30-second TTL to minimize SSM API calls

---

## 19. Monitoring Infrastructure

| Technology | Purpose |
|-----------|---------|
| **Prometheus Pushgateway** | Receives metrics pushed from Lambda |
| **Prometheus** | Scrapes Pushgateway, stores time-series data |
| **Grafana** | Dashboard UI for metrics visualization |
| **Next.js Dashboard** | Live agent-level visibility (`frontend/`) |
| **Docker Compose** | Local/EC2 deployment of monitoring stack |

**Ports:** Pushgateway :9091 · Prometheus :9090 · Grafana :3000 · Next.js :3001

---

## 20. Cost Summary

### Free-Tier Mode (Groq fallback, no Azure)

```
┌─────────────────────────────────────────┐
│        TOTAL MONTHLY COST: $0           │
├─────────────────────────────────────────┤
│ AWS Free Tier:        $0                │
│ Groq LLM:             $0 (free tier)    │
│ Qdrant Cloud:         $0 (free tier)    │
│ GitHub:               $0 (free)         │
│ sentence-transformers: $0 (local)       │
│ All Python packages:  $0 (open source)  │
└─────────────────────────────────────────┘
```

### Production Mode (Azure GPT-4o)

```
┌─────────────────────────────────────────┐
│  ESTIMATED COST per 1,000 events:       │
├─────────────────────────────────────────┤
│ Azure GPT-4o (in):  $2.50 / 1M tokens   │
│ Azure GPT-4o (out): $10.00 / 1M tokens  │
│ Per event (avg):    ~$0.04 - $0.10      │
│   (4-5 LLM calls × ~3K tokens each)     │
│ AWS:                ~$0 (free tier)     │
│ Qdrant + GitHub:    $0                  │
├─────────────────────────────────────────┤
│ 1,000 events:       ~$40 - $100/month   │
└─────────────────────────────────────────┘
```

> Cost is tracked per event in `state["llm_summary"]["total_cost_usd"]` and exported as the `repomind_llm_cost_usd_total` Prometheus counter.
