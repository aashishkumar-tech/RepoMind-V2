"""
review — Human-in-the-Loop Review Handler (V2)

PURPOSE:
─────────
When a paused LangGraph pipeline is waiting for human review on a PR,
this module receives the GitHub `pull_request_review` webhook event and
resumes the graph with the verdict.

FLOW:
    GitHub PR review → Step 1 webhook handler routes to review (via SQS)
                    → review.review_handler.handle_review()
                    → loads thread_id from event_id (mapping in S3)
                    → calls agents.graph.resume_pipeline(verdict)
                    → graph runs merge_decision → merge | cleanup → END

WHY A SEPARATE STEP:
    The original pipeline (steps 1-11) is purely failure-driven.
    HITL is review-driven — a different event source, a different lifecycle.
    Keeping it isolated makes the graph topology and ownership clearer.
"""

from review.review_handler import ReviewHandler, handle_review_message
from review.models import HumanApproval, ReviewMessage

__all__ = ["ReviewHandler", "handle_review_message", "HumanApproval", "ReviewMessage"]
