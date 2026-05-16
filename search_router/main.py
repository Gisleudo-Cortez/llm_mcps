"""
Search Router MCP Server — deterministic per-intent routing for the
Brave + Exa + SearXNG + Crawl4AI search system.

Provides a single tool: `search_router_get_routing`
Call it with an intent and query BEFORE calling any search tool.
It returns a JSON routing config that specifies which engine to call,
with exactly what parameters, and when to trigger fallback.

Engine tool names this router references (implement as separate MCP servers):
  brave_search_web(q, count, extra_snippets, freshness, country,
                   search_lang, goggles_id) → /res/v1/web/search
  brave_search_news(q, count, freshness)     → /res/v1/news/search
  exa_search_query(query, type, numResults, includeDomains,
                   contents_highlights, contents_text, highlightQuery,
                   startPublishedDate)        → /search
  exa_find_similar(url, numResults, contents_text) → /findSimilar
  searxng_web_search(query, categories, num_results, time_range, language)
  page_scrape_crawl4ai_fetch(url, use_cache)
  page_scrape_crawl4ai_fetch_many(urls, use_cache)
"""

import json
from typing import Literal, Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("search_router")

# ── Goggle URLs ─────────────────────────────────────────────────────────────
# After hosting each .goggle file as a public GitHub Gist, replace the
# PENDING strings with the raw file URL (e.g. https://gist.githubusercontent.com/...)
_GOGGLES: dict[str, str] = {
    "study": "",
    "verification": "",
    "price": "",
    "tool_development": "",
}


def _goggle(key: str) -> dict:
    """Return {"goggles_id": url} only when the URL has been configured."""
    url = _GOGGLES.get(key, "")
    return {"goggles_id": url} if url.startswith("http") else {}

# ── Static domain lists ──────────────────────────────────────────────────────
_STUDY_DOMAINS = [
    "wikipedia.org", "pt.wikipedia.org", "britannica.com",
    "khanacademy.org", "arxiv.org", "scielo.br",
    "capes.gov.br", "plato.stanford.edu", "ncbi.nlm.nih.gov", "jstor.org",
]

_CONCEPT_DOMAINS: dict[str, list[str]] = {
    "computing": [
        "developer.mozilla.org", "docs.python.org",
        "en.cppreference.com", "wikipedia.org",
    ],
    "scientific": ["wikipedia.org", "britannica.com", "ncbi.nlm.nih.gov"],
    "general": ["wikipedia.org", "britannica.com", "plato.stanford.edu"],
}

_EXA_ACADEMIC_DOMAINS = [
    "arxiv.org", "pubmed.ncbi.nlm.nih.gov", "doi.org",
    "scielo.br", "nature.com", "science.org",
]

_DOC_BASE_DOMAINS = [
    "ietf.org", "w3.org", "developer.mozilla.org", "docs.python.org",
]

_BR_GOV_DOC_DOMAINS = [
    "planalto.gov.br", "receita.fazenda.gov.br", "gov.br", "in.gov.br",
]


# ── Routing builders ─────────────────────────────────────────────────────────

def _route_study(query: str, topic_age_months: Optional[int]) -> dict:
    recent = topic_age_months is not None and topic_age_months < 6
    config: dict = {
        "intent": "study",
        "strategy": "exa_primary_brave_fallback",
        "step_1_primary": {
            "engine": "exa",
            "tool": "exa_search_query",
            "params": {
                "query": query,
                "type": "neural",
                "numResults": 7,
                "includeDomains": _STUDY_DOMAINS,
                "contents_highlights": True,
                "contents_text": False,
                "highlightQuery": query,
            },
        },
        "fallback_trigger": (
            "Trigger fallback if ANY of: "
            "(a) exa results array shorter than 3 items, "
            "(b) all highlights fields are empty or under 80 characters, "
            f"(c) topic is recent (age < 6 months) — detected: {recent}"
        ),
        "step_2_fallback": {
            "engine": "brave",
            "tool": "brave_search_web",
            "params": {
                "q": query,
                "count": 10,
                "extra_snippets": True,
                **_goggle("study"),
            },
        },
        "url_extraction": {
            "tool": "page_scrape_crawl4ai_fetch",
            "note": "Use for any result URL you want to read in full. Set use_cache=True within the same session.",
        },
        "cost_note": "Exa: highlights-only (no contents_text) keeps cost low. Brave: $5/1k.",
    }
    return config


def _route_verification(
    query: str, is_scientific: bool, topic_age_months: Optional[int]
) -> dict:
    freshness = "pw" if (topic_age_months is not None and topic_age_months < 1) else None
    config: dict = {
        "intent": "verification",
        "strategy": "brave_web_plus_news_parallel_exa_supplement_if_academic",
        "step_1a_brave_web": {
            "engine": "brave",
            "tool": "brave_search_web",
            "call_in_parallel_with": "step_1b_brave_news",
            "params": {
                "q": f"{query} source",
                "count": 10,
                "extra_snippets": True,
                **_goggle("verification"),
                **({"freshness": freshness} if freshness else {}),
            },
        },
        "step_1b_brave_news": {
            "engine": "brave",
            "tool": "brave_search_news",
            "call_in_parallel_with": "step_1a_brave_web",
            "params": {
                "q": query,
                "count": 10,
                "freshness": "pm",
            },
        },
        "exa_supplement_trigger": (
            f"Call exa supplement ONLY if is_scientific=True — detected: {is_scientific}"
        ),
        "step_2_exa_supplement": {
            "engine": "exa",
            "tool": "exa_search_query",
            "params": {
                "query": query,
                "type": "neural",
                "numResults": 5,
                "includeDomains": _EXA_ACADEMIC_DOMAINS,
                "contents_highlights": True,
                "contents_text": False,
                "highlightQuery": query,
            },
        } if is_scientific else None,
        "synthesis_note": (
            "Cross-reference Brave web, Brave news timestamps, and (if present) "
            "Exa academic results to build a confidence picture. "
            "News results carry publication timestamps — use them for recency."
        ),
    }
    return config


def _route_price(query: str) -> dict:
    year = "2026"
    return {
        "intent": "price",
        "strategy": "brave_only_two_parallel_calls",
        "do_not_call_exa": True,
        "step_1a_brave_general": {
            "engine": "brave",
            "tool": "brave_search_web",
            "call_in_parallel_with": "step_1b_brave_buscape",
            "params": {
                "q": f"{query} preço {year}",
                "country": "BR",
                "search_lang": "pt",
                "freshness": "pd",
                "count": 20,
                "extra_snippets": True,
                **_goggle("price"),
            },
        },
        "step_1b_brave_buscape": {
            "engine": "brave",
            "tool": "brave_search_web",
            "call_in_parallel_with": "step_1a_brave_general",
            "params": {
                "q": f"{query} site:buscape.com.br",
                "count": 10,
                "extra_snippets": True,
            },
        },
        "parsing_note": (
            "Parse extra_snippets fields aggressively — they often contain "
            "price metadata pulled from structured page markup. "
            "Do not call Exa; its index does not cover e-commerce product pages."
        ),
    }


def _route_concept(
    query: str,
    domain_category: str,
    topic_age_months: Optional[int],
) -> dict:
    domains = _CONCEPT_DOMAINS.get(domain_category, _CONCEPT_DOMAINS["general"])
    recent = topic_age_months is not None and topic_age_months < 3
    return {
        "intent": "concept",
        "strategy": "exa_primary_brave_fallback",
        "step_1_primary": {
            "engine": "exa",
            "tool": "exa_search_query",
            "params": {
                "query": query,
                "type": "neural",
                "numResults": 5,
                "includeDomains": domains,
                "contents_highlights": True,
                "contents_text": False,
                "highlightQuery": query,
            },
        },
        "domain_category_selected": domain_category,
        "available_domain_sets": _CONCEPT_DOMAINS,
        "fallback_trigger": (
            "Trigger fallback if ANY of: "
            "(a) topic is very recent or highly niche, "
            "(b) exa results < 3, "
            f"(c) all highlights < 100 characters — recent topic detected: {recent}"
        ),
        "step_2_fallback": {
            "engine": "brave",
            "tool": "brave_search_web",
            "params": {
                "q": query,
                "count": 5,
                "extra_snippets": True,
            },
        },
        "url_extraction": {
            "tool": "page_scrape_crawl4ai_fetch",
            "note": "Use for any result URL you want to read in full.",
        },
        "cost_note": "5 results highlights-only is sufficient — do not inflate numResults for concept intent.",
    }


def _route_tool_development(query: str, need_full_text: bool) -> dict:
    return {
        "intent": "tool_development",
        "strategy": "searxng_primary_brave_fallback_exa_tertiary",
        "step_1_primary": {
            "engine": "searxng",
            "tool": "searxng_web_search",
            "params": {
                "query": query,
                "categories": "it",
                "num_results": 10,
            },
            "query_preparation": (
                "Before passing: strip dynamic values (absolute paths, memory addresses, "
                "timestamps, UUIDs, hex addresses). Add library name + version if it's "
                "an error message query."
            ),
        },
        "fallback_trigger": "searxng returns fewer than 3 relevant results",
        "step_2_fallback": {
            "engine": "brave",
            "tool": "brave_search_web",
            "params": {
                "q": query,
                "count": 10,
                "extra_snippets": True,
                **_goggle("tool_development"),
            },
        },
        "exa_tertiary_trigger": (
            f"Call Exa tertiary ONLY when need_full_text=True AND you know the official "
            f"docs domain. Current need_full_text: {need_full_text}. "
            "If the official domain is unknown, use Brave first to discover it."
        ),
        "step_3_exa_tertiary": {
            "engine": "exa",
            "tool": "exa_search_query",
            "params": {
                "query": query,
                "type": "keyword",
                "numResults": 3,
                "includeDomains": [
                    "INSTRUCTION: restrict to the single official docs domain "
                    "(e.g. docs.python.org, docs.rs, axum.rs). "
                    "Discover it via Brave if unknown."
                ],
                "contents_highlights": False,
                "contents_text": True,
            },
        } if need_full_text else None,
        "type_note": (
            "Use type=keyword (not neural) for Exa on this intent — library and API "
            "names are precise identifiers that benefit from exact matching."
        ),
    }


def _route_documentation(
    query: str,
    known_url: Optional[str],
    need_full_text: bool,
    extra_domains: Optional[list[str]],
    is_br_regulatory: bool,
) -> dict:
    if known_url:
        return {
            "intent": "documentation",
            "strategy": "url_known_skip_search",
            "step_1_crawl4ai": {
                "tool": "page_scrape_crawl4ai_fetch",
                "params": {"url": known_url, "use_cache": True},
                "note": "URL is already known — skip search entirely.",
            },
        }

    base_domains = list(_DOC_BASE_DOMAINS)
    if is_br_regulatory:
        base_domains.extend(_BR_GOV_DOC_DOMAINS)
    if extra_domains:
        base_domains.extend(extra_domains)

    return {
        "intent": "documentation",
        "strategy": "exa_neural_plus_findSimilar_brave_discovery_crawl4ai_extraction",
        "step_1_exa_search": {
            "engine": "exa",
            "tool": "exa_search_query",
            "params": {
                "query": query,
                "type": "neural",
                "numResults": 7,
                "includeDomains": base_domains,
                "contents_highlights": True,
                "contents_text": need_full_text,
                "highlightQuery": query,
            },
        },
        "step_2_findSimilar": {
            "engine": "exa",
            "tool": "exa_find_similar",
            "trigger": "After step 1: if any result is highly relevant, call findSimilar on its URL",
            "params": {
                "url": "INSTRUCTION: use the URL of the most relevant result from step 1",
                "numResults": 5,
                "contents_text": True,
            },
        },
        "step_3_multi_page_option": {
            "description": (
                "When multiple sub-pages of a docs site need to be read: "
                "use Brave with site:<docs_domain> to discover relevant sub-pages, "
                "collect the URLs, then pass to page_scrape_crawl4ai_fetch_many."
            ),
            "brave_discovery": {
                "tool": "brave_search_web",
                "params": {
                    "q": (
                        "INSTRUCTION: format as '<topic> site:<docs_domain>' — "
                        "e.g., 'middleware site:docs.axum.rs'"
                    ),
                    "count": 10,
                },
            },
            "crawl4ai_extraction": {
                "tool": "page_scrape_crawl4ai_fetch_many",
                "params": {
                    "urls": "INSTRUCTION: pass URL list collected from Brave results",
                    "use_cache": True,
                },
            },
        },
        "domains_used": base_domains,
        "cost_note": (
            "contents_text=True on Exa costs more — use Crawl4AI on result URLs instead "
            "when you already have the URL and want the full page text."
        ),
    }


# ── Main tool ────────────────────────────────────────────────────────────────

@mcp.tool(
    name="search_router_get_routing",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def search_router_get_routing(
    intent: Literal[
        "study", "verification", "price", "concept",
        "tool_development", "documentation"
    ],
    query: str,
    topic_age_months: Optional[int] = None,
    is_scientific: bool = False,
    domain_category: Literal["computing", "scientific", "general"] = "general",
    known_url: Optional[str] = None,
    need_full_text: bool = False,
    extra_domains: Optional[list[str]] = None,
    is_br_regulatory: bool = False,
) -> str:
    """
    Return a routing config for one search query. Call this BEFORE any search tool.

    **TRIGGER CONDITION:** Call at the start of every search task. The returned JSON
    tells you which engine to call (Brave / Exa / SearXNG), with exactly what parameters,
    in what order, and when to trigger fallback or supplemental calls.
    Never call search tools without consulting this router first.

    **ENGINE OVERVIEW:**

    Brave — independent search index, no captcha risk, 669ms avg latency.
    Strengths: freshness filtering, Brazil/Portuguese targeting (country=BR,
    search_lang=pt), news endpoint with publication timestamps, Goggles for
    domain-level ranking control, e-commerce and price pages.
    Use for: price research, source verification, news, any query where recency
    and keyword precision matter.

    Exa — neural semantic search. Finds relevant documents even when query
    terminology differs from document terminology.
    Strengths: full-text extraction in one call (contents.text), precise passage
    extraction (contents.highlights + highlightQuery), domain restriction
    (includeDomains), findSimilar endpoint that expands research from a known URL.
    Use for: study content, concept explanations, documentation research,
    academic source supplementation.

    SearXNG — local metasearch engine (no API key required).
    Strengths: aggregates GitHub, Stack Overflow, and other technical sources in
    one call via categories=it. Zero cost.
    Use for: tool development queries as the primary engine.

    Crawl4AI — self-hosted Docker crawler with real Chromium.
    Strengths: JavaScript-rendered pages (React, Vue, VitePress docs), clean
    fit_markdown output, parallel multi-URL fetching, session cache.
    Use for: reading a specific URL in full after search returns it.
    Tools: page_scrape_crawl4ai_fetch (one URL), page_scrape_crawl4ai_fetch_many (batch).

    **SIMULTANEOUS CALL RULE:**
    Do NOT call Brave and Exa simultaneously by default.
    Call the primary engine first, evaluate results, then decide on fallback.
    EXCEPTION: verification intent — Brave web and Brave news ARE called in parallel
    (same engine, two endpoints). Exa supplements only if is_scientific=True.

    **INTENT GUIDE:**

    study         → Exa primary (educational domains, highlights), Brave fallback.
                    Use when creating study materials, flashcards, summaries, course notes.

    verification  → Brave web + news in parallel (Goggles boost gov/news sources).
                    Exa supplements only for scientific/academic claims.
                    Use when fact-checking a specific assertion.

    price         → Brave only (country=BR, freshness=pd, Price Goggle + Buscapé call).
                    Never call Exa for prices — its index does not cover e-commerce.

    concept       → Exa primary (highlightQuery in question form), Brave fallback.
                    Use when explaining a concept, definition, or mechanism.
                    Choose domain_category: 'computing', 'scientific', or 'general'.

    tool_development → SearXNG primary (categories=it), Brave fallback, Exa tertiary.
                    Use when looking for libraries, APIs, error messages, code examples.
                    Set need_full_text=True only when you need the full doc text.

    documentation → Exa + findSimilar, then Brave for sub-page discovery, Crawl4AI
                    for full-page extraction. If known_url is provided, skips search
                    and returns Crawl4AI config directly.
                    Set is_br_regulatory=True for gov.br / planalto.gov.br content.
                    Set extra_domains to append product-specific docs domains.

    **CONSTRAINT WARNING:**
    Goggles are optional. By default all goggle slots are empty and goggles_id is
    omitted from Brave params. To activate a goggle, host the .goggle file at a
    public URL and set the matching key in _GOGGLES inside search_router/main.py.

    **OUTPUT EXPECTATION:** JSON routing config with step-by-step call instructions,
    exact parameter values, fallback trigger conditions, and cost notes.
    Follow the steps in order. Where a step is null, skip it.

    Args:
        intent: One of the six search intents — see INTENT GUIDE above.
        query: The search query exactly as you would pass it to the engine.
        topic_age_months: Approximate age of the topic in months. Used to detect whether
            freshness matters (e.g., topic < 6 months may not be in Exa's index deeply).
            Pass None if unknown.
        is_scientific: True when the query involves scientific, medical, or academic claims.
            Triggers Exa academic supplement in verification intent.
        domain_category: For concept intent only. 'computing' for CS/programming concepts,
            'scientific' for biology/chemistry/physics, 'general' for everything else.
        known_url: For documentation intent: if you already have the specific URL to read,
            pass it here. Router returns a Crawl4AI config and skips search entirely.
        need_full_text: For tool_development and documentation intents: set True when you
            need the full document text (not just highlights). Triggers Exa contents_text=True
            or Crawl4AI, depending on intent. Costs more on Exa — prefer Crawl4AI when URL
            is known.
        extra_domains: For documentation intent: list of product/technology-specific docs
            domains to add to the base domain list (e.g. ['docs.axum.rs', 'docs.rs']).
        is_br_regulatory: For documentation intent: set True when researching Brazilian
            government or regulatory documentation. Adds planalto.gov.br, gov.br, etc.
    """
    dispatch = {
        "study": lambda: _route_study(query, topic_age_months),
        "verification": lambda: _route_verification(query, is_scientific, topic_age_months),
        "price": lambda: _route_price(query),
        "concept": lambda: _route_concept(query, domain_category, topic_age_months),
        "tool_development": lambda: _route_tool_development(query, need_full_text),
        "documentation": lambda: _route_documentation(
            query, known_url, need_full_text, extra_domains, is_br_regulatory
        ),
    }

    config = dispatch[intent]()
    return json.dumps(config, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run(transport="stdio")
