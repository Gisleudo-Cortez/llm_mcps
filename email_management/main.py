#!/usr/bin/env python3
"""Email Management MCP Server.

Provides tools for classifying emails, routing attachments, and
processing inboxes — all using local himalaya + ollama, zero cloud
by default.

Cloud fallback (google/gemma-4-31b-it:free, deepseek-v4-flash) is opt-in.
"""

import hashlib
import json
import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# ── Structured logging ──────────────────────────────────────────────────
# Logs to ~/.config/email-mcp/email-mcp.log (JSON lines, append-only)
# Levels: DEBUG (per-email), INFO (batch summary), WARNING (cloud fallback),
#          ERROR (failures)
_LOG_DIR = Path.home() / ".config" / "email-mcp"
_LOG_FILE = _LOG_DIR / "email-mcp.log"

logger = logging.getLogger("email_mcp")
logger.setLevel(logging.DEBUG)

# File handler — JSON lines, append-only, survives restarts
_log_fh = logging.FileHandler(_LOG_FILE)
_log_fh.setLevel(logging.DEBUG)
_log_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
logger.addHandler(_log_fh)

# Console handler for stderr (visible during MCP dev, swallowed in production)
_log_sh = logging.StreamHandler(sys.stderr)
_log_sh.setLevel(logging.WARNING)
_log_sh.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
logger.addHandler(_log_sh)

from mcp.server.fastmcp import FastMCP

try:
    from .async_classifier import classify_email_async, classify_batch, ClassificationResult
    from .config import load_config, ServerConfig, AccountConfig
    from .himalaya_wrapper import (
        check_imap_connection,
        download_attachments,
        list_envelopes,
        read_message_body,
    )
    from .router import load_rules, RoutingResult
except ImportError:
    from async_classifier import classify_email_async, classify_batch, ClassificationResult
    from config import load_config, ServerConfig, AccountConfig
    from himalaya_wrapper import (
        check_imap_connection,
        download_attachments,
        list_envelopes,
        read_message_body,
    )
    from router import load_rules, RoutingResult

mcp = FastMCP("email-management")

try:
    config = load_config()
except FileNotFoundError:
    print("ERROR: server_config.yaml not found at ~/.config/email-mcp/server_config.yaml", file=sys.stderr)
    config = None


def _get_config() -> ServerConfig:
    if config is None:
        raise RuntimeError("Server config not loaded")
    return config


def _get_account(name: str) -> AccountConfig:
    cfg = _get_config()
    for a in cfg.accounts:
        if a.name == name:
            return a
    raise ValueError(f"Account '{name}' not found in config")


def _move_attachment(src: str, dest_dir: str, skip_existing: bool = True) -> Optional[str]:
    """Move a file to destination with content-hash dedup. Returns final path or None.

    If a file with the same name exists at the destination:
    - Same content (SHA-256 match) → skip (true duplicate), return None
    - Different content → append _1, _2, etc. before extension (collision)
    - skip_existing=False → always overwrite
    """
    src_path = Path(src)
    # Sanitize filename — strip path separators, reject traversal attempts
    safe_name = src_path.name.replace("/", "_").replace("\\", "_").replace("..", "_")
    if safe_name in (".", "..", ""):
        safe_name = "unnamed_attachment"
    dest_path = Path(dest_dir).expanduser().resolve() / safe_name
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    if dest_path.exists() and skip_existing:
        # Content-hash comparison — only skip if truly identical
        src_hash = _sha256_file(src_path)
        dest_hash = _sha256_file(dest_path)
        if src_hash == dest_hash:
            logger.debug("dedup skip: %s (hash match)", dest_path.name)
            return None
        # Name collision, different content — rename with suffix
        stem = dest_path.stem
        suffix = dest_path.suffix
        counter = 1
        while dest_path.exists():
            dest_path = dest_path.parent / f"{stem}_{counter}{suffix}"
            counter += 1
        logger.debug("name collision: renamed to %s", dest_path.name)

    shutil.move(str(src_path), str(dest_path))
    return str(dest_path)


def _sha256_file(path: Path, chunk_size: int = 65536) -> str:
    """Compute SHA-256 hash of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _audit_log(action: str, details: dict) -> None:
    """Append an audit entry to the log file (JSONL format, append-only).

    Records every file operation: routed, skipped, errored.
    Survives restarts — the log is the durable record of what happened.
    """
    entry = {
        "timestamp": datetime.now().isoformat(),
        "action": action,
        **details,
    }
    logger.info("audit: %s %s", action, json.dumps(details, default=str))


async def _classify_and_route(acct: AccountConfig, msg_id: int, sender: str, recipient: str, subject: str, body: str) -> dict:
    """Classify an email and find its routing rule. Returns classification + routing dict."""
    cfg = _get_config()

    cls = await classify_email_async(
        sender=sender,
        recipient=recipient,
        subject=subject,
        body=body,
        classification_config=cfg.classification,
    )

    ruleset = load_rules(acct.rules_file)
    routing: Optional[RoutingResult] = ruleset.match(sender, recipient)

    return {
        "id": msg_id,
        "subject": subject[:80],
        "sender": sender,
        "label": cls.label,
        "confidence": cls.confidence,
        "needs_review": cls.needs_review,
        "model_used": cls.model_used,
        "latency_ms": round(cls.latency_ms, 1),
        "routing_path": routing.expanded_path if routing else None,
        "action": routing.action if routing else "save",
        "cloud_fallback_used": cls.model_used in ("google/gemma-4-31b-it:free", "deepseek/deepseek-v4-flash"),
        "keyword_fallback_used": cls.model_used == "keyword-fallback",
    }


async def _batch_classify_and_route(acct: AccountConfig, messages: list) -> list[dict]:
    """Classify multiple emails concurrently and return routing results."""
    cfg = _get_config()

    emails = []
    for msg in messages:
        emails.append({
            "sender": msg.sender,
            "recipient": msg.recipient,
            "subject": msg.subject,
            "body": msg.body,
        })

    # Await batch classification
    results = await classify_batch(
        emails=emails,
        classification_config=cfg.classification,
    )

    ruleset = load_rules(acct.rules_file)
    output = []
    for msg, cls in zip(messages, results):
        routing: Optional[RoutingResult] = ruleset.match(msg.sender, msg.recipient)
        output.append({
            "id": msg.id,
            "subject": msg.subject[:80],
            "sender": msg.sender,
            "label": cls.label,
            "confidence": cls.confidence,
            "needs_review": cls.needs_review,
            "model_used": cls.model_used,
            "latency_ms": round(cls.latency_ms, 1),
            "routing_path": routing.expanded_path if routing else None,
            "action": routing.action if routing else "save",
            "cloud_fallback_used": cls.model_used in ("google/gemma-4-31b-it:free", "deepseek/deepseek-v4-flash"),
            "keyword_fallback_used": cls.model_used == "keyword-fallback",
        })
    return output


@mcp.tool()
async def filter_attachments(
    account_name: str,
    folder: str = "INBOX",
    search_query: str = "",
    page_size: int = 50,
) -> dict:
    """Scan a folder, classify emails with attachments, return what would happen.

    Dry-run only — does NOT download or move anything. Classify-first approach.

    **TRIGGER CONDITION:** Use when the user wants to preview what would happen
    before committing to a route_attachments or process_inbox call.

    **OUTPUT EXPECTATION:** JSON with count, list of {id, subject, sender, label,
    confidence, model_used, routing_path, action, cloud_fallback_used}
    """
    try:
        acct = _get_account(account_name)
    except ValueError as e:
        return {"error": str(e)}

    envelopes = list_envelopes(
        account=acct.himalaya_account,
        folder=folder,
        page_size=page_size,
        search_query=search_query or None,
    )

    # Collect emails with attachments
    messages = []
    for env in envelopes:
        msg = read_message_body(acct.himalaya_account, env.id)
        if msg.has_attachments:
            messages.append(msg)

    # Batch classify
    results = await _batch_classify_and_route(acct, messages)

    return {
        "account": account_name,
        "folder": folder,
        "scanned": len(envelopes),
        "with_attachments": len(results),
        "results": results,
    }


@mcp.tool()
async def classify_email_tool(
    account_name: str,
    email_id: int,
) -> dict:
    """Classify a single email and return its label, confidence, and routing path.

    **TRIGGER CONDITION:** Use when the user wants to check a specific email's
    classification without processing it.

    **OUTPUT EXPECTATION:** JSON with classification result and routing info.
    """
    try:
        acct = _get_account(account_name)
    except ValueError as e:
        return {"error": str(e)}

    msg = read_message_body(acct.himalaya_account, email_id)
    return await _classify_and_route(acct, email_id, msg.sender, msg.recipient, msg.subject, msg.body)


@mcp.tool()
async def route_attachments(
    account_name: str,
    folder: str = "INBOX",
    search_query: str = "",
    page_size: int = 50,
    dry_run: bool = False,
) -> dict:
    """Classify, download, and route attachments from a folder.

    Full pipeline: list → classify-first → download → move to computed paths.
    Post-route: does nothing to the email (no mark-read, no move, no delete).

    **TRIGGER CONDITION:** Use when the user wants to process attachments from
    a specific folder. Set dry_run=true to only classify without moving.

    **OUTPUT EXPECTATION:** JSON with summary of routed/skipped/errored files,
    cloud fallback usage, and per-email details.
    """
    try:
        acct = _get_account(account_name)
    except ValueError as e:
        return {"error": str(e)}

    cfg = _get_config()

    envelopes = list_envelopes(
        account=acct.himalaya_account,
        folder=folder,
        page_size=page_size,
        search_query=search_query or None,
    )

    # Collect emails with attachments
    messages = []
    for env in envelopes:
        msg = read_message_body(acct.himalaya_account, env.id)
        if msg.has_attachments:
            messages.append(msg)

    # Batch classify
    results = await _batch_classify_and_route(acct, messages)

    logger.info("batch_start account=%s folder=%s scanned=%d with_attachments=%d",
                account_name, folder, len(envelopes), len(messages))

    routed = []
    skipped = []
    errored = []
    cloud_fallback_count = 0
    keyword_fallback_count = 0

    dl_root = Path(cfg.attachments.download_dir)
    dl_dir = dl_root / datetime.now().strftime("%Y%m%d-%H%M%S")
    dl_dir.mkdir(parents=True, exist_ok=True)

    for result in results:
        if result["cloud_fallback_used"]:
            cloud_fallback_count += 1
        if result["keyword_fallback_used"]:
            keyword_fallback_count += 1

        if result["routing_path"] is None:
            skipped.append({
                "id": result["id"],
                "subject": result["subject"],
                "reason": "no matching rule",
            })
            continue

        if result["action"] == "archive-no-save":
            routed.append({
                "id": result["id"],
                "subject": result["subject"],
                "label": result["label"],
                "action": "archive-no-save",
            })
            continue

        if dry_run:
            routed.append({
                "id": result["id"],
                "subject": result["subject"],
                "label": result["label"],
                "would_save_to": result["routing_path"],
                "action": "save",
            })
            continue

        # Download and move
        try:
            downloaded = download_attachments(acct.himalaya_account, result["id"], str(dl_dir))
            for filepath in downloaded:
                final_path = _move_attachment(
                    filepath,
                    result["routing_path"],
                    skip_existing=cfg.attachments.skip_existing,
                )
                if final_path:
                    routed.append({
                        "id": result["id"],
                        "subject": result["subject"],
                        "file": Path(final_path).name,
                        "saved_to": final_path,
                        "label": result["label"],
                    })
                    _audit_log("routed", {"email_id": result["id"], "file": Path(final_path).name,
                                          "dest": final_path, "label": result["label"]})
                else:
                    skipped.append({
                        "id": result["id"],
                        "subject": result["subject"],
                        "file": Path(filepath).name,
                        "reason": "already exists at destination (hash match)",
                    })
                    _audit_log("skipped_dedup", {"email_id": result["id"], "file": Path(filepath).name})
        except RuntimeError as e:
            errored.append({
                "id": result["id"],
                "subject": result["subject"],
                "error": str(e),
            })
            logger.error("route error email_id=%d: %s", result["id"], str(e)[:200])
            _audit_log("error", {"email_id": result["id"], "error": str(e)[:500]})

    # Clean up temp dir
    for f in dl_dir.iterdir():
        try:
            f.unlink()
        except OSError:
            pass
    try:
        dl_dir.rmdir()
    except OSError:
        pass

    # Batch summary log
    logger.info("batch_done account=%s folder=%s routed=%d skipped=%d errors=%d cloud=%d keyword=%d",
                account_name, folder, len(routed), len(skipped), len(errored),
                cloud_fallback_count, keyword_fallback_count)
    if cloud_fallback_count > 0:
        logger.warning("cloud_fallback_used count=%d — email content was sent to cloud for %d emails",
                       cloud_fallback_count, cloud_fallback_count)

    return {
        "account": account_name,
        "folder": folder,
        "scanned": len(envelopes),
        "routed": len(routed),
        "skipped": len(skipped),
        "errors": len(errored),
        "cloud_fallback_used": cloud_fallback_count,
        "keyword_fallback_used": keyword_fallback_count,
        "routed_details": routed,
        "skipped_details": skipped,
        "error_details": errored,
        "timestamp": datetime.now().isoformat(),
    }


@mcp.tool()
async def process_inbox(
    account_name: str,
    folder: str = "INBOX",
    page_size: int = 50,
) -> dict:
    """Full inbox processing: scan, classify, route attachments, report.

    One command for cron/scheduled use. Same as route_attachments with dry_run=false.

    **TRIGGER CONDITION:** Use for automated daily sweeps via cron or when the
    user says "process my inbox".

    **OUTPUT EXPECTATION:** Same as route_attachments.
    """
    return await route_attachments(
        account_name=account_name,
        folder=folder,
        page_size=page_size,
        dry_run=False,
    )


@mcp.tool()
def list_accounts() -> dict:
    """List all configured email accounts.

    **OUTPUT EXPECTATION:** JSON with account names and himalaya account references.
    """
    cfg = _get_config()
    return {
        "accounts": [
            {
                "name": a.name,
                "himalaya_account": a.himalaya_account,
                "rules_file": a.rules_file,
                "folders": a.folders,
            }
            for a in cfg.accounts
        ]
    }


@mcp.tool()
def list_rules(account_name: str) -> dict:
    """Return routing rules for a specific account as structured JSON.

    **OUTPUT EXPECTATION:** JSON with rules_file path and rule list.
    """
    try:
        acct = _get_account(account_name)
    except ValueError as e:
        return {"error": str(e)}

    ruleset = load_rules(acct.rules_file)
    return {
        "account": account_name,
        "rules_file": acct.rules_file,
        "rules": [
            {
                "recipient": r.recipient,
                "sender": r.sender,
                "path": r.path,
                "label": r.label,
                "action": r.action,
            }
            for r in ruleset.rules
        ],
    }


@mcp.tool()
def check_connection(account_name: str) -> dict:
    """Verify himalaya can connect to IMAP for a specific account.

    **TRIGGER CONDITION:** Use before processing to diagnose connection issues.

    **OUTPUT EXPECTATION:** JSON with connected: true/false, account name.
    """
    try:
        acct = _get_account(account_name)
    except ValueError as e:
        return {"error": str(e)}

    ok = check_imap_connection(acct.himalaya_account)
    return {
        "account": account_name,
        "connected": ok,
    }


def main():
    mcp.run()


if __name__ == "__main__":
    main()
