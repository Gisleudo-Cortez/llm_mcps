"""Unit tests for staleness scoring functions."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import math
from datetime import datetime, timedelta, timezone

import pytest

from cache.scoring import decay_score, eviction_score, final_score, is_hard_excluded


def _dt(days_ago: float) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days_ago)


# --- decay_score ---

def test_decay_at_creation():
    score = decay_score(_dt(0), half_life_days=3.0)
    assert score == pytest.approx(1.0, abs=0.01)


def test_decay_at_half_life():
    score = decay_score(_dt(3.0), half_life_days=3.0)
    assert score == pytest.approx(0.5, abs=0.01)


def test_decay_at_two_half_lives():
    score = decay_score(_dt(6.0), half_life_days=3.0)
    assert score == pytest.approx(0.25, abs=0.01)


def test_decay_static_category_barely_decays():
    # 100-year half-life: 1 year old should still be ~0.998
    score = decay_score(_dt(365), half_life_days=36500.0)
    assert score > 0.99


# --- final_score ---

def test_final_score_perfect_fresh():
    score = final_score(1.0, _dt(0), "web_search")
    # alpha=0.55, temporal=0.45, decay~1.0: 0.55*1 + 0.45*1 = 1.0
    assert score == pytest.approx(1.0, abs=0.02)


def test_final_score_degraded():
    # 30-day-old web_search entry, cosine=0.91 (just above threshold)
    score = final_score(0.91, _dt(30), "web_search")
    # decay at 30d with HL=3: 0.5^10 ≈ 0.001
    # 0.55*0.91 + 0.45*0.001 ≈ 0.501
    assert 0.4 < score < 0.6


def test_static_category_ignores_age():
    old = final_score(0.9, _dt(365), "static")
    fresh = final_score(0.9, _dt(0), "static")
    # Both should be nearly 0.9 (temporal_weight=0.01)
    assert abs(old - fresh) < 0.02
    assert old == pytest.approx(0.9, abs=0.02)


# --- is_hard_excluded ---

def test_static_never_excluded():
    entry = {"document_kind": "STATIC", "category": "static", "created_at": _dt(1000)}
    assert is_hard_excluded(entry) is False


def test_expired_state_excluded():
    entry = {"document_kind": "VERSIONED", "category": "web_search",
             "validity_state": "EXPIRED", "created_at": _dt(0)}
    assert is_hard_excluded(entry) is True


def test_fresh_entry_not_excluded():
    entry = {"document_kind": "VERSIONED", "category": "web_search",
             "validity_state": "VALID", "created_at": _dt(1)}
    assert is_hard_excluded(entry) is False


def test_old_web_search_excluded():
    # max_age for web_search = 30 days
    entry = {"document_kind": "VERSIONED", "category": "web_search",
             "validity_state": "VALID", "created_at": _dt(31)}
    assert is_hard_excluded(entry) is True


# --- eviction_score ---

def test_eviction_score_higher_for_expensive_frequent():
    cheap_rare = {"access_count": 1, "api_cost_usd": 0.001, "latency_ms": 100,
                  "category": "web_search", "content_tokens": 500}
    expensive_frequent = {"access_count": 50, "api_cost_usd": 0.05, "latency_ms": 2000,
                          "category": "web_search", "content_tokens": 500}
    assert eviction_score(expensive_frequent) > eviction_score(cheap_rare)


def test_eviction_score_static_category_higher():
    static = {"access_count": 1, "api_cost_usd": 0.01, "latency_ms": 500,
              "category": "static", "content_tokens": 1000}
    live = {"access_count": 1, "api_cost_usd": 0.01, "latency_ms": 500,
            "category": "live", "content_tokens": 1000}
    assert eviction_score(static) > eviction_score(live)
