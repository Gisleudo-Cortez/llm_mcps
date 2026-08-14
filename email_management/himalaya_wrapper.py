"""Subprocess wrapper around himalaya CLI v2.0.0 for IMAP operations.

Updated for himalaya v2.0.0 (2026-08-14):
- --output json → --json
- --folder → -m/--mailbox
- folder list → mailbox list
- message export removed → attachment list for attachment detection
- envelope list wraps in {"envelopes": [...]} (not raw array)
- envelope from/to are arrays of {name, email} objects (not strings)
- search is a separate command (envelope search), not inline in list

Credentials are handled by himalaya's own config.toml — this module never sees passwords.
"""

import json
import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("email_mcp.himalaya")


@dataclass
class Envelope:
    id: int
    subject: str
    sender: str
    date: str
    flags: List[str]


@dataclass
class MessageBody:
    id: int
    subject: str
    sender: str
    recipient: str
    body: str
    has_attachments: bool


def _run_himalaya(
    args: list,
    account: Optional[str] = None,
    timeout: int = 30,
    json_output: bool = True,
    retries: int = 1,
) -> str:
    """Run himalaya command, return stdout. Raise on non-zero exit.

    v2: --json replaces --output json. --account can go before or after subcommand.
    Includes retry with 2s backoff for transient IMAP failures.
    """
    cmd = ["himalaya"]
    cmd.extend(args)
    if account:
        cmd.extend(["--account", account])
    if json_output:
        cmd.append("--json")

    last_error: Exception = RuntimeError(f"himalaya failed: {' '.join(cmd)}")
    for attempt in range(retries + 1):
        if attempt > 0:
            logger.debug("Retry %d/%d for: %s", attempt, retries, " ".join(cmd))
            time.sleep(2)

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            if result.returncode != 0:
                last_error = RuntimeError(
                    f"himalaya failed (exit {result.returncode}): {result.stderr.strip()}\n"
                    f"Command: {' '.join(cmd)}"
                )
                logger.warning("himalaya exit %d: %s", result.returncode, result.stderr.strip()[:200])
                continue

            return result.stdout.strip()
        except subprocess.TimeoutExpired:
            last_error = RuntimeError(f"himalaya timed out after {timeout}s: {' '.join(cmd)}")
            logger.warning("himalaya timeout after %ds: %s", timeout, " ".join(cmd))
            continue

    raise last_error


def list_envelopes(
    account: str,
    folder: str = "INBOX",
    page: int = 1,
    page_size: int = 50,
    search_query: Optional[str] = None,
) -> List[Envelope]:
    """List email envelopes from a mailbox with optional search.

    v2: --folder → -m/--mailbox. Search is a separate command (envelope search).
    Envelope JSON wraps in {"envelopes": [...]} with from/to as object arrays.
    """
    if search_query:
        args = ["envelope", "search", "-m", folder] + search_query.split()
    else:
        args = ["envelope", "list", "-m", folder, "-p", str(page), "-s", str(page_size)]

    output = _run_himalaya(args, account=account)
    if not output:
        return []

    data = json.loads(output)
    raw_list = data.get("envelopes", [])
    if not isinstance(raw_list, list):
        return []

    envelopes: List[Envelope] = []
    for item in raw_list:
        if not isinstance(item, dict) or "id" not in item:
            continue
        from_list = item.get("from", [])
        sender = ""
        if isinstance(from_list, list) and from_list:
            sender = from_list[0].get("email", "") if isinstance(from_list[0], dict) else str(from_list[0])
        envelopes.append(Envelope(
            id=int(item["id"]),  # v2: id is string, convert to int
            subject=item.get("subject", ""),
            sender=sender,
            date=item.get("date", ""),
            flags=item.get("flags", []),
        ))
    return envelopes


def _check_attachments(account: str, email_id: int) -> bool:
    """Check if an email has attachments via attachment list --json.

    v2: Replaces removed 'message export --full' approach.
    Returns True if the email has at least one attachment.
    """
    try:
        args = ["attachment", "list", str(email_id)]
        output = _run_himalaya(args, account=account, timeout=15)
        data = json.loads(output)
        attachments = data.get("attachments", [])
        return len(attachments) > 0
    except (RuntimeError, json.JSONDecodeError, KeyError) as e:
        logger.debug("attachment list failed for %d: %s", email_id, e)
        return False


def read_message_body(account: str, email_id: int) -> MessageBody:
    """Read plain text body of an email by ID.

    v2: message export removed. Uses message read (plain text) for headers+body,
    attachment list --json for attachment detection.

    Header parsing handles multi-line headers (RFC 5322 continuation lines)
    and the header/body separator (blank line).
    """
    # Get message text (without --json for lighter output)
    args = ["message", "read", str(email_id)]
    text_output = _run_himalaya(args, account=account, json_output=False)

    # Parse headers from plain text (RFC 5322 format)
    lines = text_output.split("\n")
    subject = ""
    sender = ""
    recipient = ""
    body_start = 0
    in_headers = True

    for i, line in enumerate(lines):
        if in_headers:
            # Header/body separator: blank line after at least one header
            if line.strip() == "" and i > 0:
                in_headers = False
                body_start = i + 1
                continue
            # Skip continuation lines (start with whitespace) — they belong to previous header
            if line and line[0].isspace():
                continue
            lower = line.lower()
            if lower.startswith("subject:"):
                subject = line.split(":", 1)[1].strip()
            elif lower.startswith("from:"):
                sender = line.split(":", 1)[1].strip()
            elif lower.startswith("to:"):
                recipient = line.split(":", 1)[1].strip()
        else:
            break

    body = "\n".join(lines[body_start:])

    # Check attachments via attachment list (lightweight, reliable)
    has_attachments = _check_attachments(account, email_id)

    return MessageBody(
        id=email_id,
        subject=subject,
        sender=sender,
        recipient=recipient,
        body=body,
        has_attachments=has_attachments,
    )


def download_attachments(account: str, email_id: int, output_dir: str) -> List[str]:
    """Download all attachments from an email. Returns list of downloaded file paths.

    v2: --dir replaces --downloads-dir. Omit attachment-id to download all.
    """
    args = ["attachment", "download", str(email_id), "--dir", output_dir]
    output = _run_himalaya(args, account=account, timeout=60, json_output=False)

    paths: List[str] = []
    for line in output.split("\n"):
        line = line.strip()
        if line and Path(line).exists():
            paths.append(line)
    return paths


def check_imap_connection(account: str) -> bool:
    """Verify himalaya can connect to IMAP for this account.

    v2: 'folder list' → 'mailbox list'.
    """
    try:
        _run_himalaya(["mailbox", "list"], account=account, timeout=10)
        return True
    except RuntimeError:
        return False