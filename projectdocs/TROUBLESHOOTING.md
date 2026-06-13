# 🔧 Troubleshooting Guide — RepoMind V2

## 1. Common Issues

### 1.1 Installation Issues

| Problem | Cause | Solution |
|---------|-------|----------|
| `ModuleNotFoundError` | Dependencies not installed | `uv pip install -r requirements.txt` |
| `uv` command not found | uv not installed | Windows: `powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"` / Linux: `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| `sentence-transformers` takes forever | Downloads PyTorch (~2GB) | Wait; it's a one-time download |
| SSL errors on install | Corporate proxy/firewall | Use `uv pip install` (handles SSL better) or: `pip install --trusted-host pypi.org -r requirements.txt` |
| Python version mismatch | Python < 3.10 | Install Python 3.12: [python.org](https://www.python.org/downloads/) or `uv python install 3.12` |
| `.venv` activation fails (Windows) | PowerShell execution policy | `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser` |

### 1.2 Configuration Issues

| Problem | Cause | Solution |
|---------|-------|----------|
| `.env not loading` | Wrong location | Ensure `.env` is in project root (same level as `run_local.py`) |
| `Missing required env var` | Not set in `.env` | Copy from `.env.example`, fill all required values |
| `GITHUB_PRIVATE_KEY_PATH` error | File not found | Place `private-key.pem` in project root |
| **`AZURE_OPENAI_ENDPOINT` empty** | Not configured | Set in `.env` from Azure Portal → OpenAI resource → Keys and Endpoint |
| **`AzureKeyCredential` import fails** | Old `openai` SDK | Upgrade: `pip install -U openai>=1.82.0 azure-identity>=1.19.0` |
| `GROQ_API_KEY` empty | Not configured | Get free key from [console.groq.com](https://console.groq.com) (only needed if Azure creds are absent) |
| **Both Azure + Groq creds missing** | No LLM provider | System falls back to heuristic. Set at least one. |
| `LLM_JUDGE_ENABLED` toggle ignored | Set as bool | Must be string `"true"` or `"false"` (env vars are always strings) |

### 1.3 Local Development Issues

| Problem | Cause | Solution |
|---------|-------|----------|
| Port 8000 already in use | Another process running | Kill: `netstat -ano \| findstr :8000` then `taskkill /PID <pid> /F` |
| Port 3001 already in use (frontend) | Another process running | Change port in `frontend/package.json` `scripts.dev` |
| Webhook returns 401 | Invalid signature | In dev mode, check if signature validation is too strict |
| Triage returns "unknown" | No LLM available | Set `AZURE_OPENAI_*` or `GROQ_API_KEY`, or rely on keyword fallback |
| **Solver returns `solver_mode = "direct_llm"` always** | `deepagents` not installed | Run `pip install deepagents==0.6.8 langchain-openai==0.2.14` |
| **Validator always rejects** | Bad solver output | Check `state["solver_result"]["code_changes"]` is non-empty; check tier 1 timeout |
| **Judge skipped** | `LLM_JUDGE_ENABLED=false` | Set to `"true"` to enable |
| **Frontend shows mock data** | `/api/events` unreachable | Backend not running, or CORS issue. Start backend or check console. |
| No artifacts saved | Storage not configured | In dev mode, check `./data/` directory exists |
| Connection refused on Qdrant | Qdrant not running | Start Docker: `docker run -p 6333:6333 qdrant/qdrant` |

### 1.4 AWS Deployment Issues

| Problem | Cause | Solution |
|---------|-------|----------|
| `sam build` fails | Docker not running | Start Docker Desktop |
| `sam deploy` permission error | IAM insufficient | Add `AWSLambdaFullAccess`, `AmazonS3FullAccess`, `AmazonSQSFullAccess` |
| Lambda timeout | Processing takes too long | Increase timeout in `template.yaml` (max 900s); deep agent alone can take 45s |
| Lambda memory exhausted | Deep agent loads many files | Bump WorkerFunction MemorySize to 2048 MB in `template.yaml` |
| S3 AccessDenied | Missing bucket policy | Check SAM template `S3CrudPolicy` is set |
| SQS message not received | Batch size or visibility | Check `VisibilityTimeout` > Lambda timeout |

### 1.5 Pipeline Issues

| Problem | Cause | Solution |
|---------|-------|----------|
| Logs empty / fetch fails | GitHub API rate limit | Check rate limit: `curl -H "Authorization: token xxx" https://api.github.com/rate_limit` |
| Triage always returns `unknown` | Azure / Groq API error | Check `AZURE_OPENAI_API_KEY`; check Azure status page; fallback to Groq |
| **Solver hits 45s timeout often** | Deep agent reading many files | Reduce tool budget in `agents/deep_solver.py::_ToolBudget` |
| **Validator stuck in retry loop** | Cap not enforced | Check `validation_attempts` increments; max is 2 |
| **Hallucination flag always true** | Strict judge prompt | Review log excerpt quality; ensure it contains the actual error |
| **Total cost too high** | Many retries / large prompts | Disable judge (`LLM_JUDGE_ENABLED=false`); reduce tool budget; switch to Groq |
| Policy always denies | Rules too strict | Review `policy/default.yaml`, adjust confidence thresholds |
| PR not created | Policy denied OR no code_changes | Check artifacts.json → `policy.decision`; check `solver_result.code_changes` is non-empty |
| **Empty PR opened** | Should not happen post-v1.3.0 | If you see this, upgrade — `pr_creator/pr_creator.py` now skips empty diffs |
| PR creation fails | GitHub permissions | Check GitHub App has `Contents: write` and `Pull requests: write` |

### 1.1 V2 HITL & Self-Serve Issues

| Issue | Probable Cause | Fix |
|-------|----------------|-----|
| **PR opened but never merges, even after approval** | Step 12 isn't receiving `pull_request_review` events | Verify GitHub App is subscribed to `Pull request reviews`; check Lambda logs for `review.review_handler` entries |
| **"awaiting_review" status, no checkpoint in S3** | Lambda IAM role missing `s3:PutObject` on `checkpoints/*` | Update Lambda role per [DEPLOYMENT.md §5.5](./DEPLOYMENT.md#55-v14--required-iam-permissions-for-the-worker-lambda) |
| **Reviewer approved, but RepoMind says "no paused graph found"** | PR↔event index missing or `event_id` mismatch | Inspect `s3://…/indexes/by-pr/<owner>-<repo>/<pr_number>.json`; if absent, the PR was opened outside the V2 flow — manually merge |
| **Welcome PR opened twice on the same repo** | Idempotency check failed | Verify `repomind/welcome` branch exists; if not, delete `.repomind.yml` manually and re-trigger via App reinstall |
| **`.repomind.yml` edits not taking effect** | Worker reads from GitHub `default_branch` only | Merge your `.repomind.yml` change to the default branch; takes effect on **next** webhook event |
| **Repo getting comments instead of PRs** | `mode: dry_run` is active | Edit `.repomind.yml` → `mode: auto_fix` and merge |
| **Repo doing nothing at all** | `mode: disabled` is set | Edit `.repomind.yml` → `mode: dry_run` or `auto_fix` and merge |
| **Pipeline auto-merges without review** | `hitl_required: false` is set | Edit `.repomind.yml` → `hitl_required: true` and merge (recommended) |
| **Checkpoint never expires** | No lifecycle rule on S3 bucket | Add a lifecycle policy: `checkpoints/*` → expire after 30 days, `indexes/*` → 90 days |
| **`KeyError: 'repomind_config'` in policy_node** | State not flowing config through | Ensure worker passes `repomind_config=cfg.to_dict()` to `run_pipeline()` |
| **`AttributeError: BaseCheckpointSaver` at import** | LangGraph not installed | `pip install langgraph==0.3.4` — or rely on the fallback to `dict` base |
| **Resume fails with `thread_id not found`** | Different `event_id` between pause & resume | Step 12 must pass the **same `event_id`** that was used at pause time — check the PR↔event index |

---

## 2. Debugging Steps

### 2.1 Check Configuration

```bash
# Verify Python
python --version

# Verify imports
python -c "from shared.config import settings; print(settings.ENVIRONMENT)"

# Verify .env is loaded
python -c "from shared.config import settings; print('APP_ID:', settings.GITHUB_APP_ID)"
```

### 2.2 Check Local Server

```bash
# Start server
python run_local.py

# In another terminal:
curl http://localhost:8080/health
# Expected: {"status": "healthy", "service": "repomind-webhook"}
```

### 2.3 Check Pipeline Steps

```bash
# Run pipeline simulation
python test_local_pipeline.py

# Run specific tests
pytest tests/test_triage.py -v -s
pytest tests/test_policy.py -v -s
```

### 2.4 Check AWS Logs

```bash
# Real-time logs
sam logs -n WebhookFunction --stack-name repomind --tail
sam logs -n WorkerFunction --stack-name repomind --tail

# Filter errors
sam logs -n WorkerFunction --stack-name repomind --filter ERROR

# Check DLQ
aws sqs get-queue-attributes \
  --queue-url <dlq-url> \
  --attribute-names ApproximateNumberOfMessages
```

### 2.5 Check S3 Artifacts

```bash
# List events
aws s3 ls s3://repomind-data-123456789012/events/ --recursive

# Read a specific artifact
aws s3 cp s3://repomind-data-123456789012/events/<slug>/<event-id>/artifacts.json -

# Read timeline
aws s3 cp s3://repomind-data-123456789012/events/<slug>/<event-id>/timeline.json -
```

---

## 3. Error Messages Reference

| Error Message | Module | Cause | Fix |
|--------------|--------|-------|-----|
| `Invalid webhook signature` | webhook/signature.py | HMAC mismatch | Verify `GITHUB_WEBHOOK_SECRET` matches GitHub App setting |
| `Not a failed workflow run` | webhook/webhook_handler.py | Event is not a failure | Normal — only failures are processed |
| `Failed to fetch logs` | worker/log_fetcher.py | GitHub API error | Check GitHub token, repo permissions, rate limits |
| `Triage fallback to heuristic` | triage/triage.py | Groq API unavailable | Check `GROQ_API_KEY`, Groq service status |
| `Policy denied` | policy_engine/policy.py | Safety rules blocked fix | Review policy rules, adjust thresholds |
| `PR creation failed` | pr_creator/pr_creator.py | GitHub API error | Check App permissions (Contents + PRs) |
| `Qdrant connection failed` | rag/indexer.py | Qdrant not running | Start Qdrant or skip vector indexing |
| `Collection not found` | rag/retriever.py | No data indexed yet | Run indexer first or ignore for new deployments |

---

## 4. FAQ

### Q: Can I run without Groq API key?

**A:** Yes, for development. Triage and Planner will fall back to keyword/template heuristics. Results won't be as accurate but the pipeline still works.

### Q: Can I run without Qdrant?

**A:** Yes. Vector indexing will be skipped with a warning. The pipeline still processes failures and creates PRs.

### Q: Can I run without AWS?

**A:** Yes. Set `ENVIRONMENT=development`. Storage uses local filesystem (`./data/`), queue logs messages locally. Perfect for development.

### Q: Why does policy always deny my fixes?

**A:** Check:

1. Is the `failure_type` in the allowed list? (Only dependency, import, syntax, config errors are auto-allowed)
2. Is the `confidence` above the threshold? (0.7–0.9 depending on type)
3. Is the `risk_level` "low"? (Medium and high are denied)
4. Review `policy/default.yaml` and adjust rules if needed.

### Q: How do I add a new failure type?

**A:**

1. Add to `FAILURE_TYPES` list in `triage/triage.py`
2. Add keyword patterns in `_keyword_fallback()`
3. Add a policy rule in `policy/default.yaml`
4. Add a test in `tests/test_triage.py`

### Q: How do I add support for a new repository?

**A:**

1. Install the GitHub App on the repository
2. Optionally create `policy/<org>-<repo>.yaml` for custom rules
3. Add to `repos.yaml` if maintaining a registry

### Q: The first run is very slow. Why?

**A:** First run downloads the `all-MiniLM-L6-v2` model (~90MB). Subsequent runs use the cached model and are much faster.

---

## 5. Getting Help

1. **Check logs:** Always start with the logs (terminal or CloudWatch)
2. **Run tests:** `pytest tests/ -v -s` to isolate the issue
3. **Check artifacts:** Look at `artifacts.json` and `timeline.json` in S3
4. **Review policy:** `policy/default.yaml` for blocked fixes
5. **Open an issue:** Provide logs, artifacts, and error messages
