"""
agents/models.py — Pydantic State Models for LangGraph Pipeline

HOW IT WORKS:
─────────────
Defines the shared state that flows through the LangGraph graph.

LangGraph uses a "state" object that every node reads from and writes to.
Think of it as a shared blackboard — each node picks up data, does its work,
and writes results back.

STATE LIFECYCLE:
    1. Graph starts with PipelineState (excerpt, repo, event_id)
    2. evidence_node   → reads excerpt, writes similar_incidents
    3. triage_node     → reads excerpt + similar_incidents, writes triage
    4. planner_node    → reads triage + excerpt, writes plan_summary
    5. policy_node     → reads triage + plan_summary, writes policy
    6. Final state     → all fields populated, returned to caller

WHY PYDANTIC + TYPED DICT:
    LangGraph expects a TypedDict or dataclass for state.
    We use TypedDict for graph state (LangGraph requirement)
    and Pydantic for input/output validation at the edges.

V2 MULTI-TENANCY:
    `installation_id` was added so each node can mint a token for the
    *correct* GitHub App install (one install per customer account).
    Without it, the worker would fall back to env var GITHUB_INSTALLATION_ID
    and try to act on a different account's repos → 403 Forbidden.

COMMUNICATION:
─────────────
agents/graph.py imports PipelineState and uses it as the graph's state schema.
Each node function receives and returns partial state updates.
"""

from typing import TypedDict, Optional, Dict, Any, List

from pydantic import BaseModel


# ──────────────────────────────────────────────
# LangGraph State (TypedDict — required by LangGraph)
# ──────────────────────────────────────────────
class PipelineState(TypedDict, total=False):
    """
    The shared state object that flows through the LangGraph graph.

    Every node reads what it needs and writes its results.
    LangGraph merges partial updates automatically.

    IMPORTANT: LangGraph DROPS any field not declared here. Any new
    cross-node value must be added to this TypedDict or it won't survive
    state merging between nodes.

    Fields:
        event_id:           Unique event identifier
        repo:               Full repo name (e.g. "user/mlproject")
        workflow_run_id:    GitHub workflow run ID
        run_url:            URL to the GitHub Actions run
        head_branch:        Branch that triggered the run
        head_sha:           Commit SHA
        installation_id:    GitHub App install ID for this repo (V2 multi-tenancy)

        excerpt:            Log excerpt from Step 2
        similar_incidents:  Past similar failures from Qdrant (Step 3)

        triage:             Failure classification result (Step 5)
        plan_summary:       Fix plan (Step 6)
        policy:             Policy evaluation result (Step 7)
        pr:                 PR creation result (Step 8)

        error:              Error message if a node fails
        status:             Pipeline status: "running" | "completed" | "failed" | "denied"
    """
    # Input (set by caller)
    event_id: str
    repo: str
    workflow_run_id: int
    run_url: str
    head_branch: str
    head_sha: str
    excerpt: str

    # V2 multi-tenancy: install ID for the GitHub App on the target repo
    installation_id: int

    # Step 3: Evidence retrieval
    similar_incidents: List[Dict[str, Any]]

    # Step 5: Triage
    triage: Dict[str, Any]

    # Step 6: Plan
    plan_summary: Dict[str, Any]

    # Step 7: Policy
    policy: Dict[str, Any]

    # Step 8: PR
    pr: Dict[str, Any]

    # Solver / Validator agents (Priority 4 — Agent Swarm)
    validation: Dict[str, Any]           # Validator agent output
    validation_attempts: int             # Number of validation loops
    solver_feedback: str                 # Feedback from validator to solver

    # ── V2: User config + HITL ──
    repomind_config: Dict[str, Any]      # Parsed .repomind.yml from user repo
    mode: str                            # "auto_fix" | "dry_run" | "disabled"
    hitl_required: bool                  # Human approval required before merge?
    pr_url: str                          # URL of the PR (if mode=auto_fix)
    pr_number: int                       # PR number (for review lookups)
    human_approval: str                  # "pending" | "approved" | "rejected" | "timeout" | "skipped"
    review_data: Dict[str, Any]          # PR review payload from GitHub
    merge_result: Dict[str, Any]         # Output of merge_node
    cleanup_result: Dict[str, Any]       # Output of cleanup_node

    # Control
    error: str
    status: str


# ──────────────────────────────────────────────
# Pydantic models for input/output validation
# ──────────────────────────────────────────────
class PipelineInput(BaseModel):
    """Validated input to start the LangGraph pipeline."""
    event_id: str
    repo: str
    workflow_run_id: int
    run_url: str
    head_branch: str = ""
    head_sha: str = ""
    excerpt: str
    # V2: User repo config
    repomind_config: Optional[Dict[str, Any]] = None
    mode: str = "auto_fix"
    hitl_required: bool = True
    # V2: Multi-tenancy
    installation_id: Optional[int] = None


class PipelineOutput(BaseModel):
    """Validated output from the completed pipeline."""
    event_id: str
    repo: str
    status: str
    triage: Optional[Dict[str, Any]] = None
    plan_summary: Optional[Dict[str, Any]] = None
    policy: Optional[Dict[str, Any]] = None
    pr: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    # V2: HITL artifacts
    pr_url: Optional[str] = None
    human_approval: Optional[str] = None
    merge_result: Optional[Dict[str, Any]] = None
    cleanup_result: Optional[Dict[str, Any]] = None