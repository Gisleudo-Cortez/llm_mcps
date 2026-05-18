"""Unit tests for query_hash and lookup helpers."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cache.lookup import query_hash


def test_hash_deterministic():
    h1 = query_hash("hello world", {"model": "gpt-4"})
    h2 = query_hash("hello world", {"model": "gpt-4"})
    assert h1 == h2


def test_hash_differs_on_query():
    h1 = query_hash("hello world", {})
    h2 = query_hash("hello earth", {})
    assert h1 != h2


def test_hash_differs_on_params():
    h1 = query_hash("test query", {"model": "gpt-4"})
    h2 = query_hash("test query", {"model": "gpt-3.5"})
    assert h1 != h2


def test_hash_params_order_independent():
    h1 = query_hash("q", {"a": 1, "b": 2})
    h2 = query_hash("q", {"b": 2, "a": 1})
    assert h1 == h2


def test_hash_strips_whitespace():
    h1 = query_hash("  hello  ", {})
    h2 = query_hash("hello", {})
    assert h1 == h2


def test_hash_is_64_char_hex():
    h = query_hash("any query", {})
    assert len(h) == 64
    assert all(c in "0123456789abcdef" for c in h)
