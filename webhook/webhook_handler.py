"""
webhook/webhook_handler.py — FastAPI Webhook Endpoint

HOW IT WORKS:
─────────────
This is the ENTRY POINT of the entire RepoMind pipeline.

1. GitHub sends a POST to /webhook when a workflow_run completes
2. We validate the HMAC-SHA256 signature (reject forgeries)
3. We parse the payload and check if it's a failure event
4. If yes → generate event_id → build SQS message → publish to queue
5. Return 202 Accepted (async processing)

ENDPOINTS:
    POST /webhook  — receives GitHub webhook events
    GET  /health   — health check (for monitoring)

WHAT THIS MODULE DOES NOT DO:
    ❌ Download logs
    ❌ Call LLM
    ❌ Write to S3
    ❌ Any heavy processing

Step 1 is LIGHTWEIGHT by design. All heavy work is in Step 2.

COMMUNICATION:
─────────────
GitHub → POST /webhook → validate → parse → SQS message → Step 2 Worker
                                                    ↑
                                            (this is the handoff)

V2 MULTI-TENANCY:
─────────────────
Every webhook delivered to a GitHub App carries `installation.id` in the
payload. We MUST forward that ID through the SQS message so the worker
can mint an installation token for the *correct* account. Without this,
the worker falls back to the env var `GITHUB_INSTALLATION_ID` and tries
to act on a different account's repos → 403 Forbidden.
"""

from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import JSONResponse

from webhook.models import GitHubWebhookPayload, SQSMessage
from webhook.signature import validate_signature
from webhook.sqs_client import get_queue_client
from shared.config import settings
from shared.event_id import generate_event_id
from shared.logger import get_logger

logger = get_logger("webhook.webhook_handler")

# ──────────────────────────────────────────────
# FastAPI Application
# ──────────────────────────────────────────────
app = FastAPI(
    title="RepoMind Webhook Handler",
    description="Receives GitHub webhook events and queues CI failures for auto-fix",
    version="1.0.0",
)

# Queue client (SQS in prod, local in dev)
queue = get_queue_client()


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────
def _extract_installation_id(raw: dict) -> int:
    """
    Pull `installation.id` out of any GitHub webhook payload.

    GitHub adds an `installation` object to *every* webhook delivered to
    a GitHub App, regardless of event type. We use this ID to mint the
    correct installation token in the worker.

    Returns 0 if not present (will trigger env-var fallback in the worker —
    useful for local dev, fatal for cross-account prod).
    """
    return int((raw.get("installation") or {}).get("id", 0) or 0)


# ──────────────────────────────────────────────
# Health Check
# ──────────────────────────────────────────────
@app.get("/health")
async def health_check():
    """
    Health check endpoint.
    Used by API Gateway, load balancers, and monitoring tools.
    """
    return {
        "status": "healthy",
        "service": "repomind-webhook",
        "environment": settings.ENVIRONMENT,
    }


# ──────────────────────────────────────────────
# Webhook Endpoint
# ──────────────────────────────────────────────
@app.post("/webhook")
async def receive_webhook(request: Request):
    """
    Receive and process GitHub webhook events.

    Flow:
        1. Read raw body (needed for signature validation)
        2. Validate HMAC-SHA256 signature
        3. Parse payload into GitHubWebhookPayload
        4. Check if it's a failed workflow_run
        5. Generate event_id
        6. Build SQSMessage
        7. Publish to queue
        8. Return 202 Accepted

    Returns:
        202: Event accepted and queued for processing
        200: Event received but not actionable (not a failure)
        403: Invalid signature
        400: Malformed payload
    """
    # ── Step 1: Read raw body ──
    body = await request.body()

    # ── Step 2: Validate signature ──
    signature = request.headers.get("X-Hub-Signature-256", "")
    github_event = request.headers.get("X-GitHub-Event", "")

    logger.info("webhook_received", event_type=github_event)

    if settings.GITHUB_WEBHOOK_SECRET:
        if not validate_signature(body, signature, settings.GITHUB_WEBHOOK_SECRET):
            logger.warning("webhook_signature_invalid")
            raise HTTPException(status_code=403, detail="Invalid signature")

    # ── Step 3: Parse payload ──
    try:
        payload_dict = await request.json()
        payload = GitHubWebhookPayload(**payload_dict)
    except Exception as e:
        logger.error("webhook_parse_error", error=str(e))
        raise HTTPException(status_code=400, detail=f"Invalid payload: {str(e)}")

    # ── Step 4: Dispatch by event type ──
    # V2: We now handle four event types beyond just workflow_run:
    #   - workflow_run                → CI failure / verification (legacy path)
    #   - installation                → app installed/uninstalled (welcome PR)
    #   - installation_repositories   → repos added/removed (welcome PR)
    #   - pull_request_review         → human review (HITL resume)

    if github_event == "installation":
        return await _handle_installation(payload, payload_dict)

    if github_event == "installation_repositories":
        return await _handle_installation_repositories(payload, payload_dict)

    if github_event == "pull_request_review":
        return await _handle_pull_request_review(payload, payload_dict)

    if github_event != "workflow_run":
        logger.info("webhook_ignored", reason="unsupported event", event_type=github_event)
        return JSONResponse(
            status_code=200,
            content={"status": "ignored", "reason": f"Event '{github_event}' not handled"},
        )

    # ── Step 4a: Check for fix/* branch verification (Step 10) ──
    wf = payload.workflow_run
    if (
        wf
        and wf.head_branch.startswith("fix/")
        and payload.is_completed_workflow()
    ):
        repo_name = payload.repository.full_name
        event_id = generate_event_id(repo_name, wf.id)

        # V2 multi-tenancy: forward installation.id so the worker mints a
        # token for the right install (verification path).
        installation_id = _extract_installation_id(payload_dict)

        sqs_message = SQSMessage(
            event_id=event_id,
            repo=repo_name,
            workflow_run_id=wf.id,
            run_url=wf.html_url,
            head_branch=wf.head_branch,
            head_sha=wf.head_sha,
            message_type="verification",
            conclusion=wf.conclusion or "",
            installation_id=installation_id,
        )

        logger.info(
            "webhook_verification_routed",
            event_id=event_id,
            repo=repo_name,
            branch=wf.head_branch,
            conclusion=wf.conclusion,
            installation_id=installation_id,
        )

        success = queue.publish(sqs_message.model_dump())
        if not success:
            logger.error("webhook_queue_failed", event_id=event_id)
            raise HTTPException(status_code=500, detail="Failed to queue verification event")

        return JSONResponse(
            status_code=202,
            content={
                "status": "accepted",
                "event_id": event_id,
                "message_type": "verification",
                "message": "Verification event queued for Step 10",
            },
        )

    if not payload.is_failed_workflow():
        logger.info(
            "webhook_ignored",
            reason="not a failure",
            action=payload.action,
            conclusion=payload.workflow_run.conclusion if payload.workflow_run else None,
        )
        return JSONResponse(
            status_code=200,
            content={"status": "ignored", "reason": "Not a failed workflow"},
        )

    # ── Step 5: Generate event ID ──
    wf = payload.workflow_run
    repo_name = payload.repository.full_name
    event_id = generate_event_id(repo_name, wf.id)

    # V2 multi-tenancy: forward installation.id from the webhook payload so
    # the worker can mint an installation token for the correct GitHub App
    # install (otherwise it falls back to env var → 403 on cross-account repos).
    installation_id = _extract_installation_id(payload_dict)

    # ── Step 6: Build SQS message ──
    sqs_message = SQSMessage(
        event_id=event_id,
        repo=repo_name,
        workflow_run_id=wf.id,
        run_url=wf.html_url,
        head_branch=wf.head_branch,
        head_sha=wf.head_sha,
        installation_id=installation_id,
    )

    logger.info(
        "webhook_processing",
        event_id=event_id,
        repo=repo_name,
        run_id=wf.id,
        branch=wf.head_branch,
        installation_id=installation_id,
    )

    # ── Step 7: Publish to queue ──
    success = queue.publish(sqs_message.model_dump())

    if not success:
        logger.error("webhook_queue_failed", event_id=event_id)
        raise HTTPException(status_code=500, detail="Failed to queue event")

    # ── Step 8: Return 202 ──
    logger.info("webhook_accepted", event_id=event_id, installation_id=installation_id)
    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "event_id": event_id,
            "message": "Event queued for processing",
        },
    )


# ──────────────────────────────────────────────
# Ping endpoint (GitHub sends a ping on App install)
# ──────────────────────────────────────────────
@app.post("/webhook/ping")
async def handle_ping():
    """Handle GitHub's ping event sent when webhook is first configured."""
    logger.info("ping_received")
    return {"status": "pong"}


# ──────────────────────────────────────────────
# V2: Installation event handlers
# ──────────────────────────────────────────────
async def _handle_installation(
    payload: GitHubWebhookPayload,
    raw: dict,
) -> JSONResponse:
    """
    Handle a GitHub App `installation` event.

    Triggered when the app is installed or uninstalled on a GitHub account.
    On `created` action with target=repo selection, GitHub also includes
    the list of repos in `repositories`.

    For each newly-installed repo, we queue a "send welcome PR" message.
    """
    action = payload.action  # "created" | "deleted" | "suspend" | "unsuspend"
    if action != "created":
        logger.info("installation_event_ignored", action=action)
        return JSONResponse(
            status_code=200,
            content={"status": "ignored", "reason": f"installation action={action}"},
        )

    installation_raw = raw.get("installation") or {}
    installation_id = int(installation_raw.get("id", 0) or 0)
    repos = raw.get("repositories") or []
    repo_names = [r.get("full_name", "") for r in repos if r.get("full_name")]

    logger.info(
        "installation_created",
        installation_id=installation_id,
        repo_count=len(repo_names),
    )

    queued = 0
    for repo_name in repo_names:
        msg = SQSMessage(
            event_id=f"install-{installation_id}-{repo_name.replace('/', '-')}",
            repo=repo_name,
            workflow_run_id=0,
            run_url="",
            message_type="installation",
            installation_id=installation_id,
            repos_added=[repo_name],
        )
        if queue.publish(msg.model_dump()):
            queued += 1

    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "message_type": "installation",
            "installation_id": installation_id,
            "repos_queued": queued,
        },
    )


async def _handle_installation_repositories(
    payload: GitHubWebhookPayload,
    raw: dict,
) -> JSONResponse:
    """
    Handle `installation_repositories` events.

    Triggered when an existing installation has repos added or removed.
    We only act on `added`.
    """
    action = payload.action  # "added" | "removed"
    if action != "added":
        logger.info("installation_repositories_event_ignored", action=action)
        return JSONResponse(
            status_code=200,
            content={"status": "ignored", "reason": f"action={action}"},
        )

    installation_raw = raw.get("installation") or {}
    installation_id = int(installation_raw.get("id", 0) or 0)
    repos = raw.get("repositories_added") or []
    repo_names = [r.get("full_name", "") for r in repos if r.get("full_name")]

    logger.info(
        "installation_repositories_added",
        installation_id=installation_id,
        repos=repo_names,
    )

    queued = 0
    for repo_name in repo_names:
        msg = SQSMessage(
            event_id=f"install-{installation_id}-{repo_name.replace('/', '-')}",
            repo=repo_name,
            workflow_run_id=0,
            run_url="",
            message_type="installation",
            installation_id=installation_id,
            repos_added=[repo_name],
        )
        if queue.publish(msg.model_dump()):
            queued += 1

    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "message_type": "installation",
            "installation_id": installation_id,
            "repos_queued": queued,
        },
    )


# ──────────────────────────────────────────────
# V2: Pull request review handler (HITL resume)
# ──────────────────────────────────────────────
async def _handle_pull_request_review(
    payload: GitHubWebhookPayload,
    raw: dict,
) -> JSONResponse:
    """
    Handle a GitHub `pull_request_review` event.

    Triggered when a human approves / requests changes / comments on a PR.
    We pass actionable reviews (approved + changes_requested) to review
    via SQS to resume the paused HITL graph.
    """
    action = payload.action  # "submitted" | "edited" | "dismissed"
    if action != "submitted":
        logger.info("pr_review_ignored_action", action=action)
        return JSONResponse(
            status_code=200,
            content={"status": "ignored", "reason": f"action={action}"},
        )

    review_raw = raw.get("review") or {}
    pr_raw = raw.get("pull_request") or {}
    repo_raw = raw.get("repository") or {}

    review_state = review_raw.get("state", "")
    if review_state not in ("approved", "changes_requested"):
        logger.info("pr_review_ignored_state", state=review_state)
        return JSONResponse(
            status_code=200,
            content={"status": "ignored", "reason": f"state={review_state}"},
        )

    repo_name = repo_raw.get("full_name", "")
    pr_number = pr_raw.get("number", 0)
    if not (repo_name and pr_number):
        logger.warning("pr_review_missing_context")
        return JSONResponse(
            status_code=400,
            content={"status": "error", "reason": "Missing repo or PR number"},
        )

    # V2 multi-tenancy: forward installation.id for the review resume path too.
    installation_id = _extract_installation_id(raw)

    # Build the SQS message — review will resolve event_id from PR number
    msg = SQSMessage(
        event_id="",  # review looks it up from repo + pr_number
        repo=repo_name,
        workflow_run_id=0,
        run_url="",
        head_sha=(pr_raw.get("head") or {}).get("sha", ""),
        message_type="review",
        pr_number=pr_number,
        pr_url=pr_raw.get("html_url", ""),
        review_id=review_raw.get("id", 0),
        review_state=review_state,
        review_body=review_raw.get("body") or "",
        reviewer=(review_raw.get("user") or {}).get("login", ""),
        installation_id=installation_id,
    )

    success = queue.publish(msg.model_dump())
    if not success:
        logger.error("review_queue_failed", repo=repo_name, pr_number=pr_number)
        raise HTTPException(status_code=500, detail="Failed to queue review event")

    logger.info(
        "review_queued",
        repo=repo_name,
        pr_number=pr_number,
        review_state=review_state,
        installation_id=installation_id,
    )
    return JSONResponse(
        status_code=202,
        content={
            "status": "accepted",
            "message_type": "review",
            "repo": repo_name,
            "pr_number": pr_number,
            "review_state": review_state,
        },
    )