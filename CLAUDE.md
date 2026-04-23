# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This repository is a collection of independent MCP (Model Context Protocol) servers built with [FastMCP](https://github.com/jlowin/fastmcp). Each subdirectory is a self-contained Python project with its own virtual environment managed by `uv`.

## Project Structure

Each server lives in its own directory with an isolated `uv` environment:

| Directory | Server Name | Purpose |
|-----------|-------------|---------|
| `page_scrape/` | Page Scrape Server | Fetch and parse web pages (Trafilatura + BeautifulSoup) |
| `rag_tools/` | RAG Document Tools | Document reading, indexing, and semantic search (ChromaDB + SentenceTransformers) |
| `current_date_time/` | System Utilities Server | Date/time tools with timezone support |
| `arch_system_tools/` | Arch System Tools | Arch Linux system utilities (fs, packages, git, services, network) |
| `local_searxng/` | SearXNG Search Server | Web search via a local SearXNG instance at `localhost:8080` |

## Commands

All commands must be run from inside the specific server's directory.

**Install dependencies:**
```sh
cd <server_dir>
uv sync
```

**Run a server (stdio transport for MCP):**
```sh
cd <server_dir>
uv run python main.py
```

**Test page_scrape manually (the only server with a test script):**
```sh
cd page_scrape
uv run python test_scrape.py
```

**Lint (ruff is configured at the repo root):**
```sh
ruff check .
ruff format .
```

## Architecture

### MCP Server Pattern

Every server follows the same pattern:
1. Instantiate `FastMCP("Server Name")` at module level.
2. Decorate tool functions with `@mcp.tool()`.
3. Run via `mcp.run(transport="stdio")` in `if __name__ == "__main__"`.

All tools return plain strings (or FastMCP `Image` objects for vision). Tool docstrings follow a structured format with `**TRIGGER CONDITION**`, `**SEQUENCE GUIDANCE**`, `**CONSTRAINT WARNING**`, and `**OUTPUT EXPECTATION**` sections — this is intentional as it guides AI agents on when and how to call each tool.

### RAG Tools (`rag_tools/`)

The most complex server. Key design decisions:
- **ChromaDB** is persisted to `./chroma_db` (relative to the server directory).
- The **SentenceTransformer** model (`all-MiniLM-L6-v2`) is lazy-loaded to avoid blocking during MCP handshake.
- Chunking strategy: sentence-boundary splitting with ~800-char target chunks and 100-char overlap.
- Intended tool call order: `get_doc_metadata` → `read_doc_content` → `chunk_and_preview` → `index_document_for_search` → `semantic_search`.

### Arch System Tools (`arch_system_tools/`)

All subprocess calls go through the `run_command()` helper which:
- Uses `subprocess.run` without `shell=True` (injection-safe).
- Enforces a 30-second timeout.
- Truncates output at 50,000 characters.
- Returns stderr on non-zero exit codes.

Input sanitization is applied at every tool boundary before constructing command lists.

### Local SearXNG (`local_searxng/`)

Requires a SearXNG instance running at `http://localhost:8080`. The server will fail requests gracefully if the instance is down.

## Dependencies

- Python ≥ 3.13 (all servers)
- `uv` for environment/dependency management
- `ruff` for linting/formatting (cache in `.ruff_cache/`)
- External tools required by `arch_system_tools`: `eza`, `ugrep`, `qalc`, `fastfetch`, `tldr`, Docker, systemd
