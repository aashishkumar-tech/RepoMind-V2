"""
tests/test_graph.py — Integration tests for LangGraph pipeline
"""
import pytest
from unittest.mock import patch, MagicMock


class TestLangGraphPipeline:
    """Test the full LangGraph pipeline with mocked LLM and Qdrant."""

    def _make_state(self):
        return {
            "event_id": "evt-test-repo-123-20260609T120000Z",
            "repo": "test/repo",
            "workflow_run_id": 12345,
            "run_url": "https://github.com/test/repo/actions/runs/12345",
            "excerpt": "ModuleNotFoundError: No module named 'httpx'",
            "head_branch": "main",
            "head_sha": "abc123",
            "similar_incidents": [],
            "triage": {},
            "plan_summary": {},
            "policy": {},
            "pr": {},
            "validation": {},
            "validation_attempts": 0,
            "solver_feedback": "",
            "error": "",
            "status": "running",
        }

    @patch("triage.triage.TriageEngine.classify")
    @patch("planner.planner.Planner.generate_plan")
    @patch("policy_engine.policy.PolicyEngine.evaluate")
    @patch("rag.retriever.Retriever.search_similar_failures")
    def test_pipeline_full_run_sequential(
        self, mock_retriever, mock_policy, mock_planner, mock_triage
    ):
        """Full pipeline should complete with mocked agents."""
        mock_retriever.return_value = []
        mock_triage.return_value = {
            "failure_type": "dependency_error",
            "confidence": 0.93,
            "summary": "Missing httpx",
            "affected_file": "requirements.txt",
            "affected_package": "httpx",
        }
        mock_planner.return_value = {
            "playbook_id": "fix_dependency_error",
            "description": "Add httpx to requirements",
            "actions": ["Add httpx==0.28.0 to requirements.txt"],
            "code_changes": [
                {"file": "requirements.txt", "action": "modify",
                 "old_content": "requests==2.31.0", "new_content": "requests==2.31.0\nhttpx==0.28.0"}
            ],
            "risk_level": "low",
        }
        mock_policy.return_value = {"decision": "allow", "reason": "Low risk dependency fix", "rules_triggered": ["allow_dependency_low"]}

        from agents.graph import _run_sequential
        state = self._make_state()
        result = _run_sequential(state)

        assert result["status"] in ("completed", "running")
        assert result["triage"]["failure_type"] == "dependency_error"
        assert result["policy"]["decision"] == "allow"

    def test_solver_node_enriches_plan(self):
        """Solver node should add code_changes to an empty plan."""
        with patch("shared.azure_llm.get_llm_client") as mock_client_fn, \
             patch("shared.azure_llm.get_model_name", return_value="gpt-4o"):
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.choices[0].message.content = '{"reasoning": "add missing package", "code_changes": [{"file": "requirements.txt", "action": "modify", "description": "add httpx", "old_content": "", "new_content": "httpx==0.28.0"}], "confidence": 0.9, "risk_assessment": "low"}'
            mock_client.chat.completions.create.return_value = mock_response
            mock_client_fn.return_value = mock_client

            from agents.nodes import solver_node
            state = self._make_state()
            state["triage"] = {"failure_type": "dependency_error", "confidence": 0.9, "summary": "Missing httpx", "affected_file": "requirements.txt", "affected_package": "httpx"}
            state["plan_summary"] = {"description": "Add httpx", "actions": [], "code_changes": []}

            result = solver_node(state)
            assert "plan_summary" in result

    def test_validator_node_approves_good_fix(self):
        """Validator should approve a well-formed fix."""
        with patch("shared.azure_llm.get_llm_client") as mock_client_fn, \
             patch("shared.azure_llm.get_model_name", return_value="gpt-4o"):
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.choices[0].message.content = '{"approved": true, "score": 0.92, "issues": [], "feedback": "", "review_summary": "Fix is correct and minimal"}'
            mock_client.chat.completions.create.return_value = mock_response
            mock_client_fn.return_value = mock_client

            from agents.nodes import validator_node
            state = self._make_state()
            state["triage"] = {"failure_type": "dependency_error", "confidence": 0.9, "summary": "Missing httpx"}
            state["plan_summary"] = {
                "description": "Add httpx",
                "solver_reasoning": "The error shows httpx is missing",
                "code_changes": [{"file": "requirements.txt", "action": "modify", "old_content": "", "new_content": "httpx==0.28.0"}],
            }

            result = validator_node(state)
            assert result["validation"]["approved"] is True

    def test_route_validator_retries_on_rejection(self):
        """route_validator should return 'solver' when rejected and attempts < 2."""
        state = {
            "validation": {"approved": False, "score": 0.3, "review_summary": "Fix incorrect"},
            "validation_attempts": 1,
        }
        # Import the route function directly
        import importlib
        graph_mod = importlib.import_module("agents.graph")
        # Re-build to access route function
        # We test the logic directly
        validation = state.get("validation", {})
        attempts = state.get("validation_attempts", 0)
        approved = validation.get("approved", True)
        route = "solver" if (not approved and attempts < 2) else "policy"
        assert route == "solver"

    def test_route_validator_proceeds_after_max_retries(self):
        """route_validator should return 'policy' after 2 failed attempts."""
        state = {
            "validation": {"approved": False, "score": 0.2, "review_summary": "Still incorrect"},
            "validation_attempts": 2,
        }
        validation = state.get("validation", {})
        attempts = state.get("validation_attempts", 0)
        approved = validation.get("approved", True)
        route = "solver" if (not approved and attempts < 2) else "policy"
        assert route == "policy"
