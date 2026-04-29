# Ollama Max Evolution Plan

Generated: 2026-04-29
Branch: `upgrade/ollama-max-evolution`
Baseline: `analysis.md` (current tool inventory + ecosystem comparison)

---

## 0. Strategic Premise

Ollama Max ($100/mo) provides:
- **10 concurrent cloud models** with 250x Free-tier GPU time
- **Frontier cloud models**: deepseek-v4-pro (1M ctx), glm-5.1 (SWE-Bench SOTA), devstral-2 (123B tool-using coder), qwen3-coder-next, gemini-3-flash-preview
- **Local concurrent multi-model serving** (unlike LM Studio's single-model-per-instance)
- **Native tool calling** via `/api/chat` — the Qwen3.5/3.6 modelfiles already include tool-calling templates
- **Embedding models**: `all-minilm`, `nomic-embed-text`, `mxbai-embed-large`, `qwen3-embedding`
- **Structured output** (JSON mode, JSON schema)

The upgrade replaces LM Studio as the primary LLM backend and leverages Ollama's multi-model, multi-capability architecture to deeply integrate LLM capabilities across all servers.

---

## 1. Synergy Map — How Ollama Unlocks Cross-Server Workflows

Current state: servers are independent. LLM capabilities are isolated in `llm_tools`. Data flows only through the LLM's context window as text.

With Ollama Max, every server gains access to model intelligence at the API level:

```
┌─────────────────────────────────────────────────────────────────┐
│                     OLLAMA (localhost:11434)                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐  ┌──────────────┐ │
│  │ Local     │  │ Local    │  │ Cloud        │  │ Embedding    │ │
│  │ qwen3.6   │  │ nemotron │  │ devstral-2   │  │ nomic-embed  │ │
│  │ 27b       │  │ 4b      │  │ 123B coder   │  │ mxbai-embed  │ │
│  └──────────┘  └──────────┘  │ glm-5.1      │  │ qwen3-embed  │ │
│  ┌──────────┐  ┌──────────┐  │ deepseek-v4  │  └──────────────┘ │
│  │ qwen3.5  │  │ gemma4   │  └──────────────┘                   │
│  │ 9b reason│  │ 31b     │                                      │
│  └──────────┘  └──────────┘                                      │
└─────────────────────────────────────────────────────────────────────┘
         │              │              │                │
    ┌────┴────┐   ┌─────┴─────┐  ┌────┴────┐    ┌──────┴──────┐
    │llm_tools│   │rag_tools  │  │code_intel│   │memory_notes │
    │(upgrade)│   │(hybrid)   │  │(NEW)    │   │(upgrade)    │
    └─────────┘   └───────────┘  └─────────┘    └─────────────┘
```

### Ollama-Enhanced Synergies

| Workflow | Servers Involved | Ollama Role | Model Tier |
|----------|-----------------|-------------|------------|
| **Deep research** | local_searxng → page_scrape → llm_tools | Cloud devstral-2 synthesizes multi-source analysis | Cloud |
| **Code review loop** | arch_system_tools → code_check → llm_tools | Local qwen3.6-27b reviews; cloud glm-5.1 for complex audit | Local + Cloud |
| **RAG pipeline** | rag_tools (hybrid search) → llm_tools | Local nomic-embed for embeddings; local qwen3.5 for synthesis | Local |
| **Vision pipeline** | rag_tools (images) → Ollama vision model | qwen3.5-vision or kimi-k2.6 for image understanding | Local/Cloud |
| **Data exploration** | data_query → llm_tools → python_repl | Local qwen3.5 generates SQL; cloud deepseek-v4 for statistical insight | Local + Cloud |
| **Memory-enriched chat** | memory_notes → llm_tools | Local model with context from knowledge graph | Local |
| **Agentic coding** | code_intel (NEW) → llm_tools → code_check → python_repl | Cloud devstral-2 plans changes; local nemotron-4b validates | Cloud + Local |

---

## 2. Unique Function Inventory (What Must Be Preserved)

Before any upgrade, each server's irreplaceable capabilities must be protected:

| Server | Irreplaceable Capability | Risk in Upgrade |
|--------|------------------------|-----------------|
| `page_scrape` | Trafilatura extraction + BS4 link mapping | Low — no LLM dependency |
| `rag_tools` | ChromaDB vector store + sentence-aware chunker + image extraction | Medium — embeddings migration path needed |
| `current_date_time` | IANA timezone math + API date range generation | None — pure computation |
| `arch_system_tools` | Arch-specific package/service/network diagnostics | Low — no LLM dependency |
| `local_searxng` | Local SearXNG metasearch + quality filtering | None — no LLM dependency |
| `python_repl` | Stateless subprocess code execution | Low — could add Ollama-aware validation |
| `data_query` | Read-only SQLite + DuckDB analytics | None — no LLM dependency |
| `memory_notes` | Persistent key-value JSON store | Medium — upgrade to knowledge graph |
| `command_docs` | man/tldr/cheat.sh three-tier lookup | None — no LLM dependency |
| `awesome_lists` | sindresorhus/awesome README parser | None — no LLM dependency |
| `llm_tools` | Local LLM delegation via OpenAI-compatible API | **High — complete rewrite target** |
| `code_check` | 20-language format + lint pipeline | Low — no LLM dependency |

---

## 3. Upgrade Phases

### Phase 1: Ollama Migration (llm_tools rewrite + config)

**Goal**: Replace LM Studio as the primary backend. Make Ollama the default.

**Why first**: Every subsequent phase depends on a working Ollama-backed LLM server. This is the foundation.

#### 3.1.1 Rewrite `llm_tools/main.py`

Current: single `LM_STUDIO_BASE` URL, single `_client`, single model resolution.
Target: Ollama-native multi-model server with tiered routing.

```
Architecture:
├── _get_client()          → OpenAI client pointing to Ollama (localhost:11434/v1)
├── _get_native_client()   → Ollama native API (localhost:11434/api/chat) for tool calling
├── _resolve_model(tier)   → tier-aware model selection
│   ├── tier="fast"        → nemotron-4b / gemma4-4b  (local, <1s)
│   ├── tier="standard"    → qwen3.5-9b / qwen3.6-27b (local, reasoning)
│   ├── tier="deep"        → devstral-2 / glm-5.1     (cloud, coding/reasoning)
│   └── tier="auto"        → pick based on task complexity heuristic
├── _resolve_embedder()    → Ollama embedding endpoint (localhost:11434/api/embed)
└── Config via env vars:
    OLLAMA_URL          → default http://localhost:11434
    OLLAMA_FAST_MODEL   → default nemotron-3-nano-4b
    OLLAMA_STANDARD_MODEL → default qwen3.5:9b
    OLLAMA_DEEP_MODEL   → default devstral-2
    OLLAMA_EMBED_MODEL  → default nomic-embed-text
```

#### 3.1.2 New/Modified Tools

| Tool | Change | Description |
|------|--------|-------------|
| `list_available_models` | **Modify** | Show both local + cloud models with tier labels |
| `summarize` | **Modify** | Add `tier` parameter (fast/standard/deep) |
| `ask_model` | **Modify** | Add `tier` parameter; increase input limits for cloud models |
| `analyze_code` | **Modify** | Default to `tier="deep"` for cloud devstral-2 |
| `interpret_data` | **Modify** | Add `tier` parameter |
| `generate_code` | **NEW** | Code generation with tool-calling context (cloud devstral-2) |
| `agent_chat` | **NEW** | Multi-turn agent loop with native tool calling via `/api/chat` |
| `embed_text` | **NEW** | Generate embeddings via Ollama's embedding endpoint |
| `model_info` | **NEW** | Show model capabilities (tool use, vision, context length, reasoning) |

#### 3.1.3 Update MCP Config

```json
{
  "llm-tools": {
    "command": ".../llm_tools/.venv/bin/python",
    "args": [".../llm_tools/main.py"],
    "env": {
      "OLLAMA_URL": "http://localhost:11434",
      "OLLAMA_FAST_MODEL": "nemotron-3-nano-4b",
      "OLLAMA_STANDARD_MODEL": "qwen3.5:9b",
      "OLLAMA_DEEP_MODEL": "devstral-2",
      "OLLAMA_EMBED_MODEL": "nomic-embed-text"
    }
  }
}
```

#### 3.1.4 Upgrade `llm_tools/pyproject.toml`

Add: `httpx>=0.28.0` (for native Ollama API calls alongside OpenAI SDK).

---

### Phase 2: Hybrid Search in rag_tools

**Goal**: BM25 + vector + RRF fusion. Replace pure dense-vector search.

**Why second**: RAG is the backbone of the document intelligence pipeline. Hybrid search is the single highest-impact RAG improvement (20-35% relevance gain per benchmarks).

#### 3.2.1 Architecture

```
Current:  query → embed → ChromaDB cosine → results
Target:   query → BM25 (FTS5/Whoosh) + embed → ChromaDB cosine
              → RRF fusion → reranked results
              → optional: cross-encoder reranking via Ollama
```

#### 3.2.2 Implementation Steps

1. **Add FTS5 index alongside ChromaDB**
   - On `index_document_for_search`, also store chunks in SQLite with FTS5
   - SQLite file: `<chroma_db_path>/rag_fts.db`
   - Schema: `CREATE VIRTUAL TABLE chunks USING fts5(text, source, chunk_idx, collection)`

2. **Implement BM25 search**
   - New internal function: `_bm25_search(query, collection, limit)`
   - Uses SQLite FTS5 `MATCH` with `bm25()` ranking

3. **Implement RRF fusion**
   - New internal function: `_rrf_fuse(bm25_results, vector_results, k=60)`
   - Reciprocal Rank Fusion: `score = Σ 1/(k + rank_i)` across both result sets

4. **Upgrade `semantic_search` tool**
   - Add `search_mode` parameter: `"hybrid"` (default), `"vector"`, `"keyword"`
   - Hybrid mode runs both, fuses via RRF, returns top results
   - Add `rerank` parameter: when true, uses Ollama `ask_model` to rerank top results

5. **Ollama-native embeddings**
   - Add `_get_ollama_embedder()` as alternative to SentenceTransformers
   - New env var: `RAG_EMBED_PROVIDER` = `"local"` (SentenceTransformers) or `"ollama"` (Ollama API)
   - When `"ollama"`: calls `POST /api/embed` with `nomic-embed-text` or `mxbai-embed-large`
   - Benefit: no need to load sentence-transformers model in the MCP server process

6. **Update `pyproject.toml`**
   - No new dependencies needed — SQLite FTS5 is stdlib, RRF is pure Python
   - Optionally add `whoosh>=2.7` as alternative to FTS5 for richer tokenization

#### 3.2.3 Tool Changes

| Tool | Change |
|------|--------|
| `index_document_for_search` | Also index into FTS5 SQLite |
| `semantic_search` | Add `search_mode` and `rerank` params |
| `list_indexed_collections` | Show both vector and FTS5 stats |
| `delete_from_index` | Also delete from FTS5 |

---

### Phase 3: Knowledge Graph Memory

**Goal**: Replace flat JSON memory with entity-relation-observation graph.

**Why third**: Memory is cross-cutting — it supports every other server. A knowledge graph enables much richer retrieval and agent context.

#### 3.3.1 Architecture

```
Current:  memories.json → [{key, value, category, timestamp}]
Target:   SQLite database with:
  ├── entities(id, name, type, created_at)
  ├── relations(id, from_entity, to_entity, relation_type, created_at)
  ├── observations(id, entity_id, content, source, timestamp)
  └── fts5_search(text, entity_id)  ← FTS5 over observations
```

Inspired by `modelcontextprotocol/server-memory` (official MCP Memory) but using local SQLite instead of in-memory storage, for persistence.

#### 3.3.2 New Tools

| Tool | Description |
|------|-------------|
| `create_entity(name, entity_type)` | Add a named entity (person, project, concept, tool, server) |
| `add_observation(entity_name, content, source)` | Attach a fact/note to an entity |
| `create_relation(from_entity, to_entity, relation_type)` | Link two entities (e.g., "rag_tools" --uses--> "ChromaDB") |
| `search_memory(query)` | FTS5 + semantic search across entities, relations, observations |
| `get_entity(name)` | Retrieve entity + all observations + relations |
| `get_related(entity_name, depth)` | Traverse graph up to N hops |
| `delete_entity(name)` | Remove entity and cascade |
| `export_graph(format)` | Export as JSON-LD, Mermaid, or dot for visualization |

#### 3.3.3 Migration Path

- On first run, if `memories.json` exists, auto-migrate entries:
  - Each entry becomes an entity (key=name, category=entity_type)
  - The value becomes an observation
- Keep `memories.json` read-only as backup; new writes go to SQLite

#### 3.3.4 Ollama Integration

- `search_memory` with semantic mode: uses Ollama embeddings to find conceptually related entities
- `summarize_entity(entity_name)`: uses local LLM to generate a natural-language summary of an entity's observations
- Auto-suggest relations: after adding observations, Ollama can suggest potential relations between entities

---

### Phase 4: Code Intelligence Server (NEW)

**Goal**: Tree-sitter-based code intelligence — call graphs, impact analysis, symbol search.

**Why new**: This is the single biggest gap identified in the analysis. No server currently provides semantic code understanding.

#### 3.4.1 Architecture

```
code_intel/
├── main.py
├── pyproject.toml
└── .venv/

Dependencies:
  tree-sitter + tree-sitter-language bindings
  (no LLM needed for core parsing — Ollama used for semantic analysis)
```

#### 3.4.2 Tools

| Tool | Description |
|------|-------------|
| `index_codebase(path, languages)` | Parse directory with tree-sitter, extract symbols, build call graph |
| `search_symbols(query, kind, path)` | Find functions, classes, methods by name/pattern |
| `get_callers(symbol)` | What calls this function? (reverse call graph) |
| `get_callees(symbol)` | What does this function call? (forward call graph) |
| `impact_analysis(file_path)` | If I change this file, what else might break? (dependency graph traversal) |
| `get_definitions(symbol)` | Go-to-definition: find where a symbol is defined |
| `get_references(symbol)` | Find all references to a symbol |
| `summarize_module(path, model)` | Use Ollama to generate a natural-language description of a file/module |
| `architecture_overview(path)` | Mermaid diagram of the codebase structure |

#### 3.4.3 Ollama Integration

- `summarize_module`: local qwen3.5-9b generates module descriptions
- `architecture_overview`: local model generates Mermaid from call graph data
- `explain_symbol`: cloud devstral-2 explains complex code patterns
- All pure-tree-sitter operations (index, search, callers, impact) work without LLM

#### 3.4.4 Synergies

| Connected Server | Synergy |
|-----------------|---------|
| `arch_system_tools` | `search_contents` (grep) → `search_symbols` (semantic) — two search tiers |
| `code_check` | `impact_analysis` → `check_code` — validate changed files |
| `python_repl` | `get_callees` → `execute_python` — test specific call chains |
| `llm_tools` | `summarize_module` → `analyze_code` — understand then review |
| `rag_tools` | Code indexed here for structural search; rag_tools for documentation search |
| `memory_notes` → Phase 3 | Code entities stored as knowledge graph nodes |

---

### Phase 5: Ollama-Enhanced Server Upgrades

**Goal**: Add Ollama-aware capabilities to existing servers where it creates clear value.

#### 3.5.1 `page_scrape` — Smart Extraction

| Enhancement | Description |
|------------|-------------|
| `smart_extract(url, query)` | NEW tool: fetch page + use Ollama to extract only sections relevant to a query |
| `summarize_page(url, style)` | NEW tool: fetch + summarize in one call (replaces two-step workflow) |

#### 3.5.2 `python_repl` — Validation Gates

| Enhancement | Description |
|------------|-------------|
| `execute_and_validate(code, language)` | NEW tool: run code + auto-lint via code_check + auto-review via Ollama |
| `fix_code(code, error_message)` | NEW tool: use Ollama to suggest fixes for failing code |

#### 3.5.3 `data_query` — Natural Language SQL

| Enhancement | Description |
|------------|-------------|
| `ask_database(question, db_path)` | NEW tool: Ollama generates SQL from natural language, executes it, returns results |
| `schema_summary(db_path)` | NEW tool: Ollama generates a human-readable schema description |

#### 3.5.4 `local_searxng` — Research Mode

| Enhancement | Description |
|------------|-------------|
| `deep_research(query, depth)` | NEW tool: multi-step search → scrape → synthesize loop via Ollama |

#### 3.5.5 `arch_system_tools` — Write Operations (with Ollama safeguards)

| Enhancement | Description |
|------------|-------------|
| `write_file(path, content)` | NEW tool: write content to a file (with backup) |
| `git_commit(message, files)` | NEW tool: stage + commit (with Ollama-generated commit message suggestion) |

---

### Phase 6: FastMCP 3.x Feature Adoption

**Goal**: Leverage FastMCP 3.x Providers, Transforms, Auth, and Session State.

#### 3.6.1 Migration Checklist (All Servers)

| Feature | Application | Servers |
|---------|-------------|---------|
| `@mcp.tool(timeout=30.0)` | Replace manual `subprocess.run(timeout=30)` | arch_system_tools, python_repl, code_check |
| `ctx.set_state()` / `ctx.get_state()` | Replace module-level globals (`_client`, `_embed_model`) | llm_tools, rag_tools |
| `Namespace` transform | Prefix tools when mounted behind gateway | All (when gateway is adopted) |
| `ResourcesAsTools` | Expose ChromaDB collections as tools | rag_tools |
| `FastMCPProvider` | Mount memory_notes inside llm_tools for context injection | llm_tools, memory_notes |

#### 3.6.2 Auth (Selective)

Add `auth=require_scopes("admin")` to destructive tools:
- `memory_notes/forget`
- `rag_tools/delete_from_index`
- `python_repl/execute_python` (consider `sandbox` scope)
- Future: `arch_system_tools/write_file`, `arch_system_tools/git_commit`

---

### Phase 7: Agentic Orchestration

**Goal**: Enable multi-step, multi-server workflows without manual agent intervention.

#### 3.7.1 Architecture: Ollama-as-Orchestrator

Use Ollama's native tool calling (`/api/chat` with tools) to let a cloud model orchestrate across MCP servers:

```
User query
  → Ollama cloud model (devstral-2 / glm-5.1)
    → Calls MCP tools via native tool calling
    → Sees results
    → Decides next step
    → Calls more tools
    → Returns final answer
```

This replaces the need for a separate orchestration layer (mcp-agent, LangGraph, etc.) — the LLM itself is the orchestrator.

#### 3.7.2 Implementation

New server: `orchestrator/`

| Tool | Description |
|------|-------------|
| `plan_and_execute(task, max_steps)` | Break task into steps, execute via tool calls, verify results |
| `research(topic, depth)` | Multi-source research loop: search → scrape → index → synthesize |
| `code_task(description, path)` | Agentic coding: read codebase → plan changes → implement → test → commit |

This server is thin — it delegates orchestration to Ollama's tool-calling capability and uses the existing 12 servers as its tools.

#### 3.7.3 Key Models for Orchestration

| Role | Model | Why |
|------|-------|-----|
| Orchestrator (planning) | devstral-2 (cloud) | 123B tool-using coder, designed for agentic workflows |
| Executor (fast tasks) | qwen3.5-9b (local) | Fast, reasoning-capable, tool calling |
| Verifier (quality gate) | glm-5.1 (cloud) | SWE-Bench SOTA, catches subtle issues |
| Fallback | qwen3.6-27b (local) | Strong local model when cloud is unavailable |

---

## 4. Dependency Graph — What Blocks What

```
Phase 1 (Ollama migration)
  ├── blocks → Phase 2 (RAG needs Ollama embeddings)
  ├── blocks → Phase 3 (Memory needs Ollama for semantic search)
  ├── blocks → Phase 5 (All Ollama-enhanced servers)
  └── blocks → Phase 7 (Orchestration needs Ollama tool calling)

Phase 2 (Hybrid search)
  └── blocks → Phase 3 (Memory semantic search reuses RAG patterns)

Phase 3 (Knowledge graph)
  └── feeds → Phase 4 (Code entities can be stored as knowledge graph nodes)

Phase 4 (Code intelligence)
  └── feeds → Phase 5 (python_repl validation, code_check integration)

Phase 6 (FastMCP 3.x)
  └── independent — can be done in parallel with any phase

Phase 7 (Orchestration)
  └── depends on all prior phases
```

---

## 5. Implementation Order & Commits

Each phase is a separate commit (or set of commits) on this branch. Merge to main only after testing.

| Phase | Commit Prefix | Files Changed | Test Required |
|-------|--------------|---------------|---------------|
| 1 | `feat: ollama-migration` | `llm_tools/main.py`, `llm_tools/pyproject.toml` | Yes — existing llm_tools tests + new |
| 2 | `feat: hybrid-search` | `rag_tools/main.py` | Yes — semantic search behavior change |
| 3 | `feat: knowledge-graph` | `memory_notes/main.py` | Yes — migration from JSON |
| 4 | `feat: code-intelligence` | `code_intel/` (new directory) | Yes — new server |
| 5 | `feat: ollama-enhanced-*` | Multiple servers | Per-server tests |
| 6 | `refactor: fastmcp-3x` | All servers | Regression tests |
| 7 | `feat: orchestrator` | `orchestrator/` (new directory) | Integration tests |

---

## 6. What NOT to Change

- **Local-first architecture** — Cloud models are used for deep tasks, but all infrastructure (SearXNG, ChromaDB, filesystem) stays local
- **Unix-philosophy servers** — Each server still does one thing well; Ollama enhances, doesn't merge
- **Structured docstrings** — The TRIGGER/SEQUENCE/CONSTRAINT/OUTPUT pattern stays; add `MODEL TIER` guidance
- **Arch Linux specialization** — arch_system_tools remains Arch-specific
- **Subprocess isolation pattern** — `run_command()` with timeout and truncation stays; Ollama calls use the same timeout discipline
- **Read-only safety on arch_system_tools** — Write operations (Phase 5) are opt-in with explicit `enable_write_ops` config

---

## 7. Risk Mitigations

| Risk | Mitigation |
|------|-----------|
| Ollama cloud unavailable | All tools fall back to local models; `OLLAMA_DEEP_MODEL` env var allows disabling cloud |
| Cloud costs exceed budget | `agent_chat` and `generate_code` track token usage per call; add `max_cloud_calls` session limit |
| Knowledge graph migration loses data | `memories.json` kept as read-only backup; migration is additive |
| Hybrid search slower than vector-only | FTS5 is very fast; RRF adds negligible overhead; provide `search_mode="vector"` escape hatch |
| Tree-sitter language gaps | Start with Python, JS/TS, Go, Rust (mature bindings); add languages incrementally |
| Tool calling reliability | Use Ollama native `/api/chat` for tool calls (not OpenAI-compatible); validate tool call JSON before execution |
| Breaking existing workflows | Phase 1 preserves all 5 current llm_tools tools with backward-compatible signatures; new params have defaults |

---

## 8. Model Routing Strategy

```
Task Complexity Assessment (in llm_tools):
┌────────────────────────────────────────────────────────┐
│ Input chars < 500 AND simple task (summarize, format)? │
│   → tier="fast"  (nemotron-4b, local)                  │
├────────────────────────────────────────────────────────┤
│ Input chars < 5000 OR reasoning/coding task?           │
│   → tier="standard" (qwen3.5-9b or qwen3.6-27b, local)│
├────────────────────────────────────────────────────────┤
│ Multi-source synthesis, complex code gen, or agent?    │
│   → tier="deep" (devstral-2 or glm-5.1, cloud)         │
├────────────────────────────────────────────────────────┤
│ Vision task (images, screenshots, diagrams)?            │
│   → vision model (qwen3.5-vision or kimi-k2.6)        │
└────────────────────────────────────────────────────────┘
```

User can override with explicit `model` or `tier` parameter on any call.

---

## 9. Testing Strategy

| Phase | Test Type | What to Verify |
|-------|-----------|----------------|
| 1 | Unit | Model resolution, tier routing, fallback logic |
| 1 | Integration | Ollama API connectivity (local + cloud) |
| 1 | Regression | All 5 existing llm_tools tests still pass |
| 2 | Unit | BM25 search, RRF fusion math |
| 2 | Integration | Hybrid vs. vector-only relevance comparison |
| 3 | Unit | CRUD operations on entities, relations, observations |
| 3 | Migration | `memories.json` → SQLite auto-migration |
| 4 | Unit | Tree-sitter parsing, symbol extraction, call graph |
| 4 | Integration | Cross-repo analysis (this repo as test target) |
| 5 | Unit | Per-server new tool functionality |
| 6 | Regression | All existing tests pass with FastMCP 3.x |
| 7 | Integration | End-to-end multi-server workflow |