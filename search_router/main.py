"""
Search Router MCP Server — deterministic per-intent routing for the
Brave + Exa + SearXNG + Crawl4AI search system.

Provides a single tool: `search_router_get_routing`
Call it with an intent and query BEFORE calling any search tool.
It returns a JSON routing config that specifies which engine to call,
with exactly what parameters, and when to trigger fallback.

Engine tool names this router references (implement as separate MCP servers):
  brave_search_web(query, count, extra_snippets, freshness, country,
                   search_lang, goggles_id)
  brave_search_news(query, count, freshness, country, search_lang, extra_snippets)
  exa_search_query(query, search_type, num_results, include_domains,
                   exclude_domains, start_published_date, end_published_date,
                   include_text, include_highlights, highlight_query,
                   num_sentences, highlights_per_result)
  exa_find_similar(url, num_results, include_domains, exclude_domains,
                   include_text, include_highlights, highlight_query,
                   num_sentences, highlights_per_result)
  searxng_web_search(query, num_results, categories, time_range, language)
  page_scrape_crawl4ai_fetch(url, use_cache)
  page_scrape_crawl4ai_fetch_many(urls, use_cache)
"""

import json
import os
from typing import Literal, Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("search_router")

# When set (e.g. on the VPS, which has no SearXNG), the router never routes
# to SearXNG — tool_development becomes Brave-primary with Exa fallback.
_SEARXNG_DISABLED = os.getenv("SEARXNG_DISABLED", "").lower() in ("1", "true", "yes")

# ── Cache integration ────────────────────────────────────────────────────────

def _cache_section(
    query: str,
    category: str,
    document_kind: str,
    intent: str,
    extra_params: dict | None = None,
) -> dict:
    """
    Return the standard cache block injected at the top of every routing config.
    Agents must execute step_0 before calling any search engine.
    """
    params_dict = {"intent": intent, **(extra_params or {})}
    params_json = json.dumps(params_dict, sort_keys=True)
    return {
        "STEP_0__CACHE_LOOKUP": {
            "tool": "cache_lookup",
            "mandatory": "Call this BEFORE any search engine. On hit: skip ALL steps below. On miss: proceed.",
            "params": {
                "query": query,
                "category": category,
                "parameters": params_json,
            },
            "on_hit": (
                "Cache hit — use the returned `summary` and `full_content` directly. "
                "Skip ALL search engine steps below. Do not call Brave, Exa, or SearXNG."
            ),
            "on_miss": "Cache miss — proceed with the search steps below.",
        },
        "STEP_LAST__CACHE_STORE": {
            "tool": "cache_store",
            "mandatory": "After search completes successfully: store the result in cache for future reuse.",
            "params": {
                "query": query,
                "category": category,
                "document_kind": document_kind,
                "parameters": params_json,
            },
            "guidance": (
                "Pass the full concatenated search result text as `full_content`. "
                "Write a 2–3 sentence distillation as `summary`. "
                "Pass the primary result URL as `source_url`. "
                "Pass `api_cost_usd` and `latency_ms` if known — "
                "they improve eviction scoring."
            ),
        },
    }


# ── Goggle URLs ─────────────────────────────────────────────────────────────
# After hosting each .goggle file as a public GitHub Gist, replace the
# PENDING strings with the raw file URL (e.g. https://gist.githubusercontent.com/...)
_GOGGLES: dict[str, str] = {
    "study": "",
    "verification": "",
    "price": "",
    "concept": "",
    "tool_development": "",
    "documentation": "",
    "store_discovery": "",
    "reputation": "",
    "recommendation": "",
}


def _goggle(key: str) -> dict:
    """Return {"goggles_id": url} only when the URL has been configured."""
    url = _GOGGLES.get(key, "")
    return {"goggles_id": url} if url.startswith("http") else {}

# ── Static domain lists ──────────────────────────────────────────────────────
_STUDY_DOMAINS = [
    "arxiv.org",
    "paperswithcode.com",
    "aclanthology.org",
    "openreview.net",
    "huggingface.co",
]

_CONCEPT_DOMAINS: dict[str, list[str]] = {
    "computing": [
        "arxiv.org",
        "developer.mozilla.org",
        "docs.python.org",
        "docs.rs",
        "huggingface.co",
        "github.com",
        "wikipedia.org",
    ],
    "scientific": ["arxiv.org", "wikipedia.org", "britannica.com", "ncbi.nlm.nih.gov", "nature.com"],
    "general": ["wikipedia.org", "britannica.com", "arxiv.org"],
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

def _route_store_discovery(query: str) -> dict:
    """Brave-only store discovery for the Brazilian market. No Exa — domain
    restrictions make it useless for store discovery. No SearXNG — too noisy
    for commercial intent."""
    return {
        "intent": "store_discovery",
        "cache": _cache_section(query, "web_search", "VERSIONED", "store_discovery"),
        "strategy": "brave_only_no_exa_no_searxng",
        "do_not_call_exa": True,
        "do_not_call_searxng": True,
        "step_1_brave_general": {
            "engine": "brave",
            "tool": "brave_search_web",
            "call_in_parallel_with": "step_2_brave_buscape",
            "params": {
                "query": query,
                "country": "BR",
                "search_lang": "pt-br",
                "count": 15,
                "freshness": "pm",
                "extra_snippets": True,
                **_goggle("store_discovery"),
            },
        },
        "step_2_brave_buscape": {
            "engine": "brave",
            "tool": "brave_search_web",
            "call_in_parallel_with": "step_1_brave_general",
            "params": {
                "query": f"{query} site:buscape.com.br",
                "count": 10,
                "extra_snippets": True,
            },
        },
        "url_extraction": {
            "tool": "page_scrape_crawl4ai_fetch",
            "note": "Use for any store URL you want to inspect in full.",
        },
        "cost_note": "Brave only — two parallel calls ($5/1k each). No Exa, no SearXNG.",
    }


def _route_reputation(query: str) -> dict:
    """Brave-only reputation check for Brazilian stores/companies. Two parallel
    calls: general web for forum discussions and Reclame Aqui for structured
    complaint data. No Exa — domain restrictions block commerce/consumer sites.
    No SearXNG — too noisy for trust signals."""
    return {
        "intent": "reputation",
        "cache": _cache_section(query, "web_search", "VERSIONED", "reputation"),
        "strategy": "brave_only_two_parallel_calls",
        "do_not_call_exa": True,
        "do_not_call_searxng": True,
        "step_1_brave_general": {
            "engine": "brave",
            "tool": "brave_search_web",
            "call_in_parallel_with": "step_2_brave_reclame_aqui",
            "params": {
                "query": query,
                "country": "BR",
                "search_lang": "pt-br",
                "count": 10,
                "freshness": "py",
                "extra_snippets": True,
                **_goggle("reputation"),
            },
        },
        "step_2_brave_reclame_aqui": {
            "engine": "brave",
            "tool": "brave_search_web",
            "call_in_parallel_with": "step_1_brave_general",
            "params": {
                "query": f"{query} site:reclameaqui.com.br",
                "count": 10,
                "extra_snippets": True,
            },
        },
        "guidance": (
            "The agent's query MUST include the company/website name plus "
            "'reclame aqui confiável review' or similar reputation keywords. "
            "Cross-reference Reclame Aqui score with forum complaints (Reddit, "
            "Ludopedia) for a complete trust picture. For small companies not on "
            "Reclame Aqui, the general search is the primary signal — an absent "
            "Reclame Aqui page is a data point, not a verdict."
        ),
        "cost_note": "Brave only — two parallel calls for reputation coverage.",
    }


def _route_recommendation(query: str) -> dict:
    """Brave-only product recommendation. Two parallel calls: general web for
    reviews/comparisons, Reddit for real-user experiences. No Exa — domain
    restrictions actively block review sites and forums. No SearXNG — too
    noisy for curated recommendations."""
    return {
        "intent": "recommendation",
        "cache": _cache_section(query, "web_search", "VERSIONED", "recommendation"),
        "strategy": "brave_only_two_parallel_calls",
        "do_not_call_exa": True,
        "do_not_call_searxng": True,
        "step_1_brave_general": {
            "engine": "brave",
            "tool": "brave_search_web",
            "call_in_parallel_with": "step_2_brave_reddit",
            "params": {
                "query": query,
                "count": 10,
                "freshness": "py",
                "extra_snippets": True,
                **_goggle("recommendation"),
            },
        },
        "step_2_brave_reddit": {
            "engine": "brave",
            "tool": "brave_search_web",
            "call_in_parallel_with": "step_1_brave_general",
            "params": {
                "query": f"{query} site:reddit.com",
                "count": 10,
                "extra_snippets": True,
            },
        },
        "guidance": (
            "This intent is for product selection — 'what X should I buy for Y?' "
            "NOT for 'what is X?' (use concept) or 'how much does X cost?' (use price). "
            "Query examples: 'best AWG enameled copper wire for PCB repair', "
            "'melhor filamento PLA para miniaturas impressão 3D'. "
            "The Reddit parallel surfaces real-user comparisons and long-term "
            "experiences that review sites miss."
        ),
        "cost_note": "Brave only — general + Reddit parallel ($5/1k each).",
    }


def _route_study(query: str, topic_age_months: Optional[int]) -> dict:
    recent = topic_age_months is not None and topic_age_months < 6
    config: dict = {
        "intent": "study",
        "cache": _cache_section(query, "research", "VERSIONED", "study"),
        "strategy": "exa_primary_brave_fallback",
        "step_1_primary": {
            "engine": "exa",
            "tool": "exa_search_query",
            "params": {
                "query": query,
                "search_type": "neural",
                "num_results": 7,
                "include_domains": _STUDY_DOMAINS,
                "include_highlights": True,
                "include_text": False,
                "highlight_query": query,
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
                "query": query,
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
        "cache": _cache_section(
            query, "news", "EVENT", "verification",
            {"freshness": freshness} if freshness else None,
        ),
        "strategy": "brave_web_plus_news_parallel_exa_supplement_if_academic",
        "step_1a_brave_web": {
            "engine": "brave",
            "tool": "brave_search_web",
            "call_in_parallel_with": "step_1b_brave_news",
            "params": {
                "query": f"{query} source",
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
                "query": query,
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
                "search_type": "neural",
                "num_results": 5,
                "include_domains": _EXA_ACADEMIC_DOMAINS,
                "include_highlights": True,
                "include_text": False,
                "highlight_query": query,
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
        "cache": _cache_section(query, "live", "EVENT", "price"),
        "strategy": "brave_only_three_parallel_calls",
        "do_not_call_exa": True,
        "step_1a_brave_general": {
            "engine": "brave",
            "tool": "brave_search_web",
            "call_in_parallel_with": ["step_1b_brave_mercadolivre", "step_1c_brave_buscape"],
            "params": {
                "query": f"{query} preço {year}",
                "country": "BR",
                "search_lang": "pt-br",
                "freshness": "pd",
                "count": 20,
                "extra_snippets": True,
                **_goggle("price"),
            },
        },
        "step_1b_brave_mercadolivre": {
            "engine": "brave",
            "tool": "brave_search_web",
            "call_in_parallel_with": ["step_1a_brave_general", "step_1c_brave_buscape"],
            "params": {
                "query": f"{query} site:mercadolivre.com.br",
                "country": "BR",
                "search_lang": "pt-br",
                "count": 15,
                "extra_snippets": True,
            },
            "note": (
                "Mercado Livre is the dominant BR marketplace. Their pages block crawlers, "
                "but Brave's index surfaces listings via search snippets. Parse "
                "extra_snippets aggressively — they often carry price + seller metadata."
            ),
        },
        "step_1c_brave_buscape": {
            "engine": "brave",
            "tool": "brave_search_web",
            "call_in_parallel_with": ["step_1a_brave_general", "step_1b_brave_mercadolivre"],
            "params": {
                "query": f"{query} site:buscape.com.br",
                "count": 10,
                "extra_snippets": True,
            },
        },
        "parsing_note": (
            "Three parallel calls — execute all simultaneously. "
            "Mercado Livre is the primary BR price signal (dominant marketplace). "
            "Buscapé adds retail price comparison. "
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
    # Computing concepts are technical docs; scientific/general are reference-grade
    cache_category = "docs_technical" if domain_category == "computing" else "reference"
    return {
        "intent": "concept",
        "cache": _cache_section(
            query, cache_category, "VERSIONED", "concept",
            {"domain_category": domain_category},
        ),
        "strategy": "exa_primary_brave_fallback",
        "step_1_primary": {
            "engine": "exa",
            "tool": "exa_search_query",
            "params": {
                "query": query,
                "search_type": "neural",
                "num_results": 5,
                "include_domains": domains,
                "include_highlights": True,
                "include_text": False,
                "highlight_query": query,
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
                "query": query,
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
    if _SEARXNG_DISABLED:
        return {
            "intent": "tool_development",
            "cache": _cache_section(query, "docs_technical", "VERSIONED", "tool_development"),
            "strategy": "brave_primary_exa_fallback",
            "step_1_primary": {
                "engine": "brave",
                "tool": "brave_search_web",
                "params": {
                    "query": query,
                    "count": 10,
                    "extra_snippets": True,
                    **_goggle("tool_development"),
                },
            },
            "query_preparation": (
                "Before passing: strip dynamic values (absolute paths, memory addresses, "
                "timestamps, UUIDs, hex addresses). Add library name + version if it's "
                "an error message query."
            ),
            "fallback_trigger": "brave returns fewer than 3 relevant results",
            "step_2_fallback": {
                "engine": "exa",
                "tool": "exa_search_query",
                "params": {
                    "query": query,
                    "search_type": "keyword",
                    "num_results": 5,
                    "include_highlights": True,
                    "include_text": False,
                },
            },
            "url_extraction": {
                "tool": "page_scrape_crawl4ai_fetch",
                "note": "Use for any result URL you want to read in full.",
            },
            "cost_note": "Brave primary (SearXNG disabled on this host).",
        }
    return {
        "intent": "tool_development",
        "cache": _cache_section(query, "docs_technical", "VERSIONED", "tool_development"),
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
                "query": query,
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
                "search_type": "keyword",
                "num_results": 3,
                "include_domains": [
                    "INSTRUCTION: restrict to the single official docs domain "
                    "(e.g. docs.python.org, docs.rs, axum.rs). "
                    "Discover it via Brave if unknown."
                ],
                "include_highlights": False,
                "include_text": True,
            },
        } if need_full_text else None,
        "type_note": (
            "Use search_type=keyword (not neural) for Exa on this intent — library and API "
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
    _STATIC_DOC_DOMAINS = {"ietf.org", "w3.org", "rfc-editor.org"}

    if known_url:
        # Can't inspect domains when URL is known; treat conservatively
        extra_set = set(extra_domains or [])
        doc_kind = "STATIC" if (extra_set & _STATIC_DOC_DOMAINS) else "VERSIONED"
        cache_cat = "static" if doc_kind == "STATIC" else "docs_technical"
        return {
            "intent": "documentation",
            "cache": _cache_section(
                query, cache_cat, doc_kind, "documentation",
                {"known_url": known_url},
            ),
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

    # Only treat as STATIC when the caller *explicitly* targets a standards body
    # via extra_domains. base_domains includes ietf.org/w3.org as general discovery
    # domains, not as a signal that the query is about a specific standard.
    explicit_set = set(extra_domains or [])
    doc_kind = "STATIC" if (explicit_set & _STATIC_DOC_DOMAINS) else "VERSIONED"
    cache_cat = "static" if doc_kind == "STATIC" else "docs_technical"

    return {
        "intent": "documentation",
        "cache": _cache_section(query, cache_cat, doc_kind, "documentation"),
        "strategy": "exa_neural_plus_findSimilar_brave_discovery_crawl4ai_extraction",
        "step_1_exa_search": {
            "engine": "exa",
            "tool": "exa_search_query",
            "params": {
                "query": query,
                "search_type": "neural",
                "num_results": 7,
                "include_domains": base_domains,
                "include_highlights": True,
                "include_text": need_full_text,
                "highlight_query": query,
            },
        },
        "step_2_findSimilar": {
            "engine": "exa",
            "tool": "exa_find_similar",
            "trigger": "After step 1: if any result is highly relevant, call findSimilar on its URL",
            "params": {
                "url": "INSTRUCTION: use the URL of the most relevant result from step 1",
                "num_results": 5,
                "include_text": True,
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
                    "query": (
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
        "tool_development", "documentation", "store_discovery",
        "reputation", "recommendation",
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

    **CACHE INTEGRATION:** Every routing config now includes a `cache` block with
    two mandatory steps:
    - `step_0_cache_lookup` — call `cache_lookup` BEFORE any search engine call.
      On hit: use cached content, skip all search steps.
      On miss: proceed with the search steps.
    - `step_last_cache_store` — after a successful search, call `cache_store`
      with the result to populate the cache for future queries.
    The `cache` block pre-fills the correct `category` and `document_kind` for
    each intent (e.g., `price` → `live`, documentation on IETF/W3C → `static`).

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

    store_discovery → Brave only (country=BR, search_lang=pt-br, freshness=pm,
                       Buscapé parallel). No Exa — domain restrictions block commerce.
                       No SearXNG — too noisy for store discovery.
                       Use when finding Brazilian stores in a specific niche.

    reputation     → Brave only (country=BR, search_lang=pt-br, freshness=py,
                       Reclame Aqui parallel). No Exa, no SearXNG.
                       Use when checking if a store/company is trustworthy.
                       Query must include company name + 'reclame aqui confiável'.

    recommendation → Brave only (freshness=py, Reddit parallel). No Exa, no SearXNG.
                       Use for product selection — 'what X should I buy for Y?'
                       NOT for 'what is X?' (concept) or 'how much?' (price).

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
        "store_discovery": lambda: _route_store_discovery(query),
        "reputation": lambda: _route_reputation(query),
        "recommendation": lambda: _route_recommendation(query),
    }

    config = dispatch[intent]()
    return json.dumps(config, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run(transport="stdio")
