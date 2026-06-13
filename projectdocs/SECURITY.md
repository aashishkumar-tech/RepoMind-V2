# 🔒 Security Document — RepoMind V2

## 1. Security Overview

RepoMind processes sensitive data including CI logs, source code references, and GitHub credentials. This document describes the security measures implemented at every layer.

**V2 update:** RepoMind now offers a **Human-in-the-Loop merge gate** by default. Any change RepoMind proposes must be approved by a repo reviewer before being merged. Combined with the new `.repomind.yml` per-repo opt-in, this brings RepoMind much closer to a "supervised junior engineer" model than a free-roaming bot.

---

## 2. Threat Model

| Threat | Risk | Mitigation |
|--------|------|------------|
| **Forged webhook** | Unauthorized pipeline trigger | HMAC-SHA256 signature validation |
| **Secret leakage in logs** | Credential exposure | 10-pattern sanitizer, S3 encryption |
| **Malicious PR** | Code injection via auto-fix | Policy engine (deny-by-default), risk limits, **HITL approval gate (V2)** |
| **Bot merges without consent** | Unintended code change | **`.repomind.yml` self-serve + dry-run-by-default (V2)**, branch protection |
| **LLM prompt injection** | Manipulated triage/plan | Input sanitization, structured output, low temp |
| **Token theft** | GitHub / Azure / Groq API abuse | Short-lived JWT, env vars, no hardcoding, NoEcho in CFN |
| **S3 data exposure** | Unauthorized artifact access | Bucket policies, no public access, **encryption at rest for checkpoints (V2)** |
| **Stale checkpoint replay** | Resuming an outdated paused graph | Checkpoint TTL (30 days) + `event_id` thread isolation |
| **Cross-repo PR mapping confusion** | Resuming wrong graph from review | PR↔event index is scoped per `<owner>-<repo>/<pr_number>` |

---

## 2a. V2 HITL Security Guarantees

| Guarantee | Mechanism |
|-----------|-----------|
| **No auto-merge without explicit human approval** | `interrupt_before=["merge_decision"]` — graph cannot reach `merge_node` without `state["human_approval"] == "approved"` |
| **Approval must come from a real GitHub reviewer** | Only `pull_request_review` webhook events (signed by GitHub HMAC) can supply approval |
| **Reviewer identity is recorded** | `state["review_data"]["reviewer"]` is persisted in the final artifact |
| **Default mode is dry-run** | `SAFE_DEFAULT_CONFIG.mode = "dry_run"` — repos without `.repomind.yml` only get comments, never PRs |
| **Owner can disable instantly** | Set `mode: disabled` in `.repomind.yml` → next event short-circuits with zero side-effects |
| **Protected paths are enforced before LLM call** | `policy.py::_evaluate_user_config()` denies any plan touching `.repomind.yml::protected_paths` |
| **Paused state is encrypted** | S3 checkpoint objects inherit bucket-level SSE-S3 (or KMS if configured) |
| **Welcome PR is idempotent** | `WelcomePRCreator` skips if `.repomind.yml` exists OR `repomind/welcome` branch exists — no churn on re-install |

---

## 3. Authentication & Authorization

### 3.1 GitHub App Authentication

```
┌─────────────────────────────────────────────────┐
│  Authentication Flow                             │
│                                                  │
│  Private Key (.pem)                              │
│       │                                          │
│       ▼                                          │
│  Generate JWT (RS256, 10-min expiry)             │
│       │                                          │
│       ▼                                          │
│  Exchange JWT → Installation Token (~1hr)        │
│       │                                          │
│       ▼                                          │
│  Authenticated API calls (PyGithub)              │
└─────────────────────────────────────────────────┘
```

**Security Properties:**
- Private key never leaves the server
- JWT expires in 10 minutes (short window)
- Installation tokens cached in memory only (not persisted)
- Tokens auto-refresh before expiry

### 3.2 Webhook Signature Validation

```python
# Implementation: webhook/signature.py
def validate_signature(payload: bytes, signature: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(
        secret.encode(), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
```

**Security Properties:**
- HMAC-SHA256 (cryptographically secure)
- `hmac.compare_digest()` — constant-time comparison (prevents timing attacks)
- Raw payload bytes validated (prevents JSON re-serialization attacks)
- 401 returned immediately on failure (no processing)

---

## 4. Secret Management

### 4.1 Environment Variables

All secrets are loaded from environment variables — **never hardcoded**:

| Secret | Env Var | Storage |
|--------|---------|---------|
| GitHub App ID | `GITHUB_APP_ID` | .env / SSM |
| Installation ID | `GITHUB_INSTALLATION_ID` | .env / SSM |
| Private Key | `GITHUB_PRIVATE_KEY_PATH` | File on disk / SSM |
| Webhook Secret | `GITHUB_WEBHOOK_SECRET` | .env / SSM (NoEcho) |
| **Azure OpenAI Endpoint** | `AZURE_OPENAI_ENDPOINT` | .env / SSM |
| **Azure OpenAI API Key** | `AZURE_OPENAI_API_KEY` | .env / SSM (NoEcho) |
| Groq API Key (fallback) | `GROQ_API_KEY` | .env / SSM (NoEcho) |
| Azure Storage Conn String | `AZURE_STORAGE_CONNECTION_STRING` | .env / SSM (NoEcho, optional) |
| Azure Service Bus Conn String | `AZURE_SERVICE_BUS_CONNECTION_STRING` | .env / SSM (NoEcho, optional) |
| Gmail Password | `GMAIL_APP_PASSWORD` | .env / SSM |

> **Azure recommended best practice:** use `DefaultAzureCredential` (managed identity) instead of API key in production. The `azure-identity` package is included for this.

### 4.2 SAM Template (NoEcho)

Sensitive parameters in `template.yaml` use `NoEcho: true`:

```yaml
Parameters:
  GitHubWebhookSecret:
    Type: String
    NoEcho: true   # Masked in CloudFormation console
  GroqApiKey:
    Type: String
    NoEcho: true
```

### 4.3 `.gitignore` Protection

```gitignore
.env
private-key.pem
*.pem
data/
.aws-sam/
```

---

## 5. Log Sanitization

### 5.1 Sanitizer Patterns

The `Sanitizer` class (`worker/sanitizer.py`) applies 10 regex patterns to all CI logs:

| # | Pattern | Detects | Replacement |
|---|---------|---------|-------------|
| 1 | `AKIA[0-9A-Z]{16}` | AWS Access Key ID | `[REDACTED:aws_access_key]` |
| 2 | `[A-Za-z0-9/+=]{40}` | AWS Secret Key | `[REDACTED:aws_secret_key]` |
| 3 | `gh[ps]_[A-Za-z0-9_]{36,}` | GitHub Token | `[REDACTED:github_token]` |
| 4 | `Bearer\s+[A-Za-z0-9\-._~+/]+=*` | Bearer Token | `[REDACTED:bearer_token]` |
| 5 | `(?i)password\s*[:=]\s*\S+` | Password Field | `[REDACTED:password_field]` |
| 6 | `[email pattern]` | Email Address | `[REDACTED:email_address]` |
| 7 | `\b(?:10\|172\.(?:1[6-9]\|2\d\|3[01])\|192\.168)\.\d+\.\d+\b` | Private IP | `[REDACTED:private_ip]` |
| 8 | `(?i)(?:mysql\|postgres\|mongodb\|redis)://\S+` | Connection String | `[REDACTED:connection_string]` |
| 9 | `eyJ[...JWT pattern...]` | JWT Token | `[REDACTED:jwt_token]` |
| 10 | `(?i)(?:secret\|api_key\|token)\s*[:=]\s*\S+` | Generic Secret | `[REDACTED:generic_secret]` |

### 5.2 Sanitization Pipeline

```
Raw CI Logs → Sanitizer (10 patterns) → Sanitized Logs → Excerpt → S3
                                                                    │
                                                            Sanitized text
                                                            stored, NOT raw
```

**Important:** Sanitization happens BEFORE any storage, LLM calls, or embedding generation.

---

## 6. Policy Engine (Safety Guardrails)

### 6.1 Deny-by-Default Design

```
Rule Evaluation:
  1. Check rules in order (first match wins)
  2. If NO rule matches → DEFAULT DENY
  3. If policy engine ERRORS → DENY (fail-closed)
```

### 6.2 Risk Level Constraints

| Risk Level | Auto-Fix Allowed | Conditions |
|------------|-----------------|------------|
| `low` | ✅ Yes | High confidence, known failure type |
| `medium` | ⚠️ Limited | Per-rule basis, some types only |
| `high` | ❌ Never | Always denied |

### 6.3 Confidence Thresholds

| Failure Type | Min Confidence for Auto-Fix |
|-------------|---------------------------|
| Dependency Error | 0.70 |
| Import Error | 0.80 |
| Syntax Error | 0.90 |
| Config Error | 0.85 |
| All others | Denied |

---

## 7. LLM Security

### 7.1 Input Sanitization

- Logs are sanitized before LLM processing (10 regex patterns redact AWS keys, GitHub tokens, passwords, PII)
- No raw secrets reach the LLM prompt
- Excerpt is truncated to prevent prompt overflow
- Tier 1 deep agent tools are **read-only** — cannot write to disk, network, or repo

### 7.2 Output Validation

- LLM responses parsed as JSON (structured output)
- `failure_type` validated against whitelist (10 known types)
- `confidence` validated as float in [0, 1]
- `risk_level` validated against `low/medium/high`
- Solver `code_changes` validated by Step 9 quality gate (syntax + ruff + mypy)
- Validator agent peer-reviews solver output before policy
- Invalid responses → fallback to heuristic (no blind trust)

### 7.3 Temperature Control

| Step | Temperature | Rationale |
|------|-------------|-----------|
| Triage | 0.1 | Deterministic classification |
| Planner | 0.2 | Slightly creative for plan generation |
| Solver (Tier 1 + 2) | 0.2 | Slightly creative for code diffs |
| Validator | 0.1 | Deterministic peer review |
| LLM-as-Judge | 0.0 | Fully deterministic audit |

### 7.4 Prompt Hashing & Trace Privacy

- Every prompt is hashed (SHA-256, first 12 chars) for cache analysis
- **Raw prompts are never stored** in `state["llm_traces"]` — only the hash
- Traces contain only metadata: tokens, latency, cost, response_id, agent, model, success
- Per-event traces are saved to S3 only if `LOG_LEVEL=DEBUG` (default: not saved)

### 7.5 Hallucination Detection

The **LLM-as-Judge** independently audits triage output:

- Sets `hallucination_flag = true` if triage references files/packages/errors not in the actual log
- Provides `verdict_summary` and `issues` list explaining what was hallucinated
- Result is exposed on the dashboard and via `repomind_llm_hallucinations_total` Prometheus metric
- Enables auditors to detect unsafe agent behavior without re-running the swarm

### 7.6 Tool Sandbox (Tier 1 Deep Agent)

Solver Tier 1 tools are sandboxed:

- **Read-only**: `read_repo_file`, `list_repo_directory`, `search_repo_code`
- **Capped**: 8 reads max, 50 KB per file, 45 s wall-clock timeout
- **Path-validated**: paths are normalized and rejected if they escape the workspace root
- **No network**: tools cannot make external HTTP calls
- **No exec**: tools cannot execute commands or eval code

---

## 8. Network Security

### 8.1 External Connections

| Connection | Protocol | Authentication |
|-----------|----------|----------------|
| GitHub API | HTTPS | Installation Token |
| Groq API | HTTPS | API Key (header) |
| Qdrant | HTTP/HTTPS | Optional API Key |
| Gmail SMTP | TLS (port 587) | App Password |

### 8.2 AWS Security

- Lambda runs in VPC (optional, for Qdrant access)
- S3 bucket: no public access, default encryption
- SQS: no public access, IAM-only
- API Gateway: HTTPS only

---

## 9. Data Retention & Privacy

| Data | Retention | Encryption |
|------|-----------|------------|
| Raw CI logs | 30 days | S3 default (AES-256) |
| Excerpts | 90 days | S3 default |
| Artifacts | 180 days | S3 default |
| Embeddings | 1 year | S3 + Qdrant |
| Timeline | 180 days | S3 default |

---

## 10. Security Best Practices

| # | Practice | Implementation |
|---|----------|----------------|
| 1 | Never hardcode secrets | `.env` + env vars |
| 2 | Sanitize before storage | Sanitizer runs first |
| 3 | Validate webhook origin | HMAC-SHA256 |
| 4 | Deny by default | Policy engine |
| 5 | Short-lived tokens | JWT (10min), install token (~1hr) |
| 6 | Constant-time comparison | `hmac.compare_digest()` |
| 7 | Input validation | Pydantic models everywhere |
| 8 | Error masking | Don't expose internals in API responses |
| 9 | Git ignore secrets | `.gitignore` for `.env`, `.pem` |
| 10 | Least privilege IAM | SAM policies scoped to specific resources |
