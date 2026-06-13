# 📁 Repository Structure — RepoMind V2

## Complete File Tree

```
RepoMind/
│
├── .env.example              # Environment variable template (Azure + Groq + AWS + GitHub)
├── .gitignore                # Git ignore rules
├── Makefile                  # Quick commands (make lint, test, format, all)
├── README.md                 # Top-level project README (architecture + Quick Start)
├── pyproject.toml            # Unified config (ruff, black, mypy, pytest, coverage)
├── requirements.txt          # Python dependencies (azure, openai, deepagents, langgraph...)
├── requirements-dev.txt      # Dev dependencies (ruff, black, mypy, coverage)
├── repos.yaml                # Target repositories configuration
├── run_local.py              # Local development server (Uvicorn on port 8000)
├── test_local_pipeline.py    # Full pipeline simulation (no AWS needed)
├── samconfig.toml            # AWS SAM deploy config
├── template.yaml             # AWS SAM deployment template (IaC)
│
├── .github/                  # 🔄 CI/CD
│   └── workflows/
│       └── ci.yml            #    GitHub Actions: lint, format, typecheck, tests
│
├── projectdocs/              # 📚 Project documentation (20 files)
│   ├── README.md             #    Documentation index
│   ├── ARCHITECTURE.md       #    System architecture (6-agent swarm + HITL)
│   ├── HLD.md                #    High-Level Design
│   ├── LLD.md                #    Low-Level Design
│   ├── TECH_STACK.md         #    Technology stack (Azure + deepagents + ...)
│   ├── INSTALLATION.md       #    Installation guide
│   ├── HOW_TO_RUN.md         #    How to run locally & deploy
│   ├── REPO_STRUCTURE.md     #    This file
│   ├── API_REFERENCE.md      #    REST API & data schemas
│   ├── PIPELINE_WORKFLOW.md  #    Step-by-step pipeline flow
│   ├── LANGGRAPH_PIPELINE.md #    LangGraph swarm topology + retry edge + HITL
│   ├── CONFIGURATION.md      #    Environment & policy config + .repomind.yml
│   ├── TESTING.md            #    Test strategy & guide
│   ├── TESTING_GUIDE.md      # ✨ V2 — One-stop test command reference (E2E + unit)
│   ├── ONBOARDING.md         #    3-step user onboarding flow (V2 rewrite)
│   ├── DEPLOYMENT.md         #    AWS deployment guide
│   ├── SECURITY.md           #    Security documentation
│   ├── TROUBLESHOOTING.md    #    Debugging & FAQ
│   ├── CONTRIBUTING.md       #    Contribution guidelines
│   ├── GLOSSARY.md           #    Terminology reference (incl. HITL terms)
│   └── CHANGELOG.md          #    Version history (v2.0.0 at top)
│
├── frontend/                 # 🖼️ Next.js 14 Live Dashboard ✨ NEW
│   ├── package.json          #    next 14.2.3 + react 18 + typescript 5
│   ├── next.config.js        #    Next.js config
│   ├── tsconfig.json         #    TypeScript config
│   └── app/
│       ├── layout.tsx        #    Root layout
│       └── page.tsx          #    Live dashboard (5s polling, 6-card stats,
│                             #     RAG card, LLM Cost card, Judge card)
│
├── shared/                   # 🔧 Cross-cutting utilities
│   ├── __init__.py           #    Package init
│   ├── config.py             #    Centralized settings (Azure + Groq + LLM_JUDGE_ENABLED)
│   ├── event_id.py           #    Event ID generation (evt-<slug>-<run>-<ts>)
│   ├── logger.py             #    Structured logging (structlog, JSON/console)
│   ├── timeline.py           #    Pipeline step timing & progress tracker
│   ├── storage.py            #    S3 (prod) / LocalStorage (dev) abstraction
│   ├── github_auth.py        #    GitHub App JWT auth + token caching
│   ├── notifier.py           #    Email (SMTP) + PR comment notifications
│   ├── azure_llm.py          #    LLM factory: Azure primary, Groq fallback
│   ├── llm_observability.py  #    traced_completion + cost engine + summarize_traces
│   └── repomind_config.py    # ✨ V2 — .repomind.yml loader + parser + safe defaults
│
├── webhook/                    # 📡 Webhook Handler (GitHub → SQS)
│   ├── __init__.py           #    Package init
│   ├── models.py             #    Pydantic models (WebhookPayload, Installation,
│   │                         #     PullRequestReview, SQSMessage) — V2 extended
│   ├── signature.py          #    HMAC-SHA256 webhook signature validation
│   ├── sqs_client.py         #    SQS publisher (prod) / LocalQueue (dev)
│   ├── webhook_handler.py    #    FastAPI app — routes workflow_run, installation,
│   │                         #     installation_repositories, pull_request_review (V2)
│   └── lambda_handler.py     #    Mangum adapter for AWS Lambda
│
├── worker/                    # ⚙️ Worker — Core Pipeline Orchestrator
│   ├── __init__.py           #    Package init
│   ├── log_fetcher.py        #    Download GitHub Actions logs (ZIP → text)
│   ├── sanitizer.py          #    Redact secrets (10 regex patterns)
│   ├── excerpt.py            #    Heuristic excerpt generator (error lines)
│   └── worker.py             #    Pre-pipeline + .repomind.yml load + mode dispatch
│                             #     (workflow / installation / review) (V2)
│
├── rag/                    # 🧠 Vector DB — Embeddings + Search (RAG)
│   ├── __init__.py           #    Package init
│   ├── embedder.py           #    Azure text-embedding-3-small / MiniLM fallback
│   ├── indexer.py            #    Qdrant upsert + S3 backup
│   ├── rag_metrics.py        #    RAG evaluation (retrieval, context, generation quality)
│   └── retriever.py          #    Similarity search with filters
│
├── agents/                    # 🔀 LangGraph — 6-Agent Swarm + HITL
│   ├── __init__.py           #    Package init
│   ├── models.py             #    PipelineState TypedDict (incl. V2 HITL fields:
│   │                         #     repomind_config, mode, hitl_required, pr_url,
│   │                         #     human_approval, review_data, merge_result, cleanup_result)
│   ├── nodes.py              #    All 6 agent nodes (evidence, triage, planner,
│   │                         #     solver-hybrid, validator, policy)
│   ├── graph.py              #    StateGraph + retry edge + HITL interrupt_before
│   │                         #     + resume_pipeline() + post-graph hooks (V2)
│   ├── deep_solver.py        #    Anthropic-style deep agent (Tier 1)
│   │                         #     with 3 tools + 2 sub-agents + 8-read budget
│   ├── llm_judge.py          #    LLM-as-Judge (factuality + completeness +
│   │                         #     calibration + hallucination detection)
│   ├── hitl_nodes.py         # ✨ V2 — pr_creator, merge_decision, merge, cleanup
│   │                         #     + route_after_merge_decision router
│   └── checkpointer.py       # ✨ V2 — S3CheckpointSaver(BaseCheckpointSaver)
│                             #     + get_checkpointer() factory (S3 in prod, MemorySaver dev)
│
├── triage/                    # 🔍 Triage — Failure Classification
│   ├── __init__.py           #    Package init
│   └── triage.py             #    Azure GPT-4o (with traced_completion) + keyword fallback
│                             #    Now consumes RAG context (top-3 similar incidents)
│
├── planner/                    # 📋 Planner — Fix Plan Generation
│   ├── __init__.py           #    Package init
│   └── planner.py            #    Azure GPT-4o (with traced_completion) + template fallback
│                             #    Now consumes RAG context (top-2 similar past fixes)
│
├── policy_engine/                    # 🛡️ Policy — Safety Evaluation
│   ├── __init__.py           #    Package init
│   └── policy.py             #    Rule-based YAML policy engine (deny-by-default)
│                             #    + .repomind.yml user-config pre-filter (V2)
│
├── pr_creator/                    # 🔀 PR Creator — GitHub Pull Request
│   ├── __init__.py           #    Package init
│   ├── pr_creator.py         #    Create branch, apply changes, open PR
│   │                         #    + mode dispatch (auto_fix/dry_run/disabled) (V2)
│   ├── comment_poster.py     # ✨ V2 — Smart PR/commit comment poster
│   │                         #    (post_dry_run, post_status, post_apology)
│   └── welcome_pr.py         # ✨ V2 — Idempotent .repomind.yml welcome PR creator
│
├── code_quality/                    # 🧹 Code Quality Gate — Pre-PR Validation
│   ├── __init__.py           #    Package init
│   └── code_checker.py       #    Syntax + ruff + black + mypy checks
│
├── verifier/                   # ✅ Verifier + Rollback — Post-PR Validation
│   ├── __init__.py           #    Package init
│   ├── models.py             #    VerificationResult + RollbackResult dataclasses
│   ├── verifier.py           #    CI result verification on fix/* branches
│   └── rollback.py           #    Revert PR creator + anti-flapping + rate limiting
│
├── observability/                   # 📊 Observability + Kill Switch
│   ├── __init__.py           #    Package init
│   ├── metrics.py            #    Prometheus metrics (pipeline + 6 NEW LLM metrics)
│   └── killswitch.py         #    SSM-backed kill switch + cache + decorator
│
├── review/                   # 👤 Human Review Handler ✨ V2 — NEW MODULE
│   ├── __init__.py           #    Package init
│   ├── models.py             #    ReviewMessage Pydantic + to_human_approval()
│   └── review_handler.py     #    ReviewHandler.handle() + PR↔event mapping
│                             #     (store_pr_event_mapping, lookup_event_id_for_pr)
│                             #     + handle_review_message() SQS entry point
│
├── monitoring/               # 🖥️ Monitoring Infrastructure (Docker Compose)
│   ├── _build_dashboard.py   #    Aceternity SaaS-style dashboard generator
│   ├── dashboard-preview.html #   Generated monitoring dashboard (Chart.js)
│   ├── docker-compose.yml    #    Pushgateway + Prometheus + Grafana stack
│   ├── prometheus.yml        #    Prometheus scrape config
│   └── provisioning/
│       ├── dashboards/
│       │   ├── dashboard.yml #    Grafana dashboard provisioning
│       │   └── json/
│       │       └── repomind-dashboard.json # Pre-built Grafana dashboard
│       └── datasources/
│           └── datasource.yml #   Grafana auto-provisioned Prometheus source
│
├── policy/                   # 📜 Policy Configuration
│   └── default.yaml          #    Default safety rules (deny-by-default)
│
└── tests/                    # 🧪 Test Suite
    ├── __init__.py           #    Package init
    ├── test_signature.py     #    Webhook HMAC validation tests (6 tests)
    ├── test_event_id.py      #    Event ID generation tests (7 tests)
    ├── test_sanitizer.py     #    Log sanitization tests (8 tests)
    ├── test_excerpt.py       #    Excerpt generation tests (7 tests)
    ├── test_triage.py        #    Triage classification tests (8 tests)
    ├── test_policy.py        #    Policy evaluation tests (8 tests)
    ├── test_webhook.py       #    Webhook handler tests (3 tests)
    ├── test_rag.py         #    Vector DB tests (6 tests, mocked)
    ├── test_rag_metrics.py   #    RAG evaluation metrics tests (21 tests)
    ├── test_code_quality.py         #    Code quality gate tests (12 tests)
    ├── test_verifier.py        #    Verifier + rollback tests (15 tests)
    ├── test_observability.py        #    Observability + kill switch tests (14 tests)
    ├── test_graph.py         #    LangGraph 6-agent pipeline + retry routing (5 tests)
    ├── test_deep_solver.py   #    Hybrid solver helpers + fallback chain (11 tests)
    ├── test_llm_observability.py # Tracing + cost + LLM-as-judge (14 tests)
    ├── test_repomind_config.py   # ✨ V2 — .repomind.yml parser + safe defaults (~10 tests)
    ├── test_policy_user_config.py # ✨ V2 — User-config policy pre-filter (~8 tests)
    ├── test_pr_creator_modes.py   # ✨ V2 — auto_fix/dry_run/disabled dispatch (~10 tests)
    ├── test_comment_poster.py     # ✨ V2 — PR/commit comment targeting (~10 tests)
    ├── test_welcome_pr.py         # ✨ V2 — Idempotent welcome PR creator (~8 tests)
    ├── test_hitl.py               # ✨ V2 — 4 HITL nodes + router (~14 tests)
    └── test_review.py             # ✨ V2 — PR↔event mapping + ReviewHandler (~10 tests)
```

---

## Module Summary

| Module | Files | Purpose |
|--------|-------|---------|
| `shared/` | 10 | Cross-cutting: config, logging, auth, storage, notifications, Azure LLM, observability, **`.repomind.yml`** |
| `webhook/` | 5 | Webhook ingestion: validate, parse, queue (+ install/review events in V2) |
| `worker/` | 4 | Pre-pipeline orchestrator: fetch logs, sanitize, excerpt, run swarm (+ config/mode dispatch V2) |
| `rag/` | 4 | Vector DB: embed text, index events, retrieve similar failures, RAG metrics |
| `agents/` | 7 | **LangGraph 6-agent swarm** + **deep solver** + **LLM-as-Judge** + **HITL nodes** + **S3 checkpointer** |
| `triage/` | 1 | Triage agent: LLM-powered classification with RAG context |
| `planner/` | 1 | Planner agent: LLM-powered plan generation with RAG context |
| `policy_engine/` | 1 | Policy: rule-based safety evaluation + `.repomind.yml` pre-filter |
| `pr_creator/` | 3 | PR Creator + comment poster + welcome PR creator (mode-aware) |
| `code_quality/` | 1 | Code Quality Gate: syntax + lint + format + type checks |
| `verifier/` | 3 | Verifier: CI result checking + revert PR rollback |
| `observability/` | 2 | Observability: Prometheus metrics + SSM kill switch |
| `review/` | 3 | **Human review handler** + PR↔event mapping (V2) |
| `frontend/` | 4 | Next.js 14 live dashboard (stats + RAG + LLM cost + Judge cards) |
| `monitoring/` | 6 | Docker Compose: Pushgateway + Prometheus + Grafana + dashboards |
| `policy/` | 1 | YAML policy configuration |
| `tests/` | 23 | Unit + integration tests (16 prior + 7 new in V2) |
| **Total** | **79+** | **6-agent CI auto-fix swarm with HITL + self-serve config** |

---

## File Size Estimates

| Category | Files | Approx. Lines |
|----------|-------|---------------|
| Source Code (Python) | 50 | ~8,400 |
| Source Code (TypeScript) | 4 | ~600 |
| Tests | 23 | ~4,500 |
| Configuration | 12 | ~600 |
| Documentation | 20 | ~8,200 |
| **Total** | **109+** | **~22,300** |

---

## New Files in v2.0.0

| File | Purpose | Lines |
|------|---------|-------|
| `shared/repomind_config.py` | `.repomind.yml` parser + safe defaults + sample generator | ~210 |
| `agents/checkpointer.py` | `S3CheckpointSaver(BaseCheckpointSaver)` + factory | ~190 |
| `agents/hitl_nodes.py` | `pr_creator`, `merge_decision`, `merge`, `cleanup` + router | ~260 |
| `pr_creator/comment_poster.py` | Smart PR/commit comment posting (dry-run/status/apology) | ~180 |
| `pr_creator/welcome_pr.py` | Idempotent welcome PR creator (writes `.repomind.yml`) | ~170 |
| `review/__init__.py` | Module init | ~5 |
| `review/models.py` | `ReviewMessage` Pydantic + `to_human_approval()` | ~80 |
| `review/review_handler.py` | `ReviewHandler` + PR↔event mapping + SQS entry | ~220 |
| `tests/test_repomind_config.py` | YAML parser tests | ~150 |
| `tests/test_policy_user_config.py` | User-config policy pre-filter tests | ~140 |
| `tests/test_pr_creator_modes.py` | Mode dispatch tests | ~160 |
| `tests/test_comment_poster.py` | Comment poster tests | ~150 |
| `tests/test_welcome_pr.py` | Welcome PR idempotency tests | ~130 |
| `tests/test_hitl.py` | HITL nodes + router tests | ~220 |
| `tests/test_review.py` | PR↔event mapping + review handler tests | ~180 |
| `projectdocs/TESTING_GUIDE.md` | E2E + unit test command reference | ~400 |

## New Files in v1.3.0

| File | Purpose | Lines |
|------|---------|-------|
| `shared/azure_llm.py` | LLM factory (Azure primary, Groq fallback) | ~80 |
| `shared/llm_observability.py` | `traced_completion` + cost engine + `summarize_traces` | ~340 |
| `agents/deep_solver.py` | Anthropic-style deep agent (Tier 1) with tools + sub-agents | ~380 |
| `agents/llm_judge.py` | LLM-as-Judge (factuality + completeness + calibration + hallucination) | ~240 |
| `frontend/package.json` | Next.js + React + TypeScript dependencies | ~30 |
| `frontend/next.config.js` | Next.js configuration | ~10 |
| `frontend/app/layout.tsx` | Root layout | ~25 |
| `frontend/app/page.tsx` | Live dashboard (6 stats + 7-step pipeline + 3 quality cards) | ~470 |
| `tests/test_graph.py` | LangGraph swarm + retry routing tests | ~200 |
| `tests/test_deep_solver.py` | Hybrid solver fallback tests | ~280 |
| `tests/test_llm_observability.py` | Tracing + cost + judge tests | ~330 |
| `README.md` (root) | Top-level project README | ~250 |
