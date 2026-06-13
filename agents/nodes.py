"""
agents/nodes.py — LangGraph Node Functions

HOW IT WORKS:
─────────────
Each function here is a "node" in the LangGraph directed graph.
A node receives the current pipeline state and returns a partial update.

NODE PATTERN:
    def my_node(state: PipelineState) -> dict:
        # Read from state
        data = state["some_field"]
        # Do work
        result = process(data)
        # Return partial update (LangGraph merges it into state)
        return {"result_field": result}

NODES IN THIS PIPELINE:
    1. evidence_node   → Retrieves similar past incidents from Qdrant
    2. triage_node     → Classifies the failure type using LLM
    3. planner_node    → Generates a fix plan / selects playbook
    4. policy_node     → Evaluates policy rules (allow/deny)

IMPORTANT:
    - Nodes are STATELESS functions (no self, no side effects beyond returns)
    - Each node is independently testable
    - Nodes communicate ONLY through the shared state dict
    - If a node fails, it sets state["error"] and state["status"] = "failed"

COMMUNICATION FLOW (through state):
    evidence_node: reads excerpt → writes similar_incidents
    triage_node:   reads excerpt, similar_incidents → writes triage
    planner_node:  reads excerpt, triage → writes plan_summary
    policy_node:   reads triage, plan_summary → writes policy, status
"""

from typing import Dict, Any, Optional

from shared.logger import get_logger

logger = get_logger("agents.nodes")


# ──────────────────────────────────────────────
# Node 1: Evidence Retrieval (Step 3)
# ──────────────────────────────────────────────
def evidence_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Retrieve similar past failures from Qdrant for RAG context.

    Reads: state["excerpt"], state["repo"]
    Writes: state["similar_incidents"], state["_rag_retrieval_ms"]

    If Qdrant is unavailable, returns empty list (non-fatal).
    Records retrieval latency for RAG evaluation metrics.
    """
    import time as _time

    excerpt = state.get("excerpt", "")
    repo = state.get("repo", "")
    event_id = state.get("event_id", "")

    logger.info("evidence_node_start", event_id=event_id)

    retrieval_start = _time.perf_counter()
    try:
        from rag.retriever import Retriever
        retriever = Retriever()
        similar = retriever.search_similar_failures(
            excerpt=excerpt,
            repo=repo,
            top_k=3,
        )
        retrieval_ms = (_time.perf_counter() - retrieval_start) * 1000

        # Evaluate retrieval quality
        try:
            from rag.rag_metrics import RAGEvaluator
            evaluator = RAGEvaluator()
            retrieval_metrics = evaluator.evaluate_retrieval(
                query_text=excerpt,
                results=similar,
                top_k_requested=3,
                latency_ms=retrieval_ms,
            )
            context_metrics = evaluator.evaluate_context_quality(
                query_text=excerpt,
                results=similar,
            )
            logger.info(
                "rag_retrieval_evaluated",
                event_id=event_id,
                hit_rate=retrieval_metrics["hit_rate"],
                mean_sim=retrieval_metrics["mean_similarity"],
                mrr=retrieval_metrics["mrr"],
                diversity=context_metrics["context_diversity"],
            )
        except Exception as me:
            logger.debug("rag_metrics_skipped", reason=str(me))
            retrieval_metrics = {}
            context_metrics = {}

        logger.info(
            "evidence_node_complete",
            event_id=event_id,
            matches=len(similar),
            retrieval_ms=round(retrieval_ms, 2),
        )
        return {
            "similar_incidents": similar,
            "_rag_retrieval_ms": retrieval_ms,
            "_rag_retrieval_metrics": retrieval_metrics,
            "_rag_context_metrics": context_metrics,
        }

    except Exception as e:
        retrieval_ms = (_time.perf_counter() - retrieval_start) * 1000
        logger.warning("evidence_node_failed", event_id=event_id, error=str(e))
        # Non-fatal: proceed without RAG context
        return {
            "similar_incidents": [],
            "_rag_retrieval_ms": retrieval_ms,
            "_rag_retrieval_metrics": {},
            "_rag_context_metrics": {},
        }


# ──────────────────────────────────────────────
# Node 2: Triage (Step 5)
# ──────────────────────────────────────────────
def triage_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Classify the CI failure using LLM + past incident context.

    Reads: state["excerpt"], state["repo"], state["similar_incidents"]
    Writes: state["triage"], state["_rag_generation_metrics"]

    If triage fails completely, sets status to "failed".
    Evaluates RAG generation impact after classification.
    """
    excerpt = state.get("excerpt", "")
    repo = state.get("repo", "")
    event_id = state.get("event_id", "")
    similar_incidents = state.get("similar_incidents", [])

    logger.info("triage_node_start", event_id=event_id)

    try:
        from triage.triage import TriageEngine
        engine = TriageEngine()
        triage = engine.classify(
            excerpt=excerpt,
            repo=repo,
            similar_incidents=similar_incidents,
        )

        # Evaluate RAG generation impact (did retrieval help triage?)
        rag_generation_metrics = {}
        try:
            from rag.rag_metrics import RAGEvaluator
            evaluator = RAGEvaluator()
            rag_generation_metrics = evaluator.evaluate_generation_impact(
                query_text=excerpt,
                retrieved_contexts=similar_incidents,
                triage_result=triage,
            )
            logger.info(
                "rag_generation_evaluated",
                event_id=event_id,
                rag_value=rag_generation_metrics.get("rag_value_score", 0),
                grounding_rate=rag_generation_metrics.get("grounding_rate", 0),
                type_aligned=rag_generation_metrics.get("type_aligned_with_context", False),
            )
        except Exception as me:
            logger.debug("rag_generation_metrics_skipped", reason=str(me))

        logger.info(
            "triage_node_complete",
            event_id=event_id,
            failure_type=triage.get("failure_type"),
            confidence=triage.get("confidence"),
        )
        return {
            "triage": triage,
            "_rag_generation_metrics": rag_generation_metrics,
        }

    except Exception as e:
        logger.error("triage_node_failed", event_id=event_id, error=str(e))
        return {
            "triage": {
                "failure_type": "unknown",
                "confidence": 0.0,
                "summary": f"Triage failed: {str(e)}",
            },
            "error": f"Triage failed: {str(e)}",
            "status": "failed",
        }


# ──────────────────────────────────────────────
# Node 3: Plan Generation (Step 6)
# ──────────────────────────────────────────────
def planner_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate a fix plan based on triage results.

    Reads: state["triage"], state["excerpt"], state["repo"]
    Writes: state["plan_summary"]

    Selects a playbook and generates actions.
    """
    triage = state.get("triage", {})
    excerpt = state.get("excerpt", "")
    repo = state.get("repo", "")
    event_id = state.get("event_id", "")

    logger.info("planner_node_start", event_id=event_id)

    try:
        from planner.planner import Planner
        planner = Planner()
        plan = planner.generate_plan(
            triage=triage,
            excerpt=excerpt,
            repo=repo,
            similar_incidents=state.get("similar_incidents", []),
        )

        logger.info(
            "planner_node_complete",
            event_id=event_id,
            playbook_id=plan.get("playbook_id"),
        )
        return {"plan_summary": plan}

    except Exception as e:
        logger.error("planner_node_failed", event_id=event_id, error=str(e))
        return {
            "plan_summary": {
                "playbook_id": "unknown",
                "actions": [],
                "error": str(e),
            },
            "error": f"Planning failed: {str(e)}",
            "status": "failed",
        }


# ──────────────────────────────────────────────
# Node 3b: Solver Agent (HYBRID — Deep Agent + Direct-LLM fallback)
# ──────────────────────────────────────────────
def solver_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate actual code changes for the fix.

    HYBRID STRATEGY:
      1. Try the Deep Agent solver (reads repo files, uses sub-agents, self-corrects)
      2. On timeout/error → fall back to direct Azure GPT-4o call (fast, always works)

    Reads:  state["triage"], state["plan_summary"], state["excerpt"], state["repo"],
            state["head_sha"], state["similar_incidents"]
    Writes: state["plan_summary"] enriched with code_changes, solver_reasoning,
            solver_confidence, solver_mode ("deep_agent" or "direct_llm")
    """
    triage = state.get("triage", {})
    plan = state.get("plan_summary", {})
    excerpt = state.get("excerpt", "")
    repo = state.get("repo", "")
    event_id = state.get("event_id", "")
    similar_incidents = state.get("similar_incidents", [])
    head_sha = state.get("head_sha") or state.get("head_branch") or "main"

    logger.info("solver_node_start", event_id=event_id)

    # Skip if plan already has specific code changes (planner template won)
    existing_changes = plan.get("code_changes", [])
    if existing_changes and len(existing_changes) > 0 and existing_changes[0].get("new_content"):
        logger.info("solver_node_skipped", reason="planner already provided code_changes")
        return {"plan_summary": plan}

    # ── Tier 1: Deep Agent solver (rich, slow, accurate) ──
    result = _try_deep_solver(
        repo=repo,
        ref=head_sha,
        triage=triage,
        plan=plan,
        excerpt=excerpt,
        similar_incidents=similar_incidents,
        event_id=event_id,
    )

    # ── Tier 2: Direct LLM fallback (fast, always works) ──
    if result is None:
        logger.warning("solver_falling_back_to_direct_llm", event_id=event_id)
        result = _direct_llm_solver(
            repo=repo,
            triage=triage,
            plan=plan,
            excerpt=excerpt,
            event_id=event_id,
        )

    # Merge result into plan_summary
    if result and result.get("code_changes"):
        plan["code_changes"] = result["code_changes"]
        plan["solver_reasoning"] = result.get("reasoning", "")
        plan["solver_confidence"] = result.get("confidence", 0.7)
        plan["solver_mode"] = result.get("mode", "direct_llm")
        if result.get("files_inspected"):
            plan["solver_files_inspected"] = result["files_inspected"]

    logger.info(
        "solver_node_complete",
        event_id=event_id,
        mode=plan.get("solver_mode", "unknown"),
        changes=len(plan.get("code_changes", [])),
        confidence=plan.get("solver_confidence", 0),
    )
    return {"plan_summary": plan}


# ──────────────────────────────────────────────
# Tier 1: Deep Agent attempt (returns None on any failure)
# ──────────────────────────────────────────────
def _try_deep_solver(
    repo: str,
    ref: str,
    triage: Dict[str, Any],
    plan: Dict[str, Any],
    excerpt: str,
    similar_incidents: list,
    event_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Attempt the deep agent solver. Returns the parsed result on success,
    or None on any failure (caller falls back to direct LLM).
    """
    try:
        from agents.deep_solver import run_deep_solver
    except ImportError as e:
        logger.warning("deep_solver_unavailable", error=str(e))
        return None

    try:
        result = run_deep_solver(
            repo=repo,
            ref=ref,
            triage=triage,
            plan=plan,
            excerpt=excerpt,
            similar_incidents=similar_incidents,
        )
        # Only return result if it produced actionable changes
        if result.get("code_changes"):
            return result
        logger.info(
            "deep_solver_returned_empty",
            event_id=event_id,
            reasoning=result.get("reasoning", "")[:200],
        )
        return None
    except TimeoutError as e:
        logger.warning("deep_solver_timeout", event_id=event_id, error=str(e))
        return None
    except Exception as e:
        logger.warning("deep_solver_failed", event_id=event_id, error=str(e)[:300])
        return None


# ──────────────────────────────────────────────
# Tier 2: Direct LLM fallback (the previous solver behavior)
# ──────────────────────────────────────────────
def _direct_llm_solver(
    repo: str,
    triage: Dict[str, Any],
    plan: Dict[str, Any],
    excerpt: str,
    event_id: str,
) -> Optional[Dict[str, Any]]:
    """
    Single-shot LLM call to generate code changes. Used when the deep agent
    times out, errors, or returns no actionable changes.
    """
    SOLVER_SYSTEM = """You are an expert software engineer fixing CI failures.
Given a failure analysis and fix plan, generate the EXACT code changes needed.
Use chain-of-thought: think through the root cause, then write the precise fix.

Respond ONLY with valid JSON:
{
    "reasoning": "step-by-step explanation of the fix",
    "code_changes": [
        {
            "file": "path/to/file",
            "action": "create|modify|delete",
            "description": "what this change does",
            "old_content": "exact string to find (for modify)",
            "new_content": "exact replacement or full new file content"
        }
    ],
    "confidence": 0.0 to 1.0,
    "risk_assessment": "low|medium|high"
}"""

    SOLVER_USER = """Repository: {repo}
Failure Type: {failure_type}
Summary: {summary}
Affected File: {affected_file}
Affected Package: {affected_package}

Fix Plan: {plan_description}
Planned Actions: {actions}

Log Excerpt:
```
{excerpt}
```

Generate the exact code changes to fix this failure. Be specific — old_content must be the exact string to find in the file."""

    try:
        from shared.azure_llm import get_llm_client, get_model_name
        from shared.llm_observability import traced_completion
        client = get_llm_client()
        model = get_model_name()

        user_prompt = SOLVER_USER.format(
            repo=repo,
            failure_type=triage.get("failure_type", "unknown"),
            summary=triage.get("summary", ""),
            affected_file=triage.get("affected_file", "unknown"),
            affected_package=triage.get("affected_package", "unknown"),
            plan_description=plan.get("description", ""),
            actions="\n".join(plan.get("actions", [])),
            excerpt=excerpt[-4000:] if len(excerpt) > 4000 else excerpt,
        )

        response, trace = traced_completion(
            client,
            model=model,
            messages=[
                {"role": "system", "content": SOLVER_SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            agent="solver",
            event_id=event_id,
            repo=repo,
            temperature=0.15,
            max_tokens=2000,
            response_format={"type": "json_object"},
        )

        import json
        result = json.loads(response.choices[0].message.content.strip())
        result["mode"] = "direct_llm"
        result["_llm_trace"] = trace
        return result

    except Exception as e:
        logger.error("direct_llm_solver_failed", event_id=event_id, error=str(e))
        return None


# ──────────────────────────────────────────────
# Node 3c: Validator Agent (peer review with retry routing)
# ──────────────────────────────────────────────
def validator_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Review the proposed fix and approve or reject it.

    Reads: state["plan_summary"], state["triage"], state["excerpt"]
    Writes: state["validation"], state["validation_attempts"]

    If rejected, routes back to solver_node (max 2 retries).
    This is the key agent-loop behaviour for the swarm.
    """
    plan = state.get("plan_summary", {})
    triage = state.get("triage", {})
    excerpt = state.get("excerpt", "")
    event_id = state.get("event_id", "")
    attempts = state.get("validation_attempts", 0)

    logger.info("validator_node_start", event_id=event_id, attempt=attempts)

    VALIDATOR_SYSTEM = """You are a senior code reviewer validating an AI-generated fix for a CI failure.
Review the proposed code changes critically. Check:
1. Does the fix actually address the root cause?
2. Are the code changes syntactically valid?
3. Could the change break anything else?
4. Is the fix minimal and safe?

Respond ONLY with valid JSON:
{
    "approved": true or false,
    "score": 0.0 to 1.0,
    "issues": ["list of issues if rejected"],
    "feedback": "specific feedback for the solver if rejected",
    "review_summary": "one-line verdict"
}"""

    VALIDATOR_USER = """Failure Type: {failure_type}
Root Cause: {summary}

Proposed Fix Description: {description}
Solver Reasoning: {reasoning}

Code Changes:
{changes}

Approve this fix? Be strict — reject if there are any correctness concerns."""

    try:
        changes_text = ""
        for c in plan.get("code_changes", []):
            changes_text += f"\nFile: {c.get('file', '')}\nAction: {c.get('action', '')}\n"
            if c.get("old_content"):
                changes_text += f"Remove: {c.get('old_content', '')[:300]}\n"
            changes_text += f"Add: {c.get('new_content', '')[:500]}\n---\n"

        if not changes_text.strip():
            return {
                "validation": {"approved": False, "score": 0.0, "review_summary": "No code changes to validate"},
                "validation_attempts": attempts + 1,
            }

        from shared.azure_llm import get_llm_client, get_model_name
        from shared.llm_observability import traced_completion
        client = get_llm_client()
        model = get_model_name()

        import json
        response, trace = traced_completion(
            client,
            model=model,
            messages=[
                {"role": "system", "content": VALIDATOR_SYSTEM},
                {"role": "user", "content": VALIDATOR_USER.format(
                    failure_type=triage.get("failure_type", "unknown"),
                    summary=triage.get("summary", ""),
                    description=plan.get("description", ""),
                    reasoning=plan.get("solver_reasoning", "No reasoning provided"),
                    changes=changes_text,
                )},
            ],
            agent="validator",
            event_id=event_id,
            temperature=0.1,
            max_tokens=800,
            response_format={"type": "json_object"},
        )

        validation = json.loads(response.choices[0].message.content.strip())
        validation["_llm_trace"] = trace

        logger.info(
            "validator_node_complete",
            event_id=event_id,
            approved=validation.get("approved"),
            score=validation.get("score"),
            attempt=attempts,
        )
        return {
            "validation": validation,
            "validation_attempts": attempts + 1,
            "solver_feedback": validation.get("feedback", ""),
        }

    except Exception as e:
        logger.error("validator_node_failed", event_id=event_id, error=str(e))
        return {
            "validation": {"approved": True, "score": 0.5, "review_summary": "Validator failed — auto-approving"},
            "validation_attempts": attempts + 1,
        }


# ──────────────────────────────────────────────
# Node 4: Policy Evaluation (Step 7)
# ──────────────────────────────────────────────
def policy_node(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Evaluate safety policy — decide whether auto-fix is allowed.

    Reads: state["triage"], state["plan_summary"], state["repo"],
           state["repomind_config"] (V2 — user's .repomind.yml)
    Writes: state["policy"], state["status"]

    Sets status to "denied" if policy blocks the fix.
    """
    triage = state.get("triage", {})
    plan = state.get("plan_summary", {})
    repo = state.get("repo", "")
    event_id = state.get("event_id", "")

    logger.info("policy_node_start", event_id=event_id)

    try:
        from policy_engine.policy import PolicyEngine
        engine = PolicyEngine()

        # V2: Reconstruct RepoMindConfig from state dict (the worker
        # serialized it before invoking the graph).
        repomind_cfg = None
        cfg_dict = state.get("repomind_config") or {}
        if cfg_dict and cfg_dict.get("_source") in ("repo", "default", "fallback"):
            try:
                from shared.repomind_config import parse_config
                repomind_cfg = parse_config(cfg_dict, source=cfg_dict.get("_source", "repo"))
            except Exception as ce:
                logger.debug("repomind_config_reparse_failed", error=str(ce))

        policy = engine.evaluate(triage, plan, repo, repomind_config=repomind_cfg)

        status = "denied" if policy.get("decision") == "deny" else "running"

        logger.info(
            "policy_node_complete",
            event_id=event_id,
            decision=policy.get("decision"),
            reason=policy.get("reason"),
        )
        return {"policy": policy, "status": status}

    except Exception as e:
        logger.error("policy_node_failed", event_id=event_id, error=str(e))
        # Fail closed — deny if policy engine errors
        return {
            "policy": {
                "decision": "deny",
                "reason": f"Policy engine error: {str(e)}",
                "rules_triggered": ["error_fallback"],
            },
            "error": f"Policy failed: {str(e)}",
            "status": "denied",
        }
