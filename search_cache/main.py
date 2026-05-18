"""
Search Cache MCP Server

Local semantic cache for agent search results. Reduces external API usage by
storing and retrieving results via two-layer lookup (exact hash + ANN cosine
similarity) with category-based staleness scoring.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP

from cache.db import get_table, scalar_query, ensure_fts_index
from cache.encoder import encode
from cache.eviction import sweep
from cache.lookup import lookup, query_hash
from cache.schema import CATEGORY_CONFIG, VALID_CATEGORIES
from cache.scoring import eviction_score, final_score, is_hard_excluded
from cache.store import store, touch

mcp = FastMCP("search_cache")


# ---------------------------------------------------------------------------
# Tool: cache_lookup
# ---------------------------------------------------------------------------
@mcp.tool(
    name="cache_lookup",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def cache_lookup(
    query: str,
    category: str = "web_search",
    parameters: str = "{}",
    similarity_threshold: float = 0.0,
) -> str:
    """
    Look up a query in the local search cache before calling any external API.

    **TRIGGER CONDITION:** Call this BEFORE every external search API call
    (Exa, Brave, SearXNG, Crawl4AI, web scraping, etc.). On a hit, use the
    returned content directly and skip the API call entirely.

    **SEQUENCE GUIDANCE:**
    - On hit: use `summary` and `full_content` from the response. Optionally
      call `cache_update_feedback` if you later verify the answer quality.
    - On miss: call the external API, then call `cache_store` with the result.
    - `parameters` JSON: include model name, temperature, tools, or any context
      that would make the same query return a different answer in a different
      context (e.g., `{"model": "gpt-4", "temperature": 0}`).
    - `category` must be one of: live, news, web_search, docs_technical,
      research, reference, static.

    **CONSTRAINT WARNING:** Stale entries near their max_age are deranked but
    not removed until `cache_sweep` runs. If `final_score` < 0.5 the entry was
    returned only as a hint — verify before using directly.

    **OUTPUT EXPECTATION:** On hit returns JSON:
      {"hit": true, "hit_type": "exact"|"semantic", "final_score": float,
       "id": str, "summary": str, "full_content": str, "source_url": str,
       "category": str, "created_at": str, "access_count": int}
    On miss returns: {"hit": false}
    """
    if category not in VALID_CATEGORIES:
        category = "web_search"

    try:
        params = json.loads(parameters)
    except json.JSONDecodeError:
        params = {}

    table = get_table()
    embedding = encode(query)
    entry = lookup(table, query, embedding, params, category)

    if entry is None:
        return json.dumps({"hit": False})

    # Update access stats without blocking
    entry_id = entry.get("id", "")
    hit_score = entry.get("final_score", 0.0)
    touch(table, entry_id, hit_score)

    created_at = entry.get("created_at", "")
    if hasattr(created_at, "isoformat"):
        created_at = created_at.isoformat()

    return json.dumps({
        "hit": True,
        "hit_type": entry.get("hit_type", "semantic"),
        "final_score": entry.get("final_score", 0.0),
        "id": entry_id,
        "summary": entry.get("summary", ""),
        "full_content": entry.get("full_content", ""),
        "source_url": entry.get("source_url", ""),
        "category": entry.get("category", category),
        "document_kind": entry.get("document_kind", "VERSIONED"),
        "created_at": str(created_at),
        "access_count": (entry.get("access_count") or 0) + 1,
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool: cache_store
# ---------------------------------------------------------------------------
@mcp.tool(
    name="cache_store",
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
def cache_store(
    query: str,
    full_content: str,
    summary: str,
    source_url: str,
    category: str = "web_search",
    filtered_terms: list[str] | None = None,
    parameters: str = "{}",
    document_kind: str = "VERSIONED",
    api_cost_usd: float = 0.0,
    latency_ms: float = 0.0,
) -> str:
    """
    Store a search result in the cache after a successful external API call.

    **TRIGGER CONDITION:** Call this after `cache_lookup` returned a miss AND
    the external API returned results. Do not call on empty or error responses.

    **SEQUENCE GUIDANCE:**
    - Pass the exact same `query` and `parameters` you passed to `cache_lookup`.
    - `full_content`: the complete raw content returned by the API or scraper.
    - `summary`: a condensed version (1–5 sentences) you or the LLM produced.
    - `document_kind`:
      - "STATIC" for books, RFCs, standards, math definitions, Wikipedia
        articles about stable facts — these never expire.
      - "EVENT" for time-bound content (conferences, sales, live scores).
      - "VERSIONED" (default) for everything else.
    - `api_cost_usd` and `latency_ms` improve eviction decisions: expensive,
      slow results are preserved longer by the LCFU eviction policy.

    **CONSTRAINT WARNING:** `category` must be one of: live, news, web_search,
    docs_technical, research, reference, static. Invalid values default to
    web_search.

    **OUTPUT EXPECTATION:** Returns JSON:
      {"stored": true, "id": str, "category": str, "ttl_expires_at": str|null,
       "content_tokens": int}
    """
    try:
        params = json.loads(parameters)
    except json.JSONDecodeError:
        params = {}

    if category not in VALID_CATEGORIES:
        category = "web_search"

    table = get_table()
    entry_id = store(
        table=table,
        query_text=query,
        full_content=full_content,
        summary=summary,
        source_url=source_url,
        category=category,
        filtered_terms=filtered_terms,
        parameters=params,
        document_kind=document_kind,
        api_cost_usd=api_cost_usd,
        latency_ms=latency_ms,
    )

    from cache.schema import CATEGORY_CONFIG
    from datetime import datetime, timedelta, timezone
    cfg = CATEGORY_CONFIG.get(category, CATEGORY_CONFIG["web_search"])
    max_age = cfg.get("max_age_days")
    ttl_str: str | None = None
    if max_age and document_kind != "STATIC":
        ttl_str = (datetime.now(timezone.utc) + timedelta(days=max_age)).isoformat()

    token_estimate = max(1, len(full_content) // 4)
    return json.dumps({
        "stored": True,
        "id": entry_id,
        "category": category,
        "document_kind": document_kind,
        "ttl_expires_at": ttl_str,
        "content_tokens": token_estimate,
    })


# ---------------------------------------------------------------------------
# Tool: cache_update_feedback
# ---------------------------------------------------------------------------
@mcp.tool(
    name="cache_update_feedback",
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def cache_update_feedback(entry_id: str, positive: bool) -> str:
    """
    Mark a cache entry as useful (+1) or bad (-1) based on agent outcome.

    **TRIGGER CONDITION:** Use after verifying that a cached result led to a
    correct or incorrect answer. Positive feedback increases eviction resilience;
    negative feedback flags the entry for earlier eviction.

    **SEQUENCE GUIDANCE:** Call with the `id` returned by `cache_lookup`. A
    negative feedback does not immediately delete the entry — it lowers its
    eviction score so it's removed sooner by `cache_sweep`.

    **OUTPUT EXPECTATION:** Returns confirmation JSON: {"updated": true, "id": str}
    """
    table = get_table()
    try:
        rows = scalar_query(table, f"id = '{entry_id}'", limit=1)
        if not rows:
            return json.dumps({"updated": False, "error": "entry not found"})
        current = rows[0].get("feedback", 0)
        new_val = max(-10, min(10, (current or 0) + (1 if positive else -1)))
        table.update(where=f"id = '{entry_id}'", values={"feedback": new_val})
        return json.dumps({"updated": True, "id": entry_id, "feedback": new_val})
    except Exception as e:
        return json.dumps({"updated": False, "error": str(e)})


# ---------------------------------------------------------------------------
# Tool: cache_invalidate
# ---------------------------------------------------------------------------
@mcp.tool(
    name="cache_invalidate",
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def cache_invalidate(
    entry_id: str = "",
    category: str = "",
    older_than_days: float = 0.0,
) -> str:
    """
    Mark one or more cache entries as EXPIRED (soft delete).

    **TRIGGER CONDITION:** Call when an agent detects a cached answer was
    factually wrong or outdated — for example, after a tool call returned
    conflicting information, or after a source document was updated.

    **SEQUENCE GUIDANCE:**
    - To invalidate a specific entry: pass `entry_id` (from cache_lookup).
    - To invalidate all entries in a category: pass `category`.
    - To invalidate old entries across all categories: pass `older_than_days`.
    - STATIC document_kind entries are exempt from category/age invalidation
      (they must be invalidated by exact `entry_id`).

    **CONSTRAINT WARNING:** This is a soft delete — entries are marked EXPIRED
    and excluded from future lookups but remain on disk until `cache_sweep` runs.

    **OUTPUT EXPECTATION:** Returns JSON: {"invalidated": int, "method": str}
    """
    table = get_table()
    count = 0

    if entry_id:
        try:
            table.update(
                where=f"id = '{entry_id}'",
                values={"validity_state": "EXPIRED"},
            )
            count = 1
        except Exception:
            pass
        return json.dumps({"invalidated": count, "method": "by_id"})

    filters = ["validity_state != 'EXPIRED'", "document_kind != 'STATIC'"]
    if category and category in VALID_CATEGORIES:
        filters.append(f"category = '{category}'")
    if older_than_days > 0:
        from datetime import datetime, timedelta, timezone
        cutoff_dt = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        cutoff_naive = cutoff_dt.strftime("%Y-%m-%dT%H:%M:%S.%f")
        filters.append(f"created_at < CAST('{cutoff_naive}' AS TIMESTAMP)")

    where_expr = " AND ".join(filters)
    try:
        rows = scalar_query(table, where_expr, limit=50_000)
        ids = [r["id"] for r in rows]
        if ids:
            id_list = ", ".join(f"'{i}'" for i in ids)
            table.update(where=f"id IN ({id_list})", values={"validity_state": "EXPIRED"})
            count = len(ids)
    except Exception as e:
        return json.dumps({"invalidated": 0, "error": str(e)})

    method = "by_category" if category else "by_age" if older_than_days else "none"
    return json.dumps({"invalidated": count, "method": method})


# ---------------------------------------------------------------------------
# Tool: cache_stats
# ---------------------------------------------------------------------------
@mcp.tool(
    name="cache_stats",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def cache_stats() -> str:
    """
    Return cache performance statistics: hit rates, distribution, and cost savings.

    **TRIGGER CONDITION:** Use when diagnosing cache effectiveness, deciding
    whether to tune thresholds, or reporting agent efficiency metrics.

    **SEQUENCE GUIDANCE:** Call at any time — read-only operation. Combine with
    `cache_list_categories` to understand TTL configuration.

    **OUTPUT EXPECTATION:** Returns JSON with:
      - total_entries, valid_entries, expired_entries
      - entries_by_category: {category: count}
      - total_access_count: total cache hits across all entries
      - estimated_cost_saved_usd: sum of api_cost_usd for entries with access_count > 0
      - top_queries: list of most-accessed entries
    """
    table = get_table()

    try:
        total = table.count_rows()
    except Exception:
        return json.dumps({"total_entries": 0, "error": "table empty or unavailable"})

    if total == 0:
        return json.dumps({"total_entries": 0, "message": "Cache is empty."})

    try:
        all_rows = scalar_query(table, "id IS NOT NULL", limit=100_000)
    except Exception as e:
        return json.dumps({"total_entries": total, "error": str(e)})

    valid = sum(1 for r in all_rows if r.get("validity_state") != "EXPIRED")
    expired = total - valid

    by_category: dict[str, int] = {}
    for r in all_rows:
        cat = r.get("category", "unknown")
        by_category[cat] = by_category.get(cat, 0) + 1

    total_hits = sum(r.get("access_count", 0) or 0 for r in all_rows)
    cost_saved = sum(
        (r.get("api_cost_usd", 0.0) or 0.0) * (r.get("access_count", 0) or 0)
        for r in all_rows
    )

    top = sorted(all_rows, key=lambda r: r.get("access_count", 0) or 0, reverse=True)[:5]
    top_queries = [
        {
            "query": r.get("query_text", "")[:80],
            "category": r.get("category", ""),
            "access_count": r.get("access_count", 0),
        }
        for r in top
    ]

    return json.dumps({
        "total_entries": total,
        "valid_entries": valid,
        "expired_entries": expired,
        "entries_by_category": by_category,
        "total_access_count": total_hits,
        "estimated_cost_saved_usd": round(cost_saved, 4),
        "top_queries": top_queries,
    }, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool: cache_sweep
# ---------------------------------------------------------------------------
@mcp.tool(
    name="cache_sweep",
    annotations={
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def cache_sweep(dry_run: bool = True) -> str:
    """
    Run LCFU eviction: mark over-age entries as EXPIRED then hard-delete them.

    **TRIGGER CONDITION:** Run periodically (daily or weekly) for maintenance.
    Use `dry_run=true` first to preview what would be removed.

    **SEQUENCE GUIDANCE:**
    1. Call with `dry_run=true` to see how many entries would be evicted.
    2. If the preview looks correct, call again with `dry_run=false` to commit.
    STATIC entries are never evicted. Entries with positive feedback are
    given higher eviction scores (preserved longer).

    **CONSTRAINT WARNING:** `dry_run=false` permanently deletes EXPIRED entries
    from disk. This cannot be undone.

    **OUTPUT EXPECTATION:** Returns JSON:
      {"newly_expired": int, "evicted": int, "dry_run": bool,
       "sample_evicted": [{id, category, query}]}
    """
    table = get_table()
    result = sweep(table, dry_run=dry_run)
    return json.dumps(result, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool: cache_list_categories
# ---------------------------------------------------------------------------
@mcp.tool(
    name="cache_list_categories",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def cache_list_categories() -> str:
    """
    List all configured categories with half-life, TTL, and similarity threshold.

    **TRIGGER CONDITION:** Call when deciding which category to assign to a
    query, or when explaining cache behaviour to the user.

    **OUTPUT EXPECTATION:** Returns a markdown table with columns:
    category | half_life | max_age | temporal_weight | similarity_threshold
    """
    from cache.schema import SIMILARITY_THRESHOLDS

    lines = [
        "| Category | Half-Life | Max Age | Temporal Weight | Sim Threshold |",
        "|---|---|---|---|---|",
    ]
    for cat, cfg in CATEGORY_CONFIG.items():
        hl = cfg["half_life_days"]
        hl_str = f"{hl:.1f}d" if hl >= 1 else f"{hl * 24:.1f}h"
        max_age = cfg["max_age_days"]
        max_str = f"{max_age}d" if max_age else "Never"
        tw = cfg["temporal_weight"]
        thresh = SIMILARITY_THRESHOLDS.get(cat, 0.90)
        lines.append(f"| {cat} | {hl_str} | {max_str} | {tw} | {thresh} |")

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio")
