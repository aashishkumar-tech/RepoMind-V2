"""
verifier — Verifier + Rollback

Provides:
  - Verifier: checks if a fix branch CI passed or failed (verifier.verifier)
  - RollbackClient: creates a revert PR when fix CI fails (verifier.rollback)
  - Models: VerificationResult dataclass (verifier.models)

TRIGGER:
    GitHub sends workflow_run.completed on fix/* branches.
    Step 1 routes it to the worker with message_type="verification".
    Worker delegates to Step 10 Verifier.

COMMUNICATION:
─────────────
Step 1 (webhook) → routes fix/* branch events → SQS → Worker
Worker → verifier.verifier.verify() → verifier.rollback.rollback() (if CI failed)
"""
