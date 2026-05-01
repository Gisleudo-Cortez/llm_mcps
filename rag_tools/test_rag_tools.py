"""
Tests for rag_tools hybrid search additions (Phase 2).

Covers: _rrf_fuse, _bm25_search, _fts_index_chunks/_fts_delete_source,
        _embed provider dispatch.
ChromaDB and sentence-transformers are NOT loaded — all LLM/vector calls are mocked.
"""
import os
import sqlite3
import tempfile
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers to build an isolated FTS5 DB for tests
# ---------------------------------------------------------------------------

def _make_fts_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
            text,
            source UNINDEXED,
            chunk_idx UNINDEXED,
            collection UNINDEXED
        )
        """
    )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# _rrf_fuse — pure function, no I/O
# ---------------------------------------------------------------------------

class TestRrfFuse:
    def setup_method(self):
        # Import fresh each test to avoid module-level state issues
        import importlib
        import sys
        # We test the function in isolation without loading heavy deps
        # by importing only the helper from the module after patching heavy imports
        self.rrf_fuse = None

    def _get_rrf(self):
        """Lazy import of _rrf_fuse with heavy deps mocked."""
        if self.rrf_fuse is not None:
            return self.rrf_fuse
        mocks = {
            "chromadb": MagicMock(),
            "sentence_transformers": MagicMock(),
            "fitz": MagicMock(),
            "pypdf": MagicMock(),
            "pandas": MagicMock(),
            "markitdown": MagicMock(),
            "PIL": MagicMock(),
            "PIL.Image": MagicMock(),
            "numpy": MagicMock(),
        }
        with patch.dict("sys.modules", mocks):
            import importlib
            import sys
            # Remove cached module if present
            sys.modules.pop("main", None)
            sys.path.insert(0, os.path.dirname(__file__))
            import main as m
            self.rrf_fuse = m._rrf_fuse
        return self.rrf_fuse

    def test_rrf_combines_scores(self):
        rrf = self._get_rrf()
        bm25 = [(1, "doc A", "/a.txt", 0, -2.5)]
        vector = [(0, "doc B", "/b.txt", 0, 0.3)]
        result = rrf(bm25, vector, k=60)
        # Both appear, doc A rank=1 in bm25 → 1/61; doc B rank=1 in vector → 1/61
        assert len(result) == 2
        scores = {r[1]: r[3] for r in result}
        assert abs(scores["/a.txt"] - 1 / 61) < 1e-9
        assert abs(scores["/b.txt"] - 1 / 61) < 1e-9

    def test_rrf_deduplicates(self):
        rrf = self._get_rrf()
        bm25 = [(1, "shared text", "/doc.txt", 2, -3.0)]
        vector = [(0, "shared text", "/doc.txt", 2, 0.1)]
        result = rrf(bm25, vector, k=60)
        # Same (source, chunk_idx) → merged into one entry with combined score
        assert len(result) == 1
        expected = 1 / 61 + 1 / 61
        assert abs(result[0][3] - expected) < 1e-9

    def test_rrf_empty_bm25(self):
        rrf = self._get_rrf()
        vector = [(0, "only vector", "/v.txt", 0, 0.2)]
        result = rrf([], vector, k=60)
        assert len(result) == 1
        assert result[0][0] == "only vector"

    def test_rrf_empty_vector(self):
        rrf = self._get_rrf()
        bm25 = [(1, "only bm25", "/b.txt", 0, -1.0)]
        result = rrf(bm25, [], k=60)
        assert len(result) == 1
        assert result[0][0] == "only bm25"

    def test_rrf_both_empty(self):
        rrf = self._get_rrf()
        assert rrf([], []) == []

    def test_rrf_rank_ordering(self):
        rrf = self._get_rrf()
        # Two docs, one appears in both lists at rank 1 → higher total score
        bm25 = [(1, "shared", "/s.txt", 0, -3.0), (2, "bm25only", "/b.txt", 0, -1.0)]
        vector = [(0, "shared", "/s.txt", 0, 0.1), (1, "veconly", "/v.txt", 0, 0.5)]
        result = rrf(bm25, vector, k=60)
        # "shared" appears rank 1 in both → score = 1/61 + 1/61 = highest
        assert result[0][1] == "/s.txt"


# ---------------------------------------------------------------------------
# _bm25_search + _fts_index_chunks + _fts_delete_source
# ---------------------------------------------------------------------------

class TestFtsHelpers:
    """Test BM25 search using a real temporary FTS5 database."""

    def _import_helpers(self, fts_path: str):
        import sys
        from unittest.mock import MagicMock
        mocks = {
            "chromadb": MagicMock(),
            "sentence_transformers": MagicMock(),
            "fitz": MagicMock(),
            "pypdf": MagicMock(),
            "pandas": MagicMock(),
            "markitdown": MagicMock(),
            "PIL": MagicMock(),
            "PIL.Image": MagicMock(),
            "numpy": MagicMock(),
        }
        sys.modules.pop("main", None)
        sys.path.insert(0, os.path.dirname(__file__))
        with patch.dict("sys.modules", mocks):
            import main as m
        # Redirect FTS to temp db
        conn = _make_fts_db(fts_path)
        m._fts_conn = conn
        return m

    def test_index_and_search_finds_text(self, tmp_path):
        fts_path = str(tmp_path / "fts.db")
        m = self._import_helpers(fts_path)
        m._fts_index_chunks(["The quick brown fox"], "/doc.txt", "test")
        results = m._bm25_search("quick fox", "test", limit=5)
        assert len(results) == 1
        assert "quick brown fox" in results[0][1]

    def test_collection_filter_isolates_results(self, tmp_path):
        fts_path = str(tmp_path / "fts.db")
        m = self._import_helpers(fts_path)
        m._fts_index_chunks(["Python programming language"], "/a.txt", "col_a")
        m._fts_index_chunks(["Python snake reptile"], "/b.txt", "col_b")
        results_a = m._bm25_search("Python", "col_a", limit=5)
        results_b = m._bm25_search("Python", "col_b", limit=5)
        assert len(results_a) == 1
        assert results_a[0][2] == "/a.txt"
        assert len(results_b) == 1
        assert results_b[0][2] == "/b.txt"

    def test_no_match_returns_empty(self, tmp_path):
        fts_path = str(tmp_path / "fts.db")
        m = self._import_helpers(fts_path)
        m._fts_index_chunks(["Hello world"], "/doc.txt", "test")
        results = m._bm25_search("astrophysics quantum", "test", limit=5)
        assert results == []

    def test_malformed_query_returns_empty_not_exception(self, tmp_path):
        fts_path = str(tmp_path / "fts.db")
        m = self._import_helpers(fts_path)
        # FTS5 MATCH syntax error should be caught and return []
        results = m._bm25_search('AND OR "unclosed', "test", limit=5)
        assert results == []

    def test_delete_source_removes_chunks(self, tmp_path):
        fts_path = str(tmp_path / "fts.db")
        m = self._import_helpers(fts_path)
        m._fts_index_chunks(["chunk one", "chunk two"], "/doc.txt", "col")
        assert len(m._bm25_search("chunk", "col", limit=10)) == 2
        m._fts_delete_source("/doc.txt", "col")
        assert m._bm25_search("chunk", "col", limit=10) == []

    def test_delete_only_affects_target_source(self, tmp_path):
        fts_path = str(tmp_path / "fts.db")
        m = self._import_helpers(fts_path)
        m._fts_index_chunks(["keep this text"], "/keep.txt", "col")
        m._fts_index_chunks(["delete this text"], "/delete.txt", "col")
        m._fts_delete_source("/delete.txt", "col")
        remaining = m._bm25_search("text", "col", limit=10)
        sources = [r[2] for r in remaining]
        assert "/keep.txt" in sources
        assert "/delete.txt" not in sources


# ---------------------------------------------------------------------------
# _embed — provider dispatch
# ---------------------------------------------------------------------------

class TestEmbedDispatch:
    def _import_main(self):
        import sys
        mocks = {
            "chromadb": MagicMock(),
            "sentence_transformers": MagicMock(),
            "fitz": MagicMock(),
            "pypdf": MagicMock(),
            "pandas": MagicMock(),
            "markitdown": MagicMock(),
            "PIL": MagicMock(),
            "PIL.Image": MagicMock(),
            "numpy": MagicMock(),
        }
        sys.modules.pop("main", None)
        sys.path.insert(0, os.path.dirname(__file__))
        with patch.dict("sys.modules", mocks):
            import main as m
        return m

    def test_local_provider_uses_sentence_transformers(self):
        m = self._import_main()
        m.RAG_EMBED_PROVIDER = "local"
        mock_model = MagicMock()
        mock_model.encode.return_value = MagicMock(tolist=lambda: [[0.1, 0.2]])
        m._embed_model = mock_model
        result = m._embed(["hello world"])
        mock_model.encode.assert_called_once_with(["hello world"])

    def test_ollama_provider_calls_api(self):
        m = self._import_main()
        m.RAG_EMBED_PROVIDER = "ollama"
        m._ollama_http = None
        mock_http = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"embeddings": [[0.5, 0.6]]}
        mock_http.post.return_value = mock_resp
        m._ollama_http = mock_http
        result = m._embed(["test text"])
        mock_http.post.assert_called_once()
        call_kwargs = mock_http.post.call_args
        assert "/api/embed" in call_kwargs[0][0]
        assert result == [[0.5, 0.6]]

    def test_local_provider_is_default(self):
        m = self._import_main()
        assert m.RAG_EMBED_PROVIDER == "local"
