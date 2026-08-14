"""MCP server for python-browse — terminal browser automation.

Eight tools mapping directly to python-browse primitives. One shared
Browser(mode="session") instance — Chrome subprocess lives as long as
the MCP server runs. Tools are serialized by FastMCP, so no lock needed.

Registered in Hermes as `mcp_python_browse_*` tools.
"""

import json
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from python_browse.browser import Browser

# ═══════════════════════════════════════════════════════════════════

mcp = FastMCP("python_browse_mcp")
_browser: Browser | None = None


async def _get_browser() -> Browser:
    """Lazy-init the shared Browser instance."""
    global _browser
    if _browser is None:
        _browser = Browser(mode="session")
        # Don't auto-start — agent must call browse_start first.
        # If already running (resumed from prior session), reconnect.
    return _browser


def _ok(data: dict) -> str:
    return json.dumps(data)


def _err(msg: str) -> str:
    return json.dumps({"error": msg})


# ═══════════════════════════════════════════════════════════════════
# Input models
# ═══════════════════════════════════════════════════════════════════


class BrowseOpenInput(BaseModel):
    """Navigate the browser to a URL."""

    url: str = Field(
        ...,
        description="Full URL to navigate to (e.g., 'https://httpbin.org/ip')",
        min_length=5,
    )


class BrowseClickInput(BaseModel):
    """Click an element by accessibility ref or text match."""

    target: str = Field(
        ...,
        description="Element ref from snapshot (e.g., '@0-12') or text substring to match",
        min_length=1,
    )


class BrowseFillInput(BaseModel):
    """Fill an input field identified by snapshot ref."""

    ref: str = Field(
        ...,
        description="Element ref from snapshot (e.g., '@0-26')",
        min_length=1,
    )
    value: str = Field(
        ...,
        description="Value to fill into the input field",
    )


class BrowseGetInput(BaseModel):
    """Read page data: text, html, url, or title."""

    what: Literal["text", "html", "url", "title"] = Field(
        ...,
        description="What to read from the current page",
    )


# ═══════════════════════════════════════════════════════════════════
# Tools
# ═══════════════════════════════════════════════════════════════════


@mcp.tool(
    name="browse_start",
    annotations={
        "title": "Start browser session",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def browse_start() -> str:
    """Launch a headless Chrome session on port 9224.

    **TRIGGER CONDITION:** Use at the start of any browsing task, or when
    the previous session was stopped. Call ONCE — subsequent tools reuse
    the same Chrome instance.

    **SEQUENCE GUIDANCE:** Call FIRST. Then `browse_open` → `browse_snapshot`
    → `browse_click`/`browse_fill` → `browse_get`. End with `browse_stop`.

    **CONSTRAINT WARNING:** Only one Chrome instance can run on port 9224.
    If port is occupied, stale Chrome is auto-killed. Wait 1-2s after
    calling — Chrome cold-starts.

    **OUTPUT EXPECTATION:** JSON: `{"status": "started", "session": {...}}`
    or `{"status": "already_running", "session": {...}}` if already up.

    **ERROR RECOVERY:** If "Port 9224 is occupied", manually kill Chrome
    on that port (`fuser -k 9224/tcp`) and retry.
    """
    try:
        b = await _get_browser()
        result = await b.start()
        return _ok(result)
    except Exception as exc:
        return _err(str(exc))


@mcp.tool(
    name="browse_stop",
    annotations={
        "title": "Stop browser session",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def browse_stop() -> str:
    """Stop the browser and terminate the Chrome subprocess.

    **TRIGGER CONDITION:** Use when done with all browsing tasks. Kills
    Chrome and clears session state.

    **SEQUENCE GUIDANCE:** Call LAST. After this, `browse_start` is needed
    to start a fresh session.

    **CONSTRAINT WARNING:** Destroys the current page. Any unsaved state
    (form fills, navigated URLs) is lost.

    **OUTPUT EXPECTATION:** `{"stopped": true}`
    """
    try:
        b = await _get_browser()
        result = await b.stop()
        return _ok(result)
    except Exception as exc:
        return _err(str(exc))


@mcp.tool(
    name="browse_status",
    annotations={
        "title": "Check browser session status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def browse_status() -> str:
    """Check whether the browser session is running.

    **TRIGGER CONDITION:** Use to diagnose connection issues — "is the
    browser running?", "did the session die?".

    **SEQUENCE GUIDANCE:** Diagnostic only. Does NOT start a session.

    **OUTPUT EXPECTATION:** `{"browser_connected": true}` or `false`.
    """
    try:
        b = await _get_browser()
        return _ok({"browser_connected": b.is_running()})
    except Exception as exc:
        return _err(str(exc))


@mcp.tool(
    name="browse_open",
    annotations={
        "title": "Navigate browser to a URL",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
async def browse_open(params: BrowseOpenInput) -> str:
    """Navigate the browser to a URL. Auto-starts session if needed.

    **TRIGGER CONDITION:** Use after `browse_start` (or standalone — will
    auto-start the session). Navigate to any http/https URL.

    **SEQUENCE GUIDANCE:** Call after `browse_start`. Follow with
    `browse_snapshot` to read the page structure before interacting.

    **CONSTRAINT WARNING:** Waits up to 20s for page load. JavaScript-heavy
    pages may take longer. Invalid URLs produce CDP-level errors.

    **OUTPUT EXPECTATION:** `{"url": "https://...", "title": "Page Title"}`
    Title may be empty for pages without <title> tags.

    **ERROR RECOVERY:** If navigation times out, the page may still be
    partially loaded — try `browse_snapshot` to check current state.
    """
    try:
        b = await _get_browser()
        result = await b.open(params.url)
        return _ok(result)
    except Exception as exc:
        return _err(str(exc))


@mcp.tool(
    name="browse_snapshot",
    annotations={
        "title": "Get accessibility tree of current page",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def browse_snapshot(
    full: bool = False,
    frame: str | None = None,
) -> str:
    """Return the page's accessibility tree as a flat list with stable refs.

    **TRIGGER CONDITION:** Use after every `browse_open` or `browse_click`
    to discover interactive elements. MUST be called before `browse_click`
    or `browse_fill` — those tools require snapshot refs.

    **SEQUENCE GUIDANCE:** `browse_open` → `browse_snapshot` → read refs →
    `browse_click(ref)` / `browse_fill(ref, value)`.

    **CONSTRAINT WARNING:** Snapshot cache is per-tool-call. You can't
    snapshot in one call and click in another without re-snapshotting.
    The refs are stable only for the current page state.

    **PARAMETERS:**
    - full (bool, default false): If true, return the complete accessibility
      tree including layout containers. Use for debugging or when you need
      to inspect page structure. Default false returns compact mode:
      interactive elements (links, buttons, inputs) + named content
      (headings, visible text) — typically 80-90% fewer nodes.
    - frame (str, optional): Restrict snapshot to a specific OOPIF frame
      identified by its `ref` from `oopif_frames` metadata.
      Example: `frame="@I-21"` returns only the TradingView ticker tape.
      Use this when the default snapshot is too large or you want focused
      data from a specific widget.

    **OUTPUT EXPECTATION:** JSON: `{"url": "...", "snapshot": [{"ref": "@0-0",
    "role": "RootWebArea", "name": ""}, {"ref": "@0-26", "role": "textbox",
    "name": "Customer name:"}, ...]}`. In compact mode, the `oopif_frames`
    key lists cross-origin iframe metadata when present.
    """
    try:
        b = await _get_browser()
        result = await b.snapshot(full=full, frame=frame)
        return _ok(result)
    except Exception as exc:
        return _err(str(exc))


@mcp.tool(
    name="browse_click",
    annotations={
        "title": "Click an element by ref or text match",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def browse_click(params: BrowseClickInput) -> str:
    """Click a page element by snapshot ref or text content.

    **TRIGGER CONDITION:** Use after `browse_snapshot` when you have a
    target ref or know the text of the button/link you want to click.

    **SEQUENCE GUIDANCE:** `browse_snapshot` → identify ref → `browse_click`.
    If the target is text (not a ref starting with '@'), the tool
    auto-snapshots and matches by name substring.

    **CONSTRAINT WARNING:** Requires a preceding snapshot with valid refs.
    Clicking submits forms, follows links, triggers JS — page state changes.
    Call `browse_snapshot` after to get fresh refs.

    **OUTPUT EXPECTATION:** `{"clicked": true, "ref": "@0-12"}`

    **ERROR RECOVERY:** "Ref not found" — call `browse_snapshot` to refresh
    refs. "No snapshot" — call `browse_snapshot` first.
    """
    try:
        b = await _get_browser()
        target = params.target

        if target.startswith("@"):
            result = await b.click(target)
            return _ok(result)

        # Text match: snapshot + find matching element by name
        snap = await b.snapshot()
        matches = [
            item
            for item in snap["snapshot"]
            if target.lower() in item["name"].lower()
        ]
        if not matches:
            return _err(f"No element found matching '{target}'")

        result = await b.click(matches[0]["ref"])
        return _ok(result)
    except Exception as exc:
        return _err(str(exc))


@mcp.tool(
    name="browse_fill",
    annotations={
        "title": "Fill an input field by ref",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
async def browse_fill(params: BrowseFillInput) -> str:
    """Fill a text input field identified by snapshot ref.

    **TRIGGER CONDITION:** Use after `browse_snapshot` when you have the
    ref of a textbox/input element to fill.

    **SEQUENCE GUIDANCE:** `browse_snapshot` → identify textbox ref
    (e.g., `@0-26` with name="Customer name:") → `browse_fill(ref, value)`.
    Chain multiple fills before clicking submit.

    **CONSTRAINT WARNING:** The ref MUST come from a preceding snapshot call.
    Filling triggers 'input' and 'change' events for JS frameworks.

    **OUTPUT EXPECTATION:** `{"filled": true, "ref": "@0-26", "value": "Ada"}`

    **ERROR RECOVERY:** "Ref not found" — call `browse_snapshot` first.
    "No snapshot" — snapshot is required before fill.
    """
    try:
        b = await _get_browser()
        result = await b.fill(params.ref, params.value)
        return _ok(result)
    except Exception as exc:
        return _err(str(exc))


@mcp.tool(
    name="browse_get",
    annotations={
        "title": "Read page content",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def browse_get(params: BrowseGetInput) -> str:
    """Read page data: text, HTML, URL, or title.

    **TRIGGER CONDITION:** Use to extract content after navigation or form
    submission. 'url' is useful to verify the current page after a redirect.
    'text' returns the page's visible text content.

    **SEQUENCE GUIDANCE:** Call after `browse_open`, `browse_click`, or
    `browse_fill`+`browse_click` to read results.

    **CONSTRAINT WARNING:** 'text' returns `document.body.innerText` —
    may include navigation text, footers, etc. 'url' returns live
    `document.location.href` (post-redirect).

    **OUTPUT EXPECTATION:**
    - what="url": `{"url": "https://httpbin.org/post"}`
    - what="title": `{"title": "Page Title"}`
    - what="text": `{"text": "..."}` (full visible text)
    - what="html": `{"html": "<html>..."}` (full page HTML)

    **ERROR RECOVERY:** Empty text usually means the page hasn't loaded.
    Try `browse_open` again.
    """
    try:
        b = await _get_browser()
        result = await b.get(params.what)
        return _ok(result)
    except Exception as exc:
        return _err(str(exc))


# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    mcp.run()
