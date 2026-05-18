"""End-to-end integration tests for the search_cache MCP tools.

These tests call the tool functions from main.py directly (no subprocess/MCP
transport), using a temp directory for the LanceDB database so the real
cache_db/ is never touched.
"""
import json
import os
import sys
import tempfile
import time

import pytest

# Point to the package root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Fixture: isolated DB directory
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Redirect LanceDB to a temp directory so tests don't pollute cache_db/."""
    import cache.db as db_module

    # Override the module-level path before any table is created
    db_dir = str(tmp_path / "cache_db")
    monkeypatch.setattr(db_module, "_DB_PATH", db_dir)
    monkeypatch.setattr(db_module, "_table", None)  # reset cached table
    yield
    monkeypatch.setattr(db_module, "_table", None)  # clean up after test


# ---------------------------------------------------------------------------
# Helpers — call the tool functions directly
# ---------------------------------------------------------------------------
def _lookup(query, category="web_search", parameters="{}"):
    from main import cache_lookup
    return json.loads(cache_lookup(query, category=category, parameters=parameters))


def _store(query, full_content, summary, source_url="https://example.com",
           category="web_search", document_kind="VERSIONED",
           api_cost_usd=0.002, latency_ms=350.0):
    from main import cache_store
    return json.loads(cache_store(
        query=query, full_content=full_content, summary=summary,
        source_url=source_url, category=category, document_kind=document_kind,
        api_cost_usd=api_cost_usd, latency_ms=latency_ms,
    ))


def _stats():
    from main import cache_stats
    return json.loads(cache_stats())


def _sweep(dry_run=True):
    from main import cache_sweep
    return json.loads(cache_sweep(dry_run=dry_run))


def _invalidate(entry_id="", category="", older_than_days=0.0):
    from main import cache_invalidate
    return json.loads(cache_invalidate(entry_id=entry_id, category=category,
                                       older_than_days=older_than_days))


def _feedback(entry_id, positive):
    from main import cache_update_feedback
    return json.loads(cache_update_feedback(entry_id=entry_id, positive=positive))


def _categories():
    from main import cache_list_categories
    return cache_list_categories()


# ===========================================================================
# 1. Cold cache miss
# ===========================================================================
def test_cold_cache_miss():
    result = _lookup("What is the capital of France?")
    assert result["hit"] is False


# ===========================================================================
# 2. Store + exact hash hit
# ===========================================================================
def test_store_then_exact_hit():
    query = "Python asyncio event loop tutorial"
    content = "asyncio provides a single-threaded concurrency model..."
    summary = "How to use asyncio event loops in Python."

    stored = _store(query, content, summary)
    assert stored["stored"] is True
    assert "id" in stored
    assert stored["category"] == "web_search"
    assert stored["content_tokens"] > 0

    result = _lookup(query, category="web_search")
    assert result["hit"] is True
    assert result["hit_type"] == "exact"
    assert result["final_score"] == pytest.approx(1.0, abs=0.01)
    assert result["summary"] == summary
    assert result["full_content"] == content
    assert result["access_count"] >= 1


# ===========================================================================
# 3. Semantic (ANN) hit — similar but not identical query
#    ANN index requires ≥256 rows; for small DBs lookup falls back to
#    linear scan via scanner. We verify a hit is still returned.
# ===========================================================================
def test_semantic_hit_similar_query():
    # Use word-reordering: cosine ~0.997 with all-MiniLM-L6-v2, well above 0.93 threshold
    canonical = "Python asyncio event loop tutorial"
    content = "asyncio is a library to write concurrent code using async/await."
    summary = "Overview of Python asyncio."

    _store(canonical, content, summary, category="docs_technical")

    # Same words, different order — should produce a semantic hit
    similar = "asyncio event loop Python tutorial"
    result = _lookup(similar, category="docs_technical")

    assert result["hit"] is True
    assert result["hit_type"] in ("exact", "semantic")
    assert result["final_score"] > 0.5


# ===========================================================================
# 4. Different category does NOT produce a hit
# ===========================================================================
def test_different_category_no_hit():
    _store("Rust ownership model", "Ownership is Rust's central feature...",
           "Rust ownership summary.", category="docs_technical")

    # Same query but wrong category — should miss
    result = _lookup("Rust ownership model", category="live")
    assert result["hit"] is False


# ===========================================================================
# 5. stats reflects stored entries
# ===========================================================================
def test_stats_after_stores():
    _store("query A", "content A", "summary A", category="research")
    _store("query B", "content B", "summary B", category="news")

    stats = _stats()
    assert stats["total_entries"] >= 2
    assert stats["valid_entries"] >= 2
    assert "research" in stats["entries_by_category"]
    assert "news" in stats["entries_by_category"]
    # Cost should accumulate (default api_cost_usd=0.002 per store)
    # access_count starts at 0 until touch() runs, so cost_saved may be 0
    assert "estimated_cost_saved_usd" in stats


# ===========================================================================
# 6. Feedback updates the entry
# ===========================================================================
def test_feedback_positive_and_negative():
    stored = _store("LLM context window limits", "Context window info...",
                    "Context window summary.")
    entry_id = stored["id"]

    pos = _feedback(entry_id, positive=True)
    assert pos["updated"] is True
    assert pos["feedback"] == 1

    neg = _feedback(entry_id, positive=False)
    assert neg["updated"] is True
    assert neg["feedback"] == 0  # 1 - 1 = 0


# ===========================================================================
# 7. Invalidate by entry_id → lookup misses
# ===========================================================================
def test_invalidate_by_id():
    stored = _store("FastAPI dependency injection", "FastAPI DI docs...",
                    "FastAPI DI overview.", category="docs_technical")
    entry_id = stored["id"]

    inv = _invalidate(entry_id=entry_id)
    assert inv["invalidated"] == 1
    assert inv["method"] == "by_id"

    result = _lookup("FastAPI dependency injection", category="docs_technical")
    assert result["hit"] is False


# ===========================================================================
# 8. Invalidate by category
# ===========================================================================
def test_invalidate_by_category():
    _store("live score A", "score...", "score summary.", category="live")
    _store("live score B", "score...", "score summary.", category="live")
    _store("research paper X", "paper...", "paper summary.", category="research")

    inv = _invalidate(category="live")
    assert inv["invalidated"] == 2
    assert inv["method"] == "by_category"

    # Research entry should be unaffected
    result = _lookup("research paper X", category="research")
    assert result["hit"] is True


# ===========================================================================
# 9. STATIC entries are exempt from category invalidation
# ===========================================================================
def test_static_exempt_from_category_invalidation():
    _store("RFC 2616 HTTP/1.1 spec", "HTTP spec text...",
           "HTTP/1.1 RFC summary.", category="static", document_kind="STATIC")

    inv = _invalidate(category="static")
    # STATIC documents must not be touched by category invalidation
    assert inv["invalidated"] == 0

    result = _lookup("RFC 2616 HTTP/1.1 spec", category="static")
    assert result["hit"] is True


# ===========================================================================
# 10. cache_store returns correct TTL for non-STATIC entries
# ===========================================================================
def test_store_ttl_present_for_versioned():
    stored = _store("Django ORM queryset docs", "ORM docs...",
                    "Django ORM summary.", category="docs_technical",
                    document_kind="VERSIONED")
    assert stored["ttl_expires_at"] is not None
    # TTL should be in the future (ISO string contains date)
    assert "2026" in stored["ttl_expires_at"] or "2027" in stored["ttl_expires_at"] or "2028" in stored["ttl_expires_at"]


def test_store_no_ttl_for_static():
    stored = _store("Alice in Wonderland full text", "Chapter 1...",
                    "Classic children's novel.", category="static",
                    document_kind="STATIC")
    assert stored["ttl_expires_at"] is None


# ===========================================================================
# 11. sweep dry_run returns counts without deleting
# ===========================================================================
def test_sweep_dry_run_no_deletion():
    _store("ephemeral query", "content", "summary.", category="web_search")
    stats_before = _stats()

    result = _sweep(dry_run=True)
    assert result["dry_run"] is True
    assert "newly_expired" in result
    assert "evicted" in result

    # Nothing should have been deleted
    stats_after = _stats()
    assert stats_after["total_entries"] == stats_before["total_entries"]


# ===========================================================================
# 12. cache_list_categories returns a markdown table
# ===========================================================================
def test_list_categories_format():
    table = _categories()
    assert "| Category |" in table
    assert "| live |" in table
    assert "| static |" in table
    assert "Never" in table  # static max_age
    lines = [l for l in table.strip().split("\n") if l.startswith("|")]
    # Header + separator + 7 category rows = 9
    assert len(lines) == 9


# ===========================================================================
# 13. Unknown category defaults to web_search (no crash)
# ===========================================================================
def test_unknown_category_defaults():
    result = _lookup("anything", category="nonexistent_category")
    assert result["hit"] is False  # just a miss, not an error

    stored = _store("anything", "content", "summary.", category="bogus")
    assert stored["stored"] is True
    assert stored["category"] == "web_search"  # silently coerced


# ===========================================================================
# 14. Feedback on non-existent entry returns error
# ===========================================================================
def test_feedback_nonexistent_entry():
    result = _feedback("00000000-0000-0000-0000-000000000000", positive=True)
    assert result["updated"] is False
    assert "error" in result
