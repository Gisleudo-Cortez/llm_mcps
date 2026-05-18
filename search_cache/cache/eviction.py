"""LCFU eviction sweep and hard-exclude marking."""
from __future__ import annotations

from datetime import datetime, timezone

from .db import scalar_query
from .scoring import eviction_score, is_hard_excluded


def _mark_expired(table) -> int:
    """
    Mark entries as EXPIRED when they are past their max_age threshold.
    Returns count of newly expired entries.
    """
    now = datetime.now(timezone.utc)

    # Find VALID entries with a non-null ttl_expires_at that has passed
    now_naive = now.strftime("%Y-%m-%dT%H:%M:%S.%f")
    expired = scalar_query(
        table,
        f"validity_state = 'VALID' AND ttl_expires_at IS NOT NULL AND ttl_expires_at < CAST('{now_naive}' AS TIMESTAMP)",
        limit=10_000,
    )

    ids = [e["id"] for e in expired]
    if ids:
        id_list = ", ".join(f"'{i}'" for i in ids)
        table.update(
            where=f"id IN ({id_list})",
            values={"validity_state": "EXPIRED"},
        )
    return len(ids)


def sweep(table, dry_run: bool = True, max_evict: int = 1000) -> dict:
    """
    1. Mark entries past max_age as EXPIRED.
    2. Hard-delete EXPIRED entries ranked by LCFU (lowest score first).

    Returns a dict with sweep statistics.
    """
    newly_expired = _mark_expired(table)

    expired_entries = scalar_query(
        table,
        "validity_state = 'EXPIRED'",
        limit=max_evict,
    )

    if not expired_entries:
        return {
            "newly_expired": newly_expired,
            "evicted": 0,
            "dry_run": dry_run,
        }

    # Sort by LCFU — lowest eviction_score first (cheapest/stalest go first)
    expired_entries.sort(key=eviction_score)

    ids_to_delete = [e["id"] for e in expired_entries]

    if not dry_run and ids_to_delete:
        id_list = ", ".join(f"'{i}'" for i in ids_to_delete)
        table.delete(f"id IN ({id_list})")

    return {
        "newly_expired": newly_expired,
        "evicted": len(ids_to_delete),
        "dry_run": dry_run,
        "sample_evicted": [
            {"id": e["id"][:8], "category": e.get("category"), "query": e.get("query_text", "")[:60]}
            for e in expired_entries[:5]
        ],
    }
