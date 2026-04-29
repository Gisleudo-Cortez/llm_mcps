#!/usr/bin/env python3
"""
Foreground launcher for all MCP servers in streamable-HTTP mode.
Used by the mcp-http systemd user service.
Ctrl-C or SIGTERM stops all children cleanly.
"""
import os
import signal
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))

SERVERS = [
    ("page_scrape",       8200),
    ("rag_tools",         8201),
    ("current_date_time", 8202),
    ("arch_system_tools", 8203),
    ("local_searxng",     8204),
    ("python_repl",       8205),
    ("data_query",        8206),
    ("memory_notes",      8207),
    ("command_docs",      8208),
    ("awesome_lists",     8209),
    ("llm_tools",         8210),
    ("code_check",        8211),
]

procs: list[subprocess.Popen] = []


def shutdown(sig=None, frame=None):
    for p in procs:
        p.terminate()
    for p in procs:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
    sys.exit(0)


signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)

for name, port in SERVERS:
    env = os.environ.copy()
    env["FASTMCP_PORT"] = str(port)
    p = subprocess.Popen(
        [
            f"{BASE}/{name}/.venv/bin/python",
            "-c",
            f"import main; main.mcp.settings.port = {port}; main.mcp.run(transport='streamable-http')",
        ],
        cwd=f"{BASE}/{name}",
        env=env,
    )
    procs.append(p)
    print(f"started {name} port={port} pid={p.pid}", flush=True)

print(f"all {len(SERVERS)} MCP servers running", flush=True)

while True:
    for p in procs:
        if p.poll() is not None:
            print(f"pid {p.pid} exited ({p.returncode}), shutting down", flush=True)
            shutdown()
    time.sleep(5)
