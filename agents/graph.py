"""
agents/graph.py — LangGraph Pipeline Graph Definition

HOW IT WORKS:
─────────────
Defines the directed graph that orchestrates Steps 3→5→6→7→8 + HITL.

    ┌──────────────┐
    │  START        │
    └──────┬───────┘
           ▼
    ┌──────────────┐
    │ evidence     │  ← Step 3: Retrieve similar past failures from Qdrant
    └──────┬───────┘
           ▼
    ┌──────────────┐
    │ triage       │  ← Step 5: Classify the failure with LLM
    └──────┬───────┘
           ▼
    ┌──────────────┐
    │ planner      │  ← Step 6: Generate fix plan / select playbook
    └──────┬───────┘
           ▼
    ┌──────────────┐
    │ solver       │  ← Hybrid deep agent solver
    └──────┬───────┘
           ▼
    ┌──────────────┐
    │ validator    │  ← Cross-checks the fix
    └──────┬───────┘
           ▼  (retry loop possible — back to solver)
    ┌──────────────┐
    │ policy       │  ← Step 7: Evaluate safety rules + .repomind.yml
    └──────┬───────┘
           ▼
    ┌──────────────┐
    │ pr_creator   │  ← V2: opens PR / dry-run comment / skip
    └──────┬───────┘
           ▼
    [INTERRUPT BEFORE — graph PAUSES if hitl_required=true]
           │
           ▼
    ┌──────────────────┐
    │ merge_decision   │  ← V2 HITL: reads human_approval after resume
    └──────┬───────────┘
           ▼
    ├── approved → merge_node    → END
    ├── rejected → cleanup_node  → END
    └── skipped  → END

CONDITIONAL EDGES:
    validator → solver (retry, max 2 attempts) | policy
    policy → pr_creator (always — pr_creator handles deny/disabled internally)
    merge_decision → merge | cleanup | END

WHY LANGGRAPH (NOT JUST SEQUENTIAL CALLS):
    - Visual graph for debugging and monitoring
    - Built-in state management and **checkpointing for HITL**
    - `interrupt_before` lets us pause for human review (V2)
    - Easy to add parallel nodes, conditional branches, loops

V2 MULTI-TENANCY:
    run_pipeline() now accepts `installation_id` and seeds it into the
    initial state. Every node (especially pr_creator_node, merge_node,
    cleanup_node) reads it from state to mint a token for the correct
    GitHub App install. Without this, cross-account installs hit 403.

USAGE:
    from agents.graph import run_pipeline
    result = run_pipeline(event_id, repo, excerpt, ..., mode="auto_fix",
                          installation_id=139630626)
    if result["status"] == "awaiting_review":
        # graph paused — review will resume it later

COMMUNICATION:
─────────────
Worker (worker/main.py) calls run_pipeline().
Step 12 (review/review_handler.py) calls resume_pipeline() with verdict.
"""

from typing import Dict, Any, Optional

from shared.logger import get_logger

logger = get_logger("agents.graph")


def _build_graph(with_hitl: bool = True):
    """
    Build the LangGraph StateGraph with full agent swarm + HITL nodes.

    Graph structure:
        START → evidence → triage → planner → solver → validator
        validator → policy (if approved or max retries reached)
        validator → solver (if rejected and attempts < 2) [RETRY LOOP]
        policy → pr_creator
        pr_creator → [INTERRUPT BEFORE merge_decision if hitl_required]
        merge_decision → approved/rejected/skipped routing
            approved → merge → END
            rejected → cleanup → END
            skipped/timeout → END

    Args:
        with_hitl: If True, includes interrupt_before=["merge_decision"]
                   for human review pause. If False (e.g. for tests or when
                   no checkpointer is available), the graph runs end-to-end
                   without pausing — useful for legacy/dry-run flows.
    """
    try:
        from langgraph.graph import StateGraph, END
    except ImportError:
        logger.warning("langgraph_not_installed", msg="Falling back to sequential execution")
        return None

    from agents.models import PipelineState
    from agents.nodes import (
        evidence_node,
        triage_node,
        planner_node,
        solver_node,
        validator_node,
        policy_node,
    )
    from agents.hitl_nodes import (
        pr_creator_node,
        merge_decision_node,
        merge_node,
        cleanup_node,
        route_after_merge_decision,
    )

    graph = StateGraph(PipelineState)

    # Add all agent nodes
    graph.add_node("evidence", evidence_node)
    graph.add_node("triage", triage_node)
    graph.add_node("planner", planner_node)
    graph.add_node("solver", solver_node)
    graph.add_node("validator", validator_node)
    graph.add_node("policy", policy_node)

    # V2: HITL nodes
    graph.add_node("pr_creator", pr_creator_node)
    graph.add_node("merge_decision", merge_decision_node)
    graph.add_node("merge", merge_node)
    graph.add_node("cleanup", cleanup_node)

    # Linear flow up to solver
    graph.set_entry_point("evidence")
    graph.add_edge("evidence", "triage")
    graph.add_edge("triage", "planner")
    graph.add_edge("planner", "solver")
    graph.add_edge("solver", "validator")

    # Conditional edge from validator: retry or proceed
    def route_validator(state):
        validation = state.get("validation", {})
        attempts = state.get("validation_attempts", 0)
        approved = validation.get("approved", True)

        if not approved and attempts < 2:
            logger.info("validator_rejected_retrying", attempts=attempts)
            return "solver"  # retry loop
        return "policy"  # proceed

    graph.add_conditional_edges(
        "validator",
        route_validator,
        {"solver": "solver", "policy": "policy"},
    )

    # Policy → PR creator (PR creator handles deny/disabled internally)
    graph.add_edge("policy", "pr_creator")

    # PR creator → merge_decision (interrupt happens BEFORE merge_decision)
    graph.add_edge("pr_creator", "merge_decision")

    # Merge decision → merge | cleanup | END
    graph.add_conditional_edges(
        "merge_decision",
        route_after_merge_decision,
        {
            "merge": "merge",
            "cleanup": "cleanup",
            "end": END,
        },
    )

    graph.add_edge("merge", END)
    graph.add_edge("cleanup", END)

    # Compile with HITL interrupt + S3 checkpointer
    compile_kwargs: Dict[str, Any] = {}
    if with_hitl:
        try:
            from agents.checkpointer import get_checkpointer
            checkpointer = get_checkpointer()
            if checkpointer is not None:
                compile_kwargs["checkpointer"] = checkpointer
                compile_kwargs["interrupt_before"] = ["merge_decision"]
                logger.info("langgraph_hitl_enabled", checkpointer=type(checkpointer).__name__)
            else:
                logger.warning(
                    "langgraph_hitl_disabled_no_checkpointer",
                    msg="No checkpointer available; graph will run end-to-end",
                )
        except Exception as e:
            logger.warning("langgraph_hitl_setup_failed", error=str(e))

    compiled = graph.compile(**compile_kwargs)
    logger.info(
        "langgraph_compiled",
        nodes=[
            "evidence", "triage", "planner", "solver", "validator", "policy",
            "pr_creator", "merge_decision", "merge", "cleanup",
        ],
        hitl=with_hitl and "checkpointer" in compile_kwargs,
    )
    return compiled


# Module-level compiled graph (lazy)
_compiled_graph = None
_compiled_graph_no_hitl = None


def get_graph(with_hitl: bool = True):
    """Get or create the compiled graph (singleton, one per HITL mode)."""
    global _compiled_graph, _compiled_graph_no_hitl
    if with_hitl:
        if _compiled_graph is None:
            _compiled_graph = _build_graph(with_hitl=True)
        return _compiled_graph
    else:
        if _compiled_graph_no_hitl is None:
            _compiled_graph_no_hitl = _build_graph(with_hitl=False)
        return _compiled_graph_no_hitl


def run_pipeline(
    event_id: str,
    repo: str,
    workflow_run_id: int,
    run_url: str,
    excerpt: str,
    head_branch: str = "",
    head_sha: str = "",
    repomind_config: Optional[Dict[str, Any]] = None,
    mode: str = "auto_fix",
    hitl_required: bool = True,
    installation_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Run the full LangGraph pipeline for a CI failure event.

    This is the main entry point for the LangGraph orchestration.
    If LangGraph is not installed, falls back to sequential execution.

    Args:
        event_id: Unique event identifier (also used as LangGraph thread_id)
        repo: Full repo name
        workflow_run_id: GitHub workflow run ID
        run_url: URL to the GitHub Actions run
        excerpt: Log excerpt from Step 2
        head_branch: Branch that triggered the run
        head_sha: Commit SHA
        repomind_config: Parsed `.repomind.yml` (dict). Optional.
        mode: "auto_fix" | "dry_run" | "disabled" (from .repomind.yml)
        hitl_required: Whether to pause for human review after PR creation
        installation_id: V2 multi-tenancy — GitHub App install ID for this
                         repo. Threaded into PipelineState so every node
                         can mint the correct installation token.

    Returns:
        Final pipeline state dict. If hitl_required and PR was opened, the
        state will contain status="awaiting_review" and the graph is paused
        at the merge_decision node — call resume_pipeline() to continue.
    """
    initial_state = {
        "event_id": event_id,
        "repo": repo,
        "workflow_run_id": workflow_run_id,
        "run_url": run_url,
        "excerpt": excerpt,
        "head_branch": head_branch,
        "head_sha": head_sha,
        # V2 multi-tenancy — must be a field declared on PipelineState or
        # LangGraph will drop it during state merging.
        "installation_id": int(installation_id or 0),
        "similar_incidents": [],
        "triage": {},
        "plan_summary": {},
        "policy": {},
        "pr": {},
        "validation": {},
        "validation_attempts": 0,
        "solver_feedback": "",
        "llm_traces": [],
        "llm_summary": {},
        "judge": {},
        # V2 HITL state
        "repomind_config": repomind_config or {},
        "mode": mode,
        "hitl_required": hitl_required,
        "pr_url": "",
        "pr_number": 0,
        "human_approval": "pending",
        "review_data": {},
        "merge_result": {},
        "cleanup_result": {},
        "error": "",
        "status": "running",
    }

    import time as _time
    pipeline_start = _time.perf_counter()

    # If hitl is requested but no checkpointer is available, run end-to-end
    # without interrupts. This keeps tests/dev simple.
    graph = get_graph(with_hitl=hitl_required)

    # Config: thread_id pins this run's checkpoints to the event_id, so
    # review/review_handler can resume by event_id alone.
    invoke_config = {"configurable": {"thread_id": event_id}}

    if graph is not None:
        # ── LangGraph execution ──
        logger.info(
            "pipeline_start_langgraph",
            event_id=event_id,
            mode=mode,
            hitl_required=hitl_required,
            installation_id=int(installation_id or 0),
        )
        try:
            final_state = graph.invoke(initial_state, config=invoke_config)
            pipeline_ms = (_time.perf_counter() - pipeline_start) * 1000
            final_state = dict(final_state)

            # Detect interrupt (graph paused for human review)
            current_status = final_state.get("status", "")
            if current_status == "awaiting_review":
                logger.info(
                    "pipeline_paused_for_review",
                    event_id=event_id,
                    pr_url=final_state.get("pr_url"),
                )
                # Don't run RAG eval / judge yet — they'll run on final resume.
                return final_state

            # Run full RAG evaluation (only on terminal completions)
            final_state = _attach_rag_report(final_state, excerpt, pipeline_ms)

            logger.info(
                "pipeline_complete_langgraph",
                event_id=event_id,
                status=final_state.get("status"),
                pipeline_ms=round(pipeline_ms, 2),
            )
            return final_state
        except Exception as e:
            logger.error("pipeline_langgraph_error", event_id=event_id, error=str(e))
            # Fall through to sequential
            initial_state["error"] = str(e)

    # ── Fallback: Sequential execution ──
    logger.info("pipeline_start_sequential", event_id=event_id)
    result = _run_sequential(initial_state)

    pipeline_ms = (_time.perf_counter() - pipeline_start) * 1000
    result = _attach_rag_report(result, excerpt, pipeline_ms)

    return result


def resume_pipeline(
    event_id: str,
    human_approval: str,
    review_data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Resume a paused HITL graph after a human review arrives.

    Called by review/review_handler.py when GitHub fires `pull_request_review`.
    The thread_id in LangGraph corresponds to event_id, so we just look up
    the checkpoint and resume with the verdict injected into state.

    Args:
        event_id: The original event ID (= LangGraph thread_id)
        human_approval: "approved" | "rejected" | "timeout"
        review_data: Raw GitHub review payload (optional, stored in state)

    Returns:
        Final pipeline state dict after resume completes.
    """
    if human_approval not in ("approved", "rejected", "timeout"):
        raise ValueError(
            f"human_approval must be approved/rejected/timeout, got {human_approval!r}"
        )

    graph = get_graph(with_hitl=True)
    if graph is None:
        logger.error("resume_pipeline_no_graph", event_id=event_id)
        return {
            "event_id": event_id,
            "status": "failed",
            "error": "LangGraph not available — cannot resume",
        }

    invoke_config = {"configurable": {"thread_id": event_id}}

    # Inject the human verdict by updating the persisted state. LangGraph's
    # update_state pattern: pass a partial dict; checkpointer merges it into
    # the latest checkpoint. Then invoke with input=None to resume from
    # where the graph paused.
    try:
        graph.update_state(
            invoke_config,
            {
                "human_approval": human_approval,
                "review_data": review_data or {},
            },
        )
    except Exception as e:
        logger.warning(
            "resume_pipeline_update_state_failed",
            event_id=event_id,
            error=str(e),
        )

    import time as _time
    resume_start = _time.perf_counter()

    try:
        # `invoke(None, ...)` resumes from the interrupt
        final_state = graph.invoke(None, config=invoke_config)
        final_state = dict(final_state)

        # Now run RAG eval / judge for the now-complete pipeline
        excerpt = final_state.get("excerpt", "")
        pipeline_ms = (_time.perf_counter() - resume_start) * 1000
        final_state = _attach_rag_report(final_state, excerpt, pipeline_ms)

        logger.info(
            "pipeline_resumed_complete",
            event_id=event_id,
            human_approval=human_approval,
            final_status=final_state.get("status"),
        )
        return final_state
    except Exception as e:
        logger.error("resume_pipeline_failed", event_id=event_id, error=str(e))
        return {
            "event_id": event_id,
            "status": "failed",
            "error": str(e),
            "human_approval": human_approval,
        }


def _attach_rag_report(
    state: Dict[str, Any],
    excerpt: str,
    pipeline_ms: float,
) -> Dict[str, Any]:
    """
    Run full RAG evaluation and attach the report to pipeline state.

    Combines retrieval + context + generation metrics from nodes
    with end-to-end timing to produce a unified RAG quality report.
    Also collects per-LLM-call traces and runs the LLM-as-judge.
    """
    # ── Collect LLM traces from each agent's output ──
    state = _collect_llm_traces(state)

    try:
        from rag.rag_metrics import evaluate_rag

        similar = state.get("similar_incidents", [])
        triage = state.get("triage", {})
        retrieval_ms = state.get("_rag_retrieval_ms", 0.0)

        rag_report = evaluate_rag(
            query_text=excerpt,
            results=similar,
            triage_result=triage,
            top_k_requested=3,
            retrieval_latency_ms=retrieval_ms,
            pipeline_latency_ms=pipeline_ms,
        )

        state["rag_evaluation"] = rag_report

        logger.info(
            "rag_report_attached",
            event_id=state.get("event_id", ""),
            grade=rag_report.get("grade", {}).get("letter", "?"),
            score=rag_report.get("grade", {}).get("score", 0),
        )
    except Exception as e:
        logger.debug("rag_report_skipped", reason=str(e))
        state["rag_evaluation"] = {}

    # ── Run LLM-as-Judge on the triage output (Tier 2) ──
    state = _run_llm_judge(state, excerpt)

    return state


def _collect_llm_traces(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Collect _llm_trace fields each agent stuffed into its own output, build
    state["llm_traces"] (list) and state["llm_summary"] (aggregated totals).
    """
    try:
        from shared.llm_observability import attach_trace, summarize_traces

        traces: list = state.get("llm_traces") or []

        # Triage agent
        triage_trace = state.get("triage", {}).get("_llm_trace")
        if triage_trace:
            attach_trace(state, triage_trace)
            traces = state["llm_traces"]

        # Planner agent
        plan_trace = state.get("plan_summary", {}).get("_llm_trace")
        if plan_trace:
            attach_trace(state, plan_trace)
            traces = state["llm_traces"]

        # Solver (direct-LLM mode) — trace attached inside plan_summary by _direct_llm_solver
        # No separate field; if plan_summary._llm_trace exists with agent="solver", it's already collected above.

        # Validator agent
        val_trace = state.get("validation", {}).get("_llm_trace")
        if val_trace:
            attach_trace(state, val_trace)
            traces = state["llm_traces"]

        state["llm_summary"] = summarize_traces(state.get("llm_traces", []))
        logger.info(
            "llm_summary_attached",
            event_id=state.get("event_id", ""),
            calls=state["llm_summary"]["total_calls"],
            tokens=state["llm_summary"]["total_tokens"],
            cost_usd=state["llm_summary"]["total_cost_usd"],
        )
    except Exception as e:
        logger.debug("llm_traces_collect_failed", error=str(e))
        state["llm_summary"] = {}

    return state


def _run_llm_judge(state: Dict[str, Any], excerpt: str) -> Dict[str, Any]:
    """
    Invoke the LLM-as-judge to score the triage output. Best-effort —
    failures are non-fatal and just leave state["judge"] empty.
    """
    try:
        from agents.llm_judge import judge_triage
        triage = state.get("triage", {})
        if not triage:
            return state
        verdict = judge_triage(triage, excerpt, event_id=state.get("event_id", ""))
        state["judge"] = verdict
        logger.info(
            "llm_judge_attached",
            event_id=state.get("event_id", ""),
            grade=verdict.get("overall_grade", "?"),
            hallucination=verdict.get("hallucination_flag", False),
        )
    except Exception as e:
        logger.debug("llm_judge_skipped", error=str(e))
        state["judge"] = {}
    return state


def _run_sequential(state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fallback: run nodes sequentially without LangGraph.

    Same logic, same nodes, same state — just called in order.
    Used when LangGraph isn't installed or fails.

    NOTE: HITL is impossible in this path (no checkpointer). We run
    pr_creator_node and merge_decision_node end-to-end. If
    state["human_approval"] == "pending", the graph just stops there
    (the worker can post a "review pending" status comment).
    """
    from agents.nodes import (
        evidence_node,
        triage_node,
        planner_node,
        solver_node,
        validator_node,
        policy_node,
    )
    from agents.hitl_nodes import (
        pr_creator_node,
        merge_decision_node,
        merge_node,
        cleanup_node,
    )

    event_id = state.get("event_id", "")

    for node_name, node_fn in [
        ("evidence", evidence_node),
        ("triage", triage_node),
        ("planner", planner_node),
        ("solver", solver_node),
        ("validator", validator_node),
        ("policy", policy_node),
        ("pr_creator", pr_creator_node),
    ]:
        try:
            update = node_fn(state)
            state.update(update)
        except Exception as e:
            logger.error(f"sequential_{node_name}_failed", event_id=event_id, error=str(e))
            if node_name in ("triage", "planner"):
                state["status"] = "failed"
                state["error"] = str(e)
                return state

    # Sequential mode cannot pause — if HITL would have paused here, we
    # mark the state as "awaiting_review" and stop. The caller is
    # responsible for resuming via resume_pipeline() once a review arrives.
    if state.get("status") == "awaiting_review":
        logger.info(
            "sequential_paused_for_review",
            event_id=event_id,
            pr_url=state.get("pr_url"),
        )
        return state

    # Otherwise, run merge_decision and the appropriate terminal node.
    try:
        state.update(merge_decision_node(state))
        approval = state.get("human_approval", "skipped")
        if approval == "approved":
            state.update(merge_node(state))
        elif approval == "rejected":
            state.update(cleanup_node(state))
    except Exception as e:
        logger.error("sequential_terminal_node_failed", event_id=event_id, error=str(e))

    if state.get("status") not in ("failed", "denied", "awaiting_review"):
        state["status"] = "completed"

    logger.info("pipeline_complete_sequential", event_id=event_id, status=state["status"])
    return state