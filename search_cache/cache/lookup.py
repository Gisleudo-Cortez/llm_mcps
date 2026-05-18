"""Two-layer cache lookup: exact SHA-256 hash → ANN cosine similarity."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .db import scalar_query
from .schema import SIMILARITY_THRESHOLDS
from .scoring import final_score, is_hard_excluded


def query_hash(query_text: str, parameters: dict) -> str:
    """Deterministic SHA-256 over query + parameters (model, temperature, etc.)."""
    payload = json.dumps({"q": query_text.strip(), **parameters}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def _coerce_datetime(val) -> datetime:
    """Normalise timestamps returned by LanceDB to UTC datetime."""
    if isinstance(val, datetime):
        return val.replace(tzinfo=timezone.utc) if val.tzinfo is None else val
    if isinstance(val, int):
        return datetime.fromtimestamp(val / 1_000_000, tz=timezone.utc)
    return datetime.now(timezone.utc)


def lookup(
    table,
    query_text: str,
    query_embedding: list[float],
    parameters: dict,
    category: str = "web_search",
) -> dict | None:
    """
    Return the best matching cache entry or None on miss.

    Layer 1: exact SHA-256 hash match (O(1) with scalar index).
    Layer 2: ANN cosine similarity search with staleness-weighted rescoring.
    """
    h = query_hash(query_text, parameters)

    # --- Layer 1: exact hash (must match category — same query, different context = miss) ---
    rows = scalar_query(
        table,
        f"query_hash = '{h}' AND category = '{category}' AND validity_state != 'EXPIRED'",
        limit=1,
    )
    if rows:
        entry = rows[0]
        if not is_hard_excluded(entry):
            entry["hit_type"] = "exact"
            entry["final_score"] = 1.0
            return entry

    # --- Layer 2: ANN cosine similarity ---
    threshold = SIMILARITY_THRESHOLDS.get(category, 0.90)
    where_expr = f"validity_state != 'EXPIRED' AND category = '{category}'"

    try:
        candidates = (
            table.search(query_embedding, vector_column_name="query_embedding")
                 .metric("cosine")
                 .where(where_expr, prefilter=True)
                 .limit(10)
                 .to_list()
        )
    except Exception:
        return None

    best: dict | None = None
    best_score = 0.0

    for row in candidates:
        # LanceDB cosine distance: 0=identical, 1=orthogonal (for normalized vectors)
        distance = row.get("_distance", 1.0)
        cosine_sim = max(0.0, 1.0 - distance)

        if cosine_sim < threshold:
            continue
        if is_hard_excluded(row):
            continue

        created_at = _coerce_datetime(row.get("created_at"))
        score = final_score(cosine_sim, created_at, category)

        if score > best_score:
            best_score = score
            best = row

    if best is not None and best_score > 0.5:
        best["hit_type"] = "semantic"
        best["final_score"] = round(best_score, 4)
        return best

    return None
