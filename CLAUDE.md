# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A collection of independent MCP (Model Context Protocol) servers built with [FastMCP](https://github.com/jlowin/fastmcp). Each subdirectory is a self-contained Python project with its own virtual environment managed by `uv`. Servers are registered in `~/.lmstudio/mcp.json`.

## Project Structure

| Directory | Server Name | Purpose |
|-----------|-------------|---------|
| `page_scrape/` | Page Scrape Server | Fetch and parse web pages (Trafilatura + BeautifulSoup) |
| `rag_tools/` | RAG Document Tools | Document reading, indexing, and semantic search (ChromaDB + SentenceTransformers) |
| `current_date_time/` | System Utilities Server | Date/time tools with timezone support |
| `arch_system_tools/` | Arch System Tools | Arch Linux system utilities (fs, packages, git, services, network, Docker) |
| `local_searxng/` | SearXNG Search Server | Web search via a local SearXNG instance |
| `python_repl/` | Python REPL | Execute Python code in an isolated subprocess |
| `data_query/` | Data Query Server | SQLite queries and DuckDB analytics over CSV/Parquet/JSON |
| `memory_notes/` | Memory & Notes | Persistent key-value memory across sessions (JSON file backend) |

## Commands

All commands must be run from inside the specific server's directory.

**Install / sync dependencies:**
```sh
cd <server_dir>
uv sync
```

**Run a server (stdio transport for MCP):**
```sh
cd <server_dir>
uv run python main.py
```

**Test page_scrape manually:**
```sh
cd page_scrape
uv run python test_scrape.py
```

**Lint and format (ruff, configured at repo root):**
```sh
ruff check .
ruff format .
```

**Syntax-check all files at once:**
```sh
python3 -c "import ast; [print('OK', f) or ast.parse(open(f).read()) for f in ['rag_tools/main.py','arch_system_tools/main.py','local_searxng/main.py','python_repl/main.py','data_query/main.py','memory_notes/main.py']]"
```

## Architecture

### MCP Server Pattern

Every server follows the same pattern:
1. Instantiate `FastMCP("Server Name")` at module level.
2. Decorate tool functions with `@mcp.tool()`.
3. Run via `mcp.run(transport="stdio")` in `if __name__ == "__main__"`.

All tools return plain strings. Tool docstrings follow a structured format with `**TRIGGER CONDITION**`, `**SEQUENCE GUIDANCE**`, `**CONSTRAINT WARNING**`, and `**OUTPUT EXPECTATION**` sections — this guides AI agents on when and how to call each tool.

### RAG Tools (`rag_tools/`)

The most complex server. Key design decisions:
- **ChromaDB** is persisted to an absolute path anchored to the script file (`os.path.dirname(os.path.abspath(__file__))/chroma_db`), so it resolves correctly regardless of working directory.
- The **SentenceTransformer** model (`all-MiniLM-L6-v2`) is lazy-loaded on first use to avoid blocking during the MCP handshake.
- **`_chunk_text(text, chunk_target, overlap)`** is a shared helper used by both `index_document_for_search` and `chunk_and_preview`. Do not duplicate this logic.
- Intended tool call order: `get_doc_metadata` → `read_doc_content` → `chunk_and_preview` → `index_document_for_search` → `semantic_search`.
- Use `list_indexed_files(collection_name)` to see which files are in a collection (distinct from `list_indexed_collections` which only shows chunk counts).

### Arch System Tools (`arch_system_tools/`)

All subprocess calls go through `run_command()` which uses `subprocess.run` without `shell=True`, enforces a 30-second timeout, and truncates output at 50k chars. Input sanitization is applied at every tool boundary.

- `search_contents` uses `ugrep` when available, falls back to `grep` automatically.
- `git_operations` supports `status`, `diff`, `log`, `show` (pass commit hash in `target`), and `blame` (pass file path in `target`).
- `container_status` supports `list`, `stats`, and `logs` (pass container name in `container_name`).

### Data Query (`data_query/`)

- SQLite tools use `sqlite3` from the stdlib with read-only URI connections (`?mode=ro`). Only `SELECT`/`WITH`/`EXPLAIN` are permitted.
- DuckDB tool uses an in-memory connection. Files are referenced directly in SQL: `read_csv_auto('/path/file.csv')`, `read_parquet(...)`, etc.
- All result sets are capped at 500 rows / 80k chars.

### Python REPL (`python_repl/`)

- Writes code to a `tempfile`, runs it in a subprocess using `sys.executable` (the server's own Python), captures stdout/stderr, then deletes the temp file.
- Each call is stateless — variables do not persist between calls.
- Timeout is capped at 60 seconds.

### Memory Notes (`memory_notes/`)

- Backed by a single `memories.json` file next to `main.py`.
- Operations: `remember(key, value, category)`, `recall(query)`, `list_memories(category)`, `forget(key)`.
- `recall` does case-insensitive substring search over both keys and values.

### Local SearXNG (`local_searxng/`)

Requires a SearXNG instance running locally. URL defaults to `http://localhost:8080/search` and can be overridden with the `SEARXNG_URL` environment variable.

## MCP Config

Servers are registered in `~/.lmstudio/mcp.json`. Each entry points to the venv Python binary and the server's `main.py`. After adding a new server, add an entry there and restart LM Studio.

## Dependencies

- Python ≥ 3.13 (all servers)
- `uv` for environment/dependency management
- `ruff` for linting/formatting (cache in `.ruff_cache/`)
- External tools required by `arch_system_tools`: `eza`, `ugrep` (optional, falls back to `grep`), `qalc`, `fastfetch`, `tldr`, Docker, systemd
