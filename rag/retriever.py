"""
rag/retriever.py — Similarity Search (Qdrant Query)

HOW IT WORKS:
─────────────
Searches the Qdrant vector database for past events that are
semantically similar to a given query text.

USE CASES:
    1. RAG for Triage (Step 5):
       "This excerpt looks like a dependency_error. Have we seen similar?"
       → Returns top-K past events with same failure pattern
       → Helps LLM make better triage decisions

    2. Analytics Dashboard:
       "Show me all failures similar to 'ModuleNotFoundError: pandas'"
       → Returns ranked list of similar incidents

    3. De-duplication:
       "Is this the same failure we saw yesterday?"
       → High similarity score = likely duplicate

SEARCH FLOW:
    1. Embed the query text → 1536-dim vector (OpenAI text-embedding-3-small)
    2. Send to Qdrant with optional filters (repo, failure_type, etc.)
    3. Get top-K results ranked by cosine similarity
    4. Return results with metadata (event_id, repo, score, preview)

FILTERING:
    Qdrant supports payload filtering during search:
    - Filter by repo (only search your own failures)
    - Filter by failure_type (only show dependency_error matches)
    - Filter by embedding_type (only search excerpts, not plans)
    - Filter by date range

RESILIENCE:
    Qdrant is non-essential — triage works fine without RAG context.
    To prevent noisy logs and wasted latency when Qdrant is unreachable
    (e.g. running in Lambda with no VPC route to the vector DB), we
    implement a simple in-process circuit breaker:

        1. First connection error  → log WARNING, open circuit for 60s
        2. While circuit is open   → skip Qdrant calls silently, return []
        3. After TTL expires       → try once more (half-open state)
        4. Success                 → close circuit, resume normal operation

    The breaker is per-Lambda-container (warm-start friendly). It does
    NOT persist across cold starts, which is fine — once a fresh container
    discovers Qdrant is down, it'll back off again.

SELF-HEALING COLLECTION BOOTSTRAP (★ NEW):
    The retriever ALSO ensures its target collection exists on first
    connect. Without this, triage in step 5 (which runs before the
    indexer) would 404 forever on a fresh deployment. Creating the
    collection from the retriever is idempotent + race-tolerant — the
    indexer's later create_collection() call simply sees it already
    exists and moves on.

QDRANT CLIENT CONNECTION (cloud-safe defaults):
    - host           = settings.QDRANT_HOST  (e.g. "xxx.aws.cloud.qdrant.io")
    - port           = int(settings.QDRANT_PORT)  (6333 for REST)
    - https          = settings.QDRANT_HTTPS  ("true" default for cloud)
    - api_key        = resolved via shared.secrets.get_qdrant_api_key()
                       which handles Secrets Manager ARN + plain-env fallback
    - prefer_grpc    = False  (gRPC is unreliable from AWS Lambda egress)
    - timeout        = 30s    (covers Qdrant Cloud cold-start wake)

COMMUNICATION:
─────────────
Step 5 (triage.py) can call:
    retriever = Retriever()
    similar = retriever.search("ModuleNotFoundError: No module named 'flask'", top_k=5)
    → Returns list of similar past incidents for context
"""

import time
from typing import List, Dict, Any, Optional

from shared.config import settings
from shared.logger import get_logger
from rag.embedder import Embedder, EMBEDDING_DIM

# Optional Secrets Manager loader. Wrapped so the module still imports if the
# helper isn't available (e.g. in some unit-test contexts that stub shared/*).
# The canonical helper already handles:
#   - Reading QDRANT_API_KEY_SECRET_ARN env var
#   - Fetching the secret from AWS Secrets Manager
#   - Unwrapping plain-string OR JSON-envelope payloads
#   - Falling back to a direct QDRANT_API_KEY env var
try:
    from shared.secrets import get_qdrant_api_key  # type: ignore
except Exception:  # pragma: no cover - defensive
    get_qdrant_api_key = None  # type: ignore[assignment]

logger = get_logger("rag.retriever")

COLLECTION_NAME = "repomind_events"

# ── Circuit breaker tunables ──────────────────────────────────────────
# How long (seconds) to skip Qdrant after a connection failure.
# After this TTL we try one request again ("half-open" probe).
_DEFAULT_CIRCUIT_BREAKER_TTL = 60

# Connection / read timeout per Qdrant request. Bumped from 5 → 30s
# because Qdrant Cloud free-tier clusters cold-wake in 5–15s on first
# request after idle. The circuit breaker still protects us — if a 30s
# timeout fires once, we back off for 60s before trying again.
_DEFAULT_QDRANT_TIMEOUT = 30

# Network-level errors that mean "Qdrant is unreachable" (vs. malformed
# query / 4xx response, which we still want to surface loudly).
_CONNECTION_ERROR_TOKENS = (
    "connection refused",
    "connection reset",
    "name or service not known",
    "temporary failure in name resolution",
    "timed out",
    "timeout",
    "no route to host",
    "network is unreachable",
)


# ──────────────────────────────────────────────
# Module helpers (no class state needed)
# ──────────────────────────────────────────────
def _resolve_qdrant_api_key() -> Optional[str]:
    """
    Resolve the Qdrant API key via the canonical shared.secrets helper,
    which already handles:
      - Reading QDRANT_API_KEY_SECRET_ARN env var
      - Fetching the secret from AWS Secrets Manager
      - Unwrapping plain-string OR JSON-envelope payloads
      - Falling back to a direct QDRANT_API_KEY env var
    Returns None on any failure (caller treats RAG as non-essential).
    """
    if get_qdrant_api_key is None:
        # Helper not importable — degrade gracefully to direct env lookup
        direct = getattr(settings, "QDRANT_API_KEY", None)
        return str(direct) if direct else None

    try:
        key = get_qdrant_api_key()
        return key if key else None
    except Exception as exc:
        logger.warning("qdrant_secret_load_failed", error=str(exc))
        return None


def _parse_https(value) -> bool:
    """Coerce an env-var style value ("true"/"false"/bool/None) to bool."""
    if isinstance(value, bool):
        return value
    if value is None:
        return True  # safe default for cloud deployments
    return str(value).strip().lower() in ("1", "true", "yes", "on")


class SearchResult:
    """A single search result from Qdrant."""

    def __init__(self, score: float, payload: Dict[str, Any]):
        self.score = score
        self.event_id = payload.get("event_id", "")
        self.repo = payload.get("repo", "")
        self.embedding_type = payload.get("embedding_type", "")
        self.failure_type = payload.get("failure_type", "unknown")
        self.confidence = payload.get("confidence", 0.0)
        self.text_preview = payload.get("text_preview", "")
        self.timestamp = payload.get("timestamp", "")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "event_id": self.event_id,
            "repo": self.repo,
            "embedding_type": self.embedding_type,
            "failure_type": self.failure_type,
            "confidence": self.confidence,
            "text_preview": self.text_preview,
            "timestamp": self.timestamp,
        }

    def __repr__(self):
        return (
            f"SearchResult(score={self.score:.3f}, "
            f"type={self.failure_type}, repo={self.repo})"
        )


class Retriever:
    """
    Searches Qdrant for semantically similar past CI failures.

    Resilient to Qdrant being down — uses a circuit breaker so a single
    connection failure doesn't slow down every subsequent Lambda call.
    """

    # Circuit-breaker state — class-level so it survives across warm
    # invocations within the same Lambda container.
    _circuit_open_until: float = 0.0
    _circuit_breaker_ttl: int = int(
        getattr(settings, "QDRANT_CIRCUIT_BREAKER_TTL", _DEFAULT_CIRCUIT_BREAKER_TTL)
    )

    def __init__(self):
        self.embedder = Embedder()
        self._qdrant = None

    # ──────────────────────────────────────────────
    # Connection management
    # ──────────────────────────────────────────────
    def _get_qdrant(self):
        """
        Lazy-load Qdrant client with cloud-safe defaults and ensure the
        target collection exists.

        Why ensure-collection here too (not just in indexer)?
            The retriever frequently runs BEFORE the indexer in a cold
            deployment (triage in step 5 happens before persistence). If
            the collection doesn't exist yet, every search returns 404
            and Qdrant logs noisy errors. Creating it here (idempotently)
            means the first search returns 0 hits cleanly, and the next
            indexer.upsert() just writes into the existing collection.

        Reads settings (all optional except host/port):
            QDRANT_HOST                 — e.g. "xxx.aws.cloud.qdrant.io"
            QDRANT_PORT                 — e.g. 6333 (HTTP/REST)
            QDRANT_HTTPS                — "true" to use TLS (default True)
            QDRANT_API_KEY_SECRET_ARN   — preferred: Secrets Manager ARN
            QDRANT_API_KEY              — fallback: plain-string API key
            QDRANT_TIMEOUT              — seconds (default 30)

        Raises:
            Any exception from QdrantClient(...) is re-raised so the
            caller's circuit-breaker logic can classify it. Collection
            ensure failures are soft-logged but do NOT raise.
        """
        if self._qdrant is None:
            from qdrant_client import QdrantClient
            from qdrant_client.models import Distance, VectorParams

            api_key = _resolve_qdrant_api_key()
            https = _parse_https(getattr(settings, "QDRANT_HTTPS", "true"))
            host = settings.QDRANT_HOST
            port = int(getattr(settings, "QDRANT_PORT", 6333) or 6333)
            timeout = int(getattr(settings, "QDRANT_TIMEOUT", _DEFAULT_QDRANT_TIMEOUT))

            # ── Construct client ──
            try:
                self._qdrant = QdrantClient(
                    host=host,
                    port=port,
                    https=https,
                    api_key=api_key,
                    prefer_grpc=False,   # gRPC is unreliable from Lambda egress
                    timeout=timeout,
                )
                logger.info(
                    "qdrant_client_initialized",
                    host=host,
                    port=port,
                    https=https,
                    has_api_key=bool(api_key),
                    timeout=timeout,
                )
            except Exception as exc:
                logger.error(
                    "qdrant_client_init_failed",
                    host=host,
                    port=port,
                    https=https,
                    has_api_key=bool(api_key),
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                self._qdrant = None
                raise

            # ── Ensure collection exists (idempotent + race-tolerant) ──
            # We do this in the retriever too because triage searches BEFORE
            # the indexer ever writes. Without this, every cold-start
            # search returns 404 until the first indexer.upsert() lands.
            try:
                collections = [
                    c.name for c in self._qdrant.get_collections().collections
                ]
                if COLLECTION_NAME not in collections:
                    try:
                        self._qdrant.create_collection(
                            collection_name=COLLECTION_NAME,
                            vectors_config=VectorParams(
                                size=EMBEDDING_DIM,
                                distance=Distance.COSINE,
                            ),
                        )
                        logger.info(
                            "qdrant_collection_created_by_retriever",
                            name=COLLECTION_NAME,
                            dim=EMBEDDING_DIM,
                        )
                    except Exception as create_exc:
                        # Race: indexer or a parallel Lambda may have created
                        # the collection in the gap between get + create.
                        msg = str(create_exc).lower()
                        if "already exists" in msg or "conflict" in msg:
                            logger.debug(
                                "qdrant_collection_race_ignored",
                                name=COLLECTION_NAME,
                            )
                        else:
                            raise
                else:
                    logger.debug(
                        "qdrant_collection_exists",
                        name=COLLECTION_NAME,
                    )
            except Exception as exc:
                # Soft-fail: don't kill the client just because collection
                # bootstrap failed. The subsequent search will surface any
                # real Qdrant issue with full context.
                logger.warning(
                    "qdrant_collection_check_failed",
                    name=COLLECTION_NAME,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

        return self._qdrant

    # ──────────────────────────────────────────────
    # Circuit breaker
    # ──────────────────────────────────────────────
    @classmethod
    def is_available(cls) -> bool:
        """
        Public helper: returns False if the circuit is currently open
        (i.e. we've recently failed and are backing off).

        Useful for callers that want to skip building an expensive query
        when they know Qdrant won't answer.
        """
        return time.time() >= cls._circuit_open_until

    @classmethod
    def _open_circuit(cls, reason: str) -> None:
        cls._circuit_open_until = time.time() + cls._circuit_breaker_ttl
        logger.warning(
            "rag_circuit_opened",
            ttl_seconds=cls._circuit_breaker_ttl,
            reason=reason,
            note="Qdrant calls will be skipped until TTL expires",
        )

    @classmethod
    def _close_circuit(cls) -> None:
        if cls._circuit_open_until > 0:
            logger.info("rag_circuit_closed")
        cls._circuit_open_until = 0.0

    @staticmethod
    def _is_connection_error(exc: Exception) -> bool:
        """Classify an exception as a transient network error."""
        msg = str(exc).lower()
        return any(tok in msg for tok in _CONNECTION_ERROR_TOKENS)

    # ──────────────────────────────────────────────
    # Public search APIs (unchanged interface)
    # ──────────────────────────────────────────────
    def search(
        self,
        query_text: str,
        top_k: int = 5,
        repo_filter: Optional[str] = None,
        failure_type_filter: Optional[str] = None,
        embedding_type_filter: Optional[str] = None,
        score_threshold: float = 0.3,
    ) -> List[SearchResult]:
        """
        Search for similar past events by text query.

        Args:
            query_text: The text to search for (e.g. an error message)
            top_k: Number of results to return (default 5)
            repo_filter: Only return results from this repo
            failure_type_filter: Only return this failure type
            embedding_type_filter: Only search "excerpt", "triage", etc.
            score_threshold: Minimum cosine similarity (0.0 to 1.0)

        Returns:
            List of SearchResult objects, sorted by descending score.
            Returns [] (empty list) on any error — RAG is non-essential,
            so triage continues without it.
        """
        # ★ Circuit-breaker fast-path: skip Qdrant if we recently failed.
        if not self.is_available():
            logger.debug(
                "rag_search_skipped_circuit_open",
                query_preview=query_text[:80],
                retry_in_seconds=int(self._circuit_open_until - time.time()),
            )
            return []

        # Step 1: Embed the query
        try:
            query_vector = self.embedder.embed_text(query_text)
        except Exception as e:
            # Embedder failure is a real bug — surface loudly.
            logger.error("embed_failed", error=str(e), query_preview=query_text[:80])
            return []

        # Step 2: Build filters
        query_filter = self._build_filter(
            repo_filter=repo_filter,
            failure_type_filter=failure_type_filter,
            embedding_type_filter=embedding_type_filter,
        )

        # Step 3: Search Qdrant
        try:
            client = self._get_qdrant()
            results = client.search(
                collection_name=COLLECTION_NAME,
                query_vector=query_vector,
                query_filter=query_filter,
                limit=top_k,
                score_threshold=score_threshold,
            )

            search_results = [
                SearchResult(score=hit.score, payload=hit.payload)
                for hit in results
            ]

            # Successful round-trip → make sure the breaker is closed.
            self._close_circuit()

            logger.info(
                "search_completed",
                query_preview=query_text[:100],
                results_count=len(search_results),
                top_score=search_results[0].score if search_results else 0.0,
            )

            return search_results

        except Exception as e:
            # ★ Differentiate transient network errors from real bugs.
            if self._is_connection_error(e):
                logger.warning(
                    "rag_search_unreachable",
                    error=str(e),
                    error_type=type(e).__name__,
                    query_preview=query_text[:80],
                    note="Qdrant unreachable — opening circuit breaker",
                )
                self._open_circuit(reason=f"{type(e).__name__}: {str(e)[:120]}")
                # Reset the cached client so the next attempt re-resolves DNS.
                self._qdrant = None
            else:
                # Genuine Qdrant error (4xx, schema mismatch, etc.)
                logger.error(
                    "search_failed",
                    error=str(e),
                    error_type=type(e).__name__,
                    query_preview=query_text[:80],
                )
            return []

    def search_similar_failures(
        self,
        excerpt: str,
        repo: Optional[str] = None,
        top_k: int = 3,
    ) -> List[Dict[str, Any]]:
        """
        Convenience method for RAG retrieval in Step 5 (triage).

        Searches for past excerpts similar to the current failure.
        Returns dicts ready to inject into LLM prompt context.

        Args:
            excerpt: Current failure excerpt
            repo: Optionally scope to same repo
            top_k: Number of past incidents to retrieve

        Returns:
            List of dicts with {failure_type, summary, score}.
            Empty list if RAG is unavailable — caller should treat this
            as "no historical context" rather than an error.
        """
        results = self.search(
            query_text=excerpt,
            top_k=top_k,
            repo_filter=repo,
            embedding_type_filter="excerpt",
            score_threshold=0.4,
        )

        return [r.to_dict() for r in results]

    # ──────────────────────────────────────────────
    # Filter construction
    # ──────────────────────────────────────────────
    def _build_filter(
        self,
        repo_filter: Optional[str] = None,
        failure_type_filter: Optional[str] = None,
        embedding_type_filter: Optional[str] = None,
    ):
        """Build Qdrant filter from optional criteria."""
        conditions = []

        if repo_filter:
            from qdrant_client.models import FieldCondition, MatchValue
            conditions.append(
                FieldCondition(key="repo", match=MatchValue(value=repo_filter))
            )

        if failure_type_filter:
            from qdrant_client.models import FieldCondition, MatchValue
            conditions.append(
                FieldCondition(
                    key="failure_type",
                    match=MatchValue(value=failure_type_filter),
                )
            )

        if embedding_type_filter:
            from qdrant_client.models import FieldCondition, MatchValue
            conditions.append(
                FieldCondition(
                    key="embedding_type",
                    match=MatchValue(value=embedding_type_filter),
                )
            )

        if conditions:
            from qdrant_client.models import Filter
            return Filter(must=conditions)

        return None