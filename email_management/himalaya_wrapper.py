"""Subprocess wrapper around himalaya CLI for IMAP operations.

All commands use JSON output for structured parsing.
Credentials are handled by himalaya's own config.toml — this module never sees passwords.
"""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional


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


def _run_himalaya(args: list, account: Optional[str] = None, timeout: int = 30) -> str:
    """Run himalaya command, return stdout. Raise on non-zero exit."""
    cmd = ["himalaya"]
    if account:
        cmd.extend(["--account", account])
    cmd.extend(args)
    cmd.append("--output")
    cmd.append("json")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"himalaya failed (exit {result.returncode}): {result.stderr.strip()}\n"
            f"Command: {' '.join(cmd)}"
        )

    return result.stdout.strip()


def list_envelopes(
    account: str,
    folder: str = "INBOX",
    page: int = 1,
    page_size: int = 50,
    search_query: Optional[str] = None,
) -> List[Envelope]:
    """List email envelopes from a folder with optional search."""
    args = ["envelope", "list", "--folder", folder, "--page", str(page), "--page-size", str(page_size)]
    if search_query:
        args.extend(search_query.split())

    output = _run_himalaya(args, account=account)
    if not output:
        return []

    raw_list = json.loads(output)
    if not isinstance(raw_list, list):
        return []

    envelopes = []
    for item in raw_list:
        if isinstance(item, dict) and "id" in item:
            envelopes.append(Envelope(
                id=item["id"],
                subject=item.get("subject", ""),
                sender=item.get("from", ""),
                date=item.get("date", ""),
                flags=item.get("flags", []),
            ))
    return envelopes


def read_message_body(account: str, email_id: int) -> MessageBody:
    """Read plain text body of an email by ID."""
    args = ["message", "read", str(email_id)]
    text_output = _run_himalaya(args, account=account)

    lines = text_output.split("\n")
    subject = ""
    sender = ""
    recipient = ""

    for line in lines[:20]:
        lower = line.lower()
        if lower.startswith("subject:"):
            subject = line.split(":", 1)[1].strip()
        elif lower.startswith("from:"):
            sender = line.split(":", 1)[1].strip()
        elif lower.startswith("to:"):
            recipient = line.split(":", 1)[1].strip()

    has_attachments = False
    try:
        export_args = ["message", "export", str(email_id), "--full"]
        export_output = _run_himalaya(export_args, account=account)
        has_attachments = "Content-Disposition: attachment" in export_output
    except RuntimeError:
        pass

    return MessageBody(
        id=email_id,
        subject=subject,
        sender=sender,
        recipient=recipient,
        body=text_output,
        has_attachments=has_attachments,
    )


def download_attachments(account: str, email_id: int, output_dir: str) -> List[str]:
    """Download all attachments from an email. Returns list of downloaded file paths."""
    args = ["attachment", "download", str(email_id), "--dir", output_dir]
    output = _run_himalaya(args, account=account, timeout=60)

    paths = []
    for line in output.split("\n"):
        line = line.strip()
        if line and Path(line).exists():
            paths.append(line)

    return paths


def check_imap_connection(account: str) -> bool:
    """Verify himalaya can connect to IMAP for this account."""
    try:
        _run_himalaya(["folder", "list"], account=account, timeout=10)
        return True
    except RuntimeError:
        return False
