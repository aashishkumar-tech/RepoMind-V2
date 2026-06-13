# RepoMind V2 — Deployment Notes

## First-Time Setup (per machine)

`samconfig.toml` is **gitignored** — it contains environment-specific
ARNs that should not be committed. You'll need to recreate it locally
the first time you deploy.

### Step 1 — Look up the secret ARNs

```powershell
# Run once — these are persistent
aws secretsmanager describe-secret --secret-id repomind/github-webhook-secret --region ap-south-1 --query ARN --output text
aws secretsmanager describe-secret --secret-id repomind/groq-api-key            --region ap-south-1 --query ARN --output text
aws secretsmanager describe-secret --secret-id repomind/openai-api-key          --region ap-south-1 --query ARN --output text
```

### Step 2 — Create `samconfig.toml`

```toml
version = 0.1

[default.deploy.parameters]
stack_name = "repomind"
resolve_s3 = true
s3_prefix = "repomind"
confirm_changeset = true
capabilities = "CAPABILITY_IAM"
disable_rollback = true
parameter_overrides = "GitHubAppId=\"<your-app-id>\" GitHubInstallationId=\"<your-install-id>\" PushgatewayUrl=\"\" GitHubWebhookSecretArn=\"<arn-from-step-1>\" GroqApiKeyArn=\"<arn-from-step-1>\" OpenAIApiKeyArn=\"<arn-from-step-1>\""
image_repositories = []

[default.global.parameters]
region = "ap-south-1"
```

### Step 3 — Deploy

```powershell
sam build --use-container
sam deploy
```

## Rotating a Secret

No redeploy needed — secrets are fetched at Lambda cold start.

```powershell
aws secretsmanager update-secret `
  --secret-id repomind/openai-api-key `
  --region ap-south-1 `
  --secret-string '<new-key>'
```

Next Lambda cold start will pick up the new value automatically.
