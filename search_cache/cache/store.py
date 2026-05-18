"""Write new entries to the cache with TTL and embedding computation."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from .db import ensure_vector_index
from .encoder import encode
from .lookup import query_hash
from .schema import CATEGORY_CONFIG, VALID_CATEGORIES, VALID_DOCUMENT_KINDS


def _token_estimate(text: str) -> int:
    """Rough token count: ~4 chars per token."""
    return max(1, len(text) // 4)


def _compute_ttl(category: str, document_kind: str) -> datetime | None:
    """Return absolute expiry datetime or None for no-expiry entries."""
    if document_kind == "STATIC":
        return None
    cfg = CATEGORY_CONFIG.get(category, CATEGORY_CONFIG["web_search"])
    max_age = cfg.get("max_age_days")
    if max_age is None:
        return None
    return datetime.now(timezone.utc) + timedelta(days=max_age)


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def store(
    table,
    query_text: str,
    full_content: str,
    summary: str,
    source_url: str,
    category: str = "web_search",
    filtered_terms: list[str] | None = None,
    parameters: dict | None = None,
    document_kind: str = "VERSIONED",
    api_cost_usd: float = 0.0,
    latency_ms: float = 0.0,
) -> str:
    """Insert a new cache entry and return its UUID."""
    if category not in VALID_CATEGORIES:
        category = "web_search"
    if document_kind not in VALID_DOCUMENT_KINDS:
        document_kind = "VERSIONED"
    if parameters is None:
        parameters = {}

    now = datetime.now(timezone.utc)
    entry_id = str(uuid.uuid4())
    embedding = encode(query_text)
    ttl = _compute_ttl(category, document_kind)

    row = {
        "id": entry_id,
        "query_hash": query_hash(query_text, parameters),
        "query_text": query_text.strip(),
        "filtered_terms": filtered_terms or [],
        "parameters": json.dumps(parameters, sort_keys=True),
        "query_embedding": embedding,
        "summary": summary,
        "full_content": full_content,
        "source_url": source_url,
        "source_version_hash": _content_hash(full_content),
        "category": category,
        "document_kind": document_kind,
        "validity_state": "VALID",
        "created_at": now,
        "last_accessed": now,
        "ttl_expires_at": ttl,
        "access_count": 0,
        "cache_hit_score": 0.0,
        "api_cost_usd": float(api_cost_usd),
        "latency_ms": float(latency_ms),
        "content_tokens": _token_estimate(full_content),
        "feedback": 0,
    }

    table.add([row])
    ensure_vector_index(table)
    return entry_id


def touch(table, entry_id: str, hit_score: float) -> None:
    """Update access_count and last_accessed on cache hit."""
    try:
        rows = table.search(None).where(f"id = '{entry_id}'").select(["access_count"]).limit(1).to_list()
        current_count = rows[0]["access_count"] if rows else 0
        table.update(
            where=f"id = '{entry_id}'",
            values={
                "access_count": current_count + 1,
                "last_accessed": datetime.now(timezone.utc),
                "cache_hit_score": float(hit_score),
            },
        )
    except Exception:
        pass
