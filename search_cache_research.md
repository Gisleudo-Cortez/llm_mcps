# Local Search Cache for AI Agents — Research Report & Implementation Plan

**Date:** 2026-05-16 | **Sources:** 45+ across 3 research threads

---

## Executive Summary

- A **two-layer lookup** (exact SHA-256 hash → ANN cosine similarity) is the production standard. Exact match is O(1); ANN is O(log n).
- **LanceDB** is the optimal single store for this use case: embedded (no daemon), columnar, DuckDB SQL for analytics, native ANN, handles billions of vectors on disk, schema evolution without data copies.
- **Staleness** is best modeled as `final_score = α·cosine + (1-α)·0.5^(age_days/half_life)` with `α=0.7` and category-specific `half_life` values.
- **Static content** (books, RFCs, standards) uses `document_kind=STATIC`, `half_life=36500 days`, `temporal_weight=0.01` — effectively permanent.
- **Asteria's staticity score** (1–10 integer) is the most elegant per-entry signal for automated TTL assignment; we adapt it as `document_kind` + `half_life` columns.
- The MCP server exposes 7 tools that agents call in sequence: `cache_lookup` → `cache_store` → `cache_sweep` (maintenance).

---

## Part 1 — Research Synthesis

### 1.1 Semantic Caching Systems Landscape

All production semantic caches converge on the same dual-backend architecture:

| Layer            | Purpose                          | Engine                          |
| ---------------- | -------------------------------- | ------------------------------- |
| Vector store     | Embedding lookup + ANN search    | FAISS, HNSW, ChromaDB, pgvector |
| Relational store | Full response + metadata storage | SQLite, PostgreSQL, Redis       |

The two-layer lookup is universal:

```
1. SHA-256(model + query_text) → exact match → instant hit
2. embed(query_text) → ANN cosine search → threshold check → hit if similar
```

Only the user query is semantically compared. Model ID, temperature, and system prompt are hash-matched to prevent cross-context false positives.

**Cosine similarity thresholds by use case:**
| Use Case | Threshold |
|----------|-----------|
| General FAQ / web search | 0.88–0.92 |
| Technical docs / code | 0.93–0.95 |
| Agentic tool calls | 0.90 + secondary LLM validation |
| Compliance / legal | 0.96–0.98 |

**Most relevant prior art for this system:**

- **Asteria** (arXiv 2509.17360): per-entry `staticity` score (1–10), LCFU eviction, two-stage validation (ANN + lightweight judge model). Closest to the user's vision.
- **VectorQ** (arXiv 2502.03771): adaptive per-embedding threshold regions. Avoids global threshold tuning.
- **GPTCache**: dual-backend reference implementation (MIT, open source). Supports FAISS/Chroma + SQLite/PostgreSQL.

### 1.2 Hybrid DB Architecture Decision

**Recommendation: LanceDB as single store**

| Criterion            | LanceDB                 | pgvector            | ChromaDB               | sqlite-vec        |
| -------------------- | ----------------------- | ------------------- | ---------------------- | ----------------- |
| Setup                | `pip install lancedb`   | PostgreSQL required | `pip install chromadb` | `.load extension` |
| Max vectors          | Billions (disk)         | ~3M (50M+ w/ scale) | ~10M                   | ~500K             |
| SQL analytics        | DuckDB native           | Full PostgreSQL     | No                     | SQLite            |
| ANN + filter         | SQL WHERE + ANN         | Full SQL + HNSW     | 3–8x overhead          | Post-ANN SQL      |
| Hybrid (vector+BM25) | Native                  | tsvector + ANN      | FTS5 + ANN             | FTS5 + ANN        |
| Schema evolution     | Zero-copy column add    | ALTER TABLE         | Limited                | ALTER TABLE       |
| Daemon required      | No                      | Yes                 | No                     | No                |
| Best for             | Large analytical caches | Existing PG stack   | Prototype              | Edge/embedded     |

LanceDB wins on: zero infrastructure, disk-scale, DuckDB SQL for analytics (hit stats, staleness distribution), and schema evolution as the cache schema matures.

**Runner-up: sqlite-vec** if absolute minimalism is required (<500K entries, no analytics).

### 1.3 Staleness & Decay System

**The canonical formula (arXiv 2509.19376, validated production):**

```
decay_score  = 0.5 ^ (age_days / half_life_days)
final_score  = α · cosine_score + (1 - α) · decay_score
```

- `α = 0.7` default (70% semantic, 30% temporal)
- `α = 0.4` for news/breaking workloads
- `α ≈ 0.99` for STATIC content (essentially pure semantic)

**7-tier half-life table:**
| Category | Half-Life | Temporal Weight (1-α) | Max Age (hard exclude) |
|----------|-----------|----------------------|----------------------|
| `live` (prices, scores) | 0.02 days (30 min) | 0.70 | 2 hours |
| `news` | 1 day | 0.55 | 7 days |
| `web_search` (general) | 3 days | 0.45 | 30 days |
| `docs_technical` | 30 days | 0.35 | 180 days |
| `research` | 180 days | 0.25 | 2 years |
| `reference` | 1825 days (5 yr) | 0.10 | 10 years |
| `static` (books, RFCs, math) | 36500 days (100 yr) | 0.01 | Never |

**Two-axis classification:**

- **Validity state** (VALID, TEMPORAL, EXPIRED) → drives hard-exclude logic
- **Document kind** (STATIC, VERSIONED, EVENT) → drives half-life selection

**Key rules:**

1. STATIC documents never reach EXPIRED state.
2. EVENT documents use a short TTL with `validity_multiplier=1.2` while active.
3. Entries below `final_score` threshold are deranked (×0.3), not deleted — allowing re-fetch if the query is re-issued.

**LCFU Eviction (Asteria-inspired):**

```python
eviction_score = log(access_count + 1) × log(api_cost_usd × 1000 + 1) × log(latency_ms + 1) × log(staticity + 1) / content_tokens
# Lowest score → evict first (cheap, rarely-used, stale, small entries go first)
```

---

## Part 2 — Implementation Plan

### 2.1 Directory Structure

```
07-tools-and-infrastructure/lms_mcp/search_cache/
├── pyproject.toml
├── main.py               # FastMCP server + 7 tools
├── cache/
│   ├── __init__.py
│   ├── db.py             # LanceDB connection + table init
│   ├── schema.py         # PyArrow schema + category tables
│   ├── lookup.py         # Two-layer lookup (hash + ANN)
│   ├── store.py          # Cache write + embedding generation
│   ├── scoring.py        # Staleness formula + final_score fusion
│   ├── eviction.py       # LCFU sweep + hard-exclude logic
│   └── encoder.py        # SentenceTransformer lazy loader
└── tests/
    ├── test_lookup.py
    ├── test_scoring.py
    └── test_eviction.py
```

### 2.2 Core Schema

```python
# cache/schema.py
import pyarrow as pa

CACHE_SCHEMA = pa.schema([
    # Identity
    pa.field("id", pa.string()),                      # UUID
    pa.field("query_hash", pa.string()),              # SHA-256 for exact match

    # Query
    pa.field("query_text", pa.string()),              # original query
    pa.field("filtered_terms", pa.list_(pa.string())),# extracted keywords
    pa.field("parameters", pa.string()),              # JSON: model, temperature, tools, etc.

    # Embedding
    pa.field("query_embedding", pa.list_(pa.float32(), 384)),  # all-MiniLM-L6-v2

    # Cached content
    pa.field("summary", pa.string()),                 # compressed answer
    pa.field("full_content", pa.large_utf8()),        # full retrieved content
    pa.field("source_url", pa.string()),              # origin URL(s), JSON array if multiple
    pa.field("source_version_hash", pa.string()),     # SHA-256 of content at index time

    # Classification
    pa.field("category", pa.string()),                # see CATEGORY_CONFIG
    pa.field("document_kind", pa.string()),           # STATIC | VERSIONED | EVENT
    pa.field("validity_state", pa.string()),          # VALID | TEMPORAL | EXPIRED

    # Temporal
    pa.field("created_at", pa.timestamp("ms")),
    pa.field("last_accessed", pa.timestamp("ms")),
    pa.field("ttl_expires_at", pa.timestamp("ms")),   # NULL = never expire

    # Usage / quality
    pa.field("access_count", pa.int32()),
    pa.field("cache_hit_score", pa.float32()),        # cosine sim of last cache hit
    pa.field("api_cost_usd", pa.float32()),           # cost of original API call
    pa.field("latency_ms", pa.float32()),             # latency of original retrieval
    pa.field("content_tokens", pa.int32()),           # token count of full_content
    pa.field("feedback", pa.int8()),                  # +1 positive, -1 negative, 0 neutral
])

# Category configuration
CATEGORY_CONFIG = {
    "live":           {"half_life_days": 0.021, "temporal_weight": 0.70, "max_age_days": 0.083},
    "news":           {"half_life_days": 1,     "temporal_weight": 0.55, "max_age_days": 7},
    "web_search":     {"half_life_days": 3,     "temporal_weight": 0.45, "max_age_days": 30},
    "docs_technical": {"half_life_days": 30,    "temporal_weight": 0.35, "max_age_days": 180},
    "research":       {"half_life_days": 180,   "temporal_weight": 0.25, "max_age_days": 730},
    "reference":      {"half_life_days": 1825,  "temporal_weight": 0.10, "max_age_days": 3650},
    "static":         {"half_life_days": 36500, "temporal_weight": 0.01, "max_age_days": None},
}

STATIC_DOCUMENT_KINDS = {"book", "rfc", "standard", "definition", "math", "law"}
```

### 2.3 Staleness Scoring

```python
# cache/scoring.py
import math
from datetime import datetime, timezone

def decay_score(created_at: datetime, half_life_days: float) -> float:
    age_days = (datetime.now(timezone.utc) - created_at).total_seconds() / 86400
    return 0.5 ** (age_days / half_life_days)

def final_score(cosine: float, created_at: datetime, category: str) -> float:
    cfg = CATEGORY_CONFIG[category]
    alpha = 1.0 - cfg["temporal_weight"]  # semantic weight
    d_score = decay_score(created_at, cfg["half_life_days"])
    return alpha * cosine + cfg["temporal_weight"] * d_score

def is_hard_excluded(entry: dict) -> bool:
    if entry["document_kind"] == "STATIC":
        return False
    max_age = CATEGORY_CONFIG[entry["category"]]["max_age_days"]
    if max_age is None:
        return False
    age_days = (datetime.now(timezone.utc) - entry["created_at"]).total_seconds() / 86400
    return age_days > max_age

def eviction_score(entry: dict) -> float:
    """Lower = evict first."""
    freq = math.log(entry["access_count"] + 1)
    cost = math.log(entry["api_cost_usd"] * 1000 + 1)
    lat  = math.log(entry["latency_ms"] + 1)
    # staticity: map category half_life to 1-10 scale
    hl   = CATEGORY_CONFIG[entry["category"]]["half_life_days"]
    stat = math.log(hl + 1)
    size = max(entry["content_tokens"], 1)
    return (freq * cost * lat * stat) / size
```

### 2.4 Two-Layer Lookup

```python
# cache/lookup.py
import hashlib, json
from datetime import datetime, timezone

SIMILARITY_THRESHOLDS = {
    "live":           0.98,
    "news":           0.92,
    "web_search":     0.90,
    "docs_technical": 0.93,
    "research":       0.91,
    "reference":      0.90,
    "static":         0.88,
}

def query_hash(query_text: str, parameters: dict) -> str:
    payload = json.dumps({"q": query_text, **parameters}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()

def lookup(table, query_text: str, query_embedding: list[float],
           parameters: dict, category: str | None = None) -> dict | None:

    h = query_hash(query_text, parameters)

    # Layer 1: exact hash match (O(1))
    exact = table.search().where(f"query_hash = '{h}'").limit(1).to_list()
    if exact and not is_hard_excluded(exact[0]):
        return {**exact[0], "hit_type": "exact"}

    # Layer 2: ANN cosine similarity (O(log n))
    where_clause = "validity_state != 'EXPIRED'"
    if category:
        where_clause += f" AND category = '{category}'"

    candidates = (
        table.search(query_embedding, vector_column_name="query_embedding")
             .where(where_clause)
             .limit(10)
             .to_list()
    )

    threshold = SIMILARITY_THRESHOLDS.get(category or "web_search", 0.90)
    for row in candidates:
        cosine = row.get("_distance", 1.0)
        cosine_sim = 1.0 - cosine  # LanceDB returns L2 or cosine distance

        if cosine_sim < threshold:
            continue
        if is_hard_excluded(row):
            continue

        score = final_score(cosine_sim, row["created_at"], row["category"])
        if score > 0.5:
            return {**row, "hit_type": "semantic", "final_score": score}

    return None
```

### 2.5 MCP Server Tools (7 tools)

```python
# main.py
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Search Cache")

@mcp.tool()
def cache_lookup(
    query: str,
    category: str = "web_search",
    parameters: str = "{}",
    similarity_threshold: float | None = None,
) -> str:
    """
    **TRIGGER CONDITION**: Before calling any external search API (Exa, Brave, SearXNG, etc.).
    **SEQUENCE GUIDANCE**: Call first. On hit, use returned content directly. On miss, call
    the search API and then call cache_store with the result.
    **CONSTRAINT WARNING**: category must be one of: live, news, web_search, docs_technical,
    research, reference, static.
    **OUTPUT EXPECTATION**: Returns JSON with hit_type ("exact"|"semantic"|"miss"),
    summary, full_content, source_url, final_score, and cache metadata on hit;
    {"hit": false} on miss.
    """
    ...

@mcp.tool()
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
    **TRIGGER CONDITION**: After a successful external search API call that returned results.
    **SEQUENCE GUIDANCE**: Call after cache_lookup returned miss. Pass exact query and full
    result content. document_kind="STATIC" for books, RFCs, standards, mathematical definitions.
    **OUTPUT EXPECTATION**: Returns stored entry ID and computed TTL for confirmation.
    """
    ...

@mcp.tool()
def cache_update_feedback(entry_id: str, positive: bool) -> str:
    """Mark a cache entry as good (+1) or bad (-1) based on agent outcome."""
    ...

@mcp.tool()
def cache_invalidate(
    query: str | None = None,
    category: str | None = None,
    older_than_days: float | None = None,
) -> str:
    """
    **TRIGGER CONDITION**: When an agent detects a cached answer was factually wrong or
    outdated (e.g., after a tool call returned a conflicting result).
    **OUTPUT EXPECTATION**: Returns count of invalidated entries.
    """
    ...

@mcp.tool()
def cache_stats() -> str:
    """
    Return hit rate, miss rate, entry count by category, stale entry count,
    estimated API calls and cost saved. Uses DuckDB SQL over LanceDB.
    """
    ...

@mcp.tool()
def cache_sweep(dry_run: bool = True) -> str:
    """
    Run LCFU eviction on entries past max_age, then mark expired entries.
    dry_run=True reports what would be evicted without deleting.
    **TRIGGER CONDITION**: Schedule as periodic maintenance (daily or weekly).
    """
    ...

@mcp.tool()
def cache_list_categories() -> str:
    """Return all configured categories with their half-life and TTL settings."""
    ...

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

### 2.6 Embedding Encoder

```python
# cache/encoder.py
# Lazy-loaded to avoid blocking the MCP handshake
_model = None

def get_encoder():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer("all-MiniLM-L6-v2")  # 384-dim, 22MB
    return _model

def encode(text: str) -> list[float]:
    return get_encoder().encode(text, normalize_embeddings=True).tolist()
```

### 2.7 Dependencies (pyproject.toml)

```toml
[project]
name = "search-cache"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "fastmcp>=2.0",
    "lancedb>=0.20",
    "sentence-transformers>=3.0",
    "pyarrow>=18.0",
    "duckdb>=1.0",
]
```

### 2.8 MCP Config Entry (~/.lmstudio/mcp.json)

```json
{
  "search_cache": {
    "command": "/home/nero/Documents/Estudos/07-tools-and-infrastructure/lms_mcp/search_cache/.venv/bin/python",
    "args": [
      "/home/nero/Documents/Estudos/07-tools-and-infrastructure/lms_mcp/search_cache/main.py"
    ]
  }
}
```

---

## Part 3 — Agent Integration Pattern

Agents using this system should follow this call sequence:

```
1. cache_lookup(query, category, parameters)
   ├── hit  → use cached content directly, skip external API call
   └── miss → call external API (Exa, Brave, etc.)
                └── cache_store(query, result, category, document_kind, cost, latency)

2. (optional) cache_update_feedback(entry_id, positive=True/False)
   → after verifying answer quality

3. (periodic) cache_sweep(dry_run=False)
   → daily maintenance via cron or /schedule
```

### Category Selection Guide for Agents

```
query about today's prices/scores/weather  → "live"
query about recent news/events             → "news"
general web research                       → "web_search"
query about a library, API, framework      → "docs_technical"
query about a paper/study/dataset          → "research"
query about a definition/encyclopedia      → "reference"
query about a book, RFC, standard, math    → "static" + document_kind="STATIC"
```

---

## Part 4 — Build Order

1. **Scaffold** — `pyproject.toml`, `main.py` skeleton, `cache/__init__.py`
2. **Schema** — `schema.py` with `CACHE_SCHEMA` and `CATEGORY_CONFIG`
3. **DB init** — `db.py` with LanceDB connection, table creation, BM25 FTS index
4. **Encoder** — `encoder.py` (lazy SentenceTransformer)
5. **Scoring** — `scoring.py` (`decay_score`, `final_score`, `is_hard_excluded`, `eviction_score`)
6. **Lookup** — `lookup.py` (two-layer: hash → ANN)
7. **Store** — `store.py` (embed + insert with TTL computation)
8. **Tools** — wire all 7 MCP tools in `main.py`
9. **Sweep** — `eviction.py` (LCFU + max_age hard-exclude)
10. **Stats** — DuckDB SQL queries for `cache_stats()`
11. **Tests** — unit tests for scoring and lookup
12. **MCP registration** — add entry to `~/.lmstudio/mcp.json`

---

## Sources

### Semantic Caching

- [GPT Semantic Cache (arXiv 2411.05276)](https://arxiv.org/html/2411.05276v2)
- [GPTCache GitHub (Zilliz)](https://github.com/zilliztech/GPTCache)
- [Adaptive Semantic Prompt Caching with VectorQ (arXiv 2502.03771)](https://arxiv.org/html/2502.03771v1)
- [Asteria: Cross-Region Agentic Cache (arXiv 2509.17360)](https://arxiv.org/html/2509.17360v1)
- [LangChain RedisSemanticCache API Docs](https://python.langchain.com/api_reference/redis/cache/langchain_redis.cache.RedisSemanticCache.html)
- [Advancing Semantic Caching with Domain-Specific Embeddings (arXiv 2504.02268)](https://arxiv.org/html/2504.02268v1)

### Hybrid DB Architectures

- [pgvector Production RAG: HNSW, Hybrid Search](https://markaicode.com/pgvector-rag-production/)
- [Qdrant: Vector Search Filtering](https://qdrant.tech/articles/vector-search-filtering/)
- [LanceDB Documentation](https://docs.lancedb.com/)
- [sqlite-vec GitHub](https://github.com/asg017/sqlite-vec)
- [Vector Database Benchmarks 2026](https://callsphere.ai/blog/vector-database-benchmarks-2026-pgvector-qdrant-weaviate-milvus-lancedb)

### Staleness & Decay

- [RAG Is Blind to Time — Temporal Layer (TDS 2024)](https://towardsdatascience.com/rag-is-blind-to-time-i-built-a-temporal-layer-to-fix-it-in-production/)
- [Solving Freshness in RAG: A Simple Recency Prior (arXiv 2509.19376)](https://arxiv.org/html/2509.19376)
- [RAG-Enhanced LLMs for Dynamic Content Expiration (arXiv 2605.13052)](https://arxiv.org/html/2605.13052)
- [Revisiting Cache Freshness (SIGCOMM HotNets 2024)](https://arxiv.org/html/2412.20221v1)
- [RFC 9111: HTTP Caching](https://www.rfc-editor.org/rfc/rfc9111.html)
