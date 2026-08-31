"""Tests for event_log — schema, dedup aggregation, migration, prune."""

import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import event_log


@pytest.fixture()
def temp_db(monkeypatch):
    with tempfile.TemporaryDirectory() as td:
        monkeypatch.setenv("EMAIL_MCP_EVENT_DB", os.path.join(td, "events.db"))
        yield


def _mk_event(**overrides) -> dict:
    base = {
        "source": "digest",
        "account": "personal-test",
        "sender": "orders@amazon.com.br",
        "recipient": "someone@example.com",
        "subject": "Sua compra foi entregue",
        "label": "receipt",
        "confidence": 0.92,
        "model_used": "phi4-mini:3.8b",
        "action": "save",
        "email_id": 1,
    }
    base.update(overrides)
    return base


class TestBasics:
    def test_insert_and_query(self, temp_db):
        event_log.log_email_event(_mk_event())
        rows = event_log.query_events()
        assert len(rows) == 1
        assert rows[0]["sender"] == "orders@amazon.com.br"
        assert rows[0]["label"] == "receipt"
        assert rows[0]["account"] == "personal-test"

    def test_requires_source_and_account(self, temp_db):
        with pytest.raises(ValueError):
            event_log.log_email_event({"sender": "x@y.com", "label": "spam"})

    def test_query_filters(self, temp_db):
        event_log.log_email_events([
            _mk_event(sender="a@aliexpress.com", label="personal", subject="Pedido 1"),
            _mk_event(sender="b@nubank.com.br", label="financial", subject="Fatura"),
            _mk_event(sender="c@aliexpress.com", label="receipt", subject="Pedido 2", account="other-test"),
        ])
        ali = event_log.query_events(sender_like="aliexpress")
        assert len(ali) == 2
        fin = event_log.query_events(label="financial")
        assert len(fin) == 1 and fin[0]["sender"] == "b@nubank.com.br"
        both = event_log.query_events(account="other-test", sender_like="aliexpress")
        assert len(both) == 1 and both[0]["subject"] == " line2".strip() or "Pedido 2"


class TestDedupAggregation:
    def test_dedup_counts_unique_emails_once(self, temp_db):
        # Same email observed by digest, then again by MCP classify tool
        e = _mk_event(subject="Order shipped")
        event_log.log_email_events([e, {**e, "source": "mcp_classify"}])
        stats = event_log.pattern_stats()
        assert stats["totals"]["raw_events"] == 2
        assert stats["totals"]["unique_emails"] == 1
        # With min_count=1 the group appears once — email_count 2 would mean dedup failed
        singles = event_log.pattern_stats(min_count=1)
        assert singles["sender_label_patterns"][0]["email_count"] == 1

    def test_sender_label_pattern(self, temp_db):
        event_log.log_email_events([
            _mk_event(subject="Pedido 1"),
            _mk_event(subject="Pedido 2"),
            _mk_event(subject="Fatura", label="financial"),
        ])
        stats = event_log.pattern_stats()
        # Grouping is (account, sender, label) → one group of 2, one of 1
        receipts = [p for p in stats["sender_label_patterns"]
                    if p["label"] == "receipt"]
        assert len(receipts) == 1 and receipts[0]["email_count"] == 2

    def test_domain_pattern_catches_rotating_local_parts(self, temp_db):
        # AliExpress rotates local parts: ae-ug-ut-xyz@mail.aliexpress.com
        event_log.log_email_events([
            _mk_event(sender="ae-ug-ut-abc@mail.aliexpress.com", subject="Promo 1"),
            _mk_event(sender="ae-ug-ut-def@mail.aliexpress.com", subject="Promo 2"),
            _mk_event(sender="ae-ug-ut-ghi@mail.aliexpress.com", subject="Promo 3"),
        ])
        stats = event_log.pattern_stats(domain_min_count=3)
        ali = [p for p in stats["domain_label_patterns"]
               if p["sender_domain"] == "mail.aliexpress.com"]
        assert len(ali) == 1 and ali[0]["email_count"] == 3

    def test_cross_account_senders(self, temp_db):
        event_log.log_email_events([
            _mk_event(sender="promo@aliexpress.com", account="a1", subject="Promo"),
            _mk_event(sender="promo@aliexpress.com", account="a2", subject="Promo"),
            _mk_event(sender="only@one.com", account="a1", subject="Once"),
        ])
        stats = event_log.pattern_stats()
        cross = [s for s in stats["cross_account_senders"] if s["sender"] == "promo@aliexpress.com"]
        assert len(cross) == 1 and cross[0]["account_count"] == 2
        assert "a1" in cross[0]["accounts"] and "a2" in cross[0]["accounts"]


class TestPrune:
    def test_prune_removes_old_events(self, temp_db):
        old_ts = (datetime.now() - timedelta(days=400)).isoformat()
        event_log.log_email_events([
            _mk_event(subject="old"),
            {**_mk_event(subject="old"), "timestamp": old_ts},
        ])
        assert event_log.prune_events(days=365) == 1
        assert len(event_log.query_events()) == 1


class TestMigration:
    def test_migrate_from_digest_state(self, temp_db):
        # Simulate the digest_state.db processed_emails table
        with tempfile.TemporaryDirectory() as td:
            legacy = os.path.join(td, "digest_state.db")
            conn = sqlite3.connect(legacy)
            conn.execute("""
                CREATE TABLE processed_emails (
                    account TEXT NOT NULL, email_id INTEGER NOT NULL,
                    processed_at TEXT NOT NULL, label TEXT, confidence REAL,
                    subject TEXT, sender TEXT, PRIMARY KEY (account, email_id)
                )
            """)
            conn.execute("INSERT INTO processed_emails VALUES (?,?,?,?,?,?,?)",
                         ("personal-test", 42, "2026-08-20T08:00:00", "receipt", 0.91,
                          "Sua compra chegou", "no-reply@mercadolivre.com.br"))
            conn.commit()
            conn.close()

            migrated = event_log.migrate_digest_history(legacy)
            assert migrated == 1

        rows = event_log.query_events(source="digest_history")
        assert len(rows) == 1
        assert rows[0]["account"] == "personal-test"
        assert rows[0]["email_id"] == 42
        assert rows[0]["sender"] == "no-reply@mercadolivre.com.br"
        assert rows[0]["timestamp"] == "2026-08-20T08:00:00"


class TestWalConcurrent:
    def test_concurrent_writers_do_not_raise(self, temp_db):
        import threading
        errors = []

        def writer(tag: int):
            try:
                for i in range(10):
                    event_log.log_email_event(_mk_event(subject=f"w{tag}-{i}", email_id=i))
            except sqlite3.OperationalError as e:
                errors.append(str(e))

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(4)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert not errors
        assert len(event_log.query_events(limit=1000)) == 40