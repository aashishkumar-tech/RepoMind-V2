# 📡 API Reference — RepoMind V2

## 1. REST Endpoints

### 1.1 Webhook Endpoint

```
POST /webhook
```

**Description:** Receives GitHub webhook events. Supports multiple event types (extended in V2):

| Event (`X-GitHub-Event` header) | Trigger | Handler |
|---------------------------------|---------|---------|
| `workflow_run`                  | CI run completes (failed or success) | Standard pipeline (Step 2 onwards) |
| `installation`                  | GitHub App installed/uninstalled | V2 — `_handle_installation` → enqueues welcome PR job |
| `installation_repositories`     | Repos added/removed from existing install | V2 — `_handle_installation_repositories` → welcome PR per repo |
| `pull_request_review`           | Reviewer submits a review on a RepoMind PR | V2 — `_handle_pull_request_review` → resumes paused graph via Step 12 |

**Headers:**

| Header | Required | Description |
|--------|----------|-------------|
| `Content-Type` | Yes | `application/json` |
| `X-GitHub-Event` | Yes | One of: `workflow_run`, `installation`, `installation_repositories`, `pull_request_review` |
| `X-Hub-Signature-256` | Yes | `sha256=<HMAC-SHA256 hex digest>` |
| `X-GitHub-Delivery` | No | Unique delivery ID |

**Request Body:** GitHub webhook payload (see §2.1, §2.2)

**Responses:**

| Status | Description |
|--------|-------------|
| `202 Accepted` | Event queued for processing |
| `200 OK` | Event skipped (not a failure, or non-matching event) |
| `401 Unauthorized` | Invalid webhook signature |
| `400 Bad Request` | Invalid payload |
| `500 Internal Server Error` | Server error |

**Response Body (202) — workflow_run:**
```json
{
  "status": "queued",
  "event_id": "evt-myorg-service-a-123456789-20260213T154400Z",
  "message": "Event queued for processing"
}
```

**Response Body (202) — installation (V2):**
```json
{
  "status": "queued",
  "message_type": "installation",
  "installation_id": 12345678,
  "repos_added": ["myorg/repo-a", "myorg/repo-b"]
}
```

**Response Body (202) — pull_request_review (V2):**
```json
{
  "status": "queued",
  "message_type": "review",
  "pr_number": 42,
  "review_state": "approved"
}
```

---

### 1.2 Health Endpoint

```
GET /health
```

**Description:** Health check endpoint.

**Response (200):**
```json
{
  "status": "healthy",
  "service": "repomind-webhook"
}
```

---

### 1.3 Swagger UI

```
GET /docs
```

**Description:** Interactive API documentation (FastAPI auto-generated). Only available in local development.

---

## 2. Data Schemas

### 2.1 GitHub Webhook Payload (Input)

```json
{
  "action": "completed",
  "workflow_run": {
    "id": 123456789,
    "name": "CI",
    "conclusion": "failure",
    "html_url": "https://github.com/myorg/service-a/actions/runs/123456789",
    "head_branch": "main",
    "head_sha": "abc123def456"
  },
  "repository": {
    "full_name": "myorg/service-a",
    "html_url": "https://github.com/myorg/service-a"
  }
}
```

**Filter Criteria:** Only events where `action == "completed"` AND `workflow_run.conclusion == "failure"` are processed.

---

### 2.2 SQS Message (Internal)

**Schema in `webhook/models.py::SQSMessage`** — extended in V2 to carry multiple message types.

#### 2.2.1 Workflow message (default)

```json
{
  "message_type": "workflow",
  "event_id": "evt-myorg-service-a-123456789-20260213T154400Z",
  "repo": "myorg/service-a",
  "workflow_run_id": 123456789,
  "run_url": "https://github.com/myorg/service-a/actions/runs/123456789",
  "head_branch": "main",
  "head_sha": "abc123def456",
  "timestamp": "2026-02-13T15:44:00Z"
}
```

#### 2.2.2 Installation message (V2)

```json
{
  "message_type": "installation",
  "installation_id": 12345678,
  "repos_added": ["myorg/repo-a", "myorg/repo-b"],
  "timestamp": "2026-02-13T15:44:00Z"
}
```

Routed by `worker/main.py::_handle_installation` → `pr_creator/welcome_pr.py::WelcomePRCreator.create_welcome_pr()` for each repo. Idempotent.

#### 2.2.3 Review message (V2)

```json
{
  "message_type": "review",
  "repo": "myorg/service-a",
  "pr_number": 42,
  "pr_url": "https://github.com/myorg/service-a/pull/42",
  "review_id": 999999,
  "review_state": "approved",
  "review_body": "Looks good, fix verified locally.",
  "reviewer": "octocat",
  "timestamp": "2026-02-13T15:44:00Z"
}
```

Routed by `worker/main.py::_handle_review` → `review/review_handler.py::ReviewHandler.handle()` → `agents/graph.py::resume_pipeline()`.

---

### 2.3 Artifacts JSON (S3 Output)

Stored at: `events/<repo-slug>/<event-id>/artifacts.json`

```json
{
  "event_id": "evt-myorg-service-a-123456789-20260213T154400Z",
  "repo": "myorg/service-a",
  "status": "completed",
  "triage": {
    "failure_type": "dependency_error",
    "confidence": 0.87,
    "summary": "Missing dependency 'lodash' in package.json",
    "root_cause": "The package 'lodash' is imported but not listed in dependencies",
    "affected_files": ["package.json"]
  },
  "plan_summary": {
    "playbook_id": "fix_dependency_error",
    "actions": [
      "Add lodash to package.json dependencies",
      "Run npm install"
    ],
    "files_to_modify": ["package.json"],
    "code_changes": [
      {
        "file": "package.json",
        "description": "Add missing lodash dependency",
        "diff": "..."
      }
    ],
    "risk_level": "low",
    "estimated_impact": "Adds missing dependency"
  },
  "policy": {
    "decision": "allow",
    "reason": "Low-risk dependency fix with high confidence (0.87)",
    "rules_triggered": ["allow_low_risk_dependency_fix"]
  },
  "pr": {
    "url": "https://github.com/myorg/service-a/pull/42",
    "branch": "fix/dependency_error-154400Z",
    "commit_sha": "abc123",
    "title": "fix: resolve dependency_error (auto-fix)",
    "status": "created"
  },
  "indexing": {
    "status": "completed",
    "vectors_stored": 3,
    "collection": "repomind_events"
  }
}
```

---

### 2.4 Timeline JSON (S3 Output)

Stored at: `events/<repo-slug>/<event-id>/timeline.json`

```json
{
  "event_id": "evt-myorg-service-a-123456789-20260213T154400Z",
  "entries": [
    {
      "step": 2,
      "type": "logs_fetched",
      "timestamp": "2026-02-13T15:44:05Z",
      "duration_ms": 2345,
      "data": {"log_size_bytes": 45672}
    },
    {
      "step": 2,
      "type": "logs_sanitized",
      "timestamp": "2026-02-13T15:44:06Z",
      "duration_ms": 120,
      "data": {"patterns_matched": 3}
    },
    {
      "step": 2,
      "type": "excerpt_generated",
      "timestamp": "2026-02-13T15:44:06Z",
      "duration_ms": 80,
      "data": {"excerpt_lines": 45}
    },
    {
      "step": 5,
      "type": "triage_completed",
      "timestamp": "2026-02-13T15:44:08Z",
      "duration_ms": 1800,
      "data": {"triage_summary": "dependency_error (0.87 confidence)"}
    },
    {
      "step": 6,
      "type": "plan_generated",
      "timestamp": "2026-02-13T15:44:10Z",
      "duration_ms": 2100,
      "data": {"plan_summary": "Apply fix_dependency_error playbook"}
    },
    {
      "step": 7,
      "type": "policy_evaluated",
      "timestamp": "2026-02-13T15:44:10Z",
      "duration_ms": 15,
      "data": {"policy_summary": "Allowed (low-risk dependency fix)"}
    },
    {
      "step": 8,
      "type": "pr_created",
      "timestamp": "2026-02-13T15:44:15Z",
      "duration_ms": 4500,
      "data": {"pr_url": "https://github.com/myorg/service-a/pull/42"}
    }
  ]
}
```

---

### 2.5 Embedding JSON (S3 Backup)

Stored at: `embeddings/<repo-slug>/<event-id>/<type>_embedding.json`

```json
{
  "event_id": "evt-myorg-service-a-123456789-20260213T154400Z",
  "embedding_type": "excerpt",
  "model": "text-embedding-3-small",
  "dimensions": 1536,
  "vector": [0.023, -0.156, 0.089, ...],
  "text_preview": "First 500 characters of the embedded text...",
  "timestamp": "2026-02-13T15:44:12Z"
}
```

Types: `excerpt_embedding`, `triage_embedding`, `plan_embedding`, `verification_embedding`  
Models: `text-embedding-3-small` (Azure, 1536-dim) primary; `all-MiniLM-L6-v2` (384-dim) fallback.

---

### 2.6 Pipeline State (LangGraph)

The `PipelineState` `TypedDict` (defined in `agents/models.py`) is the shared blackboard
passed between every agent in the graph. After the graph completes, this is what gets
serialized into `artifacts.json`.

```python
class PipelineState(TypedDict, total=False):
    # Input
    event: dict                 # GitHub event metadata
    excerpt: str                # Sanitized log excerpt
    similar_incidents: list     # Top-K RAG retrieval results

    # Agent outputs
    triage: dict                # Step 5 output: {failure_type, confidence, root_cause, summary, _llm_trace}
    plan: dict                  # Step 6 output: {playbook_id, actions, files_to_modify, summary, _llm_trace}
    solver_result: dict         # Step 4 output: {code_changes, reasoning, confidence, solver_mode, _llm_trace}
    validation: dict            # Step 4 output: {status, confidence, issues, suggestions, _llm_trace}
    validation_attempts: int    # Retry counter (max 2)
    solver_feedback: str        # Validator's feedback for next solver attempt
    policy: dict                # Step 7 output: {decision, rule_id, reason}

    # Quality + observability
    rag_evaluation: dict        # Step 3 output: {grade, retrieval_score, context_score, generation_score, ...}
    llm_traces: list[dict]      # Per-call traces appended by traced_completion()
    llm_summary: dict           # Aggregate: {total_cost_usd, total_tokens, by_agent, ...}
    judge: dict                 # LLM-as-Judge: {factuality_score, completeness_score, ..., overall_grade}

    # V2 — Self-serve config + HITL state
    repomind_config: dict       # Snapshot of .repomind.yml (or SAFE_DEFAULT_CONFIG)
    mode: str                   # "disabled" | "dry_run" | "auto_fix"
    hitl_required: bool         # True → graph pauses at merge_decision
    pr_url: str | None          # URL of the PR opened by pr_creator_node
    pr_number: int | None       # PR number (used for PR↔event mapping)
    human_approval: str         # "approved" | "rejected" | "skipped" | "timeout"
    review_data: dict           # Raw review payload (reviewer, body, state, timestamp)
    merge_result: dict          # {sha, merged_at, merged_by} from merge_node
    cleanup_result: dict        # {closed_at, apology_comment_url} from cleanup_node
```

#### `triage` field

```json
{
  "failure_type": "dependency_error",
  "confidence": 0.87,
  "root_cause": "Missing 'requests' package in requirements.txt",
  "summary": "Build failed because 'requests' was imported but not installed.",
  "_llm_trace": { "agent": "triage", "model": "gpt-4o", "..." : "..." }
}
```

#### `plan` field

```json
{
  "playbook_id": "fix_dependency_error",
  "actions": ["add_to_requirements"],
  "files_to_modify": ["requirements.txt"],
  "summary": "Add 'requests==2.31.0' to requirements.txt",
  "_llm_trace": { "agent": "planner", "..." : "..." }
}
```

#### `solver_result` field

```json
{
  "code_changes": [
    {
      "file_path": "requirements.txt",
      "change_type": "modify",
      "diff": "+ requests==2.31.0\n"
    }
  ],
  "reasoning": "Added the missing dependency...",
  "confidence": 0.92,
  "risk_assessment": "low",
  "files_inspected": ["requirements.txt", "pyproject.toml"],
  "solver_mode": "deep_agent",
  "_llm_trace": { "agent": "solver", "..." : "..." }
}
```

`solver_mode` is `"deep_agent"` (Tier 1) or `"direct_llm"` (Tier 2 fallback).

#### `validation` field

```json
{
  "status": "approved",
  "confidence": 0.95,
  "issues": [],
  "suggestions": [],
  "_llm_trace": { "agent": "validator", "..." : "..." }
}
```

`status` is `"approved"` or `"rejected"`. Rejection routes back to solver (max 2 retries).

#### `rag_evaluation` field

```json
{
  "grade": "B",
  "overall_score": 0.78,
  "retrieval_score": 0.82,
  "context_score": 0.74,
  "generation_score": 0.79,
  "hit_rate": 0.67,
  "mean_reciprocal_rank": 0.83,
  "mean_similarity": 0.71,
  "latency_ms": 245
}
```

#### `llm_summary` field

```json
{
  "total_calls": 5,
  "successful_calls": 5,
  "total_prompt_tokens": 12450,
  "total_completion_tokens": 2890,
  "total_tokens": 15340,
  "total_cost_usd": 0.0601,
  "total_latency_ms": 8120,
  "by_agent": {
    "triage":    { "calls": 1, "tokens": 2100, "cost_usd": 0.0080, "latency_ms": 1450 },
    "planner":   { "calls": 1, "tokens": 2800, "cost_usd": 0.0110, "latency_ms": 1820 },
    "solver":    { "calls": 1, "tokens": 6500, "cost_usd": 0.0260, "latency_ms": 3200 },
    "validator": { "calls": 1, "tokens": 2600, "cost_usd": 0.0102, "latency_ms": 1100 },
    "judge":     { "calls": 1, "tokens": 1340, "cost_usd": 0.0049, "latency_ms": 550 }
  }
}
```

#### `judge` field (LLM-as-Judge)

```json
{
  "factuality_score": 0.92,
  "completeness_score": 0.85,
  "confidence_calibration": 0.78,
  "overall_score": 0.85,
  "overall_grade": "B",
  "hallucination_flag": false,
  "issues": [
    "Triage confidence (0.87) slightly higher than warranted given log ambiguity"
  ],
  "verdict_summary": "Triage correctly identified the root cause and grounded its claims in the log excerpt. Minor calibration drift but no hallucinations.",
  "_llm_trace": { "agent": "judge", "..." : "..." }
}
```

#### `_llm_trace` schema (per-call)

```json
{
  "agent": "triage",
  "model": "gpt-4o",
  "prompt_tokens": 1820,
  "completion_tokens": 280,
  "total_tokens": 2100,
  "latency_ms": 1450,
  "cost_usd": 0.0080,
  "success": true,
  "error_type": null,
  "response_id": "chatcmpl-abc123",
  "prompt_hash": "a3f2b1c8d9e7"
}
```

---

## 3. Error Response Format

All error responses follow a consistent format:

```json
{
  "detail": "Error message describing what went wrong",
  "status": "error"
}
```

---

## 3a. Internal Python API (V2)

These are not HTTP endpoints but are the public functions that backend code (worker, Step 12, Lambda handlers) call into. Documented here for clarity since they cross module boundaries.

### `shared/repomind_config.py`

```python
from shared.repomind_config import (
    RepoMindConfig,            # dataclass
    parse_config,              # dict → RepoMindConfig (with safe defaults)
    parse_yaml_text,           # raw YAML string → RepoMindConfig
    load_repomind_config,      # (repo, ref=None) → RepoMindConfig (reads .repomind.yml via GitHub API)
    generate_sample_yml,       # () → str (canonical .repomind.yml template)
    SAFE_DEFAULT_CONFIG,       # dict — mode=dry_run, hitl_required=True, max_risk_level=low
    SAMPLE_REPOMIND_YML,       # str — same as generate_sample_yml()
)
```

### `agents/graph.py`

```python
from agents.graph import run_pipeline, resume_pipeline, get_graph

# Forward run (may pause if hitl_required=True and mode=auto_fix)
result = run_pipeline(
    event_id="evt-...",
    repo="owner/repo",
    excerpt="...",
    similar_incidents=[...],
    repomind_config={...},     # V2
    mode="auto_fix",           # V2
    hitl_required=True,        # V2
)
# → returns {"status": "completed"|"awaiting_review"|"denied", ...}

# Resume after a human review arrives (Step 12 calls this)
result = resume_pipeline(
    event_id="evt-...",
    human_approval="approved",   # "approved"|"rejected"|"skipped"|"timeout"
    review_data={"reviewer": "octocat", "body": "...", "state": "approved"},
)
```

### `review/review_handler.py`

```python
from review.review_handler import (
    ReviewHandler,                # class — handle(review_message) → result
    handle_review_message,        # SQS entry point — (msg_body: dict) → dict
    store_pr_event_mapping,       # (repo, pr_number, event_id) → None
    lookup_event_id_for_pr,       # (repo, pr_number) → event_id | None
)
```

### `pr_creator/welcome_pr.py`

```python
from pr_creator.welcome_pr import WelcomePRCreator
WelcomePRCreator().create_welcome_pr(repo="owner/repo")
# → returns {"status": "created"|"skipped_file_exists"|"skipped_branch_exists", "pr_url": "..."}
```

### `pr_creator/comment_poster.py`

```python
from pr_creator.comment_poster import CommentPoster
poster = CommentPoster(repo="owner/repo")
poster.post_dry_run(commit_sha="abc123", plan_summary={...})
poster.post_status(commit_sha="abc123", text="...")
poster.post_apology(pr_number=42, reason="...")
```

### `agents/checkpointer.py`

```python
from agents.checkpointer import get_checkpointer, S3CheckpointSaver
saver = get_checkpointer()       # S3CheckpointSaver in prod, MemorySaver in dev
# Used internally by get_graph(with_hitl=True)
```

---

## 4. Rate Limits

| Service | Limit |
|---------|-------|
| GitHub API | 5,000 requests/hour (authenticated) |
| Azure OpenAI | Per-deployment TPM/RPM (set in Azure AI Studio) |
| Groq API | Varies by model (free tier: ~30 req/min) |
| Qdrant Cloud | Varies by plan |
| AWS API Gateway | 10,000 requests/second (default) |
