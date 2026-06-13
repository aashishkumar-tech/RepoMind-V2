"""
tests/test_deep_solver.py — Tests for the hybrid solver

Tests both:
  1. agents/deep_solver.py    — the Deep Agent solver itself (mocked agent)
  2. agents/nodes.py solver_node — the hybrid orchestration (deep → fallback)
"""
import pytest
from unittest.mock import patch, MagicMock


# ──────────────────────────────────────────────
# Unit tests for deep_solver helpers
# ──────────────────────────────────────────────
class TestDeepSolverHelpers:
    """Pure-function tests — no LLM calls needed."""

    def test_extract_json_block_fenced(self):
        from agents.deep_solver import _extract_json_block
        text = 'Some preamble\n```json\n{"a": 1, "b": [2, 3]}\n```\nTrailing text'
        result = _extract_json_block(text)
        assert result is not None
        assert '"a": 1' in result

    def test_extract_json_block_balanced_fallback(self):
        from agents.deep_solver import _extract_json_block
        text = 'Reasoning here. {"reasoning": "x", "code_changes": []} and more.'
        result = _extract_json_block(text)
        assert result is not None
        assert '"code_changes"' in result

    def test_extract_json_block_no_json(self):
        from agents.deep_solver import _extract_json_block
        text = "Just a plain sentence with no JSON."
        assert _extract_json_block(text) is None

    def test_format_rag_context_empty(self):
        from agents.deep_solver import _format_rag_context
        assert _format_rag_context([]) == ""

    def test_format_rag_context_with_incidents(self):
        from agents.deep_solver import _format_rag_context
        incidents = [
            {"payload": {"triage_summary": "missing httpx", "plan_description": "add httpx==0.28"}},
        ]
        result = _format_rag_context(incidents)
        assert "PAST SIMILAR" in result
        assert "missing httpx" in result

    def test_tool_budget_caps_reads(self):
        from agents.deep_solver import _ToolBudget
        budget = _ToolBudget(max_reads=2)
        assert budget.can_read()
        budget.record_read("a.py")
        assert budget.can_read()
        budget.record_read("b.py")
        assert not budget.can_read()
        assert budget.reads_used == 2
        assert budget.files_read == ["a.py", "b.py"]

    def test_empty_response_shape(self):
        from agents.deep_solver import _empty_response
        result = _empty_response("test error", reasoning="some reasoning")
        assert result["code_changes"] == []
        assert result["confidence"] == 0.0
        assert result["risk_assessment"] == "high"
        assert result["mode"] == "deep_agent"
        assert result["error"] == "test error"


# ──────────────────────────────────────────────
# Integration test: hybrid solver_node falls back on deep agent failure
# ──────────────────────────────────────────────
class TestHybridSolverFallback:
    """Test the deep-agent → direct-LLM fallback chain in solver_node."""

    def _make_state(self):
        return {
            "event_id": "evt-test-123",
            "repo": "test/repo",
            "head_sha": "abc123",
            "head_branch": "main",
            "excerpt": "ModuleNotFoundError: httpx",
            "triage": {
                "failure_type": "dependency_error",
                "confidence": 0.9,
                "summary": "Missing httpx package",
                "affected_file": "requirements.txt",
                "affected_package": "httpx",
            },
            "plan_summary": {
                "description": "Add httpx to requirements.txt",
                "actions": ["Append httpx==0.28.0"],
                "code_changes": [],  # Empty → solver must fill in
            },
            "similar_incidents": [],
        }

    def test_solver_uses_deep_agent_when_available(self):
        """When deep agent succeeds, solver_node should use its output."""
        deep_result = {
            "reasoning": "Read requirements.txt, found requests but not httpx",
            "code_changes": [
                {"file": "requirements.txt", "action": "modify",
                 "old_content": "requests==2.31.0",
                 "new_content": "requests==2.31.0\nhttpx==0.28.0"}
            ],
            "confidence": 0.95,
            "risk_assessment": "low",
            "files_inspected": ["requirements.txt"],
            "mode": "deep_agent",
        }

        with patch("agents.deep_solver.run_deep_solver", return_value=deep_result):
            from agents.nodes import solver_node
            state = self._make_state()
            result = solver_node(state)

        plan = result["plan_summary"]
        assert plan["solver_mode"] == "deep_agent"
        assert plan["solver_confidence"] == 0.95
        assert len(plan["code_changes"]) == 1
        assert plan["code_changes"][0]["file"] == "requirements.txt"
        assert plan["solver_files_inspected"] == ["requirements.txt"]

    def test_solver_falls_back_when_deep_agent_times_out(self):
        """When deep agent times out, solver_node should call the direct LLM."""
        with patch("agents.deep_solver.run_deep_solver", side_effect=TimeoutError("45s exceeded")):
            with patch("shared.azure_llm.get_llm_client") as mock_client_fn, \
                 patch("shared.azure_llm.get_model_name", return_value="gpt-4o"):
                mock_client = MagicMock()
                mock_response = MagicMock()
                mock_response.choices[0].message.content = '{"reasoning": "fallback fix", "code_changes": [{"file": "requirements.txt", "action": "modify", "description": "add httpx", "old_content": "", "new_content": "httpx==0.28.0"}], "confidence": 0.7, "risk_assessment": "low"}'
                mock_client.chat.completions.create.return_value = mock_response
                mock_client_fn.return_value = mock_client

                from agents.nodes import solver_node
                state = self._make_state()
                result = solver_node(state)

        plan = result["plan_summary"]
        assert plan["solver_mode"] == "direct_llm"
        assert plan["solver_confidence"] == 0.7
        assert len(plan["code_changes"]) == 1

    def test_solver_falls_back_when_deep_agent_raises(self):
        """When deep agent raises any exception, solver_node should fall back."""
        with patch("agents.deep_solver.run_deep_solver", side_effect=RuntimeError("deepagents not installed")):
            with patch("shared.azure_llm.get_llm_client") as mock_client_fn, \
                 patch("shared.azure_llm.get_model_name", return_value="gpt-4o"):
                mock_client = MagicMock()
                mock_response = MagicMock()
                mock_response.choices[0].message.content = '{"reasoning": "fallback", "code_changes": [{"file": "x", "action": "modify", "description": "y", "old_content": "a", "new_content": "b"}], "confidence": 0.6, "risk_assessment": "low"}'
                mock_client.chat.completions.create.return_value = mock_response
                mock_client_fn.return_value = mock_client

                from agents.nodes import solver_node
                state = self._make_state()
                result = solver_node(state)

        assert result["plan_summary"]["solver_mode"] == "direct_llm"

    def test_solver_skips_when_planner_already_provided_changes(self):
        """If planner gave concrete code_changes, solver should skip entirely."""
        from agents.nodes import solver_node
        state = self._make_state()
        state["plan_summary"]["code_changes"] = [
            {"file": "requirements.txt", "action": "modify",
             "old_content": "x", "new_content": "y"}
        ]
        result = solver_node(state)
        # Mode key should NOT be added because solver skipped
        assert "solver_mode" not in result["plan_summary"]
        assert len(result["plan_summary"]["code_changes"]) == 1

    def test_solver_returns_unchanged_plan_when_both_tiers_fail(self):
        """If deep agent AND direct LLM both fail, plan stays unchanged (no crash)."""
        with patch("agents.deep_solver.run_deep_solver", side_effect=RuntimeError("boom")):
            with patch("shared.azure_llm.get_llm_client", side_effect=RuntimeError("no creds")):
                from agents.nodes import solver_node
                state = self._make_state()
                result = solver_node(state)
        # Should not crash, just return empty plan
        assert "plan_summary" in result
        assert result["plan_summary"]["code_changes"] == []
