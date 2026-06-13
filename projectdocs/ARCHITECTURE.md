# 🏗️ Architecture Document — RepoMind V2

## 1. Overview

RepoMind is an **autonomous agent swarm** that detects failed GitHub Actions workflows, diagnoses root causes via a multi-agent LLM pipeline, generates and validates fixes, evaluates safety policies, and opens a pull request — **then waits for a human to approve the merge** (V2 HITL). It also verifies the fix in CI and rolls back automatically if the fix breaks the build.

Built for the **Microsoft Build AI Hackathon 2026** (Agent Swarms theme), RepoMind combines:

- A **6-agent LangGraph swarm** with a conditional retry edge between Solver and Validator
- A **hybrid Tier 1 + Tier 2 Solver** using Anthropic-style `deepagents` with file-reading tools
- **Azure OpenAI (GPT-4o)** primary LLM with **Groq fallback** for free-tier mode
- **Full LLM observability** with per-call cost / token / latency tracing
- An independent **LLM-as-Judge** that grades the swarm's own output
- A **live Next.js dashboard** showing every agent step and its quality scores
- **V2: Self-serve `.repomind.yml`** — repos opt in via a YAML config in their own root
- **V2: Human-in-the-Loop merge gate** — graph pauses (S3-checkpointed) until a reviewer approves

---

## 1a. V2 Components (New)

| Component                          | File                              | Role                                                    |
|------------------------------------|-----------------------------------|---------------------------------------------------------|
| **Repo config loader**             | `shared/repomind_config.py`       | Pulls `.repomind.yml` via GitHub Contents API           |
| **Comment poster**                 | `pr_creator/comment_poster.py`         | Dry-run previews, status comments, apologies            |
| **Welcome PR creator**             | `pr_creator/welcome_pr.py`             | One-time intro PR on app install                        |
| **HITL graph nodes**               | `agents/hitl_nodes.py`             | `pr_creator → merge_decision → merge / cleanup`         |
| **S3-backed checkpointer**         | `agents/checkpointer.py`           | Persists graph state across Lambda invocations          |
| **Step 12 — Review handler**       | `review/review_handler.py`        | Resumes paused graphs when PR review arrives            |
| **Webhook event dispatchers**      | `webhook/webhook_handler.py`        | `installation`, `installation_repositories`, `pull_request_review` |

```
┌────────────────────────────────────────────────────────────────────┐
│  REPO OWNER (their GitHub repo)                                    │
│  └── .repomind.yml ──┐                                             │
└────────────────────┬─┴────────────────────────────────────────────┘
                     │ GET via Contents API
                     ▼
┌────────────────────────────────────────────────────────────────────┐
│  WORKER (step2)                                                    │
│  load_repomind_config(repo) → mode, hitl_required, policy          │
└────────────────────┬───────────────────────────────────────────────┘
                     ▼
┌────────────────────────────────────────────────────────────────────┐
│  LANGGRAPH (step4)                                                 │
│  evidence → triage → planner → solver → validator → policy         │
│      → pr_creator (open PR OR comment OR skip)                     │
│      → [INTERRUPT] ← checkpoint to S3 ← Lambda exits               │
│                                                                    │
│  ⏸  graph PAUSED for hours/days waiting for human                  │
│                                                                    │
│  → merge_decision (after step12 resumes with verdict)              │
│      ├── approved → merge_node                                     │
│      ├── rejected → cleanup_node                                   │
│      └── skipped/timeout → END                                     │
└────────────────────┬───────────────────────────────────────────────┘
                     ▲
                     │
┌────────────────────┴───────────────────────────────────────────────┐
│  STEP 12 — Review Handler (review/review_handler.py)               │
│  GitHub pull_request_review webhook → look up event_id from        │
│  S3 PR↔event index → resume_pipeline(event_id, verdict)            │
└────────────────────────────────────────────────────────────────────┘
```

---

## 2. Architecture Principles

| Principle | Description |
|-----------|-------------|
| **Event-Driven** | Triggered by GitHub webhooks; asynchronous SQS processing |
| **Serverless-First** | AWS Lambda for compute; no servers to manage |
| **Single Responsibility** | Each step is a separate module doing one thing well |
| **Fail-Safe** | Policy engine denies by default; conservative approach |
| **Observable** | Structured JSON logging + per-LLM-call tracing + Prometheus + live dashboard |
| **Self-Auditing** | LLM-as-Judge independently grades the agent swarm's output |
| **Cost-Aware** | Tracks USD cost per event; supports free-tier (Groq) and paid (Azure) modes |
| **Hybrid Reasoning** | Tier 1 deep agent (tools + sub-agents) → Tier 2 direct LLM fallback |

---

## 3. High-Level Architecture

```
┌──────────────┐     ┌──────────────────┐     ┌─────────────┐
│ GitHub       │────▶│ API Gateway      │────▶│ Step 1      │
│ Actions      │     │ (Webhook URL)    │     │ Webhook     │
│ (CI Failure) │     └──────────────────┘     │ Handler     │
└──────────────┘                              └──────┬──────┘
                                                     │
                                                     ▼
                                              ┌──────────────┐
                                              │ Amazon SQS   │
                                              │ Event Queue  │
                                              └──────┬───────┘
                                                     │
                                                     ▼
   ┌─────────────────────────────────────────────────────────────────────┐
   │                  Step 2: Worker (Orchestrator)                       │
   │                                                                     │
   │  Pre-Pipeline:  Log Fetch → Sanitize → Excerpt → S3 Save            │
   │                                                                     │
   │  ┌─────────────────────────────────────────────────────────────┐    │
   │  │           Step 4: LangGraph 6-Agent Swarm                    │    │
   │  │                                                              │    │
   │  │   evidence ──▶ triage ──▶ planner ──▶ solver                │    │
   │  │     (RAG)     (LLM)       (LLM)        ↓                    │    │
   │  │                                     validator               │    │
   │  │                              ┌────────┘ │                   │    │
   │  │                              │ approve  │ reject (max 2x)   │    │
   │  │                              ▼          └──▶ solver          │    │
   │  │                            policy                            │    │
   │  └─────────────────────────────────────────────────────────────┘    │
   │                              │                                       │
   │                              ▼                                       │
   │  Post-Pipeline:                                                      │
   │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐            │
   │  │Step 9    │─▶│Step 8    │─▶│Step 3    │─▶│Step 11   │            │
   │  │Code Gate │  │PR Create │  │Index RAG │  │Metrics   │            │
   │  └──────────┘  └──────────┘  └──────────┘  └──────────┘            │
   │                                                                     │
   │  LLM Observability (cross-cutting): traced_completion + LLM-as-Judge│
   └─────────────────────────────────────────────────────────────────────┘
                  │             │             │              │
                  ▼             ▼             ▼              ▼
           ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐
           │ S3       │  │ Qdrant   │  │ Azure    │  │ Pushgateway  │
           │ (Artif.) │  │ (Vectors)│  │ OpenAI   │  │ → Prometheus │
           └──────────┘  └──────────┘  │ (Groq    │  │ → Grafana    │
                                       │  fallbk.)│  │ + Next.js UI │
                                       └──────────┘  └──────────────┘
```

**Flow at a glance:** Webhook → SQS → Worker pre-pipeline (log fetch/sanitize/excerpt) → LangGraph swarm (6 agents) → post-pipeline (code gate / PR / index / metrics) → S3 artifacts + Prometheus metrics + live dashboard.

---

## 4. Component Architecture

### 4.1 Shared Layer (`shared/`)

The foundation layer providing cross-cutting concerns:

- **`config.py`** — Centralized settings (singleton). Now includes Azure OpenAI, Azure Storage, Azure Service Bus, and `LLM_JUDGE_ENABLED` settings.
- **`event_id.py`** — Deterministic event ID generation (`evt-<slug>-<run_id>-<timestamp>`)
- **`logger.py`** — Structured JSON logging via structlog
- **`timeline.py`** — Pipeline step timing and progress tracking
- **`storage.py`** — S3 (production) / local filesystem (dev) abstraction
- **`github_auth.py`** — GitHub App JWT auth with token caching
- **`notifier.py`** — Email (Gmail SMTP) + GitHub PR comment notifications
- **`azure_llm.py`** ✨ NEW — LLM client factory: returns Azure OpenAI client when configured, else Groq
- **`llm_observability.py`** ✨ NEW — `traced_completion()` wrapper with cost / token / latency tracking + per-event aggregation

### 4.2 Step 1 — Webhook Handler (`webhook/`)

**Purpose:** Receive GitHub webhook, validate, queue event.  
**Deployment:** API Gateway → Lambda  
**Key Design:** Extremely lightweight — no heavy processing, no S3 writes, no LLM calls.

### 4.3 Step 2 — Worker (`worker/`)

**Purpose:** Core orchestrator — runs the entire fix pipeline.  
**Deployment:** SQS-triggered Lambda  
**Key Design:** Pre-pipeline (log fetch, sanitize, excerpt) prepares state, then delegates to `agents.graph.run_pipeline()` for the agent swarm. Post-pipeline (code-gate, PR, index, metrics) runs after the swarm.

### 4.4 Step 3 — Vector DB & RAG Metrics (`rag/`)

**Purpose:** Embed event data and store/retrieve from Qdrant for RAG; evaluate RAG quality.  
**Components:**

- `embedder.py` — Embedding generator (Azure `text-embedding-3-small`, falls back to local MiniLM)
- `indexer.py` — Upserts excerpt/triage/plan/verification vectors
- `retriever.py` — Searches Qdrant for similar past failures
- `rag_metrics.py` — `RAGEvaluator` produces hit rate, MRR, similarity stats, A–F grade

### 4.5 Step 4 — LangGraph 6-Agent Swarm (`agents/`)

**Purpose:** Stateful graph-based orchestration of the agent swarm.  
**Files:**

- `graph.py` — Builds the LangGraph `StateGraph`; defines edges and `should_retry_solver()` conditional router; assembles `initial_state` with `llm_traces`, `llm_summary`, `judge` fields
- `nodes.py` — All 6 agent node functions: `evidence_node`, `triage_node`, `planner_node`, `solver_node` (hybrid), `validator_node`, `policy_node`
- `models.py` — `PipelineState` `TypedDict` with all per-agent state fields
- `deep_solver.py` ✨ NEW — Anthropic-style deep agent (Tier 1) using `deepagents` + `langchain-openai`
- `llm_judge.py` ✨ NEW — Independent LLM-as-Judge that grades triage on factuality / completeness / calibration / hallucination

**Graph topology:**

```
START → evidence → triage → planner → solver → validator → [policy | solver]
                                                                ↑
                                                  retry on rejection (max 2x)
```

**Conditional routing:** `validator_node` sets `validation.status = "approved" | "rejected"`. `should_retry_solver()` reads it and either routes to `policy` (approve) or back to `solver` (reject, with feedback) up to 2 retries.

**Sequential fallback:** If LangGraph fails, `_sequential_run()` runs the same 6 nodes in order.

**Post-graph hooks (in `graph.py`):**

- `_attach_rag_report()` — Computes RAG quality grade
- `_collect_llm_traces()` — Aggregates per-agent LLM traces into `state["llm_summary"]`
- `_run_llm_judge()` — Calls `llm_judge.judge_triage()` and stores result in `state["judge"]`

### 4.6 Step 5 — Triage Agent (`triage/`)

**Purpose:** Classify CI failure type using LLM with RAG context from past failures.  
**Input:** Excerpt + top-3 similar incidents (from `state["similar_incidents"]`)  
**Output:** `{failure_type, confidence, root_cause, summary}` + `_llm_trace`  
**LLM:** Azure GPT-4o (JSON object mode, temp=0.1) with keyword-heuristic fallback if LLM fails.

### 4.7 Step 6 — Planner Agent (`planner/`)

**Purpose:** Generate fix plan with playbook ID, actions, and target files.  
**Input:** Triage result + top-2 similar past fixes  
**Output:** `{playbook_id, actions, files_to_modify, summary}` + `_llm_trace`  
**LLM:** Azure GPT-4o (JSON object mode, temp=0.2, max 1500 tokens).

### 4.8 Step 4 — Solver Agent (Hybrid Tier 1 + Tier 2)

**Purpose:** Generate concrete code changes (diffs) for the fix plan.

**Tier 1 — Deep Agent (`deep_solver.py`):**

- Uses `deepagents.create_deep_agent()` with `AzureChatOpenAI` from `langchain-openai`
- 3 read-only tools: `read_repo_file`, `list_repo_directory`, `search_repo_code`
- 2 sub-agents: `code-reader` (file inspection), `diff-writer` (diff generation)
- Tool budget: 8 reads max, 50 KB per file, 45 s timeout
- Returns `{reasoning, code_changes, confidence, risk_assessment, files_inspected, solver_mode: "deep_agent"}`

**Tier 2 — Direct LLM (`nodes.py::_direct_llm_solver`):**

- Activated when Tier 1 times out, errors, or returns empty `code_changes`
- Single Azure GPT-4o call with the full plan in the prompt
- Returns `{code_changes, reasoning, confidence, solver_mode: "direct_llm"}`

### 4.9 Step 4 — Validator Agent (`nodes.py::validator_node`)

**Purpose:** Peer review the solver's output before policy evaluation.  
**Input:** Solver's `code_changes` + plan + triage  
**Output:** `{status, confidence, issues, suggestions}` + `_llm_trace`  
**LLM:** Azure GPT-4o (JSON object mode, temp=0.1)  
**Routing:**

- `status = "approved"` → graph proceeds to `policy_node`
- `status = "rejected"` AND `validation_attempts < 2` → graph routes back to `solver_node` with `solver_feedback` injected into next solver prompt
- `status = "rejected"` AND `validation_attempts >= 2` → graph proceeds to `policy_node` anyway (last attempt is used)

### 4.10 Step 4 — LLM-as-Judge (`llm_judge.py`)

**Purpose:** Independent quality auditor — does NOT participate in the swarm; runs after the graph completes.  
**Input:** Triage result + raw excerpt  
**Output:** `{factuality_score, completeness_score, confidence_calibration, hallucination_flag, issues, overall_score, overall_grade, verdict_summary}`  
**LLM:** Azure GPT-4o (JSON object mode, temp=0.0)  
**Toggle:** `LLM_JUDGE_ENABLED=false` skips this call to save 1 LLM round-trip per event.

### 4.11 Step 7 — Policy Agent (`policy_engine/`)

**Purpose:** Rule-based safety evaluation — first-matching-rule wins, deny by default.

### 4.12 Step 8 — PR Creator (`pr_creator/`)

**Purpose:** Create GitHub branch, apply code changes, open pull request.  
**Hollow-PR Fix (v1.3.0):** When the solver produces no concrete `code_changes`, the PR creator now logs `pr_skipped_no_code_changes` and returns `{"status": "skipped"}` instead of opening an empty PR.

### 4.13 Step 9 — Code Quality Gate (`code_quality/`)

**Purpose:** Validate LLM-generated code changes before PR creation.  
**Tools:** `ast.parse` (syntax check), `ruff` (linting), `black` (formatting), `mypy` (type checking).  
**Severity:** Syntax + Ruff are **blocking**. Black + Mypy are **warnings** only.  
**Design:** Fail-open — if the checker itself crashes, it does not block the PR.

### 4.14 Step 10 — Verifier + Rollback (`verifier/`)

**Purpose:** Verify whether a fix branch CI passed after merge. Trigger rollback if CI failed.  
**Trigger:** `workflow_run.completed` webhook on `fix/*` branches, routed by Step 1.  
**Components:**

- `verifier.py` — Checks CI conclusion, triggers rollback on failure
- `rollback.py` — Creates revert PR via PyGithub with anti-flapping + rate limiting
- `models.py` — `VerificationResult` and `RollbackResult` dataclasses

**Safety Guards:** Anti-flapping (1 rollback per event), rate limiting (3/hour/repo), kill switch check, audit trail in S3.

### 4.15 Step 11 — Observability + Kill Switch (`observability/`)

**Purpose:** Prometheus metrics via Pushgateway + global kill switch via AWS SSM.  
**Pipeline metrics:** events, PRs, rollbacks, errors, step duration, confidence, kill switch state  
**LLM metrics (NEW in v1.3.0):**

- `repomind_llm_calls_total{agent, model, status}`
- `repomind_llm_tokens_total{agent, model, type}` (type=prompt|completion)
- `repomind_llm_latency_seconds{agent, model}` (histogram)
- `repomind_llm_cost_usd_total{agent, model, repo}`
- `repomind_llm_judge_score{agent, judged_agent, metric}`
- `repomind_llm_hallucinations_total{judged_agent, model}`

**Infrastructure:** Pushgateway + Prometheus + Grafana via docker-compose on EC2 (or local).  
**Design:** Metrics are non-fatal (no-op if Pushgateway is down). Kill switch is fail-safe (halts if SSM is unreachable).

### 4.16 Frontend Dashboard (`frontend/`) ✨ NEW

**Purpose:** Live agent-level visibility for hackathon demos.  
**Stack:** Next.js 14.2.3 + React 18 + TypeScript 5  
**Features:**

- 5-second polling of `/api/events` (mock fallback if API is down)
- 6-card stats bar: Total Events · PRs Created · Policy Denied · Errors · Avg RAG Grade · Total LLM Cost
- Per-event detail panel with 7-step pipeline visualization
- 📊 RAG Quality card (A–F + retrieval/context/generation breakdown)
- 💰 LLM Cost & Tokens card (stacked bar by agent)
- 🛡️ LLM-as-Judge card (factuality, completeness, calibration, hallucination flag, verdict)

---

## 5. Data Architecture

### 5.1 S3 Storage Structure

```
events/
  <repo-slug>/
    <event-id>/
      logs/
        full_logs.txt       ← Raw CI logs (30-day retention)
        excerpt.txt         ← Heuristic excerpt (90-day retention)
      artifacts.json        ← Triage + Plan + Solver + Validator + Policy + PR
                              + RAG report + LLM summary + Judge (180-day retention)
      timeline.json         ← Step-by-step execution log (180-day retention)

embeddings/
  <repo-slug>/
    <event-id>/
      excerpt_embedding.json
      triage_embedding.json
      plan_embedding.json
      verification_embedding.json
```

### 5.2 Event ID Format

```
evt-<repo-slug>-<workflow-run-id>-<timestamp>
Example: evt-myorg-service-a-123456789-20260213T154400Z
```

Properties: globally unique, lexicographically sortable, human-readable, debug-friendly.

### 5.3 Pipeline State (`agents/models.py::PipelineState`)

| Field | Producer | Description |
|-------|----------|-------------|
| `event` | webhook | GitHub event metadata |
| `excerpt` | step2 | Sanitized log excerpt |
| `similar_incidents` | step3 | Top-K RAG retrieval results |
| `triage` | step5 | Classification + `_llm_trace` |
| `plan` | step6 | Fix plan + `_llm_trace` |
| `solver_result` | step4 | Code changes + `_llm_trace` + `solver_mode` |
| `validation` | step4 | Approve/reject + feedback + `_llm_trace` |
| `validation_attempts` | step4 | Retry counter (max 2) |
| `solver_feedback` | step4 | Validator's feedback for next solver attempt |
| `policy` | step7 | Allow/deny decision |
| `rag_evaluation` | step3 | A–F grade + sub-scores + retrieval metrics |
| `llm_traces` | shared | List of per-call traces (tokens/cost/latency) |
| `llm_summary` | shared | Per-event aggregate (totals + per-agent breakdown) |
| `judge` | step4 | LLM-as-Judge verdict (grade + sub-scores + hallucination) |

---

## 6. Communication Patterns

| From | To | Protocol | Format |
|------|----|----------|--------|
| GitHub | Step 1 | HTTPS POST | Webhook payload (JSON) |
| Step 1 | Step 2 | SQS Message | `SQSMessage` (JSON) |
| Step 2 | GitHub | HTTPS (httpx) | GitHub REST API |
| Step 2 | S3 | boto3 | JSON / Text artifacts |
| Step 2 | Qdrant | HTTP | Vector upserts/queries |
| Step 4 | Azure OpenAI | HTTPS (openai SDK) | LLM chat completion (5 calls per event) |
| Step 4 | Groq | HTTPS (groq SDK) | LLM chat completion (fallback) |
| Step 4 | Filesystem (Tier 1) | Tool call | Read-only repo file access |
| Step 2 | Pushgateway | HTTP POST | Prometheus metrics (pipeline + LLM) |
| Step 2 | SSM | boto3 | Kill switch parameter read |
| Step 10 | GitHub | HTTPS (PyGithub) | Revert PR creation |
| Frontend | Backend | HTTPS GET | `/api/events` polling (5s) |

---

## 7. Error Handling Strategy

| Level | Strategy |
|-------|----------|
| **Network** | Exponential backoff retry (1s → 2s → 4s → 8s → 16s, max 5 retries) via tenacity |
| **LLM (Triage / Planner)** | Fall back to keyword heuristic if both Azure and Groq fail |
| **LLM (Solver Tier 1)** | Fall back to Tier 2 direct LLM on timeout / error / empty output |
| **LLM (Validator)** | Default to "approved" with low confidence if LLM call fails (does not block pipeline) |
| **LLM (Judge)** | Skip silently — judge is non-fatal observability |
| **Policy** | Fail-closed: if policy engine errors, decision = deny |
| **Code Quality** | Fail-open: if checker crashes, pipeline continues (does not block PR) |
| **Verification** | Non-fatal: if verification fails, error logged but no cascading failure |
| **Rollback** | Anti-flapping (1 per event), rate limiting (3/hour/repo), kill switch check |
| **Kill Switch** | Fail-safe: if SSM unreachable, assume ON (halt pipeline) |
| **LLM Tracing** | Non-fatal: trace failures never propagate to caller |
| **Metrics** | Non-fatal: if Pushgateway is down, log warning and continue |
| **Pipeline** | Partial artifacts saved on failure; error recorded in timeline |
| **Queue** | SQS DLQ after 3 failed processing attempts |

---

## 8. Security Architecture

- **Webhook Validation:** HMAC-SHA256 signature verification with constant-time comparison
- **Log Sanitization:** 10 regex patterns redact AWS keys, tokens, passwords, PII
- **GitHub Auth:** GitHub App JWT (short-lived) with installation token caching
- **Azure Auth:** API key (recommended) or `DefaultAzureCredential` for managed identity
- **Secrets:** Never stored in code; loaded from environment variables / AWS SSM
- **Policy Engine:** Conservative deny-by-default; only explicitly allowed fixes proceed
- **LLM Prompt Hashing:** SHA-256 of every prompt for cache analysis (no PII in hash)
- **Tier 1 Sandbox:** Solver tools are read-only; cannot write to disk, network, or repo

---

## 9. Deployment Architecture

- **Infrastructure-as-Code:** AWS SAM (`template.yaml`)
- **Compute:** AWS Lambda (Python 3.12, x86_64)
- **API:** Amazon API Gateway (REST)
- **Queue:** Amazon SQS with Dead Letter Queue
- **Storage:** Amazon S3 with lifecycle policies
- **Vector DB:** Qdrant Cloud (free tier) or self-hosted on EC2
- **LLM:** Azure OpenAI (region: `eastus2` recommended) or Groq Cloud
- **Frontend:** Next.js dev server (`localhost:3001`) or Azure Static Web Apps for prod
- **Monitoring:** Pushgateway + Prometheus + Grafana via docker-compose

---

## 10. Design Decisions

| Decision | Rationale |
|----------|-----------|
| SQS over Kafka | Simpler, serverless, free tier, sufficient for event volumes |
| Azure OpenAI primary, Groq fallback | Quality-first with zero-cost fallback for hackathon demos |
| Hybrid solver (deepagent → direct LLM) | Tool use for accuracy + reliability via fallback |
| Tool budget on deep agent (8 reads, 50 KB) | Predictable cost & cold-start latency for Lambda |
| 6-agent swarm with retry edge | Solver/Validator separation enables self-correction loop |
| LLM-as-Judge as separate node (post-graph) | Avoids feedback loops in the swarm itself; pure observability |
| `traced_completion` wrapper at every call site | Single source of truth for tokens, cost, latency |
| First-matching-rule policy | Predictable evaluation order, easy to debug |
| Sequential fallback for LangGraph | Reliability over orchestration elegance |
| Mock fallback in frontend | Demo works even when backend is down |
| S3 over DynamoDB for artifacts | Better for large JSON blobs, lifecycle policies, lower cost |
