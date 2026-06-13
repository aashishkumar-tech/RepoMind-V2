"""
agents/checkpointer.py — S3-backed LangGraph Checkpointer (V2 HITL)

WHY THIS EXISTS:
─────────────────
LangGraph's built-in `MemorySaver` keeps checkpoint state in-process. That's
useless for human-in-the-loop on AWS Lambda:

    Lambda timeout       = 15 min (hard cap)
    Human review latency = hours to days (sometimes weeks)

So we need a checkpointer that **persists state outside the Lambda runtime**.
This module implements `BaseCheckpointSaver` backed by S3 (in production)
or local disk (in dev).

HOW LANGGRAPH HITL WORKS:
─────────────────────────
1. Graph is compiled with `interrupt_before=["merge_decision_node"]`.
2. `graph.invoke(state, config={"configurable": {"thread_id": event_id}})`
   runs nodes in order until just BEFORE the merge_decision node.
3. LangGraph calls `checkpointer.put(...)` to save the state.
4. Lambda returns. Hours later, GitHub fires `pull_request_review` →
   review receives it → loads thread_id (= event_id) → calls
   `graph.invoke(None, config=..., resume="approved" | "rejected")`.
5. LangGraph calls `checkpointer.get(...)` to restore state.
6. Graph picks up at merge_decision_node with the human verdict in state.

S3 LAYOUT:
    checkpoints/<thread_id>/<checkpoint_id>.json    — full checkpoint blob
    checkpoints/<thread_id>/latest.txt              — pointer to most recent

COMMUNICATION:
─────────────
- agents/graph.py builds the graph with `get_checkpointer()`.
- review/review_handler.py loads state via the same checkpointer.

IMPORTANT:
    LangGraph's `BaseCheckpointSaver` interface changed across versions.
    We support the v0.3.x interface (which is what `requirements.txt` pins).
    If LangGraph isn't installed, this module is a no-op (returns None).
"""

import json
import time
from typing import Any, Dict, Optional, Iterator, Tuple, Sequence
from pathlib import Path

from shared.config import settings
from shared.logger import get_logger

logger = get_logger("agents.checkpointer")


# ──────────────────────────────────────────────
# Try to import LangGraph's checkpoint primitives. If unavailable,
# expose a no-op factory so the rest of the codebase doesn't break.
# ──────────────────────────────────────────────
try:
    from langgraph.checkpoint.base import (
        BaseCheckpointSaver,
        Checkpoint,
        CheckpointMetadata,
        CheckpointTuple,
    )
    from langgraph.checkpoint.memory import MemorySaver
    _LANGGRAPH_AVAILABLE = True
except ImportError:
    _LANGGRAPH_AVAILABLE = False
    BaseCheckpointSaver = object  # type: ignore
    Checkpoint = dict  # type: ignore
    CheckpointMetadata = dict  # type: ignore
    CheckpointTuple = tuple  # type: ignore
    MemorySaver = None  # type: ignore


# ──────────────────────────────────────────────
# S3-backed Checkpointer
# ──────────────────────────────────────────────
class S3CheckpointSaver(BaseCheckpointSaver):  # type: ignore
    """
    BaseCheckpointSaver backed by S3 (or any S3-compatible blob store).

    Each checkpoint is stored as a JSON blob keyed by thread_id + checkpoint_id.
    The "latest pointer" file `latest.txt` lets us find the newest checkpoint
    for a given thread without listing the whole prefix.

    NOTE: We use the `shared.storage` abstraction, so in dev this writes to
    `./data/checkpoints/...` on local disk. Prod hits S3.
    """

    def __init__(self, prefix: str = "checkpoints"):
        super().__init__()
        from shared.storage import get_storage
        self._storage = get_storage()
        self._prefix = prefix.rstrip("/")

    # ── Path helpers ──
    def _checkpoint_key(self, thread_id: str, checkpoint_id: str) -> str:
        return f"{self._prefix}/{thread_id}/{checkpoint_id}.json"

    def _latest_key(self, thread_id: str) -> str:
        return f"{self._prefix}/{thread_id}/latest.txt"

    # ── Required: get_tuple ──
    def get_tuple(self, config: Dict[str, Any]) -> Optional[Any]:
        """Return the latest checkpoint for the configured thread_id, or None."""
        thread_id = config.get("configurable", {}).get("thread_id")
        if not thread_id:
            return None

        # Either an explicit checkpoint_id was requested, or use latest pointer
        configurable = config.get("configurable", {})
        checkpoint_id = configurable.get("checkpoint_id")
        if not checkpoint_id:
            checkpoint_id = self._storage.get_text(self._latest_key(thread_id))
            if not checkpoint_id:
                return None

        blob = self._storage.get_json(self._checkpoint_key(thread_id, checkpoint_id))
        if not blob:
            return None

        checkpoint = blob.get("checkpoint", {})
        metadata = blob.get("metadata", {})
        parent_config = blob.get("parent_config")

        # Rebuild the LangGraph CheckpointTuple. The exact tuple shape varies
        # by LangGraph version; v0.3.x expects (config, checkpoint, metadata,
        # parent_config, pending_writes).
        return CheckpointTuple(
            config=config,
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=parent_config,
            pending_writes=blob.get("pending_writes", []),
        )

    # ── Required: list ──
    def list(
        self,
        config: Optional[Dict[str, Any]],
        *,
        filter: Optional[Dict[str, Any]] = None,
        before: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
    ) -> Iterator[Any]:
        """List checkpoints for a thread. Best-effort — used mainly for debugging."""
        # We don't implement a full listing for S3 (would require list_objects);
        # for HITL we only ever need the latest, which `get_tuple` handles.
        if config is None:
            return iter([])
        latest = self.get_tuple(config)
        return iter([latest] if latest else [])

    # ── Required: put ──
    def put(
        self,
        config: Dict[str, Any],
        checkpoint: Any,
        metadata: Any,
        new_versions: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Persist a checkpoint. Returns a config that points back at it."""
        thread_id = config.get("configurable", {}).get("thread_id")
        if not thread_id:
            raise ValueError("S3CheckpointSaver requires configurable.thread_id")

        checkpoint_id = (
            checkpoint.get("id")
            if isinstance(checkpoint, dict)
            else getattr(checkpoint, "id", None)
        ) or str(int(time.time() * 1000))

        # Convert checkpoint object → JSON-friendly dict
        checkpoint_dict = (
            dict(checkpoint) if isinstance(checkpoint, dict) else self._coerce(checkpoint)
        )
        metadata_dict = (
            dict(metadata) if isinstance(metadata, dict) else self._coerce(metadata)
        )

        blob = {
            "checkpoint": checkpoint_dict,
            "metadata": metadata_dict,
            "parent_config": config,
            "saved_at": time.time(),
        }

        try:
            self._storage.put_json(
                self._checkpoint_key(thread_id, checkpoint_id), blob
            )
            self._storage.put_text(
                self._latest_key(thread_id), checkpoint_id
            )
            logger.info(
                "checkpoint_saved",
                thread_id=thread_id,
                checkpoint_id=checkpoint_id,
            )
        except Exception as e:
            logger.error(
                "checkpoint_save_failed",
                thread_id=thread_id,
                error=str(e),
            )
            raise

        # Return updated config with the checkpoint_id baked in
        new_config = {
            **config,
            "configurable": {
                **config.get("configurable", {}),
                "thread_id": thread_id,
                "checkpoint_id": checkpoint_id,
            },
        }
        return new_config

    # ── Required: put_writes (LangGraph 0.3.x adds this) ──
    def put_writes(
        self,
        config: Dict[str, Any],
        writes: Sequence[Tuple[str, Any]],
        task_id: str,
    ) -> None:
        """
        Persist intermediate writes from a node. We append them to the
        latest checkpoint blob so they survive resume.
        """
        thread_id = config.get("configurable", {}).get("thread_id")
        checkpoint_id = config.get("configurable", {}).get("checkpoint_id")
        if not thread_id or not checkpoint_id:
            return

        try:
            blob = self._storage.get_json(
                self._checkpoint_key(thread_id, checkpoint_id)
            ) or {}
            pending = list(blob.get("pending_writes", []))
            for channel, value in writes:
                pending.append({"task_id": task_id, "channel": channel, "value": value})
            blob["pending_writes"] = pending
            self._storage.put_json(
                self._checkpoint_key(thread_id, checkpoint_id), blob
            )
        except Exception as e:
            logger.warning(
                "checkpoint_writes_failed",
                thread_id=thread_id,
                error=str(e),
            )

    # ── Helpers ──
    @staticmethod
    def _coerce(obj: Any) -> Any:
        """Best-effort coercion of LangGraph's TypedDict-ish objects to plain dict."""
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj
        try:
            return json.loads(json.dumps(obj, default=str))
        except Exception:
            return {"_repr": repr(obj)}


# ──────────────────────────────────────────────
# Factory
# ──────────────────────────────────────────────
_cached_checkpointer: Optional[Any] = None


def get_checkpointer() -> Optional[Any]:
    """
    Return the appropriate checkpointer for the current environment.

    - LangGraph not installed → None (HITL disabled, graph runs end-to-end)
    - Tests / dev with no S3   → MemorySaver if available, else None
    - Production               → S3CheckpointSaver

    The caller (agents/graph.py) must handle a None return by falling back
    to non-HITL execution.
    """
    global _cached_checkpointer

    if not _LANGGRAPH_AVAILABLE:
        return None

    if _cached_checkpointer is not None:
        return _cached_checkpointer

    try:
        # In dev with no S3 bucket, use MemorySaver (HITL works within a
        # single process — useful for local testing, useless for prod).
        if (
            settings.ENVIRONMENT == "development"
            and not settings.S3_DATA_BUCKET
        ):
            if MemorySaver is not None:
                logger.info("checkpointer_using_memory_saver")
                _cached_checkpointer = MemorySaver()
                return _cached_checkpointer
            return None

        # Otherwise, use S3 (or local-disk-via-LocalStorage in dev).
        logger.info("checkpointer_using_s3", prefix="checkpoints")
        _cached_checkpointer = S3CheckpointSaver(prefix="checkpoints")
        return _cached_checkpointer
    except Exception as e:
        logger.warning(
            "checkpointer_init_failed",
            error=str(e),
            msg="Falling back to no-checkpoint (HITL disabled)",
        )
        return None
