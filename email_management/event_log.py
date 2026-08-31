"""Durable email event log + pattern aggregation for email-management MCP.

Every email the server classifies, previews, or routes is appended to a
SQLite event store (default ~/.config/email-mcp/email_events.db,
overridable via the EMAIL_MCP_EVENT_DB environment variable for tests).
The daily digest cron writes to the same store through this module, so
there is one canonical record across MCP tool calls and scheduled scans.

Aggregations in pattern_stats() dedupe to one row per unique email
(account, sender, subject, label) so re-observing an email does not
inflate counts — an email seen twice is one email, observed twice.

The store is WAL-mode SQLite with a busy timeout: the long-running MCP
server and the scheduled digest can write concurrently without blocking.
"""

import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS email_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    source TEXT NOT NULL,
    account TEXT NOT NULL,
    himalaya_account TEXT DEFAULT '',
    email_id INTEGER,
    sender TEXT DEFAULT '',
    recipient TEXT DEFAULT '',
    subject TEXT DEFAULT '',
    label TEXT DEFAULT '',
    confidence REAL DEFAULT 0,
    model_used TEXT DEFAULT '',
    action TEXT DEFAULT '',
    routing_path TEXT DEFAULT '',
    files TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_account ON email_events(account);
CREATE INDEX IF NOT EXISTS idx_events_sender ON email_events(sender);
CREATE INDEX IF NOT EXISTS idx_events_label ON email_events(label);
CREATE INDEX IF NOT EXISTS idx_events_ts ON email_events(timestamp);
"""

# Known event sources (documented, not enforced)
SOURCES = ("mcp_classify", "mcp_filter", "mcp_route", "mcp_dry_run", "digest", "digest_history")


def get_event_db_path() -> Path:
    """Event store location. Env override EMAIL_MCP_EVENT_DB wins (tests, tooling)."""
    env = os.environ.get("EMAIL_MCP_EVENT_DB", "")
    if env:
        return Path(env)
    return Path.home() / ".config" / "email-mcp" / "email_events.db"


def _connect() -> sqlite3.Connection:
    path = get_event_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    return conn


# ── Writing ──────────────────────────────────────────────────────────────

_ENTRY_FIELDS = (
    "source", "account", "himalaya_account", "email_id", "sender", "recipient",
    "subject", "label", "confidence", "model_used", "action", "routing_path",
    "files", "timestamp",
)


def _normalize(entry: dict) -> dict:
    e = {k: entry.get(k, "") for k in _ENTRY_FIELDS}
    e["timestamp"] = e["timestamp"] or datetime.now().isoformat()
    e["confidence"] = float(e["confidence"] or 0)
    e["email_id"] = entry.get("email_id")
    if not e["source"] or not e["account"]:
        raise ValueError("event entry requires 'source' and 'account'")
    return e


def log_email_event(entry: dict) -> None:
    """Append a single event to the store. entry requires source + account."""
    log_email_events([entry])


def log_email_events(entries: List[dict]) -> None:
    """Append a batch of events in one transaction. Never partially writes."""
    if not entries:
        return
    rows = [_normalize(e) for e in entries]
    with _connect() as conn:
        conn.executemany(
            f"INSERT INTO email_events ({', '.join(_ENTRY_FIELDS)}) "
            f"VALUES ({', '.join('?' * len(_ENTRY_FIELDS))})",
            [tuple(r[k] for k in _ENTRY_FIELDS) for r in rows],
        )


# ── Reading ──────────────────────────────────────────────────────────────

def query_events(
    account: str = "",
    sender_like: str = "",
    label: str = "",
    source: str = "",
    action: str = "",
    days: Optional[int] = None,
    limit: int = 100,
) -> List[dict]:
    """Query events, most recent first. Empty filter = match all.

    sender_like is a substring match (case-insensitive), so 'aliexpress'
    catches every AliExpress subdomain sender.
    """
    clauses, params = [], []
    if account:
        clauses.append("account = ?")
        params.append(account)
    if sender_like:
        clauses.append("LOWER(sender) LIKE ?")
        params.append(f"%{sender_like.lower()}%")
    if label:
        clauses.append("label = ?")
        params.append(label)
    if source:
        clauses.append("source = ?")
        params.append(source)
    if action:
        clauses.append("action = ?")
        params.append(action)
    if days is not None:
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        clauses.append("timestamp >= ?")
        params.append(cutoff)

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT * FROM email_events {where} ORDER BY timestamp DESC, id DESC LIMIT ?"
    params.append(max(1, min(int(limit), 1000)))

    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


# ── Pattern aggregation ──────────────────────────────────────────────────
# All aggregations operate on a deduped view: one row per unique email
# (account, sender, subject, label) with its last observation time. The
# same email classified by two different tools or seen twice by the digest
# counts once.

_DEDUP_VIEW = """
SELECT account, sender, subject, label, confidence, MAX(timestamp) AS last_seen
FROM email_events
GROUP BY account, sender, subject, label
"""


def pattern_stats(min_count: int = 2, domain_min_count: int = 3, limit: int = 30) -> dict:
    """Aggregate deduped events into sender/domain pattern views.

    Returns:
      sender_label_patterns — (account, sender, label) with email counts
      domain_label_patterns — (account, sender domain, label); catches
          bulk senders that rotate local parts (AliExpress, newsletters)
      cross_account_senders — same sender landing in 2+ accounts
      label_distribution — label mix per account
      needs_attention — recent unsorted / low-confidence emails
          (candidates for new routing rules or patterns)
      totals — overall counts + generated_at
    """
    min_count = max(1, int(min_count))
    domain_min_count = max(1, int(domain_min_count))
    limit = max(1, min(int(limit), 200))

    out = {
        "sender_label_patterns": [],
        "domain_label_patterns": [],
        "cross_account_senders": [],
        "label_distribution": [],
        "needs_attention": [],
        "totals": {},
    }

    with _connect() as conn:
        conn.row_factory = sqlite3.Row

        out["sender_label_patterns"] = [dict(r) for r in conn.execute(
            f"""
            SELECT account, sender, label, COUNT(*) AS email_count,
                   MAX(last_seen) AS last_seen, MIN(subject) AS sample_subject
            FROM ({_DEDUP_VIEW})
            GROUP BY account, sender, label
            HAVING email_count >= ?
            ORDER BY email_count DESC, last_seen DESC
            LIMIT ?
            """,
            (min_count, limit),
        )]

        out["domain_label_patterns"] = [dict(r) for r in conn.execute(
            f"""
            SELECT account,
                   LOWER(SUBSTR(sender, INSTR(sender, '@') + 1)) AS sender_domain,
                   label, COUNT(*) AS email_count,
                   MAX(last_seen) AS last_seen, MIN(subject) AS sample_subject
            FROM ({_DEDUP_VIEW})
            WHERE sender LIKE '%@%'
            GROUP BY account, sender_domain, label
            HAVING email_count >= ?
            ORDER BY email_count DESC, last_seen DESC
            LIMIT ?
            """,
            (domain_min_count, limit),
        )]

        out["cross_account_senders"] = [dict(r) for r in conn.execute(
            f"""
            SELECT sender, COUNT(DISTINCT account) AS account_count,
                   GROUP_CONCAT(DISTINCT account) AS accounts,
                   COUNT(*) AS email_count, MAX(last_seen) AS last_seen
            FROM ({_DEDUP_VIEW})
            GROUP BY sender
            HAVING account_count >= 2
            ORDER BY account_count DESC, email_count DESC
            LIMIT ?
            """,
            (limit,),
        )]

        out["label_distribution"] = [dict(r) for r in conn.execute(
            f"""
            SELECT account, label, COUNT(*) AS email_count
            FROM ({_DEDUP_VIEW})
            GROUP BY account, label
            ORDER BY account, email_count DESC
            """
        )]

        out["needs_attention"] = [dict(r) for r in conn.execute(
            f"""
            SELECT account, sender, subject, label, confidence, MAX(last_seen) AS last_seen
            FROM ({_DEDUP_VIEW})
            WHERE label = 'unsorted' OR confidence < 0.5
            GROUP BY account, sender, subject
            ORDER BY last_seen DESC
            LIMIT 20
            """
        )]

        totals = conn.execute(
            "SELECT COUNT(*) AS events, COUNT(DISTINCT account) AS accounts FROM email_events"
        ).fetchone()
        deduped = conn.execute(f"SELECT COUNT(*) AS c FROM ({_DEDUP_VIEW})").fetchone()
        out["totals"] = {
            "raw_events": totals["events"],
            "unique_emails": deduped["c"],
            "accounts": totals["accounts"],
            "generated_at": datetime.now().isoformat(),
        }

    return out


# ── Housekeeping ─────────────────────────────────────────────────────────

def prune_events(days: int = 365) -> int:
    """Delete events older than `days`. Returns number of rows removed."""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    with _connect() as conn:
        cur = conn.execute("DELETE FROM email_events WHERE timestamp < ?", (cutoff,))
        return cur.rowcount


def migrate_digest_history(digest_state_db: str) -> int:
    """Import rows from the daily digest's legacy state DB into the event store.

    Reads processed_emails (account, email_id, processed_at, label,
    confidence, subject, sender) and writes each as a 'digest_history'
    event, preserving the original processed_at as the event timestamp.
    Returns the number of rows imported.
    """
    legacy = sqlite3.connect(str(digest_state_db))
    legacy.row_factory = sqlite3.Row
    try:
        rows = legacy.execute(
            "SELECT account, email_id, processed_at, label, confidence, subject, sender "
            "FROM processed_emails"
        ).fetchall()
    finally:
        legacy.close()

    entries = [
        {
            "source": "digest_history",
            "account": r["account"],
            "email_id": r["email_id"],
            "timestamp": r["processed_at"],
            "sender": r["sender"] or "",
            "subject": r["subject"] or "",
            "label": r["label"] or "",
            "confidence": r["confidence"] or 0,
        }
        for r in rows
    ]
    log_email_events(entries)
    return len(entries)