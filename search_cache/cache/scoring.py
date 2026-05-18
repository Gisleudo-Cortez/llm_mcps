"""Staleness decay scoring and eviction ranking."""
from __future__ import annotations

import math
from datetime import datetime, timezone

from .schema import CATEGORY_CONFIG


def _age_days(created_at: datetime) -> float:
    now = datetime.now(timezone.utc)
    # Handle naive datetimes stored without timezone
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return max(0.0, (now - created_at).total_seconds() / 86400.0)


def decay_score(created_at: datetime, half_life_days: float) -> float:
    """Exponential half-life decay: 1.0 at creation, 0.5 at half_life_days, etc."""
    return 0.5 ** (_age_days(created_at) / half_life_days)


def final_score(cosine_sim: float, created_at: datetime, category: str) -> float:
    """
    Fuse semantic similarity with temporal freshness.
    final_score = alpha * cosine + (1 - alpha) * decay
    where alpha = 1 - temporal_weight.
    """
    cfg = CATEGORY_CONFIG.get(category, CATEGORY_CONFIG["web_search"])
    alpha = 1.0 - cfg["temporal_weight"]
    d = decay_score(created_at, cfg["half_life_days"])
    return alpha * cosine_sim + cfg["temporal_weight"] * d


def is_hard_excluded(entry: dict) -> bool:
    """Return True if the entry is past its max_age threshold (hard exclusion)."""
    if entry.get("document_kind") == "STATIC":
        return False
    if entry.get("validity_state") == "EXPIRED":
        return True
    category = entry.get("category", "web_search")
    max_age = CATEGORY_CONFIG.get(category, {}).get("max_age_days")
    if max_age is None:
        return False
    created_at = entry.get("created_at")
    if created_at is None:
        return False
    if isinstance(created_at, int):  # microseconds since epoch from PyArrow
        created_at = datetime.fromtimestamp(created_at / 1_000_000, tz=timezone.utc)
    return _age_days(created_at) > max_age


def eviction_score(entry: dict) -> float:
    """
    LCFU eviction score (Asteria-inspired).
    Lower score = evict first. Preserves expensive, frequent, slow, stable entries.
    """
    freq = math.log(max(entry.get("access_count", 0), 0) + 1)
    cost = math.log(entry.get("api_cost_usd", 0.0) * 1000 + 1)
    lat  = math.log(entry.get("latency_ms", 0.0) + 1)
    hl   = CATEGORY_CONFIG.get(entry.get("category", "web_search"), {}).get("half_life_days", 3.0)
    stat = math.log(hl + 1)
    size = max(entry.get("content_tokens", 1), 1)
    return (freq * cost * lat * stat) / size
