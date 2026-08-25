"""Browser abstraction — dual backend (nodriver managed / raw CDP session).

Two modes:
- managed: nodriver starts/stops its own Chromium (used by tests)
- session: Chrome subprocess on fixed port, raw CDP over websockets (shared CLI)
  Uses Target.attachToTarget/detachFromTarget for multi-invocation safety.
  Each CLI invocation attaches, works, detaches — the page lives on in Chrome.
"""

import asyncio
import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import nodriver as uc
import websockets

SESSION_DIR = Path("/tmp/python-browse")
DEFAULT_CDP_PORT = 9224
CHROME_BINARY = "/usr/bin/google-chrome-stable"


# ═══════════════════════════════════════════════════════════════════
# Session file helpers
# ═══════════════════════════════════════════════════════════════════


def _ensure_session_dir() -> None:
    SESSION_DIR.mkdir(mode=0o700, exist_ok=True)


def _session_file() -> Path:
    return SESSION_DIR / "session.json"


def _read_session() -> dict | None:
    sf = _session_file()
    if not sf.exists():
        return None
    try:
        data = json.loads(sf.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    pid = data.get("pid", 0)
    if pid and not _process_alive(pid):
        return None
    return data


def _write_session(data: dict) -> None:
    _ensure_session_dir()
    _session_file().write_text(json.dumps(data))


def _clear_session() -> None:
    sf = _session_file()
    if sf.exists():
        sf.unlink(missing_ok=True)


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _chrome_ready(port: int) -> bool:
    import urllib.request

    try:
        req = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version", timeout=2
        )
        req.close()
        return True
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════
# Accessibility tree node (session-mode parsed form)
# ═══════════════════════════════════════════════════════════════════


@dataclass
class _AXNode:
    """Parsed accessibility node — same shape as nodriver CDP objects."""

    ignored: bool
    role_value: str
    name_value: str
    backend_node_id: int | None = None
    frame_id: str = ""  # "" = main frame, "@I-N" = OOPIF iframe


# ═══════════════════════════════════════════════════════════════════
# Raw CDP client (session mode)
# ═══════════════════════════════════════════════════════════════════


class _CDPClient:
    """Thin CDP JSON-RPC client over websockets.

    Connects to browser-level WebSocket, attaches to page target via
    Target.attachToTarget (creates new target if needed), and routes
    all commands through a session. On close(), detaches the session
    without killing the page — safe for multi-invocation CLI use.
    """

    def __init__(
        self, port: int = DEFAULT_CDP_PORT, target_id: str | None = None
    ):
        self._port = port
        self._target_id = target_id
        self._ws: "websockets.WebSocketClientProtocol | None" = None
        self._session_id: str | None = None
        self._msg_id = 0
        self._url = ""
        self._title = ""

    @property
    def url(self) -> str:
        return self._url

    @property
    def title(self) -> str:
        return self._title

    @property
    def target_id(self) -> str | None:
        return self._target_id

    async def connect(self) -> None:
        """Connect to browser-level WS and attach to a page target.

        If target_id is given, attaches to that existing page.
        Otherwise creates a fresh target then attaches.
        """
        import urllib.request

        # ── 1. Get browser-level WebSocket URL ──
        resp = urllib.request.urlopen(
            f"http://127.0.0.1:{self._port}/json/version", timeout=5
        )
        data = json.loads(resp.read())
        ws_url = data["webSocketDebuggerUrl"]
        self._ws = await websockets.connect(ws_url, max_size=2**26)

        # ── 2. Create target if needed ──
        if not self._target_id:
            result = await self._send_raw(
                "Target.createTarget", {"url": "about:blank"}
            )
            self._target_id = result["targetId"]

        # ── 3. Attach a session to the target ──
        attach_result = await self._send_raw(
            "Target.attachToTarget",
            {"targetId": self._target_id, "flatten": True},
        )
        self._session_id = attach_result["sessionId"]

        # ── 4. Enable domains on the session ──
        await self._send("Page.enable")
        await self._send("Runtime.enable")
        await self._send("Accessibility.enable")
        await self._send("DOM.enable")

        # ── 5. Sync current page state ──
        self._url = await self._evaluate_expression("document.location.href")
        title = await self._evaluate_expression("document.title")
        self._title = title or ""

    async def _send_raw(
        self, method: str, params: dict | None = None
    ) -> dict:
        """Send a browser-level CDP command (no session)."""
        self._msg_id += 1
        msg: dict = {"id": self._msg_id, "method": method}
        if params:
            msg["params"] = params
        await self._ws.send(json.dumps(msg))

        while True:
            raw = await self._ws.recv()
            response = json.loads(raw)
            # In flatten mode, nested session messages have sessionId.
            # We only care about direct responses to our msg_id.
            rid = response.get("id")
            if rid == self._msg_id:
                if "error" in response:
                    raise RuntimeError(
                        f"CDP error ({method}): {response['error']}"
                    )
                return response.get("result", {})

    async def _send(
        self, method: str, params: dict | None = None
    ) -> dict:
        """Send a CDP command scoped to the attached session."""
        self._msg_id += 1
        msg: dict = {
            "id": self._msg_id,
            "method": method,
            "sessionId": self._session_id,
        }
        if params:
            msg["params"] = params
        await self._ws.send(json.dumps(msg))

        while True:
            raw = await self._ws.recv()
            response = json.loads(raw)
            rid = response.get("id")
            sid = response.get("sessionId")
            if rid == self._msg_id and sid == self._session_id:
                if "error" in response:
                    raise RuntimeError(
                        f"CDP error ({method}): {response['error']}"
                    )
                return response.get("result", {})

    async def navigate(self, url: str) -> None:
        """Navigate to a URL and wait for page load."""
        result = await self._send("Page.navigate", {"url": url})

        error_text = result.get("errorText")
        if error_text:
            raise RuntimeError(f"Navigation failed: {error_text}")

        # Poll for URL change + readyState — wait up to 20s
        for _ in range(40):
            await asyncio.sleep(0.5)
            try:
                state = await self._evaluate_expression(
                    "document.readyState"
                )
                if state != "complete":
                    continue
                live_url = await self._evaluate_expression(
                    "document.location.href"
                )
                if live_url and live_url != "about:blank":
                    self._url = live_url
                    title = await self._evaluate_expression("document.title")
                    self._title = title or ""
                    return
            except Exception:
                pass

        raise RuntimeError(f"Page did not load within 20 seconds: {url}")

    async def _evaluate_expression(self, expression: str) -> str:
        """Evaluate a JS expression in session context, return value."""
        result = await self._send(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True},
        )
        value = result.get("result", {}).get("value")
        return str(value) if value is not None else ""

    async def get_ax_tree(self) -> list[_AXNode]:
        """Fetch accessibility tree as parsed _AXNode list."""
        result = await self._send("Accessibility.getFullAXTree")
        return self._parse_ax_nodes(result.get("nodes", []))

    @staticmethod
    def _parse_ax_nodes(raw_nodes: list[dict]) -> list[_AXNode]:
        """Parse raw CDP AX nodes into _AXNode list (shared helper)."""
        parsed: list[_AXNode] = []
        for node in raw_nodes:
            role_value = ""
            role = node.get("role", {})
            if isinstance(role, dict):
                role_value = role.get("value", "")
            elif isinstance(role, str):
                role_value = role

            name_value = ""
            name = node.get("name", {})
            if isinstance(name, dict):
                raw_name = name.get("value", "")
                name_value = (
                    raw_name.strip()
                    if isinstance(raw_name, str)
                    else str(raw_name)
                )
            elif isinstance(name, str):
                name_value = name.strip()

            parsed.append(
                _AXNode(
                    ignored=node.get("ignored", False),
                    role_value=role_value,
                    name_value=name_value,
                    backend_node_id=node.get("backendDOMNodeId"),
                )
            )
        return parsed

    async def get_oopif_frames(self) -> list[dict]:
        """Discover cross-origin iframe targets via Target.getTargets.

        Returns a list of dicts with 'target_id', 'url', and 'name' for
        each OOPIF (out-of-process iframe) target. These are NOT in
        Page.getFrameTree — they're separate browser targets.
        """
        result = await self._send_raw("Target.getTargets")
        frames: list[dict] = []
        for t in result.get("targetInfos", []):
            if t.get("type") != "iframe":
                continue
            url = t.get("url", "")
            # Skip empty/about:blank — those are same-origin placeholder frames
            if not url or url == "about:blank":
                continue
            frames.append(
                {
                    "target_id": t["targetId"],
                    "url": url,
                    "name": t.get("title", "") or url.split("/")[-1],
                }
            )
        return frames

    async def get_oopif_ax_tree(self, target_id: str) -> list[_AXNode]:
        """Attach to an OOPIF target, fetch its AX tree, detach."""
        # Attach
        attach_result = await self._send_raw(
            "Target.attachToTarget",
            {"targetId": target_id, "flatten": True},
        )
        iframe_sid = attach_result["sessionId"]

        # Enable Accessibility on the iframe session
        self._msg_id += 1
        msg: dict = {
            "id": self._msg_id,
            "method": "Accessibility.enable",
            "sessionId": iframe_sid,
        }
        await self._ws.send(json.dumps(msg))
        while True:
            raw = await self._ws.recv()
            response = json.loads(raw)
            rid = response.get("id")
            rsid = response.get("sessionId")
            if rid == self._msg_id and rsid == iframe_sid:
                break

        # Fetch AX tree
        self._msg_id += 1
        msg = {
            "id": self._msg_id,
            "method": "Accessibility.getFullAXTree",
            "sessionId": iframe_sid,
        }
        await self._ws.send(json.dumps(msg))
        result = {}
        while True:
            raw = await self._ws.recv()
            response = json.loads(raw)
            rid = response.get("id")
            rsid = response.get("sessionId")
            if rid == self._msg_id and rsid == iframe_sid:
                result = response.get("result", {})
                break

        # Detach
        try:
            await self._send_raw(
                "Target.detachFromTarget", {"sessionId": iframe_sid}
            )
        except Exception:
            pass

        return self._parse_ax_nodes(result.get("nodes", []))

    async def get_full_ax_tree(self) -> list[_AXNode]:
        """Fetch main frame AX tree + all OOPIF iframe AX trees merged.

        Main frame nodes get empty frame_id. OOPIF nodes get frame_id
        like '@I-0', '@I-1' etc., referencing the `oopif_frames` list
        returned alongside. The caller should pair this with
        `get_oopif_frames()` for human-readable iframe metadata.
        """
        nodes = await self.get_ax_tree()

        # Discover and merge OOPIF iframes
        oopif_meta: list[dict] = []
        try:
            frames = await self.get_oopif_frames()
        except Exception:
            frames = []

        for idx, frame in enumerate(frames):
            try:
                iframe_nodes = await self.get_oopif_ax_tree(
                    frame["target_id"]
                )
            except Exception:
                continue

            frame_label = f"@I-{idx}"
            oopif_meta.append(
                {
                    "ref": frame_label,
                    "target_id": frame["target_id"],
                    "url": frame["url"][:200],
                    "name": frame["name"],
                    "node_count": len(iframe_nodes),
                }
            )

            for node in iframe_nodes:
                if node.ignored:
                    continue
                node.frame_id = frame_label
                nodes.append(node)

        # Attach metadata to the first node (hack: use RootWebArea name)
        if nodes and oopif_meta:
            # Store oopif metadata in a way the snapshot layer can access
            nodes[0].name_value += (
                "\n[OOPIF frames: "
                + ", ".join(
                    f"{m['ref']}={m['url']}" for m in oopif_meta
                )
                + "]"
            )

        return nodes

    async def resolve_node(self, backend_node_id: int) -> str:
        """Resolve a backend node ID to a runtime object ID."""
        result = await self._send(
            "DOM.resolveNode", {"backendNodeId": backend_node_id}
        )
        return result["object"]["objectId"]

    async def call_function_on(
        self, object_id: str, function_declaration: str
    ) -> None:
        """Execute a function on a remote object."""
        await self._send(
            "Runtime.callFunctionOn",
            {
                "objectId": object_id,
                "functionDeclaration": function_declaration,
            },
        )

    async def get_content(self) -> str:
        """Return page body text."""
        return await self._evaluate_expression("document.body.innerText")

    async def get_html(self) -> str:
        """Return full page HTML."""
        return await self._evaluate_expression(
            "document.documentElement.outerHTML"
        )

    async def get_page_url(self) -> str:
        """Return live page URL."""
        url = await self._evaluate_expression("document.location.href")
        return url or self._url

    async def close(self) -> None:
        """Detach session and close WebSocket (page stays alive)."""
        if self._session_id and self._ws:
            try:
                await self._send_raw(
                    "Target.detachFromTarget",
                    {"sessionId": self._session_id},
                )
            except Exception:
                pass
            self._session_id = None

        if self._ws is not None:
            await self._ws.close()
            self._ws = None


# ═══════════════════════════════════════════════════════════════════
# Browser — dual-mode facade
# ═══════════════════════════════════════════════════════════════════


class Browser:
    """Manages a browser session via nodriver/CDP.

    Two modes:
    - managed: nodriver handles everything (used by tests)
    - session: raw CDP client over Chrome subprocess (used by CLI)
      Each CLI invocation attaches/detaches — page persists in Chrome.
    """

    def __init__(self, mode: str = "managed"):
        self._browser: "uc.Browser | None" = None
        self._tab: "uc.Tab | None" = None
        self._cdp: "_CDPClient | None" = None
        self._snapshot_cache: dict[str, _AXNode | object] = {}
        self._mode = mode
        self._chrome_proc: "subprocess.Popen | None" = None

    # ── session management ─────────────────────────────────────────

    def is_running(self) -> bool:
        if self._mode == "session":
            return _read_session() is not None
        return self._browser is not None and self._tab is not None

    async def start(self) -> dict:
        if self._mode == "session":
            if self.is_running():
                return {
                    "status": "already_running",
                    "session": _read_session(),
                }

            # Guard: kill stale Chrome on our port (no session file = orphaned)
            if _chrome_ready(DEFAULT_CDP_PORT):
                import subprocess as sp

                sp.run(
                    ["fuser", "-k", f"{DEFAULT_CDP_PORT}/tcp"],
                    capture_output=True,
                    timeout=5,
                )
                time.sleep(1)
                if _chrome_ready(DEFAULT_CDP_PORT):
                    raise RuntimeError(
                        f"Port {DEFAULT_CDP_PORT} is occupied by a foreign "
                        f"Chrome instance that could not be killed."
                    )

            # Launch Chrome subprocess
            self._chrome_proc = subprocess.Popen(
                [
                    CHROME_BINARY,
                    "--headless=new",
                    f"--remote-debugging-port={DEFAULT_CDP_PORT}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    f"--user-data-dir={SESSION_DIR / 'chrome-profile'}",
                    "--disable-gpu",
                    "--disable-dev-shm-usage",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            # Wait for Chrome to be ready
            deadline = time.time() + 10
            while time.time() < deadline:
                time.sleep(0.5)
                if _chrome_ready(DEFAULT_CDP_PORT):
                    break
            else:
                self._chrome_proc.kill()
                self._chrome_proc = None
                raise RuntimeError(
                    "Chrome did not start within 10 seconds"
                )

            # Create CDP client (creates page target + attaches)
            self._cdp = _CDPClient(DEFAULT_CDP_PORT)
            await self._cdp.connect()

            _ensure_session_dir()
            _write_session(
                {
                    "pid": self._chrome_proc.pid,
                    "port": DEFAULT_CDP_PORT,
                    "target_id": self._cdp.target_id,
                    "started_at": time.time(),
                }
            )

            return {"status": "started", "session": _read_session()}

        # managed mode — nodriver handles it
        self._browser = await uc.start(headless=True)
        return {"status": "started"}

    async def disconnect(self) -> dict:
        """Detach CDP session without killing Chrome (session mode).

        In managed mode this is a no-op. Use after each session-mode
        CLI invocation so Chrome keeps the page alive between calls.
        """
        if self._cdp is not None:
            await self._cdp.close()
            self._cdp = None
        self._snapshot_cache = {}
        return {"disconnected": True}

    async def stop(self) -> dict:
        # Session mode: detach + close CDP connection
        if self._cdp is not None:
            await self._cdp.close()
            self._cdp = None

        # Managed mode cleanup
        if self._browser is not None:
            self._browser.stop()
            self._browser = None
            self._tab = None

        self._snapshot_cache = {}

        if self._chrome_proc is not None:
            try:
                self._chrome_proc.terminate()
                self._chrome_proc.wait(timeout=5)
            except Exception:
                self._chrome_proc.kill()
            self._chrome_proc = None

        _clear_session()
        return {"stopped": True}

    async def _ensure_connected(self) -> None:
        if self._mode == "session":
            if self._cdp is not None:
                return
            ses = _read_session()
            if ses and ses.get("target_id"):
                self._cdp = _CDPClient(
                    DEFAULT_CDP_PORT, target_id=ses["target_id"]
                )
                await self._cdp.connect()
            else:
                await self.start()
        else:
            if self._browser is not None:
                return
            await self.start()

    # ── navigation ──────────────────────────────────────────────────

    async def open(self, url: str) -> dict:
        await self._ensure_connected()
        self._snapshot_cache = {}

        if self._mode == "session":
            await self._cdp.navigate(url)
            return {"url": url, "title": self._cdp.title}

        self._tab = await self._browser.get(url)
        return {"url": url, "title": self._tab.title}

    # ── snapshot ────────────────────────────────────────────────────

    # Roles that always survive compact snapshots.
    _INTERACTIVE_ROLES = frozenset({
        "link", "button", "textbox", "checkbox", "radio",
        "combobox", "listbox", "tab", "search", "spinbutton",
        "InputTime",
    })
    _NAMED_CONTENT_ROLES = frozenset({
        "heading", "StaticText", "Iframe", "alertdialog", "alert",
    })
    _COMPACT_KEEP = _INTERACTIVE_ROLES | _NAMED_CONTENT_ROLES | {"RootWebArea"}

    async def snapshot(self, full: bool = False, frame: str | None = None) -> dict:
        await self._ensure_connected()
        full_snapshot: list[dict] = []
        cache: dict[str, _AXNode | object] = {}
        ref_index = 0
        oopif_frames: list[dict] = []

        if self._mode == "session":
            nodes = await self._cdp.get_full_ax_tree()
            url = self._cdp.url

            # Collect OOPIF metadata from RootWebArea annotations
            # (get_full_ax_tree appends frame metadata to nodes[0].name)
            raw_name = nodes[0].name_value if nodes else ""
            if "[OOPIF frames:" in raw_name:
                try:
                    import ast
                    meta_start = raw_name.index("[OOPIF frames:") + len("[OOPIF frames: ")
                    meta_end = raw_name.index("]", meta_start)
                    meta_str = raw_name[meta_start:meta_end]
                    # Parse: "@I-0=https://..., @I-1=https://..."
                    for chunk in meta_str.split(", "):
                        if "=" in chunk:
                            ref, frame_url = chunk.split("=", 1)
                            oopif_frames.append({"ref": ref, "url": frame_url})
                    # Strip metadata from name
                    clean_name = raw_name[: raw_name.index("\n[OOPIF frames:")]
                    nodes[0].name_value = clean_name
                except (ValueError, IndexError, SyntaxError):
                    pass

            # Collect main-frame and OOPIF nodes separately
            main_nodes: list[dict] = []
            oopif_nodes: list[dict] = []
            for node in nodes:
                if node.ignored:
                    continue
                if node.frame_id:
                    ref = f"{node.frame_id}-{ref_index}"
                else:
                    ref = f"@0-{ref_index}"
                item = {"ref": ref, "role": node.role_value, "name": node.name_value}
                
                if node.frame_id:
                    oopif_nodes.append(item)
                else:
                    main_nodes.append(item)
                cache[ref] = node
                ref_index += 1

            # OOPIF first — data-rich frames survive truncation
            full_snapshot = oopif_nodes + main_nodes

        else:
            if self._tab is None:
                raise RuntimeError("No page open. Call open() first.")
            await self._tab.send(uc.cdp.accessibility.enable())
            nodes = await self._tab.send(
                uc.cdp.accessibility.get_full_ax_tree()
            )
            url = self._tab.url

            for node in nodes:
                if node.ignored:
                    continue
                role_value = (
                    node.role.value
                    if hasattr(node.role, "value")
                    else str(node.role)
                )
                name_value = (
                    node.name.value.strip()
                    if hasattr(node.name, "value")
                    and isinstance(node.name.value, str)
                    else str(node.name).strip()
                )
                ref = f"@0-{ref_index}"
                full_snapshot.append(
                    {"ref": ref, "role": role_value, "name": name_value or ""}
                )
                cache[ref] = node
                ref_index += 1

        self._snapshot_cache = cache

        # Frame filter: if `frame` is set, keep only nodes from that frame
        # (plus @0 RootWebArea nodes for metadata context)
        if frame is not None:
            frame_prefix = frame if frame.endswith("-") else frame + "-"
            full_snapshot = [
                item
                for item in full_snapshot
                if item["ref"].startswith(frame_prefix)
                or item["ref"].startswith("@0-")
            ]

        # Compact: filter to interactive + named content only
        if not full:
            compact: list[dict] = []
            for item in full_snapshot:
                role = item["role"]
                name = item["name"].strip() if item["name"] else ""
                if role in self._COMPACT_KEEP:
                    if role == "StaticText" and not name:
                        continue
                    if role == "Iframe" and not name:
                        continue
                    compact.append(item)
                # Always keep RootWebArea nodes (they carry frame metadata)
                elif role == "RootWebArea":
                    compact.append(item)
            result: dict = {"url": url, "snapshot": compact}
            if oopif_frames:
                result["oopif_frames"] = oopif_frames
            return result

        result = {"url": url, "snapshot": full_snapshot}
        if oopif_frames:
            result["oopif_frames"] = oopif_frames
        return result

    # ── interaction ─────────────────────────────────────────────────

    async def click(self, ref: str) -> dict:
        await self._ensure_connected()
        if self._mode == "managed" and self._tab is None:
            raise RuntimeError("No page open. Call open() first.")
        if not self._snapshot_cache:
            raise RuntimeError("No snapshot. Call snapshot() first.")
        if ref not in self._snapshot_cache:
            raise ValueError(f"Ref {ref} not found in current snapshot.")

        node = self._snapshot_cache[ref]

        if self._mode == "session":
            assert isinstance(node, _AXNode)
            if node.backend_node_id is None:
                raise ValueError(f"Ref {ref} has no backend DOM node ID.")
            object_id = await self._cdp.resolve_node(node.backend_node_id)
            await self._cdp.call_function_on(
                object_id, "function() { this.click(); }"
            )
            await asyncio.sleep(0.5)
        else:
            backend_node_id = getattr(node, "backend_dom_node_id", None)
            if backend_node_id is None:
                raise ValueError(f"Ref {ref} has no backend DOM node ID.")
            resolved = await self._tab.send(
                uc.cdp.dom.resolve_node(backend_node_id=backend_node_id)
            )
            await self._tab.send(
                uc.cdp.runtime.call_function_on(
                    function_declaration="function() { this.click(); }",
                    object_id=resolved.object_id,
                )
            )
            await self._tab.sleep(0.5)

        self._snapshot_cache = {}
        return {"clicked": True, "ref": ref}

    async def fill(self, ref: str, value: str) -> dict:
        await self._ensure_connected()
        if self._mode == "managed" and self._tab is None:
            raise RuntimeError("No page open. Call open() first.")
        if not self._snapshot_cache:
            raise RuntimeError("No snapshot. Call snapshot() first.")
        if ref not in self._snapshot_cache:
            raise ValueError(f"Ref {ref} not found in current snapshot.")

        node = self._snapshot_cache[ref]

        if self._mode == "session":
            assert isinstance(node, _AXNode)
            if node.backend_node_id is None:
                raise ValueError(f"Ref {ref} has no backend DOM node ID.")
            object_id = await self._cdp.resolve_node(node.backend_node_id)
            await self._cdp.call_function_on(
                object_id,
                (
                    f"function() {{ this.focus(); this.value = {value!r}; "
                    "this.dispatchEvent(new Event('input', {bubbles: true})); "
                    "this.dispatchEvent(new Event('change', {bubbles: true})); }"
                ),
            )
        else:
            backend_node_id = getattr(node, "backend_dom_node_id", None)
            if backend_node_id is None:
                raise ValueError(f"Ref {ref} has no backend DOM node ID.")
            resolved = await self._tab.send(
                uc.cdp.dom.resolve_node(backend_node_id=backend_node_id)
            )
            await self._tab.send(
                uc.cdp.runtime.call_function_on(
                    function_declaration=(
                        f"function() {{ this.focus(); this.value = {value!r}; "
                        "this.dispatchEvent(new Event('input', {bubbles: true})); "
                        "this.dispatchEvent(new Event('change', {bubbles: true})); }"
                    ),
                    object_id=resolved.object_id,
                )
            )

        return {"filled": True, "ref": ref, "value": value}

    # ── reading ─────────────────────────────────────────────────────

    async def get(self, what: str) -> dict:
        await self._ensure_connected()

        if self._mode == "session":
            if what == "url":
                url = await self._cdp.get_page_url()
                return {"url": url}
            if what == "title":
                return {"title": self._cdp.title}
            if what == "text":
                return {"text": await self._cdp.get_content()}
            if what == "html":
                return {"html": await self._cdp.get_html()}
            raise ValueError(f"Unknown get target: {what}")

        if self._tab is None:
            raise RuntimeError("No page open. Call open() first.")
        if what == "url":
            return {"url": self._tab.url}
        if what == "title":
            return {"title": self._tab.title}
        content = await self._tab.get_content()
        if what == "text":
            return {"text": content}
        if what == "html":
            return {"html": content}
        raise ValueError(f"Unknown get target: {what}")
