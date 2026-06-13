"""
review/models.py — Data models for HITL review events.
"""

from typing import Optional, Dict, Any, Literal
from pydantic import BaseModel, Field
from datetime import datetime, timezone


# Review states — what GitHub gives us in the `review.state` field
ReviewState = Literal[
    "approved",
    "changes_requested",
    "commented",
    "dismissed",
    "pending",
]


# What we feed back to LangGraph
HumanApproval = Literal[
    "approved",
    "rejected",
    "timeout",
    "skipped",
    "pending",
]


class ReviewMessage(BaseModel):
    """
    Normalized SQS message for a PR review event.

    Built by webhook/webhook_handler.py from the raw GitHub
    `pull_request_review` payload.
    """
    event_id: str = ""              # Original CI failure event_id (= thread_id)
    repo: str
    pr_number: int
    pr_url: str = ""
    review_id: int = 0
    review_state: str = ""          # raw GitHub state
    review_body: str = ""
    reviewer: str = ""
    head_sha: str = ""
    message_type: str = "review"
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )

    def to_human_approval(self) -> str:
        """Translate GitHub's review state into our HITL verdict."""
        state = (self.review_state or "").lower()
        if state == "approved":
            return "approved"
        if state == "changes_requested":
            return "rejected"
        # commented / dismissed / pending → not actionable, keep waiting
        return "pending"
