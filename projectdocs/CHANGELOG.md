# 📋 Changelog — RepoMind V2

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [2.0.0] — 2026-06-09 — Self-Serve + Human-in-the-Loop Release

### 🆕 Self-Serve `.repomind.yml` + Welcome PR + HITL Merge Approval

This release dramatically reduces onboarding friction (no more operator handholding for policy changes) and adds a true human-in-the-loop pattern so **RepoMind never merges code without explicit human approval**.

#### Added — Self-Serve Repo Configuration

- **`shared/repomind_config.py`** — New module. Parses `.repomind.yml` from the user's repo via GitHub Contents API.
  - `RepoMindConfig` dataclass with `mode`, `hitl_required`, `allowed_failure_types`, `max_risk_level`, `min_confidence`, `slack_webhook`, `email`.
  - `load_repomind_config(repo, ref)` — pulls the YAML, parses, validates, returns safe defaults on any failure.
  - `parse_yaml_text()` + `parse_config()` for offline parsing/testing.
  - `generate_sample_yml()` returns a documented template for the welcome PR.
  - **Safe defaults when YAML missing**: `mode: dry_run`, `hitl_required: true`.

- **`step7/policy.py`** — User config now acts as a **pre-filter** (stricter than operator rules).
  - `evaluate(triage, plan, repo, repomind_config=None)` checks the user's allowlist + thresholds first.
  - Rules `user_config_failure_type`, `user_config_min_confidence`, `user_config_max_risk` can short-circuit to deny.
  - Operator defaults still apply when user config is absent or permissive.

#### Added — Three Operating Modes

- **`step8/pr_creator.py`** — `create_pr()` now takes a `mode` parameter:
  - `mode="disabled"` → returns `{status: "skipped", reason: "mode=disabled"}`. No GitHub calls.
  - `mode="dry_run"`  → delegates to `CommentPoster.post_dry_run()`. No branch, no PR.
  - `mode="auto_fix"` → existing flow (open PR with proposed fix).

- **`step8/comment_poster.py`** — New module. Three comment types:
  - `post_dry_run()` — markdown preview with confidence, failure type, code-change diffs, "what RepoMind WOULD do".
  - `post_status()` — always-on status: "I saw your CI failure → triaged as X → action taken: Y". Eliminates the silent-agent problem.
  - `post_apology()` — graceful "your reviewer rejected my fix" message.
  - Smart targeting: if the failed commit belongs to a PR, comment on the PR; else on the commit.

#### Added — Welcome PR on Install

- **`step8/welcome_pr.py`** — New module. `WelcomePRCreator.create_welcome_pr(repo)`:
  - Idempotent: skips if `.repomind.yml` or `repomind/welcome` branch exists.
  - Adds two files: `.repomind.yml` (safe defaults) + `.repomind/README.md` (mode explainer).
  - Opens PR titled "🤖 Welcome to RepoMind — review the config to opt in".

- **`step1/webhook_handler.py`** — New endpoint handlers:
  - `_handle_installation()` — queues a welcome PR for each newly-installed repo.
  - `_handle_installation_repositories()` — same for repos added to existing installation.

#### Added — Human-in-the-Loop (HITL) Middleware

- **`step4/checkpointer.py`** — New module. `S3CheckpointSaver(BaseCheckpointSaver)`:
  - Persists LangGraph state to S3 (or LocalStorage in dev).
  - Layout: `checkpoints/<thread_id>/<checkpoint_id>.json` + `checkpoints/<thread_id>/latest.txt`.
  - Required because Lambda's 15-min timeout cannot hold state during hours-long human review.
  - `get_checkpointer()` factory: MemorySaver in dev-with-no-S3, S3CheckpointSaver in prod, None if LangGraph absent.

- **`step4/hitl_nodes.py`** — Four new graph nodes:
  - `pr_creator_node` — opens PR / dry-run comment / skip based on mode + policy.
  - `merge_decision_node` — reads `state["human_approval"]`, routes to merge/cleanup/end.
  - `merge_node` — calls `pr.merge(merge_method="squash")` only when approved.
  - `cleanup_node` — closes PR, deletes fix branch, posts apology when rejected.
  - `route_after_merge_decision()` — conditional edge selector.

- **`step4/graph.py`** — Graph extended with HITL nodes and `interrupt_before=["merge_decision"]`:
  ```
  policy → pr_creator → [INTERRUPT — graph PAUSES here]
                     → merge_decision → approved → merge → END
                                      → rejected → cleanup → END
                                      → skipped  → END
  ```
  - `get_graph(with_hitl=True)` — separate compiled singletons for HITL on/off.
  - `resume_pipeline(event_id, human_approval, review_data)` — entry point for step12 to resume a paused graph.

- **`step4/models.py`** — Added state fields: `repomind_config`, `mode`, `hitl_required`, `pr_url`, `pr_number`, `human_approval`, `review_data`, `merge_result`, `cleanup_result`.

#### Added — Step 12 (Human Review Handler)

- **`step12/`** — Entirely new module:
  - `models.py` — `ReviewMessage`, `HumanApproval` literal type.
  - `review_handler.py` — `ReviewHandler.handle(msg)`:
    - Looks up `event_id` from `repo + pr_number` via S3 index.
    - Translates GitHub review state → HITL verdict (`approved` / `rejected` / `pending`).
    - Calls `resume_pipeline(event_id, verdict, review_data)`.
    - Persists `review.json`, updates `artifacts.json`, appends timeline.
  - `store_pr_event_mapping(repo, pr_number, event_id)` — written by worker after PR creation.
  - `lookup_event_id_for_pr(repo, pr_number)` — reverse lookup used by review handler.

- **`step1/webhook_handler.py`** — New endpoint handler:
  - `_handle_pull_request_review()` — queues review messages with `message_type="review"`.

- **`step2/worker.py`** — New routing:
  - `message_type == "installation"` → `_handle_installation()` → welcome PR.
  - `message_type == "review"`       → `_handle_review()` → step12 resume.
  - Main pipeline path now loads `.repomind.yml` before invoking the graph and passes `mode + hitl_required + repomind_config` through.

#### Changed — PR Body Now Explicit About HITL

- PR body templates rewritten to clearly state:
  - ✅ Approve → may auto-merge.
  - ❌ Request changes → PR closes, apology comment.
  - 💬 Comment only → graph keeps waiting.

#### Added — Tests (~70 new tests, 7 new files)

- `tests/test_repomind_config.py` — YAML parsing, safe defaults, mode validation.
- `tests/test_policy_user_config.py` — user-config gate behaviour.
- `tests/test_pr_creator_modes.py` — mode dispatch (disabled/dry_run/auto_fix).
- `tests/test_comment_poster.py` — dry-run + status + apology comments.
- `tests/test_welcome_pr.py` — welcome PR idempotency + content.
- `tests/test_hitl.py` — all 4 HITL nodes + router + ReviewMessage.
- `tests/test_step12.py` — PR↔event mapping, review handler dispatch.

#### Added — Documentation

- `projectdocs/TESTING_GUIDE.md` — Comprehensive testing reference (unit/integration/E2E/HITL).
- `projectdocs/ONBOARDING.md` — Rewritten for V2 simpler flow (welcome PR + `.repomind.yml`).

#### Migration Notes (v1.3 → V2)

- **Breaking**: `PRCreator.create_pr()` signature added `head_sha`, `run_url`, `mode` parameters. Existing callers must pass `mode="auto_fix"` to preserve old behaviour.
- **Breaking**: Worker now loads `.repomind.yml` before invoking graph. Repos without `.repomind.yml` get **safe defaults** (`dry_run + hitl_required`), so they **will not auto-open PRs** until they explicitly opt in.
- **New env**: None required — all V2 features work with existing config.
- **S3 layout additions** (auto-created):
  - `checkpoints/<event_id>/...` — HITL state persistence.
  - `indexes/by-pr/<owner>-<repo>/<pr_number>.json` — PR ↔ event lookup.

---

## [1.3.0-alpha] — 2026-06-09 — Microsoft Build AI Hackathon Release

### 🆕 Azure OpenAI Migration + Multi-Agent Swarm + LLM Observability

#### Added — Azure OpenAI Stack

- **`shared/azure_llm.py`** — New LLM factory: Azure OpenAI primary, Groq fallback
  - `get_llm_client()` returns `AzureOpenAI(...)` if `AZURE_OPENAI_ENDPOINT` + `AZURE_OPENAI_API_KEY` are set
  - Falls back to `Groq(...)` for backwards compatibility
  - `get_model_name()` returns `AZURE_OPENAI_DEPLOYMENT_NAME` or `"llama-3.3-70b-versatile"`
- **`shared/config.py`** — 8 new Azure settings:
  - `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_API_VERSION`, `AZURE_OPENAI_DEPLOYMENT_NAME`
  - `AZURE_STORAGE_CONNECTION_STRING`, `AZURE_STORAGE_CONTAINER`
  - `AZURE_SERVICE_BUS_CONNECTION_STRING`, `AZURE_SERVICE_BUS_QUEUE`
- **`requirements.txt`** — Added `openai==1.82.0`, `azure-identity==1.19.0`; uncommented `langgraph==0.3.4`

#### Added — LangGraph 6-Agent Swarm

- **`step4/nodes.py`** — Two new agents:
  - `solver_node` — Chain-of-thought code generator (uses Azure GPT-4o)
  - `validator_node` — Peer reviewer with feedback loop and retry routing
- **`step4/graph.py`** — Rewired graph as 6-node swarm with conditional retry edge:
  ```
  evidence → triage → planner → solver → validator → policy
                                    ↑___________│ (max 2 retries on rejection)
  ```
- **`step4/models.py`** — Added `validation`, `validation_attempts`, `solver_feedback`, `llm_traces`, `llm_summary`, `judge`

#### Added — Hybrid Deep Agent Solver (Tier 1 + Tier 2)

- **`step4/deep_solver.py`** — New file using `deepagents==0.6.8` harness:
  - 3 read-only tools: `read_repo_file`, `list_repo_directory`, `search_repo_code`
  - 2 sub-agents: `code-reader` and `diff-writer`
  - Tool budget (8 reads max, 50 KB per file), 45 s timeout
  - Returns structured JSON: `{reasoning, code_changes, confidence, risk_assessment, files_inspected}`
- **`step4/nodes.py`** — `solver_node()` rewritten as hybrid:
  - **Tier 1**: Try `run_deep_solver()` — reads actual repo files via tools
  - **Tier 2**: Fall back to direct Azure GPT-4o call on timeout/error/empty output
  - Tags every result with `solver_mode = "deep_agent" | "direct_llm"`
- **`requirements.txt`** — Added `deepagents==0.6.8`, `langchain-openai==0.2.14`

#### Added — RAG-Augmented Prompts

- **`step5/triage.py`** — `classify()` now takes `similar_incidents` and injects top-3 past failures into the prompt as `{rag_context}`
- **`step6/planner.py`** — `generate_plan()` now takes `similar_incidents` and injects top-2 past fixes into the prompt; `max_tokens` raised to 1500
- **`step4/nodes.py`** — `triage_node` and `planner_node` pass `state["similar_incidents"]` to the engines

#### Added — LLM Observability (Tier 2)

- **`shared/llm_observability.py`** — Full LLM tracing layer (~340 lines):
  - `traced_completion(client, model=, messages=, agent=, ...)` — drop-in replacement for `chat.completions.create`
  - Captures `prompt_tokens`, `completion_tokens`, `total_tokens`, `latency_ms`, `cost_usd`, `success`, `error_type`, `response_id`, `prompt_hash`
  - `estimate_cost_usd(model, prompt_tokens, completion_tokens)` — Azure pricing table (June 2026)
  - `summarize_traces(traces)` — per-event totals + per-agent breakdown
  - `attach_trace(state, trace)` — append to `state["llm_traces"]`
- **All 4 LLM call sites updated** (`step5/triage.py`, `step6/planner.py`, `step4/nodes.py × 2`) to use `traced_completion`
- **`step4/graph.py`** — `_collect_llm_traces()` aggregates per-agent traces into `state["llm_summary"]`

#### Added — LLM-as-Judge

- **`step4/llm_judge.py`** — Independent quality auditor:
  - Scores triage on `factuality_score`, `completeness_score`, `confidence_calibration`
  - Sets `hallucination_flag = true` if triage invents files/packages/errors not in the log
  - Returns letter grade A–F + `verdict_summary` + issue list
  - Toggle via `LLM_JUDGE_ENABLED=false` to save 1 LLM call/event
- **`shared/config.py`** — Added `LLM_JUDGE_ENABLED` setting (default `"true"`)

#### Added — Prometheus Metrics (Step 11)

- **`step11/metrics.py`** — 6 new metrics:
  - `repomind_llm_calls_total{agent, model, status}` (Counter)
  - `repomind_llm_tokens_total{agent, model, type}` (Counter)
  - `repomind_llm_latency_seconds{agent, model}` (Histogram)
  - `repomind_llm_cost_usd_total{agent, model, repo}` (Counter)
  - `repomind_llm_judge_score{agent, judged_agent, metric}` (Gauge)
  - `repomind_llm_hallucinations_total{judged_agent, model}` (Counter)

#### Added — Next.js Dashboard (`frontend/`)

- **`frontend/package.json`** — Next.js 14.2.3 + React 18 + TypeScript 5
- **`frontend/app/page.tsx`** — Live dashboard with 5-second polling:
  - Top stats bar with 6 cards: Total Events · PRs Created · Policy Denied · Errors · Avg RAG Grade · Total LLM Cost
  - Sidebar: recent events list with status pill + RAG grade pill
  - Detail panel: 7-step agent pipeline visualization (Evidence → Triage → Planner → Solver → Validator → Policy → PR)
  - 📊 RAG Quality card: A–F grade + retrieval/context/generation sub-scores + hit rate, MRR, similarity, latency
  - 💰 LLM Cost & Tokens card: stacked bar of tokens-per-agent + per-agent cost breakdown
  - 🛡️ LLM-as-Judge card: factuality + completeness + calibration sub-scores + hallucination warning + verdict
  - Mock fallback when `/api/events` is unavailable
- **`frontend/app/layout.tsx`**, **`frontend/next.config.js`** — Next.js scaffolding

#### Added — Tests

- **`tests/test_graph.py`** — 5 tests: full pipeline, solver, validator, retry routing
- **`tests/test_deep_solver.py`** — 11 tests: helpers + hybrid fallback chain
- **`tests/test_llm_observability.py`** — 14 tests: cost estimation, prompt hashing, traced_completion (success + error), summarize_traces, LLM-as-judge

#### Changed

- **`step2/worker.py`** — Replaced inline Steps 5/6/7 calls with a single `run_pipeline()` call from `step4/graph.py` (LangGraph is now the actual runtime)
- **`step8/pr_creator.py`** — Removed hollow placeholder PR logic. When the solver produces no concrete code changes, the PR creator now logs `pr_skipped_no_code_changes` and returns `{"status": "skipped"}` instead of opening an empty PR.
- **`README.md`** (root) — New top-level README with architecture diagram, hybrid solver flow, Quick Start, and Deploy commands.

#### Backwards Compatibility

- All changes are **backwards compatible**.
- Azure credentials are **optional** — system falls back to Groq automatically if `AZURE_OPENAI_*` env vars are not set.
- LLM-as-judge can be disabled via `LLM_JUDGE_ENABLED=false`.
- Deep agent falls back to direct LLM if `deepagents` is not installed.

---

## [1.2.0-alpha] — 2026-02-26

### 🆕 RAG Evaluation Metrics

#### Added

**Step 3 — RAG Metrics (`step3/rag_metrics.py`)**
- `RAGEvaluator` class — Comprehensive RAG pipeline quality evaluation
  - **Retrieval metrics:** hit rate, mean/max/min similarity, MRR, recall@K, staleness ratio, score distribution
  - **Context quality metrics:** relevance, diversity, freshness, failure type match rate, duplicate detection
  - **Generation impact metrics:** confidence delta, type alignment, grounding score, RAG value score
  - **Grading system:** Composite score → letter grade (A–F) with breakdown
- `evaluate_rag()` — Convenience function for one-shot evaluation

**Monitoring Dashboard (`monitoring/`)**
- `_build_dashboard.py` — Aceternity SaaS-style dashboard generator (Chart.js 4.4.4)
  - Pure black background, blue-cyan gradients, glassmorphism cards
  - Frosted navbar, animated hero section, full footer
  - Muted chart palette (#4a6fa5, #3d8b9e, #4a9e7a, #c9a84c, #b85c5c)
  - 6 consolidated sections: Pipeline, Quality, Triage, Policy, Timing, System
  - ES5-only JavaScript (no const/let/arrow functions)
- `dashboard-preview.html` — Generated output (35,786 bytes, 15/15 sanity checks pass)

**Tests**
- `tests/test_rag_metrics.py` — 21 tests: retrieval, context, generation, grading, edge cases

---

### 🆕 Step 10 — Verifier + Rollback

#### Added

**Step 10 — Fix Verification (`step10/`)**
- `verifier.py` — Verifies whether fix branch CI passed or failed after merge
  - Checks workflow_run conclusion via GitHub API
  - Only processes fix/* branches (ignores everything else)
  - Triggers rollback on CI failure via RollbackClient
  - Records verification metrics to Prometheus
- `rollback.py` — Creates revert PRs for failed auto-fixes
  - Anti-flapping: max 1 rollback per event (checked via S3 marker)
  - Rate limiting: max 3 rollbacks per repo per hour (configurable)
  - Creates descriptive revert PR with full context
  - Comments on original fix PR with rollback notification
  - Sends email notification on rollback
  - Full audit trail in S3
- `models.py` — `VerificationResult` and `RollbackResult` dataclasses

**Webhook Routing**
- `step1/webhook_handler.py` — Routes fix/* branch workflow_run events to Step 10
- `step1/models.py` — Added `message_type` and `conclusion` fields to SQSMessage
- `step1/models.py` — Added `is_completed_workflow()` method to GitHubWebhookPayload

**Worker Integration**
- `step2/worker.py` — Routes verification messages to `_handle_verification()`
- Stores verification results in S3 under `events/{slug}/{event_id}/verification.json`

**Tests**
- `tests/test_step10.py` — 15 tests: models, verify pass/fail, rollback, anti-flapping, rate limiting

---

### 🆕 Step 11 — Observability + Kill Switch

#### Added

**Step 11 — Prometheus Metrics (`step11/`)**
- `metrics.py` — Central Prometheus metrics registry + Pushgateway push
  - 7 Counters: events, policy decisions, quality checks, PRs, verifications, rollbacks, errors
  - 1 Histogram: pipeline step duration (with custom buckets)
  - 2 Gauges: triage confidence, kill switch state
  - Custom CollectorRegistry (avoids global state conflicts)
  - No-op fallback when prometheus_client is not installed
  - `push_metrics()` — Non-blocking push to Pushgateway (fire-and-forget)
- `killswitch.py` — Global kill switch via AWS SSM Parameter Store
  - `is_kill_switch_enabled()` — Reads SSM parameter /repomind/kill_switch
  - Fail-safe: if SSM unreachable → assume ON (halt pipeline)
  - 30-second TTL cache to avoid hammering SSM
  - Development mode bypass (always OFF in dev)
  - `@require_kill_switch_off` decorator for protecting side-effect functions
  - `clear_cache()` for test isolation

**Monitoring Infrastructure (`monitoring/`)**
- `docker-compose.yml` — Pushgateway + Prometheus + Grafana stack
- `prometheus.yml` — Prometheus config (scrapes Pushgateway every 15s)
- `provisioning/datasources/datasource.yml` — Auto-provisions Prometheus in Grafana

**Worker Integration**
- Kill switch check at pipeline start (before any processing)
- Metrics recording throughout pipeline (events, errors, policy, quality, PRs)
- Metrics push at pipeline end via `_finalize()`

**Configuration**
- `shared/config.py` — Added PUSHGATEWAY_URL, METRICS_ENABLED, KILL_SWITCH_PARAM, VERIFICATION_ENABLED, MAX_ROLLBACKS_PER_HOUR
- `requirements.txt` — Added prometheus-client==0.21.1
- `template.yaml` — Added SSM read permissions, PushgatewayUrl parameter, new env vars

**Tests**
- `tests/test_step11.py` — 14 tests: metrics registry, no-op fallback, push success/failure, kill switch on/off/fail-safe/cache, decorator

---

## [1.1.0-alpha] — 2026-02-26

### 🆕 Step 9 — Code Quality Gate

#### Added

**Step 9 — Code Quality Checker (`step9/`)**
- `code_checker.py` — Validates LLM-generated code changes before PR creation
  - Syntax check via `ast.parse()` (blocking: broken code → no PR)
  - Ruff lint check (blocking: undefined names, unused imports)
  - Black format check (non-blocking: warning only)
  - Mypy type check (non-blocking: warning only)
  - Writes files to temp dir, runs tools, cleans up
  - Fail-open on checker errors (don't block PR if checker itself crashes)

**Worker Integration**
- Step 9 runs after Policy (Step 7) and before PR Creation (Step 8)
- `PipelineContext` now includes `code_quality` field
- `artifacts.json` now includes `code_quality` section with full report

**CI/CD Pipeline**
- `.github/workflows/ci.yml` — GitHub Actions workflow: lint, format, typecheck, tests
- `pyproject.toml` — Unified config for ruff, black, mypy, pytest, coverage
- `requirements-dev.txt` — Development dependencies (ruff, black, mypy, coverage)
- `Makefile` — Quick commands: `make lint`, `make format`, `make test`, `make all`

**Tests**
- `test_step9.py` — 12 tests for CodeChecker: syntax validation, empty changes, mixed files, report structure, nested paths

---

## [1.0.0-alpha] — 2026-02-25

### 🎉 Initial Release — Alpha

#### Added

**Tooling**
- Adopted **uv** as the primary Python package & project manager (replaces pip/venv)
- Virtual environment creation via `uv venv --python 3.12`
- Dependency installation via `uv pip install -r requirements.txt`

**Shared Layer (`shared/`)**
- `config.py` — Centralized settings from environment variables with singleton pattern
- `event_id.py` — Deterministic event ID generation (`evt-<slug>-<run_id>-<timestamp>`)
- `logger.py` — Structured logging via structlog (JSON in prod, colored console in dev)
- `timeline.py` — Pipeline step timing and progress tracking
- `storage.py` — S3 (production) and local filesystem (development) storage abstraction
- `github_auth.py` — GitHub App JWT authentication with installation token caching
- `notifier.py` — Email (Gmail SMTP) and GitHub PR comment notifications

**Step 1 — Webhook Handler**
- `models.py` — Pydantic models for GitHub webhook payload
- `signature.py` — HMAC-SHA256 webhook signature validation
- `sqs_client.py` — SQS publisher with local development fallback
- `webhook_handler.py` — FastAPI app with `/webhook` and `/health` endpoints
- `lambda_handler.py` — Mangum adapter for AWS Lambda deployment

**Step 2 — Worker (Core Orchestrator)**
- `log_fetcher.py` — GitHub Actions log downloader with retry
- `sanitizer.py` — 10-pattern secret redaction engine
- `excerpt.py` — Heuristic CI log excerpt generator
- `worker.py` — Full pipeline orchestrator (Steps 2→8)

**Step 3 — Vector DB**
- `embedder.py` — sentence-transformers embedding (all-MiniLM-L6-v2, 384-dim)
- `indexer.py` — Qdrant vector upsert with S3 backup
- `retriever.py` — Similarity search with filters for RAG

**Step 4 — LangGraph Orchestration**
- `models.py` — PipelineState TypedDict for graph state
- `nodes.py` — Graph nodes: evidence, triage, planner, policy
- `graph.py` — StateGraph builder with sequential fallback

**Step 5 — Triage**
- `triage.py` — Groq LLM failure classifier with keyword fallback (10 failure types)

**Step 6 — Planner**
- `planner.py` — Groq LLM fix plan generator with template fallback

**Step 7 — Policy**
- `policy.py` — Rule-based YAML policy engine (deny-by-default, first-match-wins)

**Step 8 — PR Creator**
- `pr_creator.py` — GitHub branch + PR creation with code changes

**Infrastructure**
- `template.yaml` — AWS SAM template (API Gateway, Lambda, SQS, S3)
- `policy/default.yaml` — Default safety policy (7 rules)
- `repos.yaml` — Target repository configuration
- `.env.example` — Environment variable template
- `requirements.txt` — Python dependencies
- `run_local.py` — Local development server (Uvicorn)
- `test_local_pipeline.py` — Full pipeline simulation

**Tests**
- `test_signature.py` — 6 tests for webhook HMAC validation
- `test_event_id.py` — 7 tests for event ID generation
- `test_sanitizer.py` — 8 tests for log sanitization
- `test_excerpt.py` — 7 tests for excerpt generation
- `test_triage.py` — 8 tests for failure classification
- `test_policy.py` — 8 tests for policy evaluation
- `test_webhook.py` — 3 tests for HTTP endpoints
- `test_step3.py` — 6 tests for vector DB (mocked)
- `test_step4.py` — 8 tests for LangGraph (mocked)

**Documentation**
- Complete `projectdocs/` folder with 17 documents

---

## [Unreleased] — Planned

### Planned Features

- **Production Deployment** — SAM deploy to AWS, webhook URL configuration
- **End-to-End Testing** — Full pipeline test with real CI failure
- **Grafana Dashboards** — Import provisioned dashboards from monitoring/
- **Step 11** — Observability + Kill Switch (Prometheus, Redis kill switch)
- Custom playbook YAML support
- Multi-repo policy management
- Dashboard UI for monitoring
- Slack/Teams notification integration
- Webhook replay for debugging
