"""python-browse CLI — terminal browser automation for AI agents.

Session mode (default): Chrome subprocess + raw CDP, shared across invocations.
Managed mode (--managed): nodriver spawns/tears down its own Chromium each time.

Every session-mode command detaches the CDP session on completion so the page
persists in Chrome for the next invocation. Only 'stop' kills the browser.

Usage:
    python-browse start
    python-browse open <url>
    python-browse snapshot
    python-browse click <ref|text>
    python-browse fill <ref> <value>
    python-browse get <text|html|url|title>
    python-browse stop
    python-browse status
"""

import argparse
import asyncio
import json
import sys

from python_browse.browser import Browser, _read_session


def _output(data: dict) -> None:
    print(json.dumps(data))


def _error(msg: str, exit_code: int = 1) -> None:
    print(json.dumps({"error": msg}), file=sys.stderr)
    sys.exit(exit_code)


async def _main() -> None:
    parser = argparse.ArgumentParser(
        prog="python-browse",
        description="Terminal browser automation for AI agents",
    )
    parser.add_argument(
        "--managed",
        action="store_true",
        help="Use managed mode (nodriver, one browser per invocation)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("start", help="Launch browser session")

    open_cmd = sub.add_parser("open", help="Navigate to a URL")
    open_cmd.add_argument("url", help="URL to navigate to")

    sub.add_parser("snapshot", help="Get accessibility tree")

    click_cmd = sub.add_parser(
        "click", help="Click an element by ref, selector, or text"
    )
    click_cmd.add_argument(
        "target", help="Element ref (@0-5), CSS selector, or text"
    )

    fill_cmd = sub.add_parser("fill", help="Fill an input element")
    fill_cmd.add_argument("ref", help="Element ref from snapshot")
    fill_cmd.add_argument("value", help="Value to fill")

    get_cmd = sub.add_parser("get", help="Read page data")
    get_cmd.add_argument(
        "what", choices=["text", "html", "url", "title"], help="What to get"
    )

    sub.add_parser("stop", help="Stop the browser")
    sub.add_parser("status", help="Show browser session status")

    args = parser.parse_args()
    mode = "managed" if args.managed else "session"
    browser = Browser(mode=mode)

    try:
        if args.command == "start":
            result = await browser.start()
            _output(result)
            # Detach from the session — Chrome keeps the page alive.
            # Next CLI invocation will reattach via target_id in session file.
            await browser.disconnect()

        elif args.command == "status":
            _output(
                {
                    "browser_connected": browser.is_running(),
                    "session": _read_session() if browser.is_running() else None,
                }
            )

        elif args.command == "stop":
            result = await browser.stop()
            _output(result)

        else:
            # Work commands: open / snapshot / click / fill / get
            # Auto-connect if not already connected
            if args.command == "open":
                result = await browser.open(args.url)
                _output(result)

            elif args.command == "snapshot":
                result = await browser.snapshot()
                _output(result)

            elif args.command == "click":
                target = args.target
                if target.startswith("@"):
                    result = await browser.click(target)
                else:
                    snap = await browser.snapshot()
                    matches = [
                        item
                        for item in snap["snapshot"]
                        if target.lower() in item["name"].lower()
                    ]
                    if not matches:
                        _error(f"No element found matching '{target}'")
                    result = await browser.click(matches[0]["ref"])
                _output(result)

            elif args.command == "fill":
                result = await browser.fill(args.ref, args.value)
                _output(result)

            elif args.command == "get":
                result = await browser.get(args.what)
                _output(result)

            # Detach — Chrome keeps the page alive for the next invocation
            await browser.disconnect()

    except Exception as exc:
        # Clean detach even on error so Chrome doesn't get wedged
        try:
            await browser.disconnect()
        except Exception:
            pass
        _error(str(exc))


def main() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    main()
