"""
agents/llm_judge.py — LLM-as-Judge Evaluator (Tier 2 LLM Observability)

──────────────────────────────────────────────
WHAT IS LLM-AS-JUDGE?
──────────────────────────────────────────────
Use a separate LLM call to evaluate the *quality* of a previous LLM's output.
This is now standard practice for production AI systems (Anthropic, OpenAI,
Google all use it internally) — it catches hallucinations, off-topic answers,
and confidence/correctness mismatches that simple metrics can't detect.

──────────────────────────────────────────────
WHAT WE JUDGE
──────────────────────────────────────────────
For now, this module judges the **triage agent**. We score:
    • factuality_score    → Are the claims grounded in the actual log excerpt?
    • completeness_score  → Did triage identify all symptoms / signals in the log?
    • hallucination_flag  → Did triage invent files / packages / errors not present?
    • confidence_calibration → Is triage.confidence aligned with actual evidence?
    • overall_grade       → A/B/C/D/F — letter grade for the whole verdict

Future: extend with judge_plan(), judge_solver(), judge_validator() as needed.

──────────────────────────────────────────────
DESIGN — Why a separate file
──────────────────────────────────────────────
- Cleanly separated from agent logic (one place to tune the judge prompt)
- Easy to A/B test with/without judge
- Can be skipped when LLM_JUDGE_ENABLED=false (cost control)
- Failure of judge never breaks the pipeline (best-effort)

──────────────────────────────────────────────
COST
──────────────────────────────────────────────
1 extra LLM call per event (~600 tokens). At gpt-4o pricing that's ~$0.005
per event. We run it inside _attach_rag_report after the pipeline completes
so it doesn't block PR creation.

──────────────────────────────────────────────
COMMUNICATION
──────────────────────────────────────────────
Called by:
    agents/graph.py _run_llm_judge() → judge_triage()
Emits to:
    structlog (always), Prometheus llm_judge_score gauge (if enabled)
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from shared.config import settings
from shared.logger import get_logger

logger = get_logger("agents.llm_judge")


JUDGE_SYSTEM_PROMPT = """You are a meticulous AI quality auditor evaluating an
automated CI failure triage agent. Your job is to grade the triage output
strictly against the actual CI log excerpt — NOT against your own ideas of
what the failure could be.

Score each dimension on a 0.0–1.0 scale:
  • factuality       → Are all claims (failure_type, affected_file, affected_package,
                       summary) directly supported by content in the log excerpt?
                       Penalize fabricated details.
  • completeness     → Did triage capture the most important signal in the log?
                       Penalize missing the obvious root cause.
  • confidence_calibration → Is triage.confidence aligned with how clear the
                       evidence in the log is? (Penalize over- or under-confidence.)

Set hallucination_flag = true if ANY claim is invented (file not in log, package
not in log, error not in log).

Respond ONLY with valid JSON in this exact shape:
{
  "factuality_score": 0.0,
  "completeness_score": 0.0,
  "confidence_calibration": 0.0,
  "hallucination_flag": false,
  "issues": ["list of specific issues, empty if clean"],
  "overall_score": 0.0,
  "overall_grade": "A|B|C|D|F",
  "verdict_summary": "one line"
}"""


JUDGE_USER_TEMPLATE = """CI LOG EXCERPT (ground truth):
```
{excerpt}
```

TRIAGE AGENT OUTPUT:
- failure_type: {failure_type}
- confidence: {confidence}
- affected_file: {affected_file}
- affected_package: {affected_package}
- summary: {summary}

Grade the triage output against the log excerpt. Be strict."""


def judge_triage(
    triage: Dict[str, Any],
    excerpt: str,
    event_id: str = "",
) -> Dict[str, Any]:
    """
    Run LLM-as-judge on a triage output.

    Args:
        triage: The triage agent's output dict
        excerpt: The original CI log excerpt (ground truth)
        event_id: Pipeline event ID for correlation

    Returns:
        Dict with judge scores, or empty dict if judge is disabled/fails.
    """
    if not _is_enabled():
        logger.debug("llm_judge_disabled")
        return {}

    if not triage or not excerpt:
        return {}

    try:
        from shared.azure_llm import get_llm_client, get_model_name
        from shared.llm_observability import traced_completion

        client = get_llm_client()
        model = get_model_name()

        # Truncate excerpt to control token cost
        excerpt_capped = excerpt[-4000:] if len(excerpt) > 4000 else excerpt

        user_msg = JUDGE_USER_TEMPLATE.format(
            excerpt=excerpt_capped,
            failure_type=triage.get("failure_type", "unknown"),
            confidence=triage.get("confidence", 0),
            affected_file=triage.get("affected_file", "unknown"),
            affected_package=triage.get("affected_package", "unknown"),
            summary=triage.get("summary", ""),
        )

        response, _trace = traced_completion(
            client,
            model=model,
            messages=[
                {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            agent="judge",
            event_id=event_id,
            temperature=0.0,
            max_tokens=400,
            response_format={"type": "json_object"},
        )

        verdict = json.loads(response.choices[0].message.content.strip())
        verdict = _normalize(verdict)
        verdict["judged_agent"] = "triage"

        _emit_metrics(verdict, judged_agent="triage", model=model)

        logger.info(
            "llm_judge_complete",
            event_id=event_id,
            grade=verdict.get("overall_grade", "?"),
            score=verdict.get("overall_score", 0),
            hallucination=verdict.get("hallucination_flag", False),
        )
        return verdict

    except Exception as e:
        logger.warning("llm_judge_failed", event_id=event_id, error=str(e)[:200])
        return {}


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────
def _is_enabled() -> bool:
    """Check if LLM-as-judge should run (default ON; set LLM_JUDGE_ENABLED=false to disable)."""
    raw = getattr(settings, "LLM_JUDGE_ENABLED", "true")
    return str(raw).lower() != "false"


def _normalize(verdict: Dict[str, Any]) -> Dict[str, Any]:
    """
    Clean / clip / fill missing fields so the verdict has a stable shape.
    """
    def _clip(v: Any, lo: float = 0.0, hi: float = 1.0) -> float:
        try:
            return max(lo, min(hi, float(v)))
        except Exception:
            return 0.0

    factuality = _clip(verdict.get("factuality_score", 0.0))
    completeness = _clip(verdict.get("completeness_score", 0.0))
    calibration = _clip(verdict.get("confidence_calibration", 0.0))

    # Compute overall_score if missing or out-of-range
    raw_overall = verdict.get("overall_score")
    try:
        overall = max(0.0, min(1.0, float(raw_overall)))
    except (TypeError, ValueError):
        overall = (factuality * 0.45) + (completeness * 0.35) + (calibration * 0.20)
        overall = round(overall, 4)

    # Compute letter grade
    if overall >= 0.85:
        letter = "A"
    elif overall >= 0.70:
        letter = "B"
    elif overall >= 0.55:
        letter = "C"
    elif overall >= 0.40:
        letter = "D"
    else:
        letter = "F"

    halluc = bool(verdict.get("hallucination_flag", False))
    issues = verdict.get("issues") or []
    if not isinstance(issues, list):
        issues = [str(issues)]

    return {
        "factuality_score": round(factuality, 4),
        "completeness_score": round(completeness, 4),
        "confidence_calibration": round(calibration, 4),
        "hallucination_flag": halluc,
        "issues": issues,
        "overall_score": round(overall, 4),
        "overall_grade": verdict.get("overall_grade") if verdict.get("overall_grade") in {"A", "B", "C", "D", "F"} else letter,
        "verdict_summary": str(verdict.get("verdict_summary", ""))[:300],
    }


def _emit_metrics(verdict: Dict[str, Any], *, judged_agent: str, model: str) -> None:
    """Push judge scores to Prometheus (no-op if metrics disabled)."""
    try:
        from observability.metrics import metrics

        metrics.llm_judge_score.labels(
            agent="judge", judged_agent=judged_agent, metric="factuality"
        ).set(verdict.get("factuality_score", 0))
        metrics.llm_judge_score.labels(
            agent="judge", judged_agent=judged_agent, metric="completeness"
        ).set(verdict.get("completeness_score", 0))
        metrics.llm_judge_score.labels(
            agent="judge", judged_agent=judged_agent, metric="calibration"
        ).set(verdict.get("confidence_calibration", 0))
        metrics.llm_judge_score.labels(
            agent="judge", judged_agent=judged_agent, metric="overall"
        ).set(verdict.get("overall_score", 0))

        if verdict.get("hallucination_flag"):
            metrics.llm_hallucinations_total.labels(
                judged_agent=judged_agent, model=model
            ).inc()
    except Exception as e:
        logger.debug("llm_judge_metrics_failed", error=str(e))
