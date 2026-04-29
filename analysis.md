# MCP Tools Analysis & Evolution Roadmap

Generated: 2026-04-29

---

## 1. Current Tool Inventory

12 servers, all FastMCP-based, stdio transport (with HTTP launcher on ports 8200-8211).

| Server | Tools | Key Capability |
|--------|-------|----------------|
| `page_scrape` | `fetch_url_content`, `extract_links` | Web fetch + link mapping (Trafilatura + BS4) |
| `rag_tools` | 12 tools | Document reading, chunking, embedding, semantic search, image extraction (ChromaDB + all-MiniLM-L6-v2) |
| `current_date_time` | 4 tools | Date/time with timezone support |
| `arch_system_tools` | 15 tools | Arch Linux system utilities (fs, packages, git, systemd, Docker, network) |
| `local_searxng` | `web_search` | Local SearXNG search with noisy-URL filtering |
| `python_repl` | `execute_python`, `list_installed_packages` | Stateless subprocess code execution |
| `data_query` | 4 tools | SQLite (read-only) + DuckDB (in-memory) analytics |
| `memory_notes` | 4 tools | JSON file key-value memory |
| `command_docs` | 3 tools | man/tldr/cheat.sh documentation lookup |
| `awesome_lists` | 3 tools | Parse sindresorhus/awesome readme |
| `llm_tools` | 5 tools | Delegate to local LLM (LM Studio / Ollama) |
| `code_check` | 3 tools | Format + lint across 20 languages |

### Cross-Cutting Patterns

- **FastMCP + stdio** — all servers follow `mcp = FastMCP("Name")` / `@mcp.tool()` / `mcp.run(transport="stdio")`
- **Structured docstrings** — TRIGGER CONDITION / SEQUENCE GUIDANCE / CONSTRAINT WARNING / OUTPUT EXPECTATION
- **Subprocess isolation** — `run_command()` helper with 30s timeout, `max_chars` truncation, no `shell=True`
- **Lazy init** — embedding models and LLM clients initialized on first call to avoid MCP handshake timeouts
- **Input sanitization** — regex validation on hostnames, service names, container names, env vars
- **Context-window protection** — all large-output tools cap at 50k-100k chars
- **Markdown output** — all tools return structured Markdown with `###` headings

### Gaps in Current Test Coverage

Only 4 of 12 servers have tests: `page_scrape` (12), `command_docs` (14), `awesome_lists` (12), `llm_tools` (22). The remaining 8 have no test files.

---

## 2. Ecosystem Comparison

### Coding & Development

| This Repo | Ecosystem Equivalent | Gap |
|-----------|---------------------|-----|
| `code_check` (format + lint) | markdown-lint-mcp (auto-fix), code-review-agent (7-step chain), Whyme-Labs QA (verification gates) | No semantic analysis, no AST awareness, no CI hooks, no auto-fix feedback loop |
| `llm_tools` (single local LLM) | dshills/mcp-pr (multi-LLM failover), huberp/oauth-blueprint (MCP sampling) | No structured review output (severity/category), no multi-provider routing, no capability-aware model selection |
| `python_repl` (stateless subprocess) | Whyme-Labs QA (executable verification gates), Playwright MCP (browser_run_code) | No verification gates, no sandbox enforcement, no multi-language support |
| `arch_system_tools` (local-only) | Docker MCP (700 stars), filesystem server (625 stars), K8s MCP | No cloud infrastructure, no remote host management, no K8s |

**Critical gap: No semantic code intelligence.** The ecosystem has tree-sitter + LSP + graph database tools (code-graph-mcp, Synapps, mcp-codebase-intelligence) that provide call graphs, impact analysis, and architecture visualization. This repo has none of that.

### Search & Knowledge

| This Repo | Ecosystem Equivalent | Gap |
|-----------|---------------------|-----|
| `local_searxng` | Tavily (AI-optimized JSON), Exa (semantic), Firecrawl (13-tool crawl), Brave (privacy) | No content extraction pipeline, no AI-optimized result formatting, no autonomous multi-step research |
| `rag_tools` (dense vector only) | rag-memory-mcp (vector + graph), knowlagebase-mcp (FTS5 + vector + RRF) | No hybrid search, no knowledge graph, no incremental re-indexing, no cross-encoder reranking |
| `page_scrape` (Trafilatura only) | Firecrawl MCP (JS rendering, screenshots), Playwright MCP (40+ browser tools) | No JavaScript rendering, no screenshots, no form interaction, no structured data extraction |
| `command_docs` (CLI docs) | Context7 (2.2M+ library docs, version-specific) | No programming library/API documentation lookup |
| `awesome_lists` (local readme parser) | No direct equivalent | Unique curation approach; no gap |

### Memory & State

| This Repo | Ecosystem Equivalent | Gap |
|-----------|---------------------|-----|
| `memory_notes` (flat JSON) | Official MCP Memory (knowledge graph), mcp-engram (52 tools, 3-tier memory), mcp-memory (Qdrant + episodic/semantic/procedural) | No vector search over memories, no knowledge graph, no lifecycle management (STM→LTM→archive), no multi-agent namespace support |

### Orchestration & Agent Workflow

| This Repo | Ecosystem Equivalent | Gap |
|-----------|---------------------|-----|
| No orchestration | mcp-agent (8.3K stars), LangGraph, Mastra, CrewAI | No inter-server chaining, no conditional routing, no parallel execution |
| No task planning | Sequential Thinking MCP, TaskFlow MCP | No task decomposition, dependency tracking, or verification gates |
| No gateway/proxy | Docker MCP Gateway, AgentGateway, Envoy AI Gateway, Cruvero | All servers run independently; no centralized auth, routing, rate limiting, or observability |

---

## 3. Ecosystem Landscape (April 2026)

### Most-Starred MCP Servers

| Server | Stars | Domain |
|--------|-------|--------|
| Playwright MCP (Microsoft) | 31,385 | Browser automation |
| GitHub MCP Server | 29,215 | Git/PR/CI integration |
| awesome-mcp-servers | 83,000+ | Registry/curated list |
| Docker MCP Gateway | 1,371 | Multi-server orchestration |
| Firecrawl MCP | 5,798 | Web scraping/research |
| Exa MCP | 4,035 | Semantic search |
| Tavily MCP | 1,410 | AI-optimized search |
| mcp-agent (LastMile) | 8,299 | Orchestration framework |

### Registry Landscape

| Registry | Scale | Specialty |
|----------|-------|-----------|
| mcp.so | ~20,222 | Largest marketplace |
| Glama | ~21,500+ | Security scanning, built-in inspector |
| Smithery | ~4,000-7,300 | CLI-first, hosted deployment |
| Official MCP Registry | ~87 curated | Canonical metadata, GitHub OIDC |
| punkpeye/awesome-mcp-servers | 83K+ stars | Community curated |

### FastMCP 3.x New Capabilities

FastMCP 3.0 (GA Feb 2026, now at PrefectHQ/fastmcp) introduces major architectural changes:

- **Providers**: `LocalProvider` (classic), `FileSystemProvider` (auto-discover with hot-reload), `OpenAPIProvider` (wrap REST APIs as MCP tools), `ProxyProvider` (proxy remote servers), `FastMCPProvider` (mount other servers)
- **Transforms**: `Namespace` (prefix tools), `ToolTransform` (rename/rewrite/modify schemas), `VersionFilter`, `Visibility` (enable/disable by tag), `ResourcesAsTools` / `PromptsAsTools`
- **Auth**: Per-component `auth=require_scopes("admin")`, `AuthMiddleware`, JWT validation, CIMD
- **Session state**: `ctx.set_state()` / `ctx.get_state()` with 1-day TTL and Redis backend
- **Background tasks**: Docket (SQLite/Postgres queues)
- **Timeouts**: `@mcp.tool(timeout=30.0)` with MCP error codes
- **CLI**: `fastmcp list`, `fastmcp call`, `fastmcp discover`, `fastmcp generate-cli`
- **MCP Apps (3.1)**: `ui://` resource scheme, typed UI metadata, extension negotiation

This repo currently uses FastMCP 3.x (`fastmcp>=3.2.0` in rag_tools) but does not leverage any of these new features.

---

## 4. Security Considerations

### OWASP MCP Top 10 (2026)

| ID | Risk | Relevance to This Repo |
|----|------|----------------------|
| MCP01 | Token/secret exposure | `memory_notes` stores plain JSON; `local_searxng` URL in env var |
| MCP02 | Privilege escalation via scope creep | All tools in each server are equally accessible; no per-tool auth |
| MCP03 | Tool poisoning (malicious descriptions) | No description validation or integrity checks |
| MCP05 | Command injection | `python_repl` executes arbitrary code; `arch_system_tools` uses subprocess with sanitized inputs |
| MCP07 | Insufficient auth/authorization | No auth on any server; stdio transport assumes trusted client |
| MCP08 | Lack of audit/telemetry | No logging or observability on any server |
| MCP09 | Shadow MCP servers | No gateway; any server can be added without central governance |

### Current Mitigations

- `arch_system_tools`: regex validation on all inputs, read-only operations only
- `data_query`: SQL injection prevention (whitelist SELECT/WITH/EXPLAIN), read-only SQLite connections
- `code_check`: subprocess timeout (30s), `FileNotFoundError` catch
- `python_repl`: 60s max timeout, output truncation at 50k chars

### Missing Mitigations

- No auth layer on any server
- No rate limiting
- No audit logging
- `python_repl` has no sandboxing (runs with full user permissions)
- No gateway/proxy for centralized security policy enforcement
- No tool-level RBAC (all-or-nothing access per server)

---

## 5. Benchmark Insights (What Models Struggle With)

Multiple MCP benchmarks (MCP-Atlas, MCPMark, LiveMCPBench, MCPAgentBench) reveal consistent findings:

1. **Tool discovery/selection is the #1 failure mode** — models struggle to pick the right tool, especially with distractors
2. **24-42% of failures involve models not calling any tools at all** — suggesting discovery and description quality are critical
3. **Cross-server orchestration is very hard** — multi-step, multi-server workflows see dramatic performance drops
4. **Parallel tool calling is poorly handled** — several models fail at dual-parallel invocations
5. **Even top models score 50-78%** on MCP tool-use tasks — significant headroom remains

**Implication for this repo**: The 12 servers expose ~60 tools total. Without orchestration or a gateway, agents must manually select and sequence tools. Better tool descriptions (the structured docstring pattern already used) and fewer, more composable tools would improve agent success rates.

---

## 6. RAG Benchmark Insights (Chunking & Retrieval)

| Strategy | Accuracy | Cost | Recommendation |
|----------|----------|------|----------------|
| Document-structure chunking | ~87% | 1-2x | Best for structured docs |
| Recursive splitting (400-512 tokens, 10-20% overlap) | ~69% | Baseline | Good starting point |
| Semantic chunking | ~54% | 3-5x | Avoid — produces too-small chunks |
| Hybrid search (dense + sparse + RRF) | Best | Moderate | Always recommended |
| Cross-encoder reranking | +20-35% NDCG@10 | +200-500ms | For high-value queries |

This repo's `rag_tools` uses recursive character splitting (800 target, 100 overlap) with dense vector search only. Moving to hybrid search (BM25 + vector + RRF) would be the single highest-impact RAG improvement.

---

## 7. Evolution Priorities

### Tier 1 — High Impact, Builds on Existing Patterns

| Priority | What | Why | Effort |
|----------|------|-----|--------|
| **1** | **Hybrid search in rag_tools** | BM25 + vector + RRF fusion is the minimum viable RAG approach in 2026; 20-35% relevance improvement | Medium — add FTS5/Whoosh alongside ChromaDB, implement RRF |
| **2** | **Code intelligence server** | Tree-sitter + call graph + impact analysis; the single biggest gap for coding workflows | Medium — new server, tree-sitter Python bindings are mature |
| **3** | **Upgrade memory_notes to knowledge graph** | Flat JSON is the weakest memory approach; entities/relations enable much richer retrieval | Medium — replace JSON with sqlite-vec or Qdrant, add entity/relation tools |
| **4** | **Adopt FastMCP 3.x features** | Providers, transforms, session state, auth, and background tasks are all unused; they'd reduce boilerplate and enable new patterns | Low — incremental migration |

### Tier 2 — Moderate Impact, New Capabilities

| Priority | What | Why | Effort |
|----------|------|-----|--------|
| **5** | **MCP gateway/proxy** | Central auth, routing, rate limiting, observability, and namespace collision prevention | Medium — adopt Docker MCP Gateway or Cruvero |
| **6** | **Browser automation** (Playwright MCP) | #1 most-used MCP server (31K stars); enables E2E testing, screenshots, form interaction | Low — adopt existing server, no need to build |
| **7** | **GitHub/Git integration** | PR management, code scanning, CI integration; this repo only has local git read operations | Low — adopt github-mcp-server |
| **8** | **Context7 or similar** for library docs | Complements `command_docs` (which covers CLI tools) with version-specific programming library docs | Low — adopt existing server |

### Tier 3 — Longer-Term, Strategic

| Priority | What | Why | Effort |
|----------|------|-----|--------|
| **9** | **Orchestration layer** (mcp-agent or similar) | Chaining tools across servers (e.g., search → scrape → index → query) without manual agent intervention | High — new architecture |
| **10** | **Multi-model orchestration** in llm_tools | Tiered model routing (fast model for triage, strong model for analysis), failover chains, capability-aware selection | Medium — extend existing server |
| **11** | **Verification gates** for python_repl | Compile/lint/test validation loops; auto-fix cycles for generated code | Medium — extend existing server |
| **12** | **Security hardening** | Per-tool auth, rate limiting, audit logging, python_repl sandboxing | High — cross-cutting |

### What NOT to Change

- **Local-first, privacy-preserving architecture** — SearXNG, LM Studio, local ChromaDB are competitive advantages
- **Unix-philosophy single-purpose servers** — Each server does one thing well; don't merge them
- **Structured docstrings** — The TRIGGER/SEQUENCE/CONSTRAINT/OUTPUT pattern aligns with benchmark findings about tool description quality
- **Arch Linux specialization** — Niche but valuable for the target user

---

## 8. Reference Repositories

### Coding & Code Intelligence

| Repo | URL | Stars | Key Feature |
|------|-----|-------|-------------|
| code-graph-mcp | github.com/sdsrss/code-graph-mcp | 22 | BM25+vector search, call graphs, impact analysis, 16 languages |
| Synapps | github.com/SynappsCodeComprehension/synapps | — | LSP-based indexing into graph DB, 19 tools |
| mcp-codebase-intelligence | github.com/g-tiwari/mcp-codebase-intelligence | — | 18 tools, semantic diff, Mermaid architecture diagrams |
| code-analyze-mcp | github.com/clouatre-labs/code-analyze-mcp | — | Pure tree-sitter, 59% token savings |
| vaur94/mcp-code | github.com/vaur94/mcp-code | — | Safe edit planning, blast-radius prediction |
| code-review-agent | github.com/architsinghh/code-review-agent | — | 7-tool review chain with self-improving pattern DB |
| Whyme-Labs QA | github.com/Whyme-Labs/whyme-qa | — | Executable verification gates (format→compile→lint→test) |

### Search & Knowledge

| Repo | URL | Stars | Key Feature |
|------|-----|-------|-------------|
| Firecrawl MCP | github.com/mendableai/firecrawl-mcp | 5,798 | 13 tools: search, scrape, crawl, browser automation |
| Tavily MCP | github.com/tavily-ai/tavily-mcp | 1,410 | AI-optimized search + extraction pipeline |
| Exa MCP | github.com/exa-labs/exa-mcp-server | 4,035 | Semantic/AI-native search |
| Context7 | context7.com | — | 2.2M+ version-specific library docs |
| rag-memory-mcp | github.com/ttommyth/rag-memory-mcp | 44 | sqlite-vec + Sentence Transformers + graph traversal |
| knowlagebase-mcp | github.com/PaulTheSecond/knowlagebase-mcp | — | FTS5 + vector + RRF fusion |

### Orchestration & Memory

| Repo | URL | Stars | Key Feature |
|------|-----|-------|-------------|
| mcp-agent | github.com/lastmile-ai/mcp-agent | 8,299 | Orchestrator-Workers, Router, Evaluator-Optimizer patterns |
| Official Memory MCP | github.com/modelcontextprotocol/server-memory | — | Knowledge graph with entities/relations/observations |
| mcp-engram-memory | github.com/wyckit/mcp-engram-memory | — | 52 tools, 3-tier memory, BM25+vector+graph, lifecycle management |
| TaskFlow MCP | github.com/CalebGerman/mcp-taskflow | — | Persistent CRUD tasks, dependency-aware planning, verification |
| Sequential Thinking MCP | pypi.org/project/sequential-thinking-mcp | — | 36K downloads/mo, virtual thought logging |

### Infrastructure & Security

| Repo | URL | Stars | Key Feature |
|------|-----|-------|-------------|
| Docker MCP Gateway | github.com/docker/mcp-gateway | 1,371 | Container isolation, profile-based server groups |
| Cruvero MCP Gateway | github.com/cruvero/cruvero-mcp-gateway | — | mTLS, progressive discovery, admin dashboard |
| AgentGateway | mintlify.com/agentgateway | — | Kubernetes-native, multiplexes stdio/HTTP/SSE |
| Envoy AI Gateway | aigateway.envoyproxy.io | — | Production-grade, MCPRoute CRD, OAuth+JWT |

### Benchmarks

| Benchmark | Tasks | Top Score |
|-----------|-------|-----------|
| MCP-Atlas | 1,000 | Claude Opus 4.5: 62.3% |
| MCPMark | 127 | GPT-5: 51.6% |
| LiveMCPBench | 95 | Claude Sonnet 4: 78.95% |
| MCPAgentBench | 178 | Claude Sonnet 4.5: 71.6% |

---

## 9. FastMCP 3.x Migration Notes

Current state: `rag_tools` uses `fastmcp>=3.2.0`; other servers use `mcp>=1.26.0` or `mcp>=1.27.0`. All use the v2 decorator pattern (`@mcp.tool()` returning the function).

Key migration opportunities:

1. **Providers** — Convert `awesome_lists` to use `FileSystemProvider` for hot-reload of the readme. Convert any REST-backed tool (future: GitHub, Firecrawl) to `OpenAPIProvider`.
2. **Transforms** — Use `Namespace` transform when mounting multiple servers behind a gateway. Use `ResourcesAsTools` to expose ChromaDB collections as tools for tool-only clients.
3. **Session state** — `rag_tools` and `memory_notes` could use `ctx.set_state()`/`ctx.get_state()` instead of module-level globals (`_embed_model`, `_client`).
4. **Auth** — Add `auth=require_scopes("admin")` to destructive tools (`forget`, `delete_from_index`, `execute_python`).
5. **Timeouts** — Add `@mcp.tool(timeout=30.0)` to all tools instead of manual `subprocess.run(timeout=30)`.
6. **CLI** — Replace manual `start_mcp_http.sh` with `fastmcp run --reload` and `fastmcp list` for introspection.