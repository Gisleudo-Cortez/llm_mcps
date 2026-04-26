import urllib.parse
from typing import Union

import requests
import trafilatura
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Page Scrape Server")

# Shared session for connection pooling and proper headers
session = requests.Session()
session.headers.update(
    {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
)


def _extract_tables(soup: BeautifulSoup, max_rows: int) -> list[str]:
    """Helper to safely extract and format HTML tables to Markdown."""
    extracted_tables = []
    tables = soup.find_all("table")

    for table in tables:
        rows = table.find_all("tr")
        if not rows:
            continue

        headers = []
        thead = table.find("thead")

        if thead:
            headers = [th.get_text(strip=True) for th in thead.find_all(["th", "td"])]
        else:
            th_elements = rows[0].find_all("th")
            if th_elements:
                headers = [th.get_text(strip=True) for th in th_elements]
                rows = rows[1:]

        if not headers:
            max_cols = max((len(r.find_all(["td", "th"])) for r in rows), default=0)
            if max_cols == 0:
                continue
            headers = [f"Col {i + 1}" for i in range(max_cols)]

        md_table = []
        md_table.append("| " + " | ".join(headers) + " |")
        md_table.append("|" + "|".join(["---" for _ in headers]) + "|")

        for row_count, row in enumerate(rows):
            if row_count >= max_rows:
                break
            cols = row.find_all(["td", "th"])
            col_data = [c.get_text(strip=True).replace("|", "\\|") for c in cols]
            col_data += [""] * (len(headers) - len(col_data))
            md_table.append("| " + " | ".join(col_data[: len(headers)]) + " |")

        extracted_tables.append("\n".join(md_table))

    return extracted_tables


def _extract_images(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Helper to safely extract and format image metadata."""
    extracted_images = []

    for idx, img in enumerate(soup.find_all("img"), 1):
        src = str(img.get("src", "")).strip()
        if not src:
            continue

        abs_url = urllib.parse.urljoin(base_url, src)
        alt = str(img.get("alt", "")).strip()
        title = str(img.get("title", "")).strip()
        width = str(img.get("width", "")).strip()
        height = str(img.get("height", "")).strip()

        dim_str = f" ({width}x{height})" if width and height else ""
        img_entry = f"{idx}. [Image] - {abs_url}{dim_str}"
        if alt:
            img_entry += f'\n   Alt: "{alt}"'
        if title:
            img_entry += f'\n   Title: "{title}"'

        extracted_images.append(img_entry)

    return extracted_images


@mcp.tool()
def fetch_url_content(
    url: str,
    include_tables: Union[bool, str] = True,
    include_images: Union[bool, str] = True,
    max_table_rows: Union[int, str] = 100,
) -> str:
    """
    Fetch and extract clean text, tables, and images from a URL using Trafilatura and BeautifulSoup.

    **TRIGGER CONDITION:** Use this when you need to fetch and parse structured content from a webpage URL that requires HTML parsing. Ideal for extracting articles, documents, or web pages with defined sections (headings, tables, images).

    **SEQUENCE GUIDANCE:** Use `fetch_url_content` as the primary tool for complete page extraction. If only certain elements are needed:
      - For text-only content: Extract with Trafilatura and discard table/image sections.
      - For data-heavy pages: Keep tables enabled to capture structured information.
      - For image-heavy documents: Enable images but note they'll be listed as metadata.

    **CONSTRAINT WARNING:**
      - URL must start with http:// or https:// (invalid URLs return clear error).
      - Maximum page content truncation at ~100,000 characters to fit within LLM context limits.
      - Network requests time out after 30 seconds; malformed HTML returns parsing errors.
      - For large pages (>100 rows tables), use `max_table_rows` parameter to limit extraction.

    **OUTPUT EXPECTATION:** Returns structured Markdown output in four sections:
      1. Page Metadata (URL, status code, element counts)
      2. Main content (cleanly extracted by Trafilatura, max 100k chars)
      3. Tables (if enabled), formatted as markdown tables
      4. Images (if enabled), listed with metadata

    The tool is designed to balance thoroughness with token efficiency—always review output for truncation warnings.
    """
    # Robust Type Casting
    if isinstance(include_tables, str):
        include_tables = include_tables.strip().lower() in ("true", "1", "yes", "y")
    if isinstance(include_images, str):
        include_images = include_images.strip().lower() in ("true", "1", "yes", "y")
    if isinstance(max_table_rows, str):
        try:
            max_table_rows = int(max_table_rows.strip())
        except ValueError:
            max_table_rows = 100

    if not url.startswith(("http://", "https://")):
        return "Error: Invalid URL format. URL must start with http:// or https://"

    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()

        final_url = response.url
        html_content = response.text

        # 1. Primary Text Extraction using Trafilatura
        extracted_text = trafilatura.extract(
            html_content,
            include_links=True,
            include_images=False,  # We handle images separately
            include_tables=False,  # We handle tables separately for strict markdown
        )
        if not extracted_text:
            extracted_text = "No primary content could be cleanly extracted (page might be JS-rendered or heavily gated)."

        # Truncate at ~100,000 characters to fit well within 90k token limits
        if len(extracted_text) > 100000:
            extracted_text = (
                extracted_text[:100000] + "\n\n... (content truncated to save context)"
            )

        # 2. Extract Tables and Images using BeautifulSoup
        soup = BeautifulSoup(html_content, "lxml")
        total_tables = len(soup.find_all("table"))
        total_images = len(soup.find_all("img"))

        markdown_tables = (
            _extract_tables(soup, max_table_rows) if include_tables else []
        )
        image_list = _extract_images(soup, final_url) if include_images else []

        # 3. Assemble Output
        output = [
            "### Section 1: Page Metadata",
            f"- Canonical URL: {final_url}",
            f"- Status Code: {response.status_code}",
            f"- Elements Detected: {total_tables} tables, {total_images} images",
            "\n---\n",
            "### Section 2: Main Content",
            extracted_text,
            "\n---\n",
        ]

        if include_tables:
            output.append("### Section 3: Tables (if found)")
            if markdown_tables:
                for t in markdown_tables:
                    output.append("```markdown\n" + t + "\n```\n")
            else:
                output.append("Tables found: 0")
            output.append("\n---\n")

        if include_images:
            output.append("### Section 4: Images (if found)")
            if image_list:
                output.append("\n\n".join(image_list))
            else:
                output.append("Images found: 0")
            output.append("\n---")

        return "\n".join(output)

    except requests.exceptions.Timeout:
        return "Error: Request timed out after 30 seconds."
    except requests.exceptions.RequestException as e:
        return f"Error: Network request failed - {str(e)}"
    except Exception as e:
        return f"Error: An unexpected parsing error occurred - {str(e)}"


@mcp.tool()
def extract_links(
    url: str,
    filter_text: str = "",
    internal_only: bool = False,
    max_links: int = 500,
) -> str:
    """
    Extract and catalog all hyperlinks from a URL for site mapping or link discovery.

    **TRIGGER CONDITION:** Use when you need to map the link structure of a page, discover
    all referenced URLs, or gather a list of resources linked from a document. Ideal for
    site audits, crawling preparation, and discovering API endpoints or related pages.

    **SEQUENCE GUIDANCE:** Call after identifying a target URL. Use `filter_text` to narrow
    results to links containing a specific keyword (e.g., "/api/", "github.com"). Set
    `internal_only=True` to restrict to links on the same domain. Pipe the resulting URLs
    to `fetch_url_content` for deeper extraction.

    **CONSTRAINT WARNING:** URL must start with http:// or https://. Pages with
    JavaScript-rendered links (SPAs) may return fewer links than visible in a browser.
    `max_links` caps output to protect context — increase only when needed. Relative URLs
    are resolved to absolute using the page's base URL.

    **OUTPUT EXPECTATION:** Returns a deduplicated, sorted list of absolute URLs with their
    anchor text, grouped by domain. Includes a summary of total and unique link counts.

    *Typical workflow:* extract_links(url) → fetch_url_content(url=one_of_the_links)
    """
    if not url.startswith(("http://", "https://")):
        return "Error: Invalid URL format. URL must start with http:// or https://"

    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "lxml")
        base_url = response.url
        parsed_base = urllib.parse.urlparse(base_url)
        base_domain = f"{parsed_base.scheme}://{parsed_base.netloc}"

        seen: set[str] = set()
        links: list[dict] = []

        for tag in soup.find_all("a", href=True):
            href = str(tag["href"]).strip()
            if not href or href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue

            abs_url = urllib.parse.urljoin(base_url, href)
            parsed = urllib.parse.urlparse(abs_url)
            if parsed.scheme not in ("http", "https"):
                continue

            is_internal = parsed.netloc == parsed_base.netloc
            if internal_only and not is_internal:
                continue

            text = tag.get_text(strip=True)[:120]

            if filter_text and filter_text.lower() not in abs_url.lower() and filter_text.lower() not in text.lower():
                continue

            if abs_url not in seen:
                seen.add(abs_url)
                links.append(
                    {
                        "url": abs_url,
                        "text": text,
                        "domain": parsed.netloc,
                        "internal": is_internal,
                    }
                )

        if not links:
            qualifier = f" matching '{filter_text}'" if filter_text else ""
            return f"No links found{qualifier} on {url}."

        total = len(links)
        links = links[:max_links]
        truncated = total > max_links

        # Group by domain for readability
        by_domain: dict[str, list[dict]] = {}
        for link in links:
            by_domain.setdefault(link["domain"], []).append(link)

        output = [
            f"### Links extracted from: {url}",
            f"- Total unique links: {total}"
            + (f" (showing first {max_links})" if truncated else ""),
            f"- Domains represented: {len(by_domain)}",
            f"- Filter applied: '{filter_text}'" if filter_text else "",
            "",
        ]

        for domain, domain_links in sorted(by_domain.items()):
            tag_label = "(internal)" if domain_links[0]["internal"] else ""
            output.append(f"**{domain}** {tag_label} — {len(domain_links)} link(s)")
            for link in domain_links:
                label = f' "{link["text"]}"' if link["text"] else ""
                output.append(f"  - {link['url']}{label}")
            output.append("")

        return "\n".join(line for line in output if line is not None)

    except requests.exceptions.Timeout:
        return "Error: Request timed out after 30 seconds."
    except requests.exceptions.RequestException as e:
        return f"Error: Network request failed — {str(e)}"
    except Exception as e:
        return f"Error: An unexpected error occurred — {str(e)}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
