#!/usr/bin/env python3
"""Email Management MCP Server.

Provides tools for classifying emails, routing attachments, and
processing inboxes — all using local himalaya + ollama, zero cloud
by default.

Cloud fallback (deepseek-v4-flash:cloud) is disabled by default.
User must set `allow_cloud_fallback: true` in server_config.yaml
to enable it.
"""

import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

try:
    from .classifier import classify_email
    from .config import load_config, ServerConfig, AccountConfig
    from .himalaya_wrapper import (
        check_imap_connection,
        download_attachments,
        list_envelopes,
        read_message_body,
    )
    from .router import load_rules, RoutingResult
except ImportError:
    from classifier import classify_email
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
    """Move a file to destination, handling duplicates. Returns final path or None."""
    src_path = Path(src)
    dest_path = Path(dest_dir).expanduser().resolve() / src_path.name
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    if dest_path.exists() and skip_existing:
        return None

    shutil.move(str(src_path), str(dest_path))
    return str(dest_path)


def _classify_and_route(acct: AccountConfig, msg_id: int, sender: str, recipient: str, subject: str, body: str) -> dict:
    """Classify an email and find its routing rule. Returns classification + routing dict."""
    cfg = _get_config()

    cls = classify_email(
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
        "routing_path": routing.expanded_path if routing else None,
        "action": routing.action if routing else "save",
        "cloud_fallback_used": cls.model_used == "deepseek-v4-flash:cloud",
        "keyword_fallback_used": cls.model_used == "keyword-fallback",
    }


@mcp.tool()
def filter_attachments(
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

    results = []
    for env in envelopes:
        msg = read_message_body(acct.himalaya_account, env.id)
        if not msg.has_attachments:
            continue

        result = _classify_and_route(acct, env.id, msg.sender, msg.recipient, msg.subject, msg.body)
        results.append(result)

    return {
        "account": account_name,
        "folder": folder,
        "scanned": len(envelopes),
        "with_attachments": len(results),
        "results": results,
    }


@mcp.tool()
def classify_email_tool(
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
    return _classify_and_route(acct, email_id, msg.sender, msg.recipient, msg.subject, msg.body)


@mcp.tool()
def route_attachments(
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

    routed = []
    skipped = []
    errored = []
    cloud_fallback_count = 0
    keyword_fallback_count = 0

    dl_root = Path(cfg.attachments.download_dir)
    dl_dir = dl_root / datetime.now().strftime("%Y%m%d-%H%M%S")
    dl_dir.mkdir(parents=True, exist_ok=True)

    for env in envelopes:
        msg = read_message_body(acct.himalaya_account, env.id)
        if not msg.has_attachments:
            continue

        result = _classify_and_route(acct, env.id, msg.sender, msg.recipient, msg.subject, msg.body)

        if result["cloud_fallback_used"]:
            cloud_fallback_count += 1
        if result["keyword_fallback_used"]:
            keyword_fallback_count += 1

        if result["routing_path"] is None:
            skipped.append({
                "id": env.id,
                "subject": result["subject"],
                "reason": "no matching rule",
            })
            continue

        if result["action"] == "archive-no-save":
            routed.append({
                "id": env.id,
                "subject": result["subject"],
                "label": result["label"],
                "action": "archive-no-save",
            })
            continue

        if dry_run:
            routed.append({
                "id": env.id,
                "subject": result["subject"],
                "label": result["label"],
                "would_save_to": result["routing_path"],
                "action": "save",
            })
            continue

        # Download and move
        try:
            downloaded = download_attachments(acct.himalaya_account, env.id, str(dl_dir))
            for filepath in downloaded:
                final_path = _move_attachment(
                    filepath,
                    result["routing_path"],
                    skip_existing=cfg.attachments.skip_existing,
                )
                if final_path:
                    routed.append({
                        "id": env.id,
                        "subject": result["subject"],
                        "file": Path(final_path).name,
                        "saved_to": final_path,
                        "label": result["label"],
                    })
                else:
                    skipped.append({
                        "id": env.id,
                        "subject": result["subject"],
                        "file": Path(filepath).name,
                        "reason": "already exists at destination",
                    })
        except RuntimeError as e:
            errored.append({
                "id": env.id,
                "subject": result["subject"],
                "error": str(e),
            })

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
def process_inbox(
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
    return route_attachments(
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
