#!/usr/bin/env bash
# Start all MCP servers in streamable-HTTP mode for Open WebUI.
# Each server listens on its own port at http://127.0.0.1:<port>/mcp
# Run with: bash start_mcp_http.sh
# Stop all with: pkill -f 'mcp.run.*streamable-http'

set -e

BASE="$(cd "$(dirname "$0")" && pwd)"

start_server() {
    local name="$1"
    local dir="$2"
    local port="$3"
    echo "Starting $name on port $port..."
    cd "$BASE/$dir"
    .venv/bin/python -c "import main; main.mcp.settings.port = $port; main.mcp.run(transport='streamable-http')" \
        > /tmp/mcp-${name}.log 2>&1 &
    echo "  PID $! → http://127.0.0.1:${port}/mcp  (log: /tmp/mcp-${name}.log)"
}

start_server "page-scrape"       "page_scrape"       8200
start_server "rag-tools"         "rag_tools"         8201
start_server "current-date-time" "current_date_time" 8202
start_server "arch-system-tools" "arch_system_tools" 8203
start_server "local-searxng"     "local_searxng"     8204
start_server "python-repl"       "python_repl"       8205
start_server "data-query"        "data_query"        8206
start_server "memory-notes"      "memory_notes"      8207
start_server "command-docs"      "command_docs"      8208
start_server "awesome-lists"     "awesome_lists"     8209
start_server "llm-tools"         "llm_tools"         8210
start_server "code-check"        "code_check"        8211

echo ""
echo "All 12 MCP servers started. Add each URL to Open WebUI:"
echo "  Admin Panel → Settings → Connections → Tool Servers → + Add"
echo ""
echo "  http://127.0.0.1:8200/mcp  (page-scrape)"
echo "  http://127.0.0.1:8201/mcp  (rag-tools)"
echo "  http://127.0.0.1:8202/mcp  (current-date-time)"
echo "  http://127.0.0.1:8203/mcp  (arch-system-tools)"
echo "  http://127.0.0.1:8204/mcp  (local-searxng)"
echo "  http://127.0.0.1:8205/mcp  (python-repl)"
echo "  http://127.0.0.1:8206/mcp  (data-query)"
echo "  http://127.0.0.1:8207/mcp  (memory-notes)"
echo "  http://127.0.0.1:8208/mcp  (command-docs)"
echo "  http://127.0.0.1:8209/mcp  (awesome-lists)"
echo "  http://127.0.0.1:8210/mcp  (llm-tools)"
echo "  http://127.0.0.1:8211/mcp  (code-check)"
