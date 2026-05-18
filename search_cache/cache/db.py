"""LanceDB connection and table initialisation."""
from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

import lancedb
import pyarrow as pa

from .schema import EMBEDDING_DIM

_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "cache_db")
_TABLE_NAME = "search_cache"
_db: lancedb.DBConnection | None = None
_table = None


def _arrow_schema() -> pa.Schema:
    return pa.schema([
        pa.field("id", pa.string()),
        pa.field("query_hash", pa.string()),
        pa.field("query_text", pa.string()),
        pa.field("filtered_terms", pa.list_(pa.string())),
        pa.field("parameters", pa.string()),
        pa.field("query_embedding", pa.list_(pa.float32(), EMBEDDING_DIM)),
        pa.field("summary", pa.string()),
        pa.field("full_content", pa.large_utf8()),
        pa.field("source_url", pa.string()),
        pa.field("source_version_hash", pa.string()),
        pa.field("category", pa.string()),
        pa.field("document_kind", pa.string()),
        pa.field("validity_state", pa.string()),
        pa.field("created_at", pa.timestamp("us", tz="UTC")),
        pa.field("last_accessed", pa.timestamp("us", tz="UTC")),
        pa.field("ttl_expires_at", pa.timestamp("us", tz="UTC")),
        pa.field("access_count", pa.int32()),
        pa.field("cache_hit_score", pa.float32()),
        pa.field("api_cost_usd", pa.float32()),
        pa.field("latency_ms", pa.float32()),
        pa.field("content_tokens", pa.int32()),
        pa.field("feedback", pa.int8()),
    ])


def get_table():
    """Return the LanceDB table, creating it on first call."""
    global _db, _table
    if _table is not None:
        return _table

    _db = lancedb.connect(_DB_PATH)

    if _TABLE_NAME in _db.list_tables().tables:
        _table = _db.open_table(_TABLE_NAME)
    else:
        _table = _db.create_table(_TABLE_NAME, schema=_arrow_schema())
        # Scalar index for fast exact-hash lookup
        try:
            _table.create_scalar_index("query_hash")
        except Exception:
            pass

    return _table


def scalar_query(table, filter_expr: str, limit: int = 1) -> list[dict]:
    """Pure relational query using lancedb's scalar filter (no vector needed)."""
    return table.search(None).where(filter_expr).limit(limit).to_list()


def ensure_vector_index(table) -> None:
    """Create the ANN vector index once the table has enough rows."""
    try:
        row_count = table.count_rows()
        if row_count >= 256:
            table.create_index(
                vector_column_name="query_embedding",
                metric="cosine",
                replace=False,
            )
    except Exception:
        pass


def ensure_fts_index(table) -> None:
    """Create or replace the full-text search index on query_text."""
    try:
        table.create_fts_index("query_text", replace=True)
    except Exception:
        pass
