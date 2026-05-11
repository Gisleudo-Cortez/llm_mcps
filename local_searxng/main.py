import os
from typing import Literal

import requests
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("searxng_mcp")
SEARXNG_URL = os.getenv("SEARXNG_URL", "http://localhost:8080/search")

session = requests.Session()
session.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"
    }
)

_NOISY_URL_PATTERNS = (
    # MDN Glossary pages are single-keyword stubs — same URL appears for any query
    # containing that keyword (e.g., every Python query → /Glossary/Python)
    "/docs/Glossary/",
    # MDN Web API reference pages match any word ending in "Report", "Event", etc.
    # regardless of query intent — produces false positives for security/CVE queries
    "developer.mozilla.org/en-US/docs/Web/API/",
    # Docker Hub official images (hub.docker.com/_/name) are identical for every
    # query about that technology regardless of the specific question
    "hub.docker.com/_/",
)


def _is_quality_result(r: dict) -> bool:
    url = r.get("url", "")
    return not any(pattern in url for pattern in _NOISY_URL_PATTERNS)


@mcp.tool(
    name="searxng_web_search",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True
}
)
def web_search(
    query: str,
    num_results: int = 5,
    categories: str = "general",
    time_range: Literal["", "day", "week", "month", "year"] = "",
    language: str = "all",
) -> str:
    """
    Search the web for real-time information using a local SearXNG metasearch engine.

    **TRIGGER CONDITION:** Use this when you need to retrieve up-to-date internet information that your installed tools cannot access. Ideal for obtaining current news, checking system status from external services, or searching general knowledge questions that require live web results.

    **SEQUENCE GUIDANCE:** Always call `web_search` before any action requiring real-time data like checking if a service is online, getting current weather, or verifying system availability. Use the returned results to inform decisions about which tools to use next (e.g., check if connection exists, then proceed with specific queries).

    **CONSTRAINT WARNING:** Avoid calling this on non-existent domains; ensure `SEARXNG_URL` points to a running instance at localhost:8080. The tool cannot bypass security restrictions or access restricted content. Results are limited to what SearXNG returns (typically 5 by default).

    **OUTPUT EXPECTATION:** Returns formatted search results with title, score, source engines, URL, and snippet for each result, separated by "---". Score reflects relevance (higher = better); engines shows how many independent sources agreed on the result — a low score with only one engine means low confidence. If no results found, returns error message with query details. If requests fail, returns specific error messages.

    **QUERY CONSTRUCTION — improve the query before calling:**

    1. **Be specific.** Put the most distinctive terms first. Drop vague filler words
       ("report", "info", "details", "how to") unless they are part of an exact title or phrase.

    2. **Quote multi-word proper nouns and exact phrases.** `"Log4Shell"`, `"Arch Linux"`,
       `"openai api"` — without quotes each word is searched independently and noise multiplies.

    3. **Choose the right category for the domain:**
       - News / current events / incidents / releases → `"news"` + `time_range`
       - Security, CVEs, vulnerabilities, advisories → `"news,it"`
       - Programming, APIs, libraries, docs → `"it"`
       - Academic papers, research → `"science"`
       - Anything else or unknown → `"general"`

    4. **Always set `time_range` for time-sensitive queries.** Anything from a specific year
       or recent period needs a range — leaving it blank gives all-time results where old pages
       outrank new ones.

    5. **Use `site:` to target authoritative sources** when you know where the answer lives:
       `site:docs.python.org`, `site:nvd.nist.gov`, `site:github.com/advisories`.

    6. **If results have low Score (< 0.5) or come from a single engine, the query failed.**
       Do not present those results as facts. Reformulate: make the query more specific, switch
       categories, split one broad query into two narrower ones, or add a `site:` constraint.

    Args:
        query: The search query — apply QUERY CONSTRUCTION rules above before passing.
        num_results: How many results to return (3–10 recommended for token efficiency).
        categories: Comma-separated categories — 'general', 'news', 'science', 'it', 'social media'.
        time_range: Recency filter. Leave blank for all-time. Use 'day'/'week' for breaking news.
        language: Language code (e.g., 'en-US', 'pt-BR', 'all').
    """
    params = {
        "q": query,
        "format": "json",
        "categories": categories,
        "language": language,
        "pageno": 1,
    }

    if time_range:
        params["time_range"] = time_range

    try:
        response = session.get(SEARXNG_URL, params=params, timeout=15)
        response.raise_for_status()

        # Guard against HTML error pages returned as non-JSON
        content_type = response.headers.get("Content-Type", "")
        if "application/json" not in content_type:
            return (
                f"Error: SearXNG returned a non-JSON response (Content-Type: '{content_type}', "
                f"status {response.status_code}). Ensure the instance is running and supports format=json."
            )

        data = response.json()
        results = [r for r in data.get("results", []) if _is_quality_result(r)]

        if not results:
            return (
                f"No results found for query: '{query}' with categories '{categories}'."
            )

        formatted_results = []
        for r in results[:num_results]:
            title = r.get("title", "No Title")
            url = r.get("url", "No URL")
            content = r.get("content", "No content available")
            score = r.get("score", None)

            # `engines` (plural list) shows multi-engine agreement — better signal than
            # `engine` (singular legacy field). Fall back gracefully.
            engines_raw = r.get("engines") or r.get("engine", "Unknown")
            if isinstance(engines_raw, list):
                engines_str = ", ".join(engines_raw)
            else:
                engines_str = engines_raw

            score_str = f"{score:.2f}" if score is not None else "n/a"

            formatted_results.append(
                f"**Title**: {title}\n"
                f"**Score**: {score_str} | **Source Engines**: {engines_str}\n"
                f"**URL**: {url}\n"
                f"**Snippet**: {content}"
            )

        return "\n\n---\n\n".join(formatted_results)

    except requests.exceptions.Timeout:
        return "Error: SearXNG search timed out after 15 seconds."
    except requests.exceptions.RequestException as e:
        return f"Error performing web search: {str(e)}"
    except Exception as e:
        return f"Unexpected error: {str(e)}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
