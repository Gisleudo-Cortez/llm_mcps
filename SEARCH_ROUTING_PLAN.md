# Search Routing System — Implementation Plan

## Overview

A two-engine search system (Brave + Exa) augmented by SearXNG (already live) and
Crawl4AI Docker (full-browser extraction). Routes six distinct intents to the right
engine with pre-tuned parameters. Agents use `search_router_get_routing` to get a
deterministic routing config before calling search tools.

---

## Component Status

| Component | Status | Notes |
|---|---|---|
| `local_searxng` MCP server | **Done** | Live, `categories: it` for tool dev |
| `page_scrape` — Crawl4AI tools | **Done** | Two new tools added: `crawl4ai_fetch`, `crawl4ai_fetch_many` |
| `search_router` MCP server | **Done** | Routing logic for all 6 intents |
| Crawl4AI Docker container | **Done** | Running on port 11235 (built with `INSTALL_TYPE=all` from cloned repo) |
| `brave_search` MCP server | **Done** | Created — awaiting `BRAVE_API_KEY` to test |
| `exa_search` MCP server | **Done** | Created — awaiting `EXA_API_KEY` to test |
| Goggles — Study | **Pending** | Host as GitHub Gist, update constant in `search_router/main.py` |
| Goggles — Verification | **Pending** | Same |
| Goggles — BR Price | **Pending** | Same |
| Goggles — Tool Dev | **Pending** | Same |
| API keys configured | **Pending** | `BRAVE_API_KEY`, `EXA_API_KEY` env vars |
| End-to-end test session | **Pending** | Test all 6 intents once keys are in |

---

## Setup Steps (in order)

### 1. Crawl4AI Docker

```bash
docker pull unclecode/crawl4ai:latest
docker run -d \
  --name crawl4ai \
  -p 11235:11235 \
  --restart unless-stopped \
  unclecode/crawl4ai:latest

# Verify
curl http://localhost:11235/health
```

Override URL via env var if you change the port: `CRAWL4AI_URL=http://localhost:11235`

### 2. Brave Search MCP server

Create `lms_mcp/brave_search/` following the FastMCP pattern.

Required env var: `BRAVE_API_KEY`

Tools to implement:
- `brave_search_web(q, count, extra_snippets, freshness, country, search_lang, goggles_id)` → calls `/res/v1/web/search`
- `brave_search_news(q, count, freshness)` → calls `/res/v1/news/search`

Cost: $5 / 1,000 queries (web and news counted separately).

### 3. Exa Search MCP server

Create `lms_mcp/exa_search/` following the FastMCP pattern.

Required env var: `EXA_API_KEY`

Tools to implement:
- `exa_search_query(query, type, numResults, includeDomains, contents_highlights, contents_text, highlightQuery, startPublishedDate)` → calls `/search`
- `exa_find_similar(url, numResults, contents_text)` → calls `/findSimilar`

Cost: varies by `contents.text` flag — omit for highlight-only calls to reduce cost.

### 4. Goggles

Create four GitHub Gists (one per file below) with public raw URLs.
After creating, update the `_GOGGLES` dict in `search_router/main.py`.

**Study Goggle** (`study.goggle`):
```
! name: Study content
! description: Boost authoritative educational sources
$boost=10,site=wikipedia.org
$boost=10,site=pt.wikipedia.org
$boost=8,site=britannica.com
$boost=8,site=khanacademy.org
$boost=8,site=arxiv.org
$boost=8,site=scielo.br
$boost=6,site=capes.gov.br
$boost=6,site=plato.stanford.edu
$boost=6,site=jstor.org
$discard,site=medium.com
$discard,site=substack.com
$discard,site=quora.com
$discard,site=reddit.com
$discard,site=pinterest.com
```

**Verification Goggle** (`verification.goggle`):
```
! name: Source verification
! description: Boost primary and authoritative news sources
$boost=10,site=gov.br
$boost=10,site=planalto.gov.br
$boost=8,site=agenciabrasil.ebc.com.br
$boost=8,site=reuters.com
$boost=8,site=apnews.com
$boost=6,site=g1.globo.com
$boost=6,site=folha.uol.com.br
$boost=6,site=estadao.com.br
$boost=6,site=bbc.com
$discard,site=reddit.com
$discard,site=twitter.com
$discard,site=facebook.com
$discard,site=instagram.com
$discard,site=tiktok.com
```

**BR Price Goggle** (`price.goggle`):
```
! name: Brazilian prices
! description: Boost BR e-commerce and price comparison
$boost=10,site=buscape.com.br
$boost=10,site=zoom.com.br
$boost=9,site=mercadolivre.com.br
$boost=9,site=amazon.com.br
$boost=8,site=kabum.com.br
$boost=8,site=magazineluiza.com.br
$boost=8,site=americanas.com.br
$boost=7,site=shopee.com.br
$boost=7,site=casasbahia.com.br
$boost=7,site=extra.com.br
$discard,site=medium.com
$discard,site=reddit.com
$discard,site=tudocelular.com
```

**Tool Dev Goggle** (`tool_dev.goggle`):
```
! name: Tool development
! description: Boost official technical documentation and code resources
$boost=10,site=github.com
$boost=10,site=stackoverflow.com
$boost=9,site=developer.mozilla.org
$boost=9,site=docs.python.org
$boost=8,site=pypi.org
$boost=8,site=npmjs.com
$boost=8,site=docs.anthropic.com
$boost=6,site=readthedocs.io
$boost=6,site=pkg.go.dev
$discard,site=medium.com
$discard,site=dev.to
$discard,site=hashnode.com
```

### 5. API Keys

Add to your shell environment (or MCP server env config):

```bash
export BRAVE_API_KEY="..."
export EXA_API_KEY="..."
```

### 6. Register new servers in MCP config

Add entries for `brave_search` and `exa_search` (and optionally `search_router`)
to your MCP client config file, pointing to each server's venv Python and `main.py`.

---

## Intent → Engine Map (quick reference)

| Intent | Primary | Fallback | Notes |
|---|---|---|---|
| `study` | Exa neural | Brave + Study Goggle | highlightQuery = subject noun phrase |
| `verification` | Brave web + news (parallel) | Exa supplement if academic | Use Verification Goggle |
| `price` | Brave only | — | BR country, pd freshness, Price Goggle |
| `concept` | Exa neural | Brave | highlightQuery = "what is X how does it work" |
| `tool_development` | SearXNG `it` | Brave → Exa | Strip dynamic values before query |
| `documentation` | Exa neural + findSimilar | Brave site: | Crawl4AI for full-page extraction |

---

## Progress Log

### 2026-05-16
- Designed routing architecture (Brave + Exa + SearXNG + Crawl4AI)
- Implemented: `page_scrape` Crawl4AI tools (`crawl4ai_fetch`, `crawl4ai_fetch_many`)
- Implemented: `search_router` MCP server with full 6-intent routing logic
- Pending: Crawl4AI Docker setup, brave_search server, exa_search server, Goggles, API keys
