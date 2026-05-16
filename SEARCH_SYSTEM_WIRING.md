# Search System — Wiring & Usage Reference

This document covers everything needed to register the new MCP servers, pass credentials,
and use the search system correctly. Give this file to any agent tasked with wiring or
using the search stack.

---

## 1. What Was Built

A four-engine search system exposed as MCP tools. Agents call `search_router_get_routing`
first, receive a JSON routing config, then execute the steps it describes.

| Server | Directory | Tools exposed | Needs |
|---|---|---|---|
| `search_router` | `lms_mcp/search_router/` | `search_router_get_routing` | nothing |
| `brave_search` | `lms_mcp/brave_search/` | `brave_search_web`, `brave_search_news` | `BRAVE_API_KEY` |
| `exa_search` | `lms_mcp/exa_search/` | `exa_search_query`, `exa_find_similar` | `EXA_API_KEY` |
| `page_scrape` | `lms_mcp/page_scrape/` | `page_scrape_crawl4ai_fetch`, `page_scrape_crawl4ai_fetch_many` (new), plus existing `page_scrape_fetch_url_content`, `page_scrape_extract_links` | Crawl4AI container |
| `local_searxng` | `lms_mcp/local_searxng/` | `searxng_web_search` | SearXNG on :8080 |

---

## 2. File Locations (absolute paths)

```
/home/nero/Documents/Estudos/07-tools-and-infrastructure/lms_mcp/
├── .env                          # API keys — never commit
├── .env.example                  # Safe placeholder for documentation
├── brave_search/
│   ├── main.py                   # Server entry point
│   └── .venv/bin/python          # Python interpreter to use
├── exa_search/
│   ├── main.py
│   └── .venv/bin/python
├── search_router/
│   ├── main.py
│   └── .venv/bin/python
├── page_scrape/
│   ├── main.py                   # Now includes Crawl4AI tools
│   └── .venv/bin/python
└── local_searxng/
    ├── main.py
    └── .venv/bin/python
```

### .env contents

```
BRAVE_API_KEY=<your_brave_api_key>
EXA_API_KEY=<your_exa_api_key>
```

File permissions: `600` (owner read/write only). Never write these values to any
other file, commit them to git, or log them.

---

## 3. MCP Server Registration

Each server runs as a stdio process. The MCP client spawns it, communicates over
stdin/stdout. The pattern is the same for all clients — only the config file format differs.

### Generic pattern (apply to whichever client config you use)

```
command:  /path/to/.venv/bin/python
args:     [/path/to/main.py]
env:      { KEY: value }   # per-server env vars (see table below)
```

### Per-server config values

**search_router**
```
command: /home/nero/Documents/Estudos/07-tools-and-infrastructure/lms_mcp/search_router/.venv/bin/python
args:    [/home/nero/Documents/Estudos/07-tools-and-infrastructure/lms_mcp/search_router/main.py]
env:     {}
```

**brave_search**
```
command: /home/nero/Documents/Estudos/07-tools-and-infrastructure/lms_mcp/brave_search/.venv/bin/python
args:    [/home/nero/Documents/Estudos/07-tools-and-infrastructure/lms_mcp/brave_search/main.py]
env:     { BRAVE_API_KEY: "<from .env>" }
```

**exa_search**
```
command: /home/nero/Documents/Estudos/07-tools-and-infrastructure/lms_mcp/exa_search/.venv/bin/python
args:    [/home/nero/Documents/Estudos/07-tools-and-infrastructure/lms_mcp/exa_search/main.py]
env:     { EXA_API_KEY: "<from .env>" }
```

**page_scrape** (updated — add CRAWL4AI_URL only if container port changes from 11235)
```
command: /home/nero/Documents/Estudos/07-tools-and-infrastructure/lms_mcp/page_scrape/.venv/bin/python
args:    [/home/nero/Documents/Estudos/07-tools-and-infrastructure/lms_mcp/page_scrape/main.py]
env:     { CRAWL4AI_URL: "http://localhost:11235" }   # default — omit if unchanged
```

**local_searxng** (already registered — no change needed)
```
env:     { SEARXNG_URL: "http://localhost:8080/search" }   # default
```

---

## 4. Crawl4AI Container

Built from the cloned repo at `lms_mcp/crawl4ai/` with `INSTALL_TYPE=all` (includes
torch, transformers, and Chromium). Running on port 11235.

```bash
# Check health
curl http://localhost:11235/health
# Expected: {"status":"ok","version":"0.8.6",...}

# Start after reboot (from the repo directory)
cd /home/nero/Documents/Estudos/07-tools-and-infrastructure/lms_mcp/crawl4ai
docker compose up -d

# Stop
docker compose down

# View logs
docker compose logs -f crawl4ai

# The container was built with:
INSTALL_TYPE=all docker compose up --build -d
# Rebuild only needed if you update the Crawl4AI repo or Dockerfile.
```

The `page_scrape` tools call Crawl4AI at `http://localhost:11235`. If the container
is not running, those two tools fail with a connection error — the other tools in
`page_scrape` (trafilatura-based) are unaffected.

---

## 5. How to Use the Search System

### Rule 1: Always call the router first

```
search_router_get_routing(intent, query, ...optional params...)
  → JSON routing config
  → execute the steps in the config
```

Never call a search tool directly without the router config. The router encodes
engine selection, parameter tuning, fallback conditions, and cost tradeoffs.

### Rule 2: One intent per call

Classify the query into exactly one intent before calling the router.

| Intent | When to use |
|---|---|
| `study` | Building study notes, flashcards, summaries, course materials |
| `verification` | Fact-checking a specific claim or assertion |
| `price` | Brazilian product prices, comparisons, e-commerce |
| `concept` | Explaining what something is or how it works |
| `tool_development` | Finding libraries, reading API docs, debugging errors |
| `documentation` | Reading specific technical or regulatory documentation pages |

### Rule 3: Follow the step order in the config

The config JSON has named steps (`step_1_primary`, `step_2_fallback`, etc.) and
`fallback_trigger` conditions. Execute step 1, evaluate it against the trigger
condition, then decide whether to run step 2.

Steps whose value is `null` must be skipped entirely.

### Rule 4: Parallel calls only where the config says so

Fields named `call_in_parallel_with` mark steps that SHOULD be called simultaneously.
All other steps are sequential: evaluate before proceeding.

---

## 6. Intent Playbooks

### study — Exa primary, Brave fallback

```
1. Call search_router_get_routing(intent="study", query=..., topic_age_months=...)
2. Call exa_search_query with params from step_1_primary
   - Set highlightQuery to the core noun phrase (not the full question)
3. If fallback_trigger conditions are met → call brave_search_web (step_2_fallback)
4. For any result URL you want to read fully → page_scrape_crawl4ai_fetch(url, use_cache=True)
```

Fallback triggers: fewer than 3 Exa results, highlights all under 80 chars, or topic
is less than 6 months old (may not be deeply indexed yet).

---

### verification — Brave web + news in parallel

```
1. Call search_router_get_routing(intent="verification", query=..., is_scientific=...)
2. Call brave_search_web (step_1a) AND brave_search_news (step_1b) simultaneously
3. Only if is_scientific=True → call exa_search_query (step_2_exa_supplement)
   with highlight_query set to the exact claim being verified
4. Cross-reference timestamps from news results with web snippet dates
```

Never verify using Exa alone — Brave's news endpoint gives publication timestamps
needed to establish the timeline of a claim.

---

### price — Brave only, two parallel calls, Brazil-targeted

```
1. Call search_router_get_routing(intent="price", query=...)
2. Call brave_search_web (step_1a_brave_general) AND brave_search_web (step_1b_brave_buscape)
   simultaneously — the router returns different params for each
3. Parse extra_snippets aggressively — they often contain structured price data
4. Do NOT call Exa for prices — its index does not cover e-commerce product pages
```

---

### concept — Exa primary with question-form highlights

```
1. Call search_router_get_routing(intent="concept", query=..., domain_category=...)
   domain_category: "computing" | "scientific" | "general"
2. Call exa_search_query with params from step_1_primary
   - Set highlightQuery in question form: "what is X how does it work"
3. If fallback → brave_search_web (step_2_fallback)
4. For any result URL → page_scrape_crawl4ai_fetch for full text
```

---

### tool_development — SearXNG first, Brave fallback, Exa tertiary

```
1. Call search_router_get_routing(intent="tool_development", query=..., need_full_text=...)
2. Clean the query first: strip paths, memory addresses, timestamps, UUIDs, hex values.
   Add library name + version if it's an error message query.
3. Call searxng_web_search(query, categories="it", num_results=10)
4. If fewer than 3 relevant results → call brave_search_web (step_2_fallback)
5. If need_full_text=True AND official docs domain is known →
   call exa_search_query(type="keyword", includeDomains=[single docs domain])
   Use keyword (not neural) — library names are exact identifiers.
```

SearXNG aggregates GitHub, Stack Overflow, and technical sources with no API cost.
Use it as the first call always for this intent.

---

### documentation — Exa + findSimilar + Crawl4AI

**Known URL path (fastest):**
```
1. Call search_router_get_routing(intent="documentation", query=..., known_url=<url>)
2. Router returns a Crawl4AI config directly — call page_scrape_crawl4ai_fetch(url, use_cache=True)
3. No search needed.
```

**Unknown URL path:**
```
1. Call search_router_get_routing(intent="documentation", query=...,
   extra_domains=[...], is_br_regulatory=..., need_full_text=...)
2. Call exa_search_query (step_1) — set highlightQuery to the specific doc aspect,
   not the product name (e.g. "error handling" not "FastAPI")
3. Take the most relevant result URL → call exa_find_similar(url) (step_2)
4. For multiple sub-pages of a docs site:
   - Call brave_search_web with "topic site:docs.example.com" to find sub-page URLs
   - Pass collected URLs to page_scrape_crawl4ai_fetch_many(urls, use_cache=True)
```

For Brazilian regulatory content (Diário Oficial, planalto.gov.br, receita.gov.br):
set `is_br_regulatory=True` — the router adds the relevant gov.br domains automatically.

---

## 7. Tool Quick Reference

### search_router_get_routing

```python
search_router_get_routing(
    intent,              # "study"|"verification"|"price"|"concept"|"tool_development"|"documentation"
    query,               # the search query string
    topic_age_months,    # int or None — approximate age of the topic
    is_scientific,       # bool — triggers Exa academic supplement in verification
    domain_category,     # "computing"|"scientific"|"general" — for concept intent only
    known_url,           # str or None — for documentation: skip search if URL known
    need_full_text,      # bool — enables full-text extraction in tool_dev / documentation
    extra_domains,       # list[str] or None — additional domains for documentation intent
    is_br_regulatory,    # bool — adds gov.br domains for documentation intent
)
# Returns: JSON string
```

---

### brave_search_web

```python
brave_search_web(
    query,           # search string
    count,           # 1–20 (default 5)
    country,         # "US"|"BR"|"GB"|... (default "US")
    search_lang,     # "en"|"pt"|"es"|... (default "en")
    freshness,       # "pd"=24h, "pw"=week, "pm"=month, "py"=year, or None
    extra_snippets,  # bool — 5 extra passage snippets per result (default False)
    goggles_id,      # raw URL of a .goggle file, or None
)
```

---

### brave_search_news

```python
brave_search_news(
    query,
    count,           # 1–20
    country,
    search_lang,
    freshness,       # same as web
    extra_snippets,
)
```

---

### exa_search_query

```python
exa_search_query(
    query,
    search_type,           # "neural" (default) | "keyword"
    num_results,           # 1–10 (default 5)
    include_domains,       # list[str] or None — restrict to these domains
    exclude_domains,       # list[str] or None
    start_published_date,  # "YYYY-MM-DD" or None
    end_published_date,    # "YYYY-MM-DD" or None
    include_text,          # bool — full page text, high cost (default False)
    include_highlights,    # bool — passage excerpts (default True)
    highlight_query,       # str — override query for highlight selection
    num_sentences,         # 1–5 sentences per passage (default 3)
    highlights_per_result, # 1–5 passages per result (default 3)
)
```

`include_domains` and `exclude_domains` are mutually exclusive in practice.
`include_text=True` returns full page content — prefer Crawl4AI when you already
have the URL, as it is cheaper and handles JavaScript-rendered pages.

---

### exa_find_similar

```python
exa_find_similar(
    url,                   # reference URL to find similar pages for
    num_results,           # 1–10
    include_domains,
    exclude_domains,
    include_text,
    include_highlights,
    highlight_query,
    num_sentences,
    highlights_per_result,
)
```

Results may include the source URL itself — deduplicate before presenting.

---

### page_scrape_crawl4ai_fetch

```python
page_scrape_crawl4ai_fetch(
    url,        # single URL to fetch
    use_cache,  # bool — True to reuse cached result within the same session (default False)
)
# Returns: fit_markdown (cleaned page content, noise-filtered)
```

Handles JavaScript-rendered pages (React, Vue, VitePress). Requires Crawl4AI
container running at `http://localhost:11235`.

---

### page_scrape_crawl4ai_fetch_many

```python
page_scrape_crawl4ai_fetch_many(
    urls,       # list[str] — 1 to 10 URLs
    use_cache,  # bool — True recommended for second reads (default True)
)
# Returns: one section per URL, labelled with the URL.
# Failed URLs are inline — batch continues regardless of individual failures.
```

Fetches up to 10 URLs in parallel (max 5 concurrent workers). Result order
matches input order.

---

## 8. Cost Notes

| Engine | Cost |
|---|---|
| SearXNG | Free (local) |
| Crawl4AI | Free (self-hosted Docker) |
| Brave Search | $5 per 1,000 queries. Web and news counted separately. |
| Exa — highlights only | Lower cost tier |
| Exa — `include_text=True` | Higher cost — use Crawl4AI instead when URL is known |
| Exa — `findSimilar` | Same cost as a neural search call |

Prefer highlights over full text on Exa. Use Crawl4AI for full-page reads after
a search has identified the URL.

---

## 9. Optional: Goggles

Goggles re-rank Brave results to prefer specific domains. They are currently
disabled (no URLs configured). To activate:

1. Host a `.goggle` file at a public URL (GitHub Gist raw URL works).
2. Open `lms_mcp/search_router/main.py`, find the `_GOGGLES` dict near the top.
3. Set the matching key to the raw URL string.
4. The router will include `goggles_id` in Brave params automatically for that intent.

Goggle file templates are at `/tmp/study.goggle`, `/tmp/verification.goggle`,
`/tmp/price.goggle`, `/tmp/tool_dev.goggle` (written during setup — recreate from
`SEARCH_ROUTING_PLAN.md` if tmp was cleared).

---

## 10. Post-Registration Test Checklist

Run these after wiring up the servers to confirm everything works end-to-end.

```bash
# 1. Crawl4AI health
curl http://localhost:11235/health

# 2. SearXNG health
curl "http://localhost:8080/search?q=test&format=json" | python3 -c "import sys,json; d=json.load(sys.stdin); print('OK, results:', len(d.get('results',[])))"

# 3. smoke-test each server directly
cd lms_mcp/search_router && uv run python -c "import main; print('router OK')"
cd lms_mcp/brave_search  && BRAVE_API_KEY=<your_brave_api_key> uv run python -c "import main; print('brave OK')"
cd lms_mcp/exa_search    && EXA_API_KEY=<your_exa_api_key> uv run python -c "import main; print('exa OK')"
```

For MCP-level testing, use the router to generate a config and then execute it:

```
1. Call search_router_get_routing(intent="concept", query="what is a Bloom filter", domain_category="computing")
2. Execute step_1_primary with exa_search_query
3. Confirm results come back with titles and highlights
```
