from typing import Literal

import requests
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("SearXNG Search Server")
SEARXNG_URL = "http://localhost:8080/search"

session = requests.Session()
session.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"
    }
)


@mcp.tool()
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

    **OUTPUT EXPECTATION:** Returns formatted search results with title, source engine, URL, and snippet for each result, separated by "---". If no results found, returns error message with query details. If requests fail, returns specific error messages. This output is ideal for confirming data availability before proceeding to more complex operations.

    *Note:* The tool requires SearXNG running locally on port 8080. Network connectivity issues or service unavailability will return error messages.

    Args:
        query: The main search query.
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
        results = data.get("results", [])

        if not results:
            return (
                f"No results found for query: '{query}' with categories '{categories}'."
            )

        formatted_results = []
        for r in results[:num_results]:
            title = r.get("title", "No Title")
            url = r.get("url", "No URL")
            content = r.get("content", "No content available")

            # engine can be a string or list depending on SearXNG version
            engine = r.get("engine", "Unknown")
            if isinstance(engine, list):
                engine = ", ".join(engine)

            formatted_results.append(
                f"**Title**: {title}\n"
                f"**Source Engine**: {engine}\n"
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
