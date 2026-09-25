#!/usr/bin/env python3
"""Daily email digest — scans accounts, classifies, writes report, pings Telegram.

Standalone scheduled job (no MCP server or Hermes agent needed). Uses the
email MCP server's classifier + himalaya wrapper directly.

Schedule: email-digest.timer (systemd user unit), twice daily.
Output: report file + Telegram summary ping.
State: SQLite DB tracks processed email IDs. --dry-run runs the full chain
(scan + classify + report to stdout) with zero persistence.

Configuration (environment variables):
  EMAIL_MCP_ACCOUNTS_FILE  JSON account map: {mcp_name: {display, group}}
                           — holds real addresses, keep OUT of any repo.
  EMAIL_MCP_STATE_DB       processed-email state DB path.
  EMAIL_DIGEST_REPORT_DIR  where report .md files are written.
  TELEGRAM_BOT_TOKEN       from environment at runtime (never hardcoded).
  TELEGRAM_HOME_CHANNEL    destination chat for the summary ping.
"""

import argparse
import asyncio
import json
import os
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path

from himalaya_wrapper import list_envelopes
from async_classifier import classify_batch, ClassificationConfig, ModelTier
from config import load_config
from event_log import log_email_events

# ── Configuration ───────────────────────────────────────────────────────

ACCOUNTS_FILE = Path(os.environ.get(
    "EMAIL_MCP_ACCOUNTS_FILE",
    str(Path.home() / ".config" / "email-mcp" / "digest_accounts.json"),
))
STATE_DB = Path(os.environ.get(
    "EMAIL_MCP_STATE_DB",
    str(Path.home() / ".config" / "email-mcp" / "digest_state.db"),
))
REPORT_DIR = Path(os.environ.get(
    "EMAIL_DIGEST_REPORT_DIR",
    str(Path.home() / "Documents" / "hermes-agent" / "reports" / "email-digests"),
))


def load_accounts():
    """Load {mcp_name: {display, group}} from the private accounts file.

    The file holds real email addresses and business group names — it must
    never be committed. JSON insertion order defines the report's group order.
    """
    if not ACCOUNTS_FILE.exists():
        raise SystemExit(
            f"Accounts file not found: {ACCOUNTS_FILE}\n"
            "Create it (see README) or point EMAIL_MCP_ACCOUNTS_FILE at one."
        )
    raw = json.loads(ACCOUNTS_FILE.read_text())
    return {
        name: {"display": info["display"], "group": info["group"]}
        for name, info in raw.items()
    }


# ── State DB ────────────────────────────────────────────────────────────


def init_db():
    STATE_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(STATE_DB))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS processed_emails (
            account TEXT NOT NULL,
            email_id INTEGER NOT NULL,
            processed_at TEXT NOT NULL,
            label TEXT,
            confidence REAL,
            subject TEXT,
            sender TEXT,
            PRIMARY KEY (account, email_id)
        )
    """)
    conn.commit()
    return conn


def is_processed(conn, account, email_id):
    cur = conn.execute(
        "SELECT 1 FROM processed_emails WHERE account = ? AND email_id = ?",
        (account, email_id),
    )
    return cur.fetchone() is not None


def mark_processed(conn, account, email_id, label, confidence, subject, sender):
    conn.execute(
        "INSERT OR REPLACE INTO processed_emails VALUES (?, ?, ?, ?, ?, ?, ?)",
        (account, email_id, datetime.now().isoformat(), label, confidence, subject[:200], sender[:200]),
    )
    conn.commit()


# ── Telegram ────────────────────────────────────────────────────────────

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT = os.environ.get("TELEGRAM_HOME_CHANNEL", "")


def send_telegram(text):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT:
        return
    # Telegram limit: 4096 chars
    if len(text) > 4000:
        text = text[:3997] + "..."
    try:
        subprocess.run(
            ["curl", "-s", "-X", "POST",
             f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
             "-d", f"chat_id={TELEGRAM_CHAT}",
             "-d", "parse_mode=Markdown",
             "-d", f"text={text}",
             "--max-time", "10"],
            capture_output=True, timeout=15,
        )
    except Exception:
        pass


# ── Digest logic ─────────────────────────────────────────────────────────

LABEL_ORDER = {
    "financial": 0, "receipt": 0, "work": 1, "urgent": 2,
    "personal": 3, "newsletter": 4, "spam": 5, "unsorted": 6,
}


async def scan_account(account_name, himalaya_account, page_size=20):
    """Scan one account, return list of (envelope, classification) for new emails.

    Uses envelope data only (no message read) for speed — the classifier
    gets subject + sender from the envelope, which is enough for categorization.
    """
    envs = list_envelopes(account=himalaya_account, page_size=page_size)
    if not envs:
        return [], []

    # Use envelope data directly (avoids slow message read + attachment list)
    messages = []
    for env in envs:
        messages.append({
            "id": env.id,
            "subject": env.subject,
            "sender": env.sender,
            "recipient": "",
            "body": env.subject,  # use subject as body proxy for classification
            "date": env.date,
        })
    return envs, messages


async def run_digest(conn, cfg, accounts, dry_run=False):
    """Run the full digest across all accounts."""
    report_lines = []
    telegram_lines = []
    total_new = 0
    total_by_label = {}
    per_group = {}

    # Group order = first-appearance order in the accounts file (canonical).
    group_order = []
    for info in accounts.values():
        if info["group"] not in group_order:
            group_order.append(info["group"])

    for mcp_name, info in accounts.items():
        display_name, group = info["display"], info["group"]
        acct = None
        for a in cfg.accounts:
            if a.name == mcp_name:
                acct = a
                break
        if not acct:
            continue

        # Scan
        try:
            envs, messages = await scan_account(mcp_name, acct.himalaya_account, page_size=20)
        except Exception as e:
            report_lines.append(f"  {display_name}: ERROR ({e})")
            continue

        # Filter to new emails only
        new_messages = []
        new_envs = []
        for env, msg in zip(envs, messages):
            if not is_processed(conn, mcp_name, env.id):
                new_messages.append(msg)
                new_envs.append(env)

        if not new_messages:
            per_group.setdefault(group, []).append((display_name, 0, []))
            continue

        # Classify
        cls_cfg = ClassificationConfig(
            tiers=[
                ModelTier(model="phi4-mini:3.8b", provider="local", timeout=15),
                ModelTier(model="nemotron-3-nano:4b", provider="local", timeout=20),
                ModelTier(model="llama3.2:3b", provider="local", timeout=15),
            ],
            allow_cloud_fallback=False,
            confidence_threshold=cfg.classification.confidence_threshold,
            max_preview_chars=cfg.classification.max_preview_chars,
            max_concurrent=4,
        )

        try:
            results = await classify_batch(
                emails=[{"sender": m["sender"], "recipient": m["recipient"],
                         "subject": m["subject"], "body": m["body"]} for m in new_messages],
                classification_config=cls_cfg,
            )
        except Exception as e:
            report_lines.append(f"  {display_name}: CLASSIFY ERROR ({e})")
            continue

        # Build per-email results
        email_results = []
        event_entries = []
        for msg, cls in zip(new_messages, results):
            label = cls.label if cls.label in LABEL_ORDER else "unsorted"
            email_results.append({
                "id": msg["id"],
                "subject": msg["subject"][:80],
                "sender": msg["sender"][:50],
                "label": label,
                "confidence": cls.confidence,
                "model": cls.model_used,
                "date": msg.get("date", ""),
            })
            # Queue for the event store (batched write below)
            event_entries.append({
                "source": "digest",
                "account": mcp_name,
                "himalaya_account": acct.himalaya_account,
                "email_id": msg["id"],
                "sender": msg["sender"],
                "recipient": display_name,
                "subject": msg["subject"][:200],
                "label": label,
                "confidence": cls.confidence,
                "model_used": cls.model_used,
                "action": "noted",
                "timestamp": datetime.now().isoformat(),
            })
            # Mark as processed (skipped in dry-run)
            if not dry_run:
                mark_processed(conn, mcp_name, msg["id"], label, cls.confidence,
                               msg["subject"], msg["sender"])
            # Update totals
            total_by_label[label] = total_by_label.get(label, 0) + 1
            total_new += 1

        # Write to the shared event store (best-effort — digest never dies on logging)
        if event_entries and not dry_run:
            try:
                log_email_events(event_entries)
            except Exception as e:
                report_lines.append(f"  {display_name}: EVENT LOG ERROR ({e})")

        # Sort by label priority, then date
        email_results.sort(key=lambda r: (LABEL_ORDER.get(r["label"], 99), r["date"]))

        per_group.setdefault(group, []).append((display_name, len(email_results), email_results))

    # ── Build report ──────────────────────────────────────────────────────

    now = datetime.now()
    header = f"DAILY EMAIL DIGEST — {now.strftime('%Y-%m-%d %H:%M')}"
    report_lines.insert(0, header)
    report_lines.append("=" * 60)
    report_lines.append("")

    LABEL_ICONS = {
        "financial": "[FIN]", "receipt": "[REC]", "work": "[WORK]",
        "urgent": "[URG]", "personal": "[PERS]", "newsletter": "[NEWS]",
        "spam": "[SPAM]", "unsorted": "[???]",
    }

    for group in group_order:
        if group not in per_group:
            continue
        report_lines.append(f"--- {group.upper()} ---")
        for display_name, count, results in per_group[group]:
            report_lines.append(f"  {display_name} ({count} new)")
            for r in results:
                icon = LABEL_ICONS.get(r["label"], "[???]")
                conf_str = f"conf={r['confidence']:.2f}" if r["confidence"] < 0.9 else ""
                report_lines.append(f"    {icon} {r['subject'][:55]} {conf_str}")
                report_lines.append(f"         from: {r['sender'][:45]}")
            report_lines.append("")
        report_lines.append("")

    # Summary
    report_lines.append("=" * 60)
    report_lines.append(f"TOTAL: {total_new} new emails")
    for label in sorted(total_by_label.keys(), key=lambda l: LABEL_ORDER.get(l, 99)):
        report_lines.append(f"  {LABEL_ICONS.get(label, '[???]')} {label}: {total_by_label[label]}")
    report_lines.append("")
    report_lines.append(f"State DB: {STATE_DB}")
    report_lines.append(f"Generated: {now.isoformat()}")

    report_text = "\n".join(report_lines)

    # ── Telegram summary (short) ──────────────────────────────────────────

    tg_lines = [f"*Daily Email Digest — {now.strftime('%H:%M')}*"]
    tg_lines.append(f"Total: *{total_new}* new emails")
    if total_by_label:
        label_summary = " | ".join(f"{LABEL_ICONS.get(l, '?')} {l}: {c}"
                                   for l, c in sorted(total_by_label.items(),
                                                       key=lambda x: LABEL_ORDER.get(x[0], 99)))
        tg_lines.append(label_summary)
    tg_lines.append("")

    for group in group_order:
        if group not in per_group:
            continue
        group_total = sum(c for _, c, _ in per_group[group])
        if group_total == 0:
            continue
        tg_lines.append(f"*{group}* ({group_total} new)")
        for display_name, count, results in per_group[group]:
            if count == 0:
                continue
            tg_lines.append(f"  {display_name}: {count}")
            # Show financial and urgent only in Telegram (keep it short)
            for r in results:
                if r["label"] in ("financial", "receipt", "urgent"):
                    icon = LABEL_ICONS.get(r["label"], "[?]")
                    tg_lines.append(f"    {icon} {r['subject'][:50]}")

    telegram_text = "\n".join(tg_lines)

    return report_text, telegram_text


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Daily email digest — scan, classify, report, Telegram ping.")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="run scan + classify + build report, but skip all persistence: "
             "no state-DB marking, no event log, no report file, no Telegram.")
    args = parser.parse_args()

    accounts = load_accounts()
    conn = init_db()
    cfg = load_config()

    report_text, telegram_text = asyncio.run(
        run_digest(conn, cfg, accounts, dry_run=args.dry_run))

    if args.dry_run:
        print(report_text)
        print("[dry-run] state DB, event log, report file, and Telegram all skipped.")
        conn.close()
        return

    # Write report file
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    report_file = REPORT_DIR / f"{now.strftime('%Y-%m-%d-%H%M')}.md"
    report_file.write_text(report_text)

    # Print to stdout (for the systemd unit's log capture)
    print(report_text)
    print(f"\nReport saved: {report_file}")

    # Send Telegram ping
    send_telegram(telegram_text)
    print("Telegram ping sent.")

    conn.close()


if __name__ == "__main__":
    main()