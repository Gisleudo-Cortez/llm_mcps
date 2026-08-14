"""MCP server for terminal command documentation — man pages, tldr, cheat.sh.

Mirrors the Neovim documentation workflow: authoritative man pages first,
practical TLDR summaries second, community cheat.sh for edge cases.
"""

import os
import re
import sys
from enum import Enum
from functools import lru_cache
from typing import Literal

import requests
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, field_validator

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _shared import run_command

# ── Server init ─────────────────────────────────────────────────────────────────

mcp = FastMCP("command_docs_mcp")

CHEAT_SH_BASE = "https://cheat.sh"

# Shared session for connection reuse
_session = requests.Session()
_session.headers.update({
    "User-Agent": "curl/7.0",
    "Accept": "text/plain",
})


# ── Enums ──────────────────────────────────────────────────────────────────────

class _ResponseFormat(str, Enum):
    markdown = "markdown"
    json = "json"


# ── Input models ───────────────────────────────────────────────────────────────

class ManLookupInput(BaseModel):
    """Input for retrieving a man page."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    command: str = Field(
        ...,
        description="Exact command name (e.g., 'tar', 'systemctl', 'git-commit').",
        min_length=1,
        max_length=100,
    )
    response_format: _ResponseFormat = Field(
        default=_ResponseFormat.markdown,
        description="Output format: 'markdown' or 'json'.",
    )

    @field_validator("command")
    @classmethod
    def _validate_command(cls, v: str) -> str:
        v = v.strip()
        if " " in v or "/" in v:
            suggested = v.replace(" ", "-")
            raise ValueError(
                f"Multi-word command '{v}' is not a valid single command name. "
                f"Did you mean '{suggested}'? Try using the hyphenated form instead."
            )
        if not re.match(r"^[a-zA-Z0-9_.@-]+$", v):
            raise ValueError("Command name must be alphanumeric with hyphens, underscores, dots, or @.")
        return v


class TldrLookupInput(BaseModel):
    """Input for retrieving a TLDR cheat sheet."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    command: str = Field(
        ...,
        description="Exact command name (e.g., 'tar', 'docker', 'git').",
        min_length=1,
        max_length=100,
    )
    response_format: _ResponseFormat = Field(
        default=_ResponseFormat.markdown,
        description="Output format: 'markdown' or 'json'.",
    )

    @field_validator("command")
    @classmethod
    def _validate_command(cls, v: str) -> str:
        v = v.strip()
        if " " in v or "/" in v:
            suggested = v.replace(" ", "-")
            raise ValueError(
                f"Multi-word command '{v}' is not a valid single command name. "
                f"Did you mean '{suggested}'? Try using the hyphenated form instead."
            )
        if not re.match(r"^[a-zA-Z0-9_.@-]+$", v):
            raise ValueError("Command name must be alphanumeric with hyphens, underscores, dots, or @.")
        return v


class CheatShLookupInput(BaseModel):
    """Input for fetching community cheat sheets from cheat.sh."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    command: str = Field(
        ...,
        description="Tool or language name (e.g., 'git', 'awk', 'python').",
        min_length=1,
        max_length=100,
    )
    query: str = Field(
        default="",
        description="Optional specific topic within the command (e.g., 'rebase', 'list comprehension').",
        max_length=100,
    )
    style: Literal["plain", "color"] = Field(
        default="plain",
        description="Output style: 'plain' (no ANSI) or 'color'.",
    )
    response_format: _ResponseFormat = Field(
        default=_ResponseFormat.markdown,
        description="Output format: 'markdown' or 'json'.",
    )

    @field_validator("command")
    @classmethod
    def _validate_command(cls, v: str) -> str:
        v = v.strip()
        if " " in v or "/" in v:
            suggested = v.replace(" ", "-")
            raise ValueError(
                f"Multi-word command '{v}' is not a valid single command name. "
                f"Did you mean '{suggested}'? Try using the hyphenated form instead."
            )
        if not re.match(r"^[a-zA-Z0-9_.@-]+$", v):
            raise ValueError("Command name must be alphanumeric with hyphens, underscores, dots, or @.")
        return v


# ── Helpers ────────────────────────────────────────────────────────────────────

def _format_response(text: str, heading: str, response_format: _ResponseFormat) -> str:
    if response_format == _ResponseFormat.markdown:
        return f"### {heading}\n\n{text}"
    import json as _json
    return _json.dumps({"heading": heading, "content": text}, indent=2, ensure_ascii=False)


# ── Cached inner helpers ────────────────────────────────────────────────────────
# lru_cache on raw subprocess/HTTP calls — the @mcp.tool() wrappers call these.
# Man pages and TLDR content are immutable between package updates, so session-
# level caching is safe. cheat.sh is network-backed; cached per command+query key.


@lru_cache(maxsize=256)
def _man_page(command: str) -> str:
    """Cached man page retrieval. Man pages don't change between package versions."""
    return run_command(["man", "-P", "cat", command])


@lru_cache(maxsize=256)
def _tldr_page(command: str) -> str:
    """Cached TLDR page retrieval. TLDR cache only updates via explicit 'tldr --update'."""
    return run_command(["tldr", command])


@lru_cache(maxsize=128)
def _cheat_sh_fetch(path: str, style: str) -> str:
    """Cached cheat.sh fetch. Content is community-maintained and rarely changes."""
    url = f"{CHEAT_SH_BASE}/{path}"
    req_params: dict = {}
    if style == "plain":
        req_params["T"] = ""
    try:
        resp = _session.get(url, params=req_params, timeout=15)
        resp.raise_for_status()
        return resp.text.strip()
    except requests.exceptions.Timeout:
        return "__TIMEOUT__"
    except requests.exceptions.RequestException as e:
        return f"__ERROR__:{e}"


# ── Tools ──────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="command_docs_man_lookup",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def command_docs_man_lookup(params: ManLookupInput) -> str:
    """Retrieve the full manual page for a terminal command.

    **TRIGGER CONDITION:** Use when you need comprehensive command documentation,
    flag descriptions, or complete option reference. Best for unfamiliar commands.

    **SEQUENCE GUIDANCE:** Provide the exact command name. Combine with
    `command_docs_tldr_lookup` for quick examples. Man pages are authoritative
    — prefer them for edge cases.

    **CONSTRAINT WARNING:** If a page doesn't exist, check spelling or ensure
    the package is installed. Some commands have multiple sections.

    **OUTPUT EXPECTATION:** Returns the full man page content: SYNOPSIS,
    DESCRIPTION, OPTIONS, EXAMPLES, SEE ALSO.
    """
    output = _man_page(params.command)
    if "No manual entry" in output or "can't open" in output.lower():
        return f"Error: No manual page found for '{params.command}'."
    return _format_response(output, f"Man Page: {params.command}", params.response_format)


@mcp.tool(
    name="command_docs_tldr_lookup",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def command_docs_tldr_lookup(params: TldrLookupInput) -> str:
    """Retrieve a simplified cheat sheet with practical examples for a command.

    **TRIGGER CONDITION:** Use when you need quick, practical usage examples
    rather than exhaustive documentation.

    **SEQUENCE GUIDANCE:** Provide the exact command name. Follow up with
    `command_docs_man_lookup` for complete flag reference. For curated recipes,
    use `command_docs_cheat_sh_lookup`.

    **CONSTRAINT WARNING:** Not all commands have TLDR pages — returns a clear
    message if none exists. Community-contributed; may miss edge cases.

    **OUTPUT EXPECTATION:** Returns simplified cheat sheet with concrete
    command invocations grouped by use case.
    """
    output = _tldr_page(params.command)
    if "No documentation" in output or "not found" in output.lower():
        return f"No TLDR page found for '{params.command}'. Try command_docs_man_lookup instead."
    return _format_response(output, f"TLDR: {params.command}", params.response_format)


@mcp.tool(
    name="command_docs_cheat_sh_lookup",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
def command_docs_cheat_sh_lookup(params: CheatShLookupInput) -> str:
    """Fetch curated command cheat sheets from cheat.sh.

    **TRIGGER CONDITION:** Use when you need community-curated command examples,
    recipes, or language-specific snippets. Especially useful for real-world
    one-liners not covered by official docs.

    **SEQUENCE GUIDANCE:** Use `command` for the tool/language name.
    Add `query` to narrow to specific use cases (e.g., command="git",
    query="rebase interactive").

    **CONSTRAINT WARNING:** Requires internet connectivity. Responses may be
    large for generic queries — use `query` to narrow scope.

    **OUTPUT EXPECTATION:** Returns formatted cheat sheet with commented
    examples. More opinionated and practical than man pages.
    """
    path = params.command
    if params.query.strip():
        safe_query = re.sub(r"[^\w\s-]", "", params.query).strip().replace(" ", "+")
        if safe_query:
            path = f"{params.command}/{safe_query}"

    text = _cheat_sh_fetch(path, params.style)
    if text == "__TIMEOUT__":
        return "Error: cheat.sh request timed out after 15 seconds."
    if text.startswith("__ERROR__:"):
        return f"Error fetching from cheat.sh: {text[9:]}"
    if not text or "Unknown topic" in text:
        return f"No cheat.sh entry found for '{path}'."
    if len(text) > 50000:
        text = text[:50000] + "\n\n... [Response truncated at 50k chars]"
    return _format_response(text, f"cheat.sh: {path}", params.response_format)


if __name__ == "__main__":
    mcp.run(transport="stdio")
