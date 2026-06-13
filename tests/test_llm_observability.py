"""
tests/test_llm_observability.py — Tier 2 LLM Observability tests

Tests:
  • cost estimation
  • prompt hashing
  • traced_completion success path (mocked client)
  • traced_completion error path (re-raises)
  • summarize_traces aggregation
  • LLM-as-judge: enabled/disabled, normalization, hallucination flag
"""
import pytest
from unittest.mock import patch, MagicMock


class TestCostEstimation:
    def test_known_model_cost(self):
        from shared.llm_observability import estimate_cost_usd
        # gpt-4o = $2.50 prompt + $10.00 completion per 1M tokens
        # 1000 prompt + 500 completion → $0.0025 + $0.005 = $0.0075
        cost = estimate_cost_usd("gpt-4o", 1000, 500)
        assert cost == pytest.approx(0.0075, rel=0.01)

    def test_unknown_model_falls_back_to_default(self):
        from shared.llm_observability import estimate_cost_usd
        cost = estimate_cost_usd("future-model-xyz", 1000, 500)
        # Should match gpt-4o default
        assert cost > 0

    def test_zero_tokens_zero_cost(self):
        from shared.llm_observability import estimate_cost_usd
        assert estimate_cost_usd("gpt-4o", 0, 0) == 0.0

    def test_groq_fallback_zero_cost(self):
        from shared.llm_observability import estimate_cost_usd
        cost = estimate_cost_usd("llama-3.3-70b-versatile", 10000, 5000)
        assert cost == 0.0


class TestPromptHashing:
    def test_hash_is_stable(self):
        from shared.llm_observability import hash_prompt
        msgs = [{"role": "system", "content": "hello"}, {"role": "user", "content": "hi"}]
        h1 = hash_prompt(msgs)
        h2 = hash_prompt(msgs)
        assert h1 == h2
        assert len(h1) == 12

    def test_hash_changes_when_prompt_changes(self):
        from shared.llm_observability import hash_prompt
        msgs1 = [{"role": "system", "content": "hello"}]
        msgs2 = [{"role": "system", "content": "world"}]
        assert hash_prompt(msgs1) != hash_prompt(msgs2)


class TestTracedCompletion:
    def _make_client(self, prompt_tokens=100, completion_tokens=50, response_id="resp_123"):
        client = MagicMock()
        usage = MagicMock(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )
        response = MagicMock(usage=usage, id=response_id)
        response.choices = [MagicMock(message=MagicMock(content="ok"))]
        client.chat.completions.create.return_value = response
        return client, response

    def test_success_path_captures_tokens(self):
        from shared.llm_observability import traced_completion
        client, _ = self._make_client(prompt_tokens=200, completion_tokens=80)
        response, trace = traced_completion(
            client,
            model="gpt-4o",
            messages=[{"role": "user", "content": "test"}],
            agent="triage",
            event_id="evt-1",
            repo="org/repo",
        )
        assert trace["success"] is True
        assert trace["agent"] == "triage"
        assert trace["model"] == "gpt-4o"
        assert trace["prompt_tokens"] == 200
        assert trace["completion_tokens"] == 80
        assert trace["total_tokens"] == 280
        assert trace["cost_usd"] > 0
        assert trace["response_id"] == "resp_123"
        assert trace["latency_ms"] >= 0
        assert trace["prompt_hash"]

    def test_error_path_reraises(self):
        from shared.llm_observability import traced_completion
        client = MagicMock()
        client.chat.completions.create.side_effect = RuntimeError("rate limit")
        with pytest.raises(RuntimeError, match="rate limit"):
            traced_completion(
                client,
                model="gpt-4o",
                messages=[{"role": "user", "content": "test"}],
                agent="solver",
            )

    def test_error_path_records_error_type(self):
        """Even when raising, the metrics should fire with status=error."""
        from shared.llm_observability import traced_completion
        client = MagicMock()
        client.chat.completions.create.side_effect = ValueError("bad input")
        with patch("observability.metrics.metrics") as mock_metrics:
            mock_metrics.llm_calls_total.labels.return_value = MagicMock()
            with pytest.raises(ValueError):
                traced_completion(
                    client,
                    model="gpt-4o",
                    messages=[{"role": "user", "content": "x"}],
                    agent="planner",
                )
            mock_metrics.llm_calls_total.labels.assert_called()


class TestSummarizeTraces:
    def test_empty_traces(self):
        from shared.llm_observability import summarize_traces
        s = summarize_traces([])
        assert s["total_calls"] == 0
        assert s["total_tokens"] == 0
        assert s["total_cost_usd"] == 0.0

    def test_aggregates_across_agents(self):
        from shared.llm_observability import summarize_traces
        traces = [
            {"agent": "triage", "model": "gpt-4o", "prompt_tokens": 100,
             "completion_tokens": 50, "total_tokens": 150, "latency_ms": 500,
             "cost_usd": 0.001, "success": True},
            {"agent": "planner", "model": "gpt-4o", "prompt_tokens": 200,
             "completion_tokens": 100, "total_tokens": 300, "latency_ms": 800,
             "cost_usd": 0.002, "success": True},
            {"agent": "triage", "model": "gpt-4o", "prompt_tokens": 50,
             "completion_tokens": 25, "total_tokens": 75, "latency_ms": 300,
             "cost_usd": 0.0005, "success": False, "error_type": "TimeoutError"},
        ]
        s = summarize_traces(traces)
        assert s["total_calls"] == 3
        assert s["successful_calls"] == 2
        assert s["failed_calls"] == 1
        assert s["total_tokens"] == 525
        assert s["prompt_tokens"] == 350
        assert s["completion_tokens"] == 175
        assert s["total_cost_usd"] == pytest.approx(0.0035, rel=0.01)

        assert "triage" in s["by_agent"]
        assert s["by_agent"]["triage"]["calls"] == 2
        assert s["by_agent"]["triage"]["failed_calls"] == 1
        assert s["by_agent"]["planner"]["calls"] == 1


class TestLLMJudge:
    def test_judge_disabled_returns_empty(self):
        from agents.llm_judge import judge_triage
        with patch("shared.config.settings") as mock_settings:
            mock_settings.LLM_JUDGE_ENABLED = "false"
            result = judge_triage(
                {"failure_type": "dependency_error"},
                "ModuleNotFoundError",
            )
            assert result == {}

    def test_judge_returns_normalized_verdict(self):
        from agents.llm_judge import judge_triage

        with patch("shared.azure_llm.get_llm_client") as mock_client_fn, \
             patch("shared.azure_llm.get_model_name", return_value="gpt-4o"):
            client = MagicMock()
            usage = MagicMock(prompt_tokens=300, completion_tokens=100, total_tokens=400)
            response = MagicMock(usage=usage, id="resp_judge_1")
            response.choices = [MagicMock(message=MagicMock(
                content='{"factuality_score": 0.9, "completeness_score": 0.85, "confidence_calibration": 0.8, "hallucination_flag": false, "issues": [], "overall_score": 0.87, "overall_grade": "A", "verdict_summary": "good"}'
            ))]
            client.chat.completions.create.return_value = response
            mock_client_fn.return_value = client

            result = judge_triage(
                triage={
                    "failure_type": "dependency_error",
                    "confidence": 0.9,
                    "summary": "Missing httpx",
                    "affected_file": "requirements.txt",
                    "affected_package": "httpx",
                },
                excerpt="ModuleNotFoundError: No module named 'httpx'",
                event_id="evt-test",
            )

        assert result["overall_grade"] == "A"
        assert result["factuality_score"] == 0.9
        assert result["hallucination_flag"] is False
        assert result["judged_agent"] == "triage"

    def test_judge_handles_invalid_json(self):
        from agents.llm_judge import judge_triage

        with patch("shared.azure_llm.get_llm_client") as mock_client_fn, \
             patch("shared.azure_llm.get_model_name", return_value="gpt-4o"):
            client = MagicMock()
            usage = MagicMock(prompt_tokens=300, completion_tokens=100, total_tokens=400)
            response = MagicMock(usage=usage, id="resp_x")
            response.choices = [MagicMock(message=MagicMock(content="not json at all"))]
            client.chat.completions.create.return_value = response
            mock_client_fn.return_value = client

            result = judge_triage(
                triage={"failure_type": "test_failure"},
                excerpt="some log",
            )
            # Should fail gracefully → empty dict
            assert result == {}

    def test_normalize_recomputes_overall_when_missing(self):
        from agents.llm_judge import _normalize
        verdict = {
            "factuality_score": 0.9,
            "completeness_score": 0.8,
            "confidence_calibration": 0.7,
            # no overall_score
        }
        result = _normalize(verdict)
        # overall = 0.9*0.45 + 0.8*0.35 + 0.7*0.20 = 0.405 + 0.28 + 0.14 = 0.825
        assert 0.82 <= result["overall_score"] <= 0.83
        assert result["overall_grade"] == "B"

    def test_normalize_clips_out_of_range_scores(self):
        from agents.llm_judge import _normalize
        verdict = {
            "factuality_score": 1.5,  # clipped to 1.0
            "completeness_score": -0.3,  # clipped to 0.0
            "confidence_calibration": "bad",  # → 0.0
            "hallucination_flag": True,
            "issues": "single issue",  # → list
        }
        result = _normalize(verdict)
        assert result["factuality_score"] == 1.0
        assert result["completeness_score"] == 0.0
        assert result["confidence_calibration"] == 0.0
        assert result["hallucination_flag"] is True
        assert isinstance(result["issues"], list)
