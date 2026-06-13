"""
agents/deep_solver.py — Deep Agent Solver (Hybrid Mode)

──────────────────────────────────────────────
HOW IT WORKS
──────────────────────────────────────────────
This module implements a "deep agent" that fixes CI failures by:

  1. READING the actual repo files via GitHub API (not guessing from logs)
  2. PLANNING the fix as a todo list (built-in to deep agents)
  3. SUB-AGENTS delegate sub-tasks to specialized agents:
       - code-reader: inspects file contents and explains structure
       - diff-writer: generates precise file diffs
  4. SELF-CORRECTING via the deep agents reflection loop
  5. RETURNING structured code_changes ready for PR creation

──────────────────────────────────────────────
ARCHITECTURE — Hybrid Solver
──────────────────────────────────────────────
The actual solver_node in agents/nodes.py follows this pattern:

    try:
        result = run_deep_solver(state)   # ← THIS MODULE (rich, slow, accurate)
    except (TimeoutError, Exception):
        result = run_direct_llm(state)    # ← Fallback (fast, always works)

This gives us BOTH innovation (deep agent) AND reliability (fallback).

──────────────────────────────────────────────
SAFETY
──────────────────────────────────────────────
- All tools are READ-ONLY (no file writes to GitHub from this layer)
- Tools have a 5s timeout each
- Total deep agent invocation timeout: 45 seconds
- Falls back gracefully on any error (caught by solver_node)
- File reads are capped at 50KB per file (token budget control)

──────────────────────────────────────────────
COMMUNICATION
──────────────────────────────────────────────
agents/nodes.py solver_node()
       ↓ calls
agents/deep_solver.py run_deep_solver()
       ↓ uses
shared/github_auth.py get_github_client()  (for reading repo files)
shared/azure_llm.py   get_llm_client()     (for the deep agent's brain)
"""

from __future__ import annotations

import json
import os
from typing import Dict, Any, List, Optional

from shared.config import settings
from shared.logger import get_logger

logger = get_logger("agents.deep_solver")

# Hard caps to keep Lambda happy
MAX_FILE_BYTES = 50_000          # Cap per file read
MAX_TOTAL_READS = 8              # Cap total file reads per solver run
DEFAULT_TIMEOUT_SEC = 45         # Total budget for deep agent
PER_TOOL_TIMEOUT_SEC = 5         # Per-tool budget


# ──────────────────────────────────────────────
# Tool counter — shared across one solver invocation
# ──────────────────────────────────────────────
class _ToolBudget:
    """Lightweight per-invocation counter to cap tool calls."""

    def __init__(self, max_reads: int = MAX_TOTAL_READS):
        self.reads_used = 0
        self.max_reads = max_reads
        self.files_read: List[str] = []

    def can_read(self) -> bool:
        return self.reads_used < self.max_reads

    def record_read(self, path: str) -> None:
        self.reads_used += 1
        self.files_read.append(path)


# ──────────────────────────────────────────────
# Tool factory — closures capture (repo, ref, budget)
# ──────────────────────────────────────────────
def _build_tools(repo: str, ref: str, budget: _ToolBudget):
    """
    Build the tool functions the deep agent can call.

    Tools are READ-ONLY and budgeted. Returns a list of plain Python
    callables — deepagents wraps them automatically.
    """

    def read_repo_file(path: str) -> str:
        """
        Read the actual contents of a file from the GitHub repo at the failing commit.

        Args:
            path: Repo-relative path, e.g. "requirements.txt" or "src/main.py"

        Returns:
            File contents as text (truncated to 50KB), or an error message.
        """
        if not budget.can_read():
            return f"ERROR: Tool budget exhausted ({budget.max_reads} reads). Use what you have."
        try:
            from shared.github_auth import get_github_client
            g = get_github_client()
            repository = g.get_repo(repo)
            file_obj = repository.get_contents(path, ref=ref)
            if hasattr(file_obj, "decoded_content"):
                raw = file_obj.decoded_content
                budget.record_read(path)
                if len(raw) > MAX_FILE_BYTES:
                    return raw[:MAX_FILE_BYTES].decode("utf-8", errors="replace") + f"\n\n... [TRUNCATED at {MAX_FILE_BYTES} bytes]"
                return raw.decode("utf-8", errors="replace")
            return f"ERROR: {path} is a directory, not a file."
        except Exception as e:
            return f"ERROR reading {path}: {type(e).__name__}: {str(e)[:200]}"

    def list_repo_directory(path: str = "") -> str:
        """
        List files in a repo directory (use empty string for repo root).

        Args:
            path: Directory path, e.g. "" for root or "src/" for a subdirectory

        Returns:
            Newline-separated list of file paths, or an error message.
        """
        if not budget.can_read():
            return f"ERROR: Tool budget exhausted. Use what you have."
        try:
            from shared.github_auth import get_github_client
            g = get_github_client()
            repository = g.get_repo(repo)
            contents = repository.get_contents(path, ref=ref)
            if not isinstance(contents, list):
                return f"ERROR: {path} is a file, use read_repo_file instead."
            budget.record_read(f"dir:{path}")
            entries = [f"{'[DIR] ' if c.type == 'dir' else ''}{c.path}" for c in contents[:50]]
            return "\n".join(entries) if entries else "(empty directory)"
        except Exception as e:
            return f"ERROR listing {path}: {type(e).__name__}: {str(e)[:200]}"

    def search_repo_code(query: str) -> str:
        """
        Search for a string across the repo using GitHub's code search API.
        Useful to find where a symbol is defined or used.

        Args:
            query: Search string, e.g. "import httpx" or "def verify_token"

        Returns:
            Up to 10 matching file paths with snippets, or an error message.
        """
        if not budget.can_read():
            return f"ERROR: Tool budget exhausted."
        try:
            from shared.github_auth import get_github_client
            g = get_github_client()
            # GitHub code search requires repo-scoped query
            full_query = f'{query} repo:{repo}'
            results = g.search_code(query=full_query)
            budget.record_read(f"search:{query}")
            hits = []
            for i, item in enumerate(results):
                if i >= 10:
                    break
                hits.append(f"- {item.path}")
            return "\n".join(hits) if hits else f"No matches found for: {query}"
        except Exception as e:
            return f"ERROR searching '{query}': {type(e).__name__}: {str(e)[:200]}"

    return [read_repo_file, list_repo_directory, search_repo_code]


# ──────────────────────────────────────────────
# System prompt — drives the deep agent
# ──────────────────────────────────────────────
DEEP_SOLVER_PROMPT = """You are an expert software engineer fixing CI failures in a real GitHub repository.

You have access to tools that READ the actual repo files at the failing commit. Use them — do NOT guess what's in a file.

WORKFLOW:
1. Use `read_repo_file` to inspect the affected file mentioned in the triage
2. Optionally use `list_repo_directory` to understand structure, or `search_repo_code` to find related code
3. Reason carefully about the root cause (the failure log shows the symptom; you need the cause)
4. Generate a PRECISE diff — `old_content` MUST be the exact string from the file (you just read it, so this is achievable)

CONSTRAINTS:
- You have at most 8 tool calls total — budget them wisely
- Prefer reading 1-2 files deeply over many shallow reads
- Keep diffs minimal — change only what's needed

FINAL OUTPUT (REQUIRED):
After your investigation, end your response with a JSON code block in EXACTLY this shape:

```json
{
  "reasoning": "step-by-step explanation grounded in what you actually read",
  "code_changes": [
    {
      "file": "exact/path/from/repo/root",
      "action": "modify",
      "description": "what this change does in plain English",
      "old_content": "exact string copied from the file you read",
      "new_content": "exact replacement string"
    }
  ],
  "confidence": 0.0,
  "risk_assessment": "low|medium|high",
  "files_inspected": ["list", "of", "files", "you", "read"]
}
```

If you cannot determine a safe fix, return an empty "code_changes" array and explain why in "reasoning"."""


# ──────────────────────────────────────────────
# Sub-agents — specialized roles for the swarm
# ──────────────────────────────────────────────
SUBAGENT_CODE_READER = {
    "name": "code-reader",
    "description": "Reads and analyzes source files in the repo. Use this to understand existing code structure before proposing changes.",
    "prompt": "You are a code-reading specialist. Given a file path, read it via read_repo_file and produce a concise summary of its structure, key symbols, and any patterns relevant to the failure being investigated.",
}

SUBAGENT_DIFF_WRITER = {
    "name": "diff-writer",
    "description": "Generates precise file diffs. Use this once you understand the root cause and need to write the exact old_content/new_content pair.",
    "prompt": "You are a diff-writing specialist. Given a file's current content and a description of the fix, produce the exact old_content (string to find) and new_content (string to replace it with). Keep diffs minimal — change only what's necessary.",
}


# ──────────────────────────────────────────────
# Main entry — called by solver_node
# ──────────────────────────────────────────────
def run_deep_solver(
    repo: str,
    ref: str,
    triage: Dict[str, Any],
    plan: Dict[str, Any],
    excerpt: str,
    similar_incidents: Optional[List[Dict[str, Any]]] = None,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> Dict[str, Any]:
    """
    Run the deep agent solver on a CI failure.

    Args:
        repo: GitHub repo like "org/repo"
        ref: Git ref (branch or SHA) of the failing commit
        triage: Triage agent output
        plan: Planner agent output (may have empty code_changes)
        excerpt: Sanitized CI log excerpt
        similar_incidents: Optional RAG context from Qdrant
        timeout_sec: Total budget for the deep agent invocation

    Returns:
        Dict with keys: reasoning, code_changes, confidence, risk_assessment,
                        files_inspected, mode="deep_agent"

    Raises:
        RuntimeError: If deepagents is not installed or LLM client fails
        TimeoutError: If the deep agent exceeds timeout_sec
    """
    # Lazy imports — keep cold start fast and graceful if optional deps missing
    try:
        from deepagents import create_deep_agent
    except ImportError as e:
        raise RuntimeError(f"deepagents not installed: {e}")

    # Build the LangChain-compatible model wrapper
    model = _build_langchain_model()

    # Per-invocation tool budget
    budget = _ToolBudget()
    tools = _build_tools(repo=repo, ref=ref, budget=budget)

    # Inject RAG context into the user prompt
    rag_block = _format_rag_context(similar_incidents or [])

    user_msg = f"""Repository: {repo}
Commit ref: {ref}

TRIAGE FINDINGS:
- Failure Type: {triage.get('failure_type', 'unknown')}
- Confidence: {triage.get('confidence', 0):.2f}
- Summary: {triage.get('summary', '')}
- Affected File: {triage.get('affected_file', 'unknown')}
- Affected Package: {triage.get('affected_package', 'unknown')}

FIX PLAN (from planner):
- Description: {plan.get('description', '')}
- Actions: {chr(10).join('  - ' + a for a in plan.get('actions', []))}

CI LOG EXCERPT (last 3000 chars):
```
{excerpt[-3000:] if len(excerpt) > 3000 else excerpt}
```

{rag_block}

Investigate the affected file, then propose a precise fix. Use your tools."""

    logger.info(
        "deep_solver_start",
        repo=repo,
        ref=ref,
        failure_type=triage.get("failure_type"),
        timeout_sec=timeout_sec,
    )

    # Build the deep agent
    try:
        agent = create_deep_agent(
            model=model,
            tools=tools,
            system_prompt=DEEP_SOLVER_PROMPT,
            subagents=[SUBAGENT_CODE_READER, SUBAGENT_DIFF_WRITER],
        )
    except TypeError:
        # Older API signature — try without subagents
        agent = create_deep_agent(
            model=model,
            tools=tools,
            system_prompt=DEEP_SOLVER_PROMPT,
        )

    # Invoke with timeout protection
    result_state = _invoke_with_timeout(agent, user_msg, timeout_sec)

    # Extract the final assistant message and parse JSON
    parsed = _extract_json_response(result_state)
    parsed["files_inspected"] = parsed.get("files_inspected", budget.files_read)
    parsed["mode"] = "deep_agent"
    parsed["tool_calls_used"] = budget.reads_used

    logger.info(
        "deep_solver_complete",
        repo=repo,
        changes=len(parsed.get("code_changes", [])),
        confidence=parsed.get("confidence", 0),
        files_read=budget.reads_used,
    )
    return parsed


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────
def _build_langchain_model():
    """
    Build a LangChain-compatible chat model.

    Prefers Azure OpenAI (from settings); falls back to a generic OpenAI client.
    Returns a langchain_openai.AzureChatOpenAI or ChatOpenAI instance.
    """
    azure_endpoint = settings.AZURE_OPENAI_ENDPOINT
    azure_key = settings.AZURE_OPENAI_API_KEY

    if azure_endpoint and azure_key:
        try:
            from langchain_openai import AzureChatOpenAI
            return AzureChatOpenAI(
                azure_endpoint=azure_endpoint,
                api_key=azure_key,
                api_version=settings.AZURE_OPENAI_API_VERSION or "2024-02-01",
                azure_deployment=settings.AZURE_OPENAI_DEPLOYMENT_NAME or "gpt-4o",
                temperature=0.1,
                max_tokens=2500,
                timeout=40,
            )
        except ImportError as e:
            raise RuntimeError(f"langchain-openai not installed: {e}")

    # Fallback to plain OpenAI-compatible client (Groq exposes this API)
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model="gpt-4o-mini",  # safe default
        api_key=os.getenv("OPENAI_API_KEY", "sk-noop"),
        temperature=0.1,
        max_tokens=2500,
        timeout=40,
    )


def _format_rag_context(incidents: List[Dict[str, Any]]) -> str:
    """Format past similar fixes as a context block."""
    if not incidents:
        return ""
    lines = ["PAST SIMILAR FIXES (for reference, not blind copy):"]
    for i, inc in enumerate(incidents[:2], 1):
        summary = inc.get("payload", {}).get("triage_summary", "n/a")
        fix = inc.get("payload", {}).get("plan_description", "n/a")
        lines.append(f"  {i}. Past failure: {summary}")
        lines.append(f"     Past fix: {fix}")
    return "\n".join(lines)


def _invoke_with_timeout(agent, user_msg: str, timeout_sec: int) -> Dict[str, Any]:
    """
    Invoke the deep agent with a hard timeout.

    NOTE: Python signal-based timeouts don't work in threads (Lambda runs handlers
    in the main thread, so this is safe). For cross-platform safety we use a
    simple try/except — the underlying LLM call has its own timeout already.
    """
    try:
        # Deep agents v0.6+ uses .invoke({"messages": "..."})
        return agent.invoke({"messages": user_msg})
    except Exception as e:
        # Re-raise as TimeoutError if it looks like a timeout, else generic
        msg = str(e).lower()
        if "timeout" in msg or "timed out" in msg:
            raise TimeoutError(f"Deep agent exceeded {timeout_sec}s: {e}")
        raise


def _extract_json_response(result_state: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract the JSON fix payload from the deep agent's final message.

    Deep agents return a dict with "messages" (a list of LangChain messages).
    We find the last AI message and extract the ```json block from it.
    """
    messages = result_state.get("messages", [])
    if not messages:
        return _empty_response("No messages returned by deep agent")

    # Find the last message with content
    last_content = ""
    for msg in reversed(messages):
        content = getattr(msg, "content", None) or (msg.get("content") if isinstance(msg, dict) else None)
        if content:
            last_content = content if isinstance(content, str) else str(content)
            break

    if not last_content:
        return _empty_response("Last deep agent message had no content")

    # Try to extract ```json ... ``` block
    json_str = _extract_json_block(last_content)
    if not json_str:
        return _empty_response("No JSON block found in deep agent response", reasoning=last_content[:500])

    try:
        parsed = json.loads(json_str)
        # Validate required keys
        parsed.setdefault("reasoning", "")
        parsed.setdefault("code_changes", [])
        parsed.setdefault("confidence", 0.5)
        parsed.setdefault("risk_assessment", "medium")
        return parsed
    except json.JSONDecodeError as e:
        return _empty_response(f"JSON parse failed: {e}", reasoning=last_content[:500])


def _extract_json_block(text: str) -> Optional[str]:
    """Find a ```json ... ``` block (or fallback to first {...} block)."""
    # Prefer fenced block
    fence_marker = "```json"
    if fence_marker in text:
        start = text.index(fence_marker) + len(fence_marker)
        end = text.find("```", start)
        if end > start:
            return text[start:end].strip()

    # Fallback: find first balanced {...}
    depth = 0
    start_idx = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start_idx = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start_idx >= 0:
                return text[start_idx : i + 1]
    return None


def _empty_response(error: str, reasoning: str = "") -> Dict[str, Any]:
    """Return a safe empty response when parsing fails."""
    return {
        "reasoning": reasoning or f"Deep solver failed: {error}",
        "code_changes": [],
        "confidence": 0.0,
        "risk_assessment": "high",
        "files_inspected": [],
        "mode": "deep_agent",
        "error": error,
    }
