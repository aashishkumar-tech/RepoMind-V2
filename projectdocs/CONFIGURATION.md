# ⚙️ Configuration Guide — RepoMind V2

## 1. Environment Variables

All configuration is managed through environment variables, loaded from a `.env` file in the project root.

### 1.1 Complete Variable Reference

#### AWS Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `AWS_REGION` | No | `ap-south-1` | AWS region for all services |
| `AWS_ACCOUNT_ID` | Prod only | *(empty)* | AWS account ID |
| `S3_SAM_BUCKET` | Prod only | `repomind-sam-deployments` | S3 bucket for SAM artifacts |
| `S3_DATA_BUCKET` | Prod only | `repomind-data` | S3 bucket for event data |

#### GitHub App Configuration

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GITHUB_APP_ID` | Yes | *(empty)* | GitHub App ID |
| `GITHUB_INSTALLATION_ID` | Yes | *(empty)* | Installation ID for the target org/repo |
| `GITHUB_PRIVATE_KEY_PATH` | Yes | `private-key.pem` | Path to GitHub App private key file |
| `GITHUB_WEBHOOK_SECRET` | Yes | *(empty)* | Webhook HMAC secret for signature validation |

#### LLM Configuration

**Primary — Azure OpenAI (recommended):**

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `AZURE_OPENAI_ENDPOINT` | Recommended | *(empty)* | Azure OpenAI endpoint URL (e.g., `https://my-resource.openai.azure.com/`) |
| `AZURE_OPENAI_API_KEY` | Recommended | *(empty)* | Azure OpenAI API key |
| `AZURE_OPENAI_API_VERSION` | No | `2024-02-01` | Azure OpenAI API version |
| `AZURE_OPENAI_DEPLOYMENT_NAME` | No | `gpt-4o` | Azure deployment name (your model alias in Azure AI Studio) |

**Fallback — Groq (free tier, used when Azure creds are absent):**

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GROQ_API_KEY` | Conditional | *(empty)* | Groq API key (only required if Azure creds are NOT set) |

**LLM Behavior:**

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `LLM_JUDGE_ENABLED` | No | `true` | Enable LLM-as-Judge quality auditor (set `false` to save 1 LLM call per event) |

> **Resolution:** `shared/azure_llm.get_llm_client()` returns the Azure client if both `AZURE_OPENAI_ENDPOINT` AND `AZURE_OPENAI_API_KEY` are set; otherwise it returns the Groq client. `get_model_name()` returns `AZURE_OPENAI_DEPLOYMENT_NAME` (Azure mode) or `llama-3.3-70b-versatile` (Groq mode).

#### Azure Storage / Service Bus (Optional)

If you choose to use Azure-native storage and queueing instead of AWS S3 / SQS:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `AZURE_STORAGE_CONNECTION_STRING` | No | *(empty)* | Azure Blob Storage connection string |
| `AZURE_STORAGE_CONTAINER` | No | `repomind-artifacts` | Blob container name |
| `AZURE_SERVICE_BUS_CONNECTION_STRING` | No | *(empty)* | Azure Service Bus connection string |
| `AZURE_SERVICE_BUS_QUEUE` | No | `repomind-events` | Service Bus queue name |

#### Email Notifications

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `GMAIL_ADDRESS` | No | *(empty)* | Gmail sender address |
| `GMAIL_APP_PASSWORD` | No | *(empty)* | Gmail App Password (NOT regular password) |
| `NOTIFICATION_EMAILS` | No | *(empty)* | Comma-separated recipient list |

#### Vector Database

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `QDRANT_HOST` | No | `localhost` | Qdrant server hostname |
| `QDRANT_PORT` | No | `6333` | Qdrant server port |

#### Application Settings

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ENVIRONMENT` | No | `development` | `development` or `production` |
| `LOG_LEVEL` | No | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `TARGET_REPO` | No | *(empty)* | Target repo for testing (e.g., `owner/repo`) |

#### Observability & Monitoring

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `METRICS_ENABLED` | No | `false` | Enable Prometheus metrics recording |
| `PUSHGATEWAY_URL` | No | *(empty)* | Pushgateway endpoint (e.g., `http://localhost:9091`) |
| `KILL_SWITCH_PARAM` | No | `/repomind/kill_switch` | SSM parameter name for global kill switch |

#### Verification & Rollback (Step 10)

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `VERIFICATION_ENABLED` | No | `true` | Enable post-PR CI verification |
| `MAX_ROLLBACKS_PER_HOUR` | No | `3` | Max revert PRs per repo per hour (rate limit) |

---

### 1.2 Setup `.env` File

```bash
# Copy the template
cp .env.example .env

# Edit with your values
notepad .env          # Windows
# nano .env           # Linux
# code .env           # VS Code
```

### 1.3 Required vs Optional

**Minimum for local development (no AWS):**
```bash
ENVIRONMENT=development
LOG_LEVEL=DEBUG
```

**Minimum for full pipeline (Azure OpenAI mode — recommended):**
```bash
GITHUB_APP_ID=123456
GITHUB_INSTALLATION_ID=789012
GITHUB_PRIVATE_KEY_PATH=private-key.pem
GITHUB_WEBHOOK_SECRET=your-secret
AZURE_OPENAI_ENDPOINT=https://my-resource.openai.azure.com/
AZURE_OPENAI_API_KEY=your-azure-key
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o
LLM_JUDGE_ENABLED=true
```

**Minimum for full pipeline (Groq fallback — free tier):**
```bash
GITHUB_APP_ID=123456
GITHUB_INSTALLATION_ID=789012
GITHUB_PRIVATE_KEY_PATH=private-key.pem
GITHUB_WEBHOOK_SECRET=your-secret
GROQ_API_KEY=gsk_your_key
LLM_JUDGE_ENABLED=false   # judge requires high-quality LLM; disable in free-tier mode
```

**For AWS deployment:**
```bash
AWS_REGION=ap-south-1
AWS_ACCOUNT_ID=123456789012
S3_DATA_BUCKET=repomind-data-123456789012
```

**For observability (Step 11):**
```bash
METRICS_ENABLED=true
PUSHGATEWAY_URL=http://localhost:9091
KILL_SWITCH_PARAM=/repomind/kill_switch
```

**For verification (Step 10):**
```bash
VERIFICATION_ENABLED=true
MAX_ROLLBACKS_PER_HOUR=3
```

---

## 2. Per-Repository Config — `.repomind.yml` (V2)

Each onboarded repository owns a `.repomind.yml` file at the **repo root** that controls RepoMind's behaviour for that repo. The worker reads it on every event (via the GitHub Contents API) and feeds it into the policy gate.

### 2.1 File Lifecycle

| Event | Action |
|-------|--------|
| GitHub App **installed** on a repo | Worker opens **"🤖 Welcome to RepoMind"** PR with a default `.repomind.yml` |
| Owner **merges the welcome PR** | Repo now has self-serve config — RepoMind respects it on every event |
| Owner **deletes the file** | RepoMind falls back to `SAFE_DEFAULT_CONFIG` (= `mode: dry_run`, `hitl_required: true`) |
| Owner edits a field | Takes effect on the **next** webhook event (no redeploy needed) |

### 2.2 Schema

```yaml
# .repomind.yml — RepoMind per-repo configuration (V2)

# Operating mode (one of: disabled | dry_run | auto_fix)
mode: dry_run

# Require human approval (PR review) before any merge?
# Set to `false` ONLY if your repo has branch protection rules
# that already gate merges (e.g. required reviewers).
hitl_required: true

# Failure-type filter — only operate on these classes of failure.
# Empty list = all types allowed.
allowed_failure_types:
  - lint
  - format
  - missing_dep
  - syntax
  - test_assertion
  # - flaky          # uncomment to also handle flaky-test rerun

# Minimum triage confidence to proceed (0.0 – 1.0).
# Stricter than the operator-side policy default (0.7).
min_triage_confidence: 0.85

# Maximum risk level RepoMind may take (low | medium | high).
# Stricter than the operator-side policy default (medium).
max_risk_level: low

# Paths RepoMind must never modify (gitignore-style globs).
# Always denies regardless of mode.
protected_paths:
  - "infra/**"
  - "**/*.env"
  - "**/secrets/**"

# Labels to attach to RepoMind-opened PRs.
pr_labels:
  - "repomind"
  - "automated-fix"

# Reviewers (GitHub usernames) auto-requested on every RepoMind PR.
# At least one approval is required when `hitl_required: true`.
reviewers:
  - octocat

# Optional: forward fix notifications to this email (in addition to GitHub).
notification_email: ""
```

### 2.3 Three Operating Modes

| Mode       | RepoMind opens PR? | Posts comment? | Merges after approval? | Use case |
|------------|--------------------|----------------|------------------------|----------|
| `disabled` | ❌ never            | ❌ never        | ❌ never                | Pause completely (incidents, audits) |
| `dry_run`  | ❌ never            | ✅ on commit/PR | ❌ never                | Evaluation phase — see what RepoMind *would* do |
| `auto_fix` | ✅ when policy allows | ✅ status updates | ✅ if reviewer approves | Production mode with HITL |

> **Safe default:** if a repo has no `.repomind.yml` (e.g. before the welcome PR is merged, or after manual deletion), RepoMind uses `SAFE_DEFAULT_CONFIG` → `mode: dry_run`, `hitl_required: true`, `max_risk_level: low`. The system **never auto-merges without explicit opt-in**.

### 2.4 Interaction with Operator Policy (`policy/default.yaml`)

User config is a **stricter pre-filter** on top of operator policy:

```
event → operator policy (policy/default.yaml) → user .repomind.yml → decision
                  allow                          deny → DENY
                  allow                          allow → ALLOW
                  deny                           any   → DENY
```

- Operator policy is the **floor** — it can always deny, but cannot override a user's `disabled` or a stricter `max_risk_level`.
- See `policy_engine/policy.py::_evaluate_user_config()` for the exact precedence rules.

### 2.5 Generating a Sample File

```python
from shared.repomind_config import generate_sample_yml
print(generate_sample_yml())
```

Or copy from the welcome PR that RepoMind opens on first install.

---

## 3. Policy Configuration

### 2.1 File Location

```
policy/
  default.yaml       ← Default policy (always loaded)
  myorg-service-a.yaml   ← Repo-specific override (optional)
```

### 2.2 Policy Schema

```yaml
version: 1
scope:
  description: "Human-readable description"

defaults:
  allow_auto_fix: false        # Global default
  max_risk_level: "medium"     # Max allowed risk

rules:                         # Ordered list — first match wins
  - id: rule_identifier        # Unique ID
    description: "..."         # Human-readable
    when:                      # Match criteria
      failure_types: [...]     # List of failure types to match
      max_risk_level: "low"    # Max risk for this rule
      min_confidence: 0.7      # Min triage confidence
    decision: "allow"          # allow | deny | manual_review
```

### 2.3 Current Default Rules

| # | Rule ID | Matches | Decision |
|---|---------|---------|----------|
| 1 | `allow_low_risk_dependency_fix` | dependency_error, risk≤low, conf≥0.7 | ✅ Allow |
| 2 | `allow_import_fix` | import_error, risk≤low, conf≥0.8 | ✅ Allow |
| 3 | `allow_syntax_fix` | syntax_error, risk≤low, conf≥0.9 | ✅ Allow |
| 4 | `allow_config_fix` | config_error, risk≤low, conf≥0.85 | ✅ Allow |
| 5 | `deny_high_risk` | risk≥high | ❌ Deny |
| 6 | `deny_low_confidence` | conf≤0.5 | ❌ Deny |
| 7 | `default_deny` | everything else | ❌ Deny |

### 2.4 Adding Custom Rules

Add a new rule before `default_deny`:

```yaml
rules:
  # ... existing rules ...
  
  - id: allow_test_fix_on_staging
    description: "Allow test fixes on staging repos"
    when:
      failure_types: ["test_failure"]
      max_risk_level: "medium"
      min_confidence: 0.75
    decision: "allow"
  
  - id: default_deny
    # ... keep this last
```

---

## 4. Operator Repo List

### 4.1 File: `repos.yaml`

Lists target repositories the system monitors:

```yaml
repos:
  - name: "myorg/service-a"
    enabled: true
  - name: "myorg/service-b"
    enabled: true
```

> **V2 note:** With self-serve `.repomind.yml`, `repos.yaml` is **optional** — any repo with the GitHub App installed is automatically enrolled. The list is now mainly for explicit allow-listing in restricted deployments.

---

## 5. AWS SAM Configuration

### 4.1 File: `template.yaml`

**Deploy-time Parameters:**

| Parameter | Description | NoEcho |
|-----------|-------------|--------|
| `GitHubAppId` | GitHub App ID | No |
| `GitHubInstallationId` | Installation ID | No |
| `GitHubWebhookSecret` | Webhook HMAC secret | Yes |
| `GroqApiKey` | Groq API key (fallback LLM) | Yes |
| `AzureOpenAIEndpoint` | Azure OpenAI endpoint URL | No |
| `AzureOpenAIApiKey` | Azure OpenAI API key | Yes |
| `AzureOpenAIDeploymentName` | Azure model deployment name (default `gpt-4o`) | No |
| `LLMJudgeEnabled` | `true`/`false` toggle for LLM-as-Judge | No |

**Lambda Settings:**

| Function | Memory | Timeout | Trigger |
|----------|--------|---------|---------|
| `WebhookFunction` | 256 MB | 30s | API Gateway POST |
| `WorkerFunction` | 1024 MB | 300s | SQS (batch=1) |

**S3 Lifecycle:**

| Prefix | Retention |
|--------|-----------|
| `events/` | 180 days |
| `checkpoints/` (V2) | 30 days (HITL paused state) |
| `indexes/by-pr/` (V2) | 90 days (PR↔event mapping) |

---

## 6. Logging Configuration

### 6.1 Log Levels

| Level | When to Use |
|-------|-------------|
| `DEBUG` | Local development (verbose) |
| `INFO` | Standard operation (default) |
| `WARNING` | Non-critical issues |
| `ERROR` | Failures requiring attention |

### 6.2 Log Format

**Development:** Colored console output
```
2026-02-13 15:44:00 [INFO] worker.main: Processing event evt-...
```

**Production:** JSON for CloudWatch
```json
{"timestamp": "2026-02-13T15:44:00Z", "level": "info", "logger": "worker.main", "event": "Processing event", "event_id": "evt-..."}
```

---

## 7. Configuration Precedence

```
1. Environment Variables (highest priority — operator scope)
2. .env file                    (operator scope)
3. policy/default.yaml          (operator scope)
4. .repomind.yml                (repo owner scope — stricter pre-filter, V2)
5. Code defaults                (lowest priority, e.g. SAFE_DEFAULT_CONFIG)
```

**Two scopes, two policies:**
- **Operator** controls what RepoMind *can* do (env vars + `policy/default.yaml`).
- **Repo owner** controls what RepoMind *should* do for their repo (`.repomind.yml`).
- Effective decision is the **intersection** — both layers must allow an action.

The `Settings.from_env()` method reads each variable with fallback:
```python
os.getenv("VARIABLE_NAME", "default_value")
```
