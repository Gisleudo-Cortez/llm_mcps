# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

A collection of independent MCP (Model Context Protocol) servers built with [FastMCP](https://github.com/jlowin/fastmcp). Each subdirectory is a self-contained Python project with its own virtual environment managed by `uv`. Servers are registered via `claude mcp add --scope user` (user-global scope).

## Project Structure

| Directory | Server Name | Purpose |
|-----------|-------------|---------|
| `search_cache/` | Search Cache | Local semantic cache for agent search results — two-layer lookup (hash + ANN), 7-tier staleness scoring, LCFU eviction (LanceDB + SentenceTransformers) |
| `email_management/` | Email Management | Classify emails, route attachments, process inboxes using himalaya + local LLMs |
| `page_scrape/` | Page Scrape Server | Fetch/parse web pages; extract links for site mapping (Trafilatura + BeautifulSoup) |
| `rag_tools/` | RAG Document Tools | Document reading, indexing, and semantic search (ChromaDB + SentenceTransformers) |
| `current_date_time/` | System Utilities Server | Date/time tools with timezone support |
| `arch_system_tools/` | Arch System Tools | Arch Linux system utilities (fs, packages, git, services, network, Docker) |
| `local_searxng/` | SearXNG Search Server | Web search via a local SearXNG instance |
| `python_repl/` | Python REPL | Execute Python code in an isolated subprocess |
| `data_query/` | Data Query Server | SQLite queries and DuckDB analytics over CSV/Parquet/JSON |
| `memory_notes/` | Memory & Notes | Persistent key-value memory across sessions (JSON file backend) |
| `command_docs/` | Command Docs | man pages, tldr cheat sheets, and cheat.sh community recipes |
| `awesome_lists/` | Awesome Lists | Browse/search the sindresorhus/awesome meta-list (local repo) |
| `llm_tools/` | LLM Tools | Delegate tasks (summarize, ask, code review, data interpretation) to local models via Ollama API |
| `code_check/` | Code Check Server | Format and lint generated code using the same tool chain as Neovim (ruff, prettier, stylua, shfmt, shellcheck, sqlfluff, …) |

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
python3 -c "import ast; [print('OK', f) or ast.parse(open(f).read()) for f in ['email_management/main.py','rag_tools/main.py','arch_system_tools/main.py','local_searxng/main.py','python_repl/main.py','data_query/main.py','memory_notes/main.py']]"
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

### Code Check (`code_check/`)

Formats and lints code snippets using the exact same binaries configured in the Neovim stack (conform.nvim + nvim-lint). All tools are expected to be on system PATH.

- `format_code(code, language)` → runs the formatter, returns formatted code. In-place formatters (ruff, prettier, stylua, shfmt, etc.) write to a temp file then return the result.
- `lint_code(code, language)` → runs the linter, returns issues or "No issues found.". JSON and TOML are validated via Python stdlib (no subprocess). Languages without a standalone linter (Go, Rust, C/C++, Java, Lua) return `[no linter]`.
- `check_code(code, language)` → runs both in one call; returns a two-section report (`=== FORMAT ===` / `=== LINT ===`). Preferred over calling the two tools separately.
- Language aliases accepted: `py`, `js`, `jsx`, `ts`, `tsx`, `sh`, `zsh`, `yml`, `md`, `cc`, `cxx`, `h`, `hpp`.
- Formatter settings match nvim: stylua column-width 100 / 2-space, shfmt -i 2 -ci, sqlfluff dialect ansi.

### LLM Tools (`llm_tools/`)

Calls Ollama's API at `http://localhost:11434/v1` (default).
Override via `OLLAMA_URL` env var.
Override the auto-selected model via `LLM_TOOLS_DEFAULT_MODEL` env var.

- `list_available_models()` → call first in any session to see loaded model IDs and context sizes.
- `summarize(text, style, model)` → four styles: concise / detailed / bullets / eli5. Temperature 0.3.
- `ask_model(prompt, context, model, system_prompt, temperature)` → RAG synthesis step; context injected as a separate section in the user message.
- `analyze_code(code, language, model)` → structured review: Correctness, Performance, Security, Style, Top Improvements. Temperature 0.2.
- `interpret_data(data, question, model)` → plain-language data analysis. Temperature 0.4.
- Model resolution order: explicit arg > `LLM_TOOLS_DEFAULT_MODEL` env var > first loaded model > hardcoded fallback.
- A module-level `_client` is lazily initialized to avoid blocking the MCP handshake.

### Email Management (`email_management/`)

Classifies emails and routes attachments using a tiered async LLM pipeline. Zero cloud by default; OpenRouter fallback is opt-in via config.

- **All tool functions are async** — FastMCP handles them natively. The old `asyncio.run()` pattern was removed (it failed with "cannot be called from a running event loop").
- **Async classifier** (`async_classifier.py`) uses `httpx.AsyncClient` for Ollama HTTP API and OpenRouter cloud fallback. Supports `asyncio.Semaphore`-limited concurrent classification.
- **Keyword fallback** triggers when all tiers fail (model not loaded, timeout, no API key). Fast, deterministic, low confidence.
- **Himalaya wrapper** (`himalaya_wrapper.py`) shells out to the `himalaya` CLI with `--output json`. Credentials live in himalaya's own `config.toml` — this server never sees passwords.
- **Router** (`router.py`) loads YAML rules with glob-pattern matching on sender/recipient. Paths support `{sender_domain}`, `{date}`, `{label}` templates.
- **Tools**: `check_connection`, `list_accounts`, `list_rules`, `classify_email_tool`, `filter_attachments`, `route_attachments`, `process_inbox`.
- **Config**: `~/.config/email-mcp/server_config.yaml`. See project README for full schema.
- **Run tests**: `cd email_management && uv run pytest tests/ -v`

### Command Docs (`command_docs/`)

- Shares a `run_command()` helper (same pattern as `arch_system_tools`).
- `man_lookup` calls `man -P cat` to produce plain-text man pages.
- `tldr_lookup` calls the system `tldr` binary.
- `cheat_sh_lookup` fetches from `https://cheat.sh/{command}/{query}?T` (plain text, ANSI stripped). Uses a shared `requests.Session`. Requires internet.
- Intended tool call order: `tldr_lookup` → `man_lookup` (for full ref) → `cheat_sh_lookup` (for community recipes).
- External tools required: `man`, `tldr`.

### Awesome Lists (`awesome_lists/`)

- Parses the local `sindresorhus/awesome` repo at `~/Downloads/git/awesome/readme.md`.
- `_parse_sections` splits the readme by `## Heading` and extracts `- [Name](URL) - description` items from each section.
- `search_awesome_lists(query)` → case-insensitive substring search across names and descriptions.
- `get_awesome_category(category)` → all items in a single section (case-insensitive match).
- No external dependencies beyond `mcp`. The readme path is hardcoded to the cloned repo location.

### Page Scrape (`page_scrape/`)

- `fetch_url_content` extracts clean text (Trafilatura), tables, and images from a URL.
- `extract_links` maps all hyperlinks on a page: resolves relative URLs, deduplicates, groups by domain, supports `internal_only` and `filter_text` options.

## MCP Config

Servers are registered via the MCP client configuration. Each entry points to the venv Python binary and the server's `main.py`.

## Dependencies

- Python ≥ 3.13 (all servers)
- `uv` for environment/dependency management
- `ruff` for linting/formatting (cache in `.ruff_cache/`)
- External tools required by `arch_system_tools`: `eza`, `ugrep` (optional, falls back to `grep`), `qalc`, `fastfetch`, `tldr`, Docker, systemd
