import os
from typing import Optional

import requests
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("brave_search")

BRAVE_API_KEY = os.getenv("BRAVE_API_KEY", "")
_BASE_URL = "https://api.search.brave.com/res/v1"

_session = requests.Session()


def _headers() -> dict:
    if not BRAVE_API_KEY:
        raise RuntimeError(
            "BRAVE_API_KEY environment variable is not set. "
            "Obtain a key at https://brave.com/search/api/ and export it."
        )
    return {
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": BRAVE_API_KEY,
    }


def _get(path: str, params: dict) -> dict:
    resp = _session.get(
        f"{_BASE_URL}{path}", headers=_headers(), params=params, timeout=15
    )
    resp.raise_for_status()
    return resp.json()


@mcp.tool(
    name="brave_search_web",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
def brave_search_web(
    query: str,
    count: int = 5,
    country: str = "US",
    search_lang: str = "en",
    freshness: Optional[str] = None,
    extra_snippets: bool = False,
    goggles_id: Optional[str] = None,
) -> str:
    """
    Search the web using the Brave Search API — independent index, no Google/Bing dependency.

    **TRIGGER CONDITION:** Use for general web search when you need current, unbiased results
    from an independent search index. Prefer over SearXNG when you need Goggle-filtered results
    (domain ranking control), structured snippets, or country-specific searches.

    **SEQUENCE GUIDANCE:** Call `search_router_get_routing` first to get the recommended params
    (goggles_id, freshness, count) for the current intent. Pass them directly to this tool.
    For verification intent: call this AND `brave_search_news` in parallel with the same query.
    For tool_development intent: call `searxng_web_search` first; use this as a fallback.
    For price intent (BR): set country="BR", search_lang="pt-br".

    **CONSTRAINT WARNING:** Requires BRAVE_API_KEY env var. Max count=20 per call. The
    `goggles_id` must be a raw URL to a hosted Goggle file (GitHub Gist raw URL). Rate limits
    apply per subscription tier.

    **OUTPUT EXPECTATION:** Returns results with title, URL, snippet, and optional extra snippets.
    Results are from Brave's independent index — may differ from Google/Bing for technical queries.
    Empty results mean the query returned nothing; try broader terms.

    Args:
        query: Search query. Use specific terms; quote exact phrases with double-quotes.
        count: Number of results (1–20).
        country: Two-letter country code for regional results (e.g., "US", "BR", "GB").
        search_lang: Language code for results (e.g., "en", "pt-br", "es").
        freshness: Recency filter — "pd" (24h), "pw" (week), "pm" (month), "py" (year),
                   or "YYYY-MM-DDtoYYYY-MM-DD" for a custom date range.
        extra_snippets: Return up to 5 extra passage snippets per result (more context, more tokens).
        goggles_id: Raw URL of a Goggle file to re-rank results by domain priority.
    """
    params: dict = {
        "q": query,
        "count": max(1, min(count, 20)),
        "country": country,
        "search_lang": search_lang,
        "extra_snippets": str(extra_snippets).lower(),
    }
    if freshness:
        params["freshness"] = freshness
    if goggles_id:
        params["goggles_id"] = goggles_id

    try:
        data = _get("/web/search", params)
    except requests.exceptions.HTTPError as e:
        return f"Error: Brave API returned HTTP {e.response.status_code} — {e.response.text[:300]}"
    except requests.exceptions.Timeout:
        return "Error: Brave Search request timed out after 15 seconds."
    except requests.exceptions.RequestException as e:
        return f"Error: {e}"
    except RuntimeError as e:
        return f"Configuration error: {e}"

    web_results = data.get("web", {}).get("results", [])
    if not web_results:
        return f"No web results found for query: '{query}'."

    out = []
    for r in web_results:
        title = r.get("title", "")
        url = r.get("url", "")
        desc = r.get("description", "")
        block = f"**{title}**\n{url}\n{desc}"
        extras = r.get("extra_snippets") or []
        if extras:
            block += "\n" + "\n".join(f"  • {s}" for s in extras)
        out.append(block)

    return "\n\n---\n\n".join(out)


@mcp.tool(
    name="brave_search_news",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
def brave_search_news(
    query: str,
    count: int = 5,
    country: str = "US",
    search_lang: str = "en",
    freshness: Optional[str] = None,
    extra_snippets: bool = False,
) -> str:
    """
    Search news articles using the Brave Search API news endpoint.

    **TRIGGER CONDITION:** Use for news, current events, recent announcements, vulnerability
    disclosures, product releases, or any query where recency and journalistic sourcing matter.
    Called in parallel with `brave_search_web` for verification intent queries.

    **SEQUENCE GUIDANCE:** For verification intent: call this and `brave_search_web` in parallel
    with the same query. Set `freshness` to the relevant time window. For price intent: not
    applicable — use `brave_search_web` only. For study/concept: not applicable.

    **CONSTRAINT WARNING:** Requires BRAVE_API_KEY env var. Max count=20. News index may have
    fewer results than web search for niche technical topics — empty results are expected for
    obscure queries; fall back to `brave_search_web` in that case.

    **OUTPUT EXPECTATION:** Returns news articles with title, source hostname, publication age,
    URL, and snippet. Age is shown as a relative string (e.g., "2 hours ago"). Articles are
    sorted by recency by default.

    Args:
        query: News search query. Include time-relevant terms or year for precision.
        count: Number of articles (1–20).
        country: Two-letter country code.
        search_lang: Language code (e.g., "en", "pt-br").
        freshness: Recency filter — "pd" (24h), "pw" (week), "pm" (month), "py" (year).
        extra_snippets: Return extra passage snippets per result.
    """
    params: dict = {
        "q": query,
        "count": max(1, min(count, 20)),
        "country": country,
        "search_lang": search_lang,
        "extra_snippets": str(extra_snippets).lower(),
    }
    if freshness:
        params["freshness"] = freshness

    try:
        data = _get("/news/search", params)
    except requests.exceptions.HTTPError as e:
        return f"Error: Brave API returned HTTP {e.response.status_code} — {e.response.text[:300]}"
    except requests.exceptions.Timeout:
        return "Error: Brave Search news request timed out after 15 seconds."
    except requests.exceptions.RequestException as e:
        return f"Error: {e}"
    except RuntimeError as e:
        return f"Configuration error: {e}"

    results = data.get("results", [])
    if not results:
        return f"No news results found for query: '{query}'."

    out = []
    for r in results:
        title = r.get("title", "")
        url = r.get("url", "")
        desc = r.get("description", "")
        source = (r.get("meta_url") or {}).get("hostname", "")
        age = r.get("age", "")
        header = f"**{title}**"
        if source:
            header += f" — {source}"
        if age:
            header += f" ({age})"
        block = f"{header}\n{url}\n{desc}"
        extras = r.get("extra_snippets") or []
        if extras:
            block += "\n" + "\n".join(f"  • {s}" for s in extras)
        out.append(block)

    return "\n\n---\n\n".join(out)


if __name__ == "__main__":
    mcp.run(transport="stdio")
