"""
tests/test_review.py — Review handler / tests (V2 HITL)
"""

from unittest.mock import patch, MagicMock

from review.review_handler import (
    ReviewHandler,
    handle_review_message,
    store_pr_event_mapping,
    lookup_event_id_for_pr,
)
from review.models import ReviewMessage


class TestPREventMapping:
    def test_store_and_lookup_round_trip(self):
        # Use a mock storage so we don't hit S3/disk
        with patch("review.review_handler.get_storage") as mock_get:
            store = MagicMock()
            blob_holder = {}

            def put_json(key, blob):
                blob_holder[key] = blob

            def get_json(key):
                return blob_holder.get(key)

            store.put_json.side_effect = put_json
            store.get_json.side_effect = get_json
            mock_get.return_value = store

            store_pr_event_mapping("owner/repo", 42, "evt-abc-123")
            event_id = lookup_event_id_for_pr("owner/repo", 42)
            assert event_id == "evt-abc-123"

    def test_lookup_missing_returns_none(self):
        with patch("review.review_handler.get_storage") as mock_get:
            store = MagicMock()
            store.get_json.return_value = None
            mock_get.return_value = store
            assert lookup_event_id_for_pr("owner/repo", 999) is None


class TestReviewHandler:
    def test_unknown_pr_is_ignored(self):
        msg = ReviewMessage(
            repo="owner/repo",
            pr_number=99,
            review_state="approved",
            reviewer="alice",
        )
        with patch("review.review_handler.lookup_event_id_for_pr") as mock_lookup:
            mock_lookup.return_value = None
            result = ReviewHandler().handle(msg)
        assert result["status"] == "ignored"
        assert "PR was not opened by RepoMind" in result["reason"]

    def test_commented_review_is_non_actionable(self):
        msg = ReviewMessage(
            event_id="evt-1",
            repo="owner/repo",
            pr_number=1,
            review_state="commented",
        )
        result = ReviewHandler().handle(msg)
        assert result["status"] == "ignored"
        assert result["event_id"] == "evt-1"

    def test_approved_review_resumes_pipeline(self):
        msg = ReviewMessage(
            event_id="evt-approved-1",
            repo="owner/repo",
            pr_number=10,
            pr_url="https://github.com/owner/repo/pull/10",
            review_id=555,
            review_state="approved",
            review_body="LGTM",
            reviewer="alice",
            head_sha="abc",
        )
        with (
            patch("agents.graph.resume_pipeline") as mock_resume,
            patch.object(ReviewHandler, "_persist_resume_artifacts"),
        ):
            mock_resume.return_value = {
                "status": "completed",
                "merge_result": {"status": "merged"},
                "human_approval": "approved",
            }
            result = ReviewHandler().handle(msg)

        assert result["status"] == "resumed"
        assert result["verdict"] == "approved"
        assert result["merge_result"]["status"] == "merged"
        mock_resume.assert_called_once()
        call_kwargs = mock_resume.call_args.kwargs
        assert call_kwargs["event_id"] == "evt-approved-1"
        assert call_kwargs["human_approval"] == "approved"

    def test_changes_requested_resumes_with_rejected(self):
        msg = ReviewMessage(
            event_id="evt-rejected-1",
            repo="owner/repo",
            pr_number=11,
            review_state="changes_requested",
            review_body="No, this is wrong.",
            reviewer="bob",
        )
        with (
            patch("agents.graph.resume_pipeline") as mock_resume,
            patch.object(ReviewHandler, "_persist_resume_artifacts"),
        ):
            mock_resume.return_value = {
                "status": "completed",
                "cleanup_result": {"status": "rejected"},
                "human_approval": "rejected",
            }
            result = ReviewHandler().handle(msg)
        assert result["verdict"] == "rejected"
        assert result["cleanup_result"]["status"] == "rejected"


class TestHandleReviewMessage:
    def test_invalid_payload_returns_failed(self):
        # Missing required field `repo`
        bad = {"pr_number": "not-an-int"}
        result = handle_review_message(bad)
        assert result["status"] == "failed"
        assert "Invalid review message" in result["error"]

    def test_valid_payload_dispatches_to_handler(self):
        good = {
            "event_id": "evt-1",
            "repo": "owner/repo",
            "pr_number": 1,
            "review_state": "commented",  # non-actionable, won't hit graph
        }
        result = handle_review_message(good)
        assert result["status"] == "ignored"
