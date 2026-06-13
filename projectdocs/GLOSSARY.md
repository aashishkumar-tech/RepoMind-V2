# 📖 Glossary — RepoMind V2

## Core Concepts

| Term | Definition |
|------|-----------|
| **CI (Continuous Integration)** | Automated process of building, testing, and validating code changes on every commit/push. |
| **CD (Continuous Deployment)** | Automated process of deploying validated code to production environments. |
| **CI/CD Pipeline** | The complete automated workflow from code commit to production deployment. |
| **Auto-Fix** | Automatically generating and applying code fixes for CI failures without human intervention. |
| **Pipeline** | The sequential processing chain in RepoMind: webhook → worker → triage → plan → policy → PR. |

---

## RepoMind-Specific Terms

| Term | Definition |
|------|-----------|
| **Event** | A single CI failure occurrence, identified by a unique `event_id`. |
| **Event ID** | Unique identifier: `evt-<repo-slug>-<run-id>-<timestamp>`. Example: `evt-myorg-service-a-123456789-20260213T154400Z` |
| **Repo Slug** | Normalized repository identifier: `owner/repo` → `owner-repo` (lowercase, no special chars). |
| **Excerpt** | Condensed version of CI logs containing only error-relevant lines (typically 50–200 lines). |
| **Triage** | The process of classifying a CI failure into a known category (e.g., dependency_error, import_error). |
| **Playbook** | A predefined set of fix actions for a specific failure type (YAML format). |
| **Plan** | The generated fix strategy including playbook ID, actions, code changes, and risk level. |
| **Policy** | Safety rules that determine whether an auto-fix is allowed (YAML-based, deny-by-default). |
| **Artifacts** | Structured JSON data recording the outcome of each pipeline step (stored in S3). |
| **Timeline** | Chronological log of all pipeline steps with timestamps and durations (stored in S3). |
| **Verification** | The process of checking the CI re-run result on a fix/* branch to confirm the auto-fix worked. |
| **Rollback** | Automatically reverting a failed fix by creating a revert PR that restores the original code. |
| **Anti-Flapping** | Safety mechanism preventing the same event from being rolled back multiple times (S3 marker check). |
| **Rate Limiting** | Restricting the number of rollbacks per hour to prevent cascading automated reverts. |
| **Kill Switch** | Emergency mechanism to instantly halt all auto-fix operations, stored in AWS SSM Parameter Store. |
| **Pushgateway** | Prometheus component that accepts metrics pushed from short-lived jobs (Lambda functions). |

---

## V2 — HITL & Self-Serve Terms

| Term | Definition |
|------|-----------|
| **HITL (Human-in-the-Loop)** | Pattern where the automated pipeline **pauses** at a critical step (here: merge) until a human supplies an explicit approval signal. RepoMind uses LangGraph's `interrupt_before` for the pause. |
| **`.repomind.yml`** | Per-repo configuration file at the repo root. Owns `mode`, `hitl_required`, `allowed_failure_types`, `max_risk_level`, `protected_paths`, `pr_labels`, `reviewers`. Created by the welcome PR; respected on every event. |
| **Operating Mode** | One of `disabled` / `dry_run` / `auto_fix`. Set in `.repomind.yml::mode`. Controls whether RepoMind opens PRs, posts comments, or merges. |
| **Dry-Run Mode** | RepoMind analyses the failure and **posts a comment** with what it would do, but never opens a PR or merges. The default mode for newly-installed repos. |
| **Auto-Fix Mode** | RepoMind opens a PR, awaits human approval (if `hitl_required: true`), and merges on `APPROVED`. |
| **Welcome PR** | The first PR RepoMind opens on a freshly-installed repo. Contains a default `.repomind.yml` and a short README. Idempotent — skipped if the file or `repomind/welcome` branch already exists. |
| **Interrupt** | LangGraph mechanism (`interrupt_before=["merge_decision"]`) that pauses graph execution and persists state to the checkpointer until externally resumed. |
| **S3 Checkpointer** | `agents/checkpointer.py::S3CheckpointSaver(BaseCheckpointSaver)` — persists paused LangGraph state to `s3://repomind-data/checkpoints/<event_id>/`. Required because Lambda's 15-min timeout can't hold state during multi-day human reviews. |
| **Resume** | The act of feeding a `human_approval` verdict into a paused graph (`graph.update_state(...)` + `graph.invoke(None)`) so it continues at `merge_decision`. |
| **Merge Decision Node** | The HITL gate node (`agents/hitl_nodes.py::merge_decision_node`). Reads `state["human_approval"]` and routes to `merge` / `cleanup` / `END`. |
| **Merge Node** | Performs the squash-merge of the approved RepoMind PR. |
| **Cleanup Node** | Closes the rejected PR, posts an apology comment, and feeds the rejection signal back to the learning loop. |
| **Step 12** | The new module `review/` that owns the review-resumed entry point. Maps incoming `pull_request_review` events to the right paused graph via the PR↔event index. |
| **PR↔Event Mapping** | S3 index at `indexes/by-pr/<owner>-<repo>/<pr_number>.json` storing `{ "event_id": "evt-..." }`. Lets Step 12 find the right `thread_id` from only a PR number. |
| **Human Approval** | One of `approved` / `rejected` / `skipped` / `timeout`. Carried in `state["human_approval"]` after Step 12 resumes the graph. |
| **Review Verdict** | The mapped form of a GitHub review state (`APPROVED` → `approved`, `CHANGES_REQUESTED` → `rejected`, `COMMENTED` → `skipped`). |
| **Comment Poster** | `pr_creator/comment_poster.py::CommentPoster` — posts dry-run / status / apology comments on PR (if the commit belongs to one) or on the commit itself. |
| **Welcome PR Creator** | `pr_creator/welcome_pr.py::WelcomePRCreator` — creates the welcome PR; idempotent. |
| **Safe Default Config** | `SAFE_DEFAULT_CONFIG` in `shared/repomind_config.py` — used when a repo has no `.repomind.yml`. Sets `mode=dry_run`, `hitl_required=True`, `max_risk_level=low`. |
| **User-Config Pre-Filter** | The `_evaluate_user_config()` method in `policy_engine/policy.py` that applies `.repomind.yml` rules **before** operator policy. Acts as a stricter filter — can deny but never relax operator rules. |

---

## Architecture Terms

| Term | Definition |
|------|-----------|
| **uv** | Ultra-fast Python package and project manager written in Rust by Astral. Replaces pip, pip-tools, virtualenv in a single tool. 10–100x faster than pip. |
| **Webhook** | HTTP callback — GitHub sends a POST request to our endpoint when a CI event occurs. |
| **HMAC-SHA256** | Hash-based Message Authentication Code using SHA-256. Used to verify webhook authenticity. |
| **SQS (Simple Queue Service)** | AWS managed message queue. Decouples webhook reception from processing. |
| **DLQ (Dead Letter Queue)** | Secondary queue for messages that fail processing after max retries. |
| **Lambda** | AWS serverless compute service. Runs code without managing servers. |
| **API Gateway** | AWS service that creates, publishes, and manages REST APIs. |
| **SAM (Serverless Application Model)** | AWS framework for building serverless applications using CloudFormation templates. |
| **SSM (Systems Manager) Parameter Store** | AWS service for storing configuration data and secrets. Used for kill switch state. Free for standard parameters. |
| **Mangum** | Python adapter that translates AWS Lambda events into ASGI requests for FastAPI. |

---

## AI / ML Terms

| Term | Definition |
|------|-----------|
| **LLM (Large Language Model)** | AI model trained on text data, used for classification and code generation. RepoMind uses **Azure OpenAI GPT-4o** (primary) and **Groq Llama 3.3 70B** (fallback). |
| **Azure OpenAI** | Microsoft-hosted OpenAI service. Provides enterprise-grade GPT-4o with regional deployment, RBAC, and SLA. RepoMind's primary LLM provider. |
| **Groq** | Free-tier AI inference platform with ultra-fast Llama models. RepoMind's fallback LLM provider (used when Azure creds are absent). |
| **GPT-4o** | OpenAI's flagship multi-modal model, used in RepoMind via Azure OpenAI for triage, planning, solving, validation, and judging. |
| **deepagents** | Anthropic-style Python harness for building deep agents with tool use and sub-agents. Used for RepoMind's Tier 1 Solver. |
| **Deep Agent** | An LLM agent that plans multi-step tool calls (read files, search code) and dispatches sub-agents to inspect a repository before producing output. RepoMind's Tier 1 Solver is a deep agent with 3 read-only tools and 2 sub-agents. |
| **Sub-Agent** | A specialized child agent invoked by a deep agent for a focused task. RepoMind uses `code-reader` (file inspection) and `diff-writer` (diff generation) sub-agents. |
| **Tool Budget** | Cap on the number / size of tool calls a deep agent may make. RepoMind's Solver caps at 8 file reads, 50 KB per file, and 45 s wall-clock. |
| **Tier 1 / Tier 2 (Hybrid Solver)** | Solver fallback strategy: Tier 1 = deep agent with tools (high quality but can fail); Tier 2 = direct LLM call (lower quality but always works). RepoMind tries Tier 1 first, falls back to Tier 2 on timeout / error / empty output. |
| **LangGraph** | LangChain's library for building stateful, directed-graph agent workflows. RepoMind uses LangGraph 0.3.4 to orchestrate its 6-agent swarm with conditional retry edges. |
| **Agent Swarm** | A coordinated group of specialized LLM agents working together on a single task. RepoMind's swarm has 6 agents: evidence, triage, planner, solver, validator, policy. |
| **Conditional Edge** | A LangGraph edge whose target depends on the current state. RepoMind uses one for the validator → solver retry loop. |
| **Retry Edge** | The conditional edge from validator back to solver when validation rejects the solver's output. Capped at 2 retries. |
| **LLM-as-Judge** | An independent LLM call that grades another LLM's output on factuality, completeness, and calibration. RepoMind's judge runs after the swarm and produces an A–F grade plus a hallucination flag. |
| **Hallucination Flag** | Boolean set by the LLM-as-Judge when the triage LLM invents files, packages, or errors that don't appear in the actual log excerpt. |
| **Factuality Score** | Judge sub-score (0–1) measuring whether the triage's claims are grounded in the provided evidence. |
| **Completeness Score** | Judge sub-score (0–1) measuring whether the triage covered all relevant errors in the log. |
| **Confidence Calibration** | Judge sub-score (0–1) measuring whether the triage's self-reported confidence aligns with its actual quality. |
| **Trace (LLM Trace)** | A single record capturing one LLM call's metadata: agent, model, prompt_tokens, completion_tokens, latency_ms, cost_usd, success, error_type, response_id, prompt_hash. Stored in `state["llm_traces"]`. |
| **traced_completion** | Drop-in replacement for `client.chat.completions.create()` that automatically records a trace. Defined in `shared/llm_observability.py`. |
| **LLM Summary** | Per-event aggregate of all traces: total tokens, total cost, success rate, per-agent breakdown. Computed by `summarize_traces()` and stored in `state["llm_summary"]`. |
| **Prompt Hash** | First 12 characters of SHA-256(prompt) — used for cache analysis without storing raw prompts. |
| **RAG (Retrieval-Augmented Generation)** | Technique combining vector search with LLM generation. RepoMind retrieves the top-3 similar past failures from Qdrant and injects them into the triage / planner prompts. |
| **RAG Grade** | A–F letter grade computed from retrieval, context, and generation quality scores. Displayed on the dashboard. |
| **Embedding** | Dense numerical vector representation of text. RepoMind uses Azure `text-embedding-3-small` (1536-dim) primarily and `all-MiniLM-L6-v2` (384-dim) as fallback. |
| **Vector DB** | Database optimized for high-dimensional vector similarity search. RepoMind uses Qdrant. |
| **Qdrant** | Open-source vector similarity search engine used for storing and querying event embeddings. |
| **Temperature** | LLM parameter controlling randomness (0.0 = deterministic, 1.0 = creative). RepoMind: Triage=0.1, Planner=0.2, Validator=0.1, Judge=0.0, Solver=0.2. |
| **JSON Mode** | LLM output mode that guarantees valid JSON responses. Used by all of RepoMind's structured-output agents. |
| **Confidence Score** | Float 0.0–1.0 indicating how certain an agent is about its output. |

---

## Monitoring & Observability Terms

| Term | Definition |
|------|-----------|
| **Prometheus** | Open-source time-series monitoring system that scrapes and stores metrics. |
| **Grafana** | Open-source visualization platform for creating dashboards from Prometheus data. |
| **Counter** | Prometheus metric that only goes up (e.g., total events, total errors). |
| **Histogram** | Prometheus metric that samples observations into configurable buckets (e.g., latency distribution). |
| **Gauge** | Prometheus metric that can go up and down (e.g., confidence score, kill switch state). |
| **Pushgateway** | Prometheus component that accepts metrics pushed from short-lived jobs like Lambda functions. |
| **Fail-Safe** | Design principle where system failure defaults to the safest state (kill switch ON = halt). |
| **TTL (Time-to-Live)** | Duration a cached value remains valid before re-fetching (kill switch uses 30s TTL). |

---

## Pipeline Terms

| Term | Definition |
|------|-----------|
| **Step 1 (Webhook Handler)** | Receives GitHub webhook, validates signature, queues event to SQS. |
| **Step 2 (Worker)** | Pre-pipeline orchestrator — fetches logs, sanitizes, generates excerpt, then delegates to step4 graph. |
| **Step 3 (Vector DB)** | Embeds event data and stores/retrieves from Qdrant for RAG; computes RAG quality grade. |
| **Step 4 (LangGraph 6-Agent Swarm)** | The agent swarm: evidence → triage → planner → solver → validator → policy with retry edge. |
| **Step 4 — Evidence Agent** | First node in the graph; assembles excerpt + similar incidents into the initial prompt context. |
| **Step 4 — Solver Agent** | Hybrid Tier 1 (deep agent) + Tier 2 (direct LLM) agent that generates concrete code changes. |
| **Step 4 — Validator Agent** | Peer-reviewer agent that approves or rejects the solver's output. Rejection triggers a retry. |
| **Step 4 — LLM-as-Judge** | Post-graph independent quality auditor that grades triage on factuality / completeness / calibration. |
| **Step 5 (Triage)** | Classifies the CI failure type using LLM (with RAG context from past failures) or keyword fallback. |
| **Step 6 (Planner)** | Generates a fix plan with actions and files-to-modify, using RAG context from past fixes. |
| **Step 7 (Policy)** | Evaluates safety rules to approve or deny the auto-fix. |
| **Step 8 (PR Creator)** | Creates a GitHub branch and pull request with the fix. Skips PR creation if no concrete code changes. |
| **Step 9 (Code Quality Gate)** | Validates LLM-generated code with syntax, ruff, black, and mypy checks before PR creation. |
| **Step 10 (Verifier + Rollback)** | Verifies CI result on fix/* branches; automatically reverts failed fixes with anti-flapping and rate limiting. |
| **Step 11 (Observability + Kill Switch)** | Prometheus metrics collection via Pushgateway, plus SSM-backed emergency kill switch with fail-safe behavior. |

---

## Failure Types

| Type | Description | Example Error |
|------|-------------|---------------|
| `dependency_error` | Missing or incompatible package | `Cannot find module 'lodash'` |
| `import_error` | Module import failure | `ModuleNotFoundError: No module named 'foo'` |
| `syntax_error` | Code syntax issues | `SyntaxError: invalid syntax` |
| `test_failure` | Test assertions failing | `FAILED tests/test_foo.py::test_bar` |
| `type_error` | Type mismatch | `TypeError: expected str, got int` |
| `configuration_error` | Config file issues | `Config file not found` |
| `build_error` | Build process failure | `Build failed with exit code 1` |
| `lint_error` | Linting violations | `Linting errors found` |
| `runtime_error` | Runtime exceptions | `RuntimeError: out of memory` |
| `unknown` | Unclassifiable failure | Catch-all category |

---

## Policy Terms

| Term | Definition |
|------|-----------|
| **Decision** | The policy outcome: `allow` (proceed), `deny` (block), or `manual_review` (human needed). |
| **Risk Level** | `low`, `medium`, or `high` — assessed by the Planner based on fix impact. |
| **First-Match-Wins** | Policy evaluation strategy — rules checked in order, first matching rule determines the decision. |
| **Fail-Closed** | Safety principle — if the policy engine errors, the decision defaults to `deny`. |
| **Fail-Open** | Resilience principle — if the code quality checker crashes, pipeline continues without blocking PR. |
| **Blocking Check** | A code quality check (syntax, ruff) whose failure prevents PR creation. |
| **Warning Check** | A code quality check (black, mypy) whose failure is logged but does not prevent PR creation. |
| **Deny-by-Default** | Default behavior — if no rule matches, the fix is denied (safety-first approach). |

---

## Security Terms

| Term | Definition |
|------|-----------|
| **Sanitization** | Process of removing/redacting sensitive data (passwords, tokens, keys) from text. |
| **Constant-Time Comparison** | `hmac.compare_digest()` — prevents timing attacks by always comparing full strings. |
| **GitHub App** | A first-class GitHub integration with granular permissions, preferred over personal tokens. |
| **Installation Token** | Short-lived OAuth token (~1 hour) generated from GitHub App JWT. |
| **JWT (JSON Web Token)** | Compact token format used for GitHub App authentication (RS256 signed). |
| **NoEcho** | CloudFormation parameter flag that masks secret values in the console. |

---

## Acronyms

| Acronym | Full Form |
|---------|-----------|
| **API** | Application Programming Interface |
| **ASGI** | Asynchronous Server Gateway Interface |
| **AWS** | Amazon Web Services |
| **CI** | Continuous Integration |
| **CD** | Continuous Deployment |
| **CLI** | Command Line Interface |
| **DLQ** | Dead Letter Queue |
| **HLD** | High-Level Design |
| **HMAC** | Hash-based Message Authentication Code |
| **HTTP** | HyperText Transfer Protocol |
| **IaC** | Infrastructure as Code |
| **JWT** | JSON Web Token |
| **LLD** | Low-Level Design |
| **LLM** | Large Language Model |
| **PR** | Pull Request |
| **RAG** | Retrieval-Augmented Generation |
| **REST** | Representational State Transfer |
| **S3** | Simple Storage Service |
| **SAM** | Serverless Application Model |
| **SDK** | Software Development Kit |
| **SMTP** | Simple Mail Transfer Protocol |
| **SQS** | Simple Queue Service |
| **SSM** | Systems Manager (AWS) |
| **TLS** | Transport Layer Security |
| **TTL** | Time-to-Live |
| **VPC** | Virtual Private Cloud |
