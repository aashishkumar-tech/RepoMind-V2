"""
shared/llm_observability.py — LLM Observability Layer (Tier 2)

──────────────────────────────────────────────
WHY THIS EXISTS
──────────────────────────────────────────────
Before this module: every LLM call (`client.chat.completions.create(...)`) was
a black box — we threw away `response.usage`, didn't know which agent burned
the most tokens, couldn't compute cost per fix, and couldn't tell if a slow
event was due to the LLM or the rest of the pipeline.

After this module: every LLM call is instrumented. We capture:
    • prompt_tokens, completion_tokens, total_tokens
    • latency_ms (start → end)
    • cost_usd (computed from current Azure pricing)
    • success / error_type
    • response_id (for correlation with provider logs)
    • prompt_hash (for prompt versioning / A-B testing)
    • agent_name (triage / planner / solver / validator)

These are pushed to:
    1. structlog JSON logs (always)
    2. Prometheus metrics (if METRICS_ENABLED=true)
    3. State["llm_traces"] list (consumed by dashboard)

──────────────────────────────────────────────
USAGE — One-liner replacement
──────────────────────────────────────────────
Before:
    response = client.chat.completions.create(model=m, messages=msgs, ...)

After:
    from shared.llm_observability import traced_completion
    response, trace = traced_completion(
        client, model=m, messages=msgs, agent="triage", event_id=evt_id, ...
    )
    # response is the same OpenAI object, untouched
    # trace is a dict with all the metrics

──────────────────────────────────────────────
COST MODEL — Azure OpenAI Pay-As-You-Go (June 2026)
──────────────────────────────────────────────
Numbers below are USD per 1M tokens. Update as Azure pricing changes.
Falls back to gpt-4o pricing for any unknown model.
"""

from __future__ import annotations

import time
import hashlib
from typing import Any, Dict, List, Optional, Tuple

from shared.logger import get_logger

logger = get_logger("shared.llm_observability")


# ──────────────────────────────────────────────
# Pricing table (USD per 1M tokens) — Azure OpenAI as of June 2026
# Update when Microsoft re-prices.
# ──────────────────────────────────────────────
PRICING_PER_1M_TOKENS = {
    # Azure OpenAI deployments
    "gpt-4o":              {"prompt": 2.50, "completion": 10.00},
    "gpt-4o-mini":         {"prompt": 0.15, "completion": 0.60},
    "gpt-4-turbo":         {"prompt": 10.00, "completion": 30.00},
    "gpt-35-turbo":        {"prompt": 0.50, "completion": 1.50},
    "gpt-3.5-turbo":       {"prompt": 0.50, "completion": 1.50},
    # Groq fallbacks (free at time of writing — set zero)
    "llama-3.3-70b-versatile":      {"prompt": 0.0, "completion": 0.0},
    "openai/gpt-oss-120b":          {"prompt": 0.0, "completion": 0.0},
    "llama-3.1-70b-versatile":      {"prompt": 0.0, "completion": 0.0},
}

DEFAULT_PRICING = {"prompt": 2.50, "completion": 10.00}  # gpt-4o


def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    """
    Estimate USD cost for a single LLM call.

    Args:
        model: Model name (e.g. "gpt-4o" or Azure deployment name)
        prompt_tokens: Input token count from response.usage
        completion_tokens: Output token count from response.usage

    Returns:
        Estimated cost in USD (rounded to 6 decimal places)
    """
    pricing = PRICING_PER_1M_TOKENS.get(model.lower(), DEFAULT_PRICING)
    prompt_cost = (prompt_tokens / 1_000_000) * pricing["prompt"]
    completion_cost = (completion_tokens / 1_000_000) * pricing["completion"]
    return round(prompt_cost + completion_cost, 6)


def hash_prompt(messages: List[Dict[str, Any]]) -> str:
    """
    Produce a short stable hash of the prompt for versioning.

    Used to detect when prompts change between releases — useful for
    A/B testing prompt revisions.
    """
    # Concatenate role + content from all messages
    raw = "".join(f"{m.get('role', '')}:{m.get('content', '')}" for m in messages)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def traced_completion(
    client: Any,
    *,
    model: str,
    messages: List[Dict[str, Any]],
    agent: str,
    event_id: str = "",
    repo: str = "",
    **kwargs: Any,
) -> Tuple[Any, Dict[str, Any]]:
    """
    Call client.chat.completions.create with full observability.

    Wraps the raw LLM call to capture tokens, latency, cost, errors.
    The response object is returned unchanged — drop-in replacement.

    Args:
        client: OpenAI-compatible client (Azure OpenAI, Groq, etc.)
        model: Model name / deployment name
        messages: Chat messages (list of {role, content})
        agent: Logical agent name — "triage", "planner", "solver",
               "validator", "judge", or any string
        event_id: Pipeline event ID for correlation (optional)
        repo: GitHub repo for label cardinality (optional)
        **kwargs: Forwarded verbatim to chat.completions.create()

    Returns:
        (response, trace) where:
          response = the raw OpenAI ChatCompletion object
          trace = {
              "agent": str, "model": str, "event_id": str, "repo": str,
              "prompt_tokens": int, "completion_tokens": int, "total_tokens": int,
              "latency_ms": float, "cost_usd": float,
              "success": bool, "error_type": str | None,
              "response_id": str | None, "prompt_hash": str,
              "started_at": float, "ended_at": float,
          }

    Raises:
        Re-raises whatever the underlying client raises (callers handle errors).
    """
    started_at = time.time()
    prompt_hash = hash_prompt(messages)

    trace: Dict[str, Any] = {
        "agent": agent,
        "model": model,
        "event_id": event_id,
        "repo": repo,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "latency_ms": 0.0,
        "cost_usd": 0.0,
        "success": False,
        "error_type": None,
        "response_id": None,
        "prompt_hash": prompt_hash,
        "started_at": started_at,
        "ended_at": 0.0,
    }

    response = None
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            **kwargs,
        )
        trace["success"] = True
    except Exception as e:
        trace["success"] = False
        trace["error_type"] = type(e).__name__
        _record_metrics(trace)
        _log_trace(trace, level="error", error=str(e)[:300])
        raise
    finally:
        ended_at = time.time()
        trace["ended_at"] = ended_at
        trace["latency_ms"] = round((ended_at - started_at) * 1000, 2)

    # Extract usage on success
    if response is not None:
        usage = getattr(response, "usage", None)
        if usage is not None:
            trace["prompt_tokens"] = int(getattr(usage, "prompt_tokens", 0) or 0)
            trace["completion_tokens"] = int(getattr(usage, "completion_tokens", 0) or 0)
            trace["total_tokens"] = int(getattr(usage, "total_tokens", 0) or 0)
        trace["response_id"] = getattr(response, "id", None)
        trace["cost_usd"] = estimate_cost_usd(
            model, trace["prompt_tokens"], trace["completion_tokens"]
        )

    _record_metrics(trace)
    _log_trace(trace, level="info")

    return response, trace


def attach_trace(state: Dict[str, Any], trace: Dict[str, Any]) -> None:
    """
    Append a trace to state["llm_traces"] (creating the list if needed).

    Call this from each agent node after traced_completion to build up
    a per-event audit trail. The dashboard reads state["llm_traces"]
    to show "tokens per agent" stacked bars.
    """
    if "llm_traces" not in state or not isinstance(state["llm_traces"], list):
        state["llm_traces"] = []
    state["llm_traces"].append(_trace_for_state(trace))


def summarize_traces(traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Summarize a list of traces into per-event totals.

    Returns:
        {
          "total_calls": int,
          "successful_calls": int,
          "failed_calls": int,
          "total_tokens": int,
          "prompt_tokens": int,
          "completion_tokens": int,
          "total_cost_usd": float,
          "total_latency_ms": float,
          "by_agent": { "<agent>": {...same shape...}, ... },
        }
    """
    summary: Dict[str, Any] = {
        "total_calls": 0,
        "successful_calls": 0,
        "failed_calls": 0,
        "total_tokens": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_cost_usd": 0.0,
        "total_latency_ms": 0.0,
        "by_agent": {},
    }

    for t in traces or []:
        agent = t.get("agent", "unknown")
        summary["total_calls"] += 1
        if t.get("success"):
            summary["successful_calls"] += 1
        else:
            summary["failed_calls"] += 1
        summary["total_tokens"] += t.get("total_tokens", 0)
        summary["prompt_tokens"] += t.get("prompt_tokens", 0)
        summary["completion_tokens"] += t.get("completion_tokens", 0)
        summary["total_cost_usd"] += t.get("cost_usd", 0.0)
        summary["total_latency_ms"] += t.get("latency_ms", 0.0)

        by = summary["by_agent"].setdefault(agent, {
            "calls": 0, "successful_calls": 0, "failed_calls": 0,
            "total_tokens": 0, "prompt_tokens": 0, "completion_tokens": 0,
            "cost_usd": 0.0, "latency_ms": 0.0,
        })
        by["calls"] += 1
        by["successful_calls"] += 1 if t.get("success") else 0
        by["failed_calls"] += 0 if t.get("success") else 1
        by["total_tokens"] += t.get("total_tokens", 0)
        by["prompt_tokens"] += t.get("prompt_tokens", 0)
        by["completion_tokens"] += t.get("completion_tokens", 0)
        by["cost_usd"] += t.get("cost_usd", 0.0)
        by["latency_ms"] += t.get("latency_ms", 0.0)

    # Round float fields for cleaner JSON
    summary["total_cost_usd"] = round(summary["total_cost_usd"], 6)
    summary["total_latency_ms"] = round(summary["total_latency_ms"], 2)
    for by in summary["by_agent"].values():
        by["cost_usd"] = round(by["cost_usd"], 6)
        by["latency_ms"] = round(by["latency_ms"], 2)

    return summary


# ──────────────────────────────────────────────
# Internal helpers
# ──────────────────────────────────────────────
def _trace_for_state(trace: Dict[str, Any]) -> Dict[str, Any]:
    """Slim down a trace before storing in pipeline state (drop large fields)."""
    return {
        "agent": trace.get("agent"),
        "model": trace.get("model"),
        "prompt_tokens": trace.get("prompt_tokens", 0),
        "completion_tokens": trace.get("completion_tokens", 0),
        "total_tokens": trace.get("total_tokens", 0),
        "latency_ms": trace.get("latency_ms", 0.0),
        "cost_usd": trace.get("cost_usd", 0.0),
        "success": trace.get("success", False),
        "error_type": trace.get("error_type"),
        "prompt_hash": trace.get("prompt_hash"),
    }


def _record_metrics(trace: Dict[str, Any]) -> None:
    """Push trace fields to Prometheus (no-op if metrics disabled)."""
    try:
        from observability.metrics import metrics

        agent = trace.get("agent", "unknown")
        model = trace.get("model", "unknown")
        repo = trace.get("repo", "") or "unknown"
        status = "success" if trace.get("success") else "error"

        # Counter: total LLM calls
        metrics.llm_calls_total.labels(agent=agent, model=model, status=status).inc()

        # Counter: total tokens (separate prompt vs completion for cost analysis)
        if trace.get("prompt_tokens", 0) > 0:
            metrics.llm_tokens_total.labels(agent=agent, model=model, type="prompt").inc(
                trace["prompt_tokens"]
            )
        if trace.get("completion_tokens", 0) > 0:
            metrics.llm_tokens_total.labels(agent=agent, model=model, type="completion").inc(
                trace["completion_tokens"]
            )

        # Histogram: latency distribution
        if trace.get("latency_ms", 0) > 0:
            metrics.llm_latency_seconds.labels(agent=agent, model=model).observe(
                trace["latency_ms"] / 1000.0
            )

        # Counter: cumulative USD cost
        if trace.get("cost_usd", 0) > 0:
            metrics.llm_cost_usd_total.labels(agent=agent, model=model, repo=repo).inc(
                trace["cost_usd"]
            )
    except Exception as e:
        # Never let metrics break the pipeline
        logger.debug("llm_metrics_record_failed", error=str(e))


def _log_trace(trace: Dict[str, Any], *, level: str = "info", error: Optional[str] = None) -> None:
    """Emit a structured log line with the trace payload."""
    fields = {
        "event_type": "llm_call",
        "agent": trace.get("agent"),
        "model": trace.get("model"),
        "event_id": trace.get("event_id"),
        "prompt_tokens": trace.get("prompt_tokens", 0),
        "completion_tokens": trace.get("completion_tokens", 0),
        "total_tokens": trace.get("total_tokens", 0),
        "latency_ms": trace.get("latency_ms", 0),
        "cost_usd": trace.get("cost_usd", 0),
        "success": trace.get("success", False),
        "prompt_hash": trace.get("prompt_hash"),
        "response_id": trace.get("response_id"),
    }
    if error:
        fields["error"] = error
    if level == "error":
        logger.error("llm_call", **fields)
    else:
        logger.info("llm_call", **fields)
