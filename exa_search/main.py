import os
from typing import Optional

import requests
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("exa_search")

EXA_API_KEY = os.getenv("EXA_API_KEY", "")
_BASE_URL = "https://api.exa.ai"

_session = requests.Session()


def _headers() -> dict:
    if not EXA_API_KEY:
        raise RuntimeError(
            "EXA_API_KEY environment variable is not set. "
            "Obtain a key at https://exa.ai/ and export it."
        )
    return {
        "Content-Type": "application/json",
        "x-api-key": EXA_API_KEY,
    }


def _post(path: str, body: dict) -> dict:
    resp = _session.post(
        f"{_BASE_URL}{path}", headers=_headers(), json=body, timeout=20
    )
    resp.raise_for_status()
    return resp.json()


def _format_results(results: list[dict]) -> str:
    out = []
    for r in results:
        title = r.get("title", "Untitled")
        url = r.get("url", "")
        published = r.get("publishedDate", "")
        date_str = f" [{published[:10]}]" if published else ""
        block = f"**{title}**{date_str}\n{url}"

        highlights = r.get("highlights") or []
        if highlights:
            block += "\n" + "\n".join(f"  › {h}" for h in highlights)

        text = r.get("text", "")
        if text:
            block += f"\n\n{text[:2000]}{'…' if len(text) > 2000 else ''}"

        out.append(block)
    return "\n\n---\n\n".join(out)


def _build_contents(
    include_highlights: bool,
    highlight_query: Optional[str],
    num_sentences: int,
    highlights_per_result: int,
    include_text: bool,
) -> dict:
    contents: dict = {}
    if include_highlights:
        hl: dict = {
            "numSentences": num_sentences,
            "highlightsPerUrl": highlights_per_result,
        }
        if highlight_query:
            hl["query"] = highlight_query
        contents["highlights"] = hl
    if include_text:
        contents["text"] = {"maxCharacters": 2000}
    return contents


@mcp.tool(
    name="exa_search_query",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
def exa_search_query(
    query: str,
    search_type: str = "auto",
    num_results: int = 5,
    include_domains: Optional[list[str]] = None,
    exclude_domains: Optional[list[str]] = None,
    start_published_date: Optional[str] = None,
    end_published_date: Optional[str] = None,
    include_text: bool = False,
    include_highlights: bool = True,
    highlight_query: Optional[str] = None,
    num_sentences: int = 3,
    highlights_per_result: int = 3,
    category: Optional[str] = None,
) -> str:
    """
    Search the web using Exa's neural semantic search or keyword search.

    **TRIGGER CONDITION:** Use for concept-level queries, academic/research content, documentation
    discovery, or any query where semantic understanding matters more than keyword matching.
    Neural mode finds pages that are *about* the concept even without the exact words.
    Keyword mode is faster and better for precise technical terms, package names, or version strings.

    **SEQUENCE GUIDANCE:** Call `search_router_get_routing` first to get recommended params
    (search_type, num_results, include_domains, highlight_query).
    - study intent → neural, 7 results, include_domains from study domain list.
    - concept intent → neural, 5 results, domain set by category (computing/scientific/general).
    - documentation intent → neural, then call `exa_find_similar` on top results, then Crawl4AI.
    - tool_development intent → keyword type, called after SearXNG and Brave as a third step.
    - verification intent (scientific) → neural supplement after Brave parallel calls.

    **SEARCH TYPES:**
    - auto (default, ~1s): Best general-purpose search, automatically selects optimal strategy
    - instant (~250ms): Fastest, for real-time apps like chat/voice
    - fast (~450ms): Speed with minimal quality sacrifice
    - neural: Semantic similarity search (original Exa behavior)
    - keyword: Exact-term matching for precise identifiers
    - deep-lite (~4s): Lightweight synthesized search with structured outputs
    - deep (~4-15s): Multi-step reasoning for complex queries with structured outputs
    - deep-reasoning (~12-40s): Highest-quality synthesized output for hardest research tasks

    **CONSTRAINT WARNING:** Requires EXA_API_KEY env var. Neural search costs more credits than
    keyword. `include_text=True` returns full page content — very high token cost; use only when
    highlights are insufficient. `include_domains` and `exclude_domains` are mutually exclusive
    in practice — do not combine them. Max 100 results for neural/deep search types.

    **OUTPUT EXPECTATION:** Returns results with title, URL, published date, and highlighted
    passage excerpts most relevant to the query. If `include_text=True`, full page text is
    appended (truncated at 2000 chars/result). Empty results for narrow domain lists are normal;
    broaden `include_domains` or remove it.

    Args:
        query: The search query. For neural mode, phrase as a natural language concept or
               question. For keyword mode, use exact technical terms.
        search_type: One of: auto, instant, fast, neural, keyword, deep-lite, deep, deep-reasoning.
        num_results: Number of results to return (1–100; higher costs more credits).
        include_domains: Restrict results to these domains (e.g., ["arxiv.org", "docs.python.org"]).
        exclude_domains: Exclude these domains from results.
        start_published_date: Only return pages published after this ISO date (e.g., "2024-01-01").
        end_published_date: Only return pages published before this ISO date.
        include_text: Return full page text content per result (very high token cost).
        include_highlights: Return highlighted passage excerpts (recommended; keep True).
        highlight_query: Override query used to select highlight passages — useful when the search
                         query is broad but you want highlights focused on a specific sub-topic.
        num_sentences: Sentences per highlight passage (1–5).
        highlights_per_result: Number of highlight passages per result (1–5).
        category: Optional content category filter: "company", "people", "news", "paper",
                  "tweet", "github", "blog", "pdf", etc.
    """
    body: dict = {
        "query": query,
        "type": search_type,
        "numResults": max(1, min(num_results, 10)),
    }
    if include_domains:
        body["includeDomains"] = include_domains
    if exclude_domains:
        body["excludeDomains"] = exclude_domains
    if start_published_date:
        body["startPublishedDate"] = start_published_date
    if end_published_date:
        body["endPublishedDate"] = end_published_date
    if category:
        body["category"] = category

    contents = _build_contents(
        include_highlights, highlight_query, num_sentences, highlights_per_result, include_text
    )
    if contents:
        body["contents"] = contents

    try:
        data = _post("/search", body)
    except requests.exceptions.HTTPError as e:
        return f"Error: Exa API returned HTTP {e.response.status_code} — {e.response.text[:300]}"
    except requests.exceptions.Timeout:
        return "Error: Exa search request timed out after 20 seconds."
    except requests.exceptions.RequestException as e:
        return f"Error: {e}"
    except RuntimeError as e:
        return f"Configuration error: {e}"

    results = data.get("results", [])
    if not results:
        return f"No results found for query: '{query}' (type={search_type})."

    return _format_results(results)


@mcp.tool(
    name="exa_find_similar",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
def exa_find_similar(
    url: str,
    num_results: int = 5,
    include_domains: Optional[list[str]] = None,
    exclude_domains: Optional[list[str]] = None,
    include_text: bool = False,
    include_highlights: bool = True,
    highlight_query: Optional[str] = None,
    num_sentences: int = 3,
    highlights_per_result: int = 3,
) -> str:
    """
    Find web pages semantically similar to a given URL using Exa.

    **TRIGGER CONDITION:** Use when you have a known-good reference URL and want to discover
    related content, alternative implementations, or additional documentation pages on the same topic.
    Ideal for the documentation intent after confirming a canonical starting page.

    **SEQUENCE GUIDANCE:** For documentation intent: first call `exa_search_query` (neural) or
    `page_scrape_crawl4ai_fetch` to identify the target URL, then call this to expand the result
    set with semantically related pages. Set `highlight_query` to the specific sub-topic you care
    about within the broader documentation domain.

    **CONSTRAINT WARNING:** Requires EXA_API_KEY env var. URL must be a real, public, indexed page.
    Private pages, localhost URLs, and very recently published pages may return poor results.
    Costs the same credits as a neural search call. Results may include the source URL itself —
    deduplicate before presenting to the user.

    **OUTPUT EXPECTATION:** Returns pages semantically similar to the given URL — same format as
    `exa_search_query` (title, URL, date, highlights). May overlap with the source page;
    filter duplicates at the agent level.

    Args:
        url: Reference URL to find similar pages for. Must be a real public URL.
        num_results: Number of similar pages to return (1–10).
        include_domains: Restrict results to specific domains.
        exclude_domains: Exclude specific domains from results.
        include_text: Return full page text per result (high token cost).
        include_highlights: Return highlighted excerpts (recommended).
        highlight_query: Query string used to select relevant highlight passages.
        num_sentences: Sentences per highlight passage (1–5).
        highlights_per_result: Highlight passages per result (1–5).
    """
    body: dict = {
        "url": url,
        "numResults": max(1, min(num_results, 10)),
    }
    if include_domains:
        body["includeDomains"] = include_domains
    if exclude_domains:
        body["excludeDomains"] = exclude_domains

    contents = _build_contents(
        include_highlights, highlight_query, num_sentences, highlights_per_result, include_text
    )
    if contents:
        body["contents"] = contents

    try:
        data = _post("/findSimilar", body)
    except requests.exceptions.HTTPError as e:
        return f"Error: Exa API returned HTTP {e.response.status_code} — {e.response.text[:300]}"
    except requests.exceptions.Timeout:
        return "Error: Exa findSimilar request timed out after 20 seconds."
    except requests.exceptions.RequestException as e:
        return f"Error: {e}"
    except RuntimeError as e:
        return f"Configuration error: {e}"

    results = data.get("results", [])
    if not results:
        return f"No similar pages found for URL: '{url}'."

    return _format_results(results)


if __name__ == "__main__":
    mcp.run(transport="stdio")
