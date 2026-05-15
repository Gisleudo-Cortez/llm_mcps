"""
Page Scrape MCP Server — fetch web content with anti-bot evasion layers.

Provides tools:
  - page_scrape_fetch_url_content: Extract text, tables, images from any URL
  - page_scrape_extract_links: Catalog hyperlinks for site mapping

Features:
  - Full Layer 1 header evasion (7 headers, not 1)
  - Cloudflare/bot-detection page diagnosis
  - Custom headers override per call
  - Proxy support (SOCKS5, HTTP, HTTPS)
  - Google Cache fallback
  - Retry with exponential backoff
"""

import ipaddress
import json
import socket
import time
import urllib.parse
from typing import Optional

import trafilatura
from bs4 import BeautifulSoup
from curl_cffi import requests
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

mcp = FastMCP("page_scrape_mcp")

# ═══════════════════════════════════════════════════════════════════════════
# SSRF Defense-in-Depth — blocked networks
# ═══════════════════════════════════════════════════════════════════════════
_BLOCKED_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.ip_network("0.0.0.0/8"),       # Current network
    ipaddress.ip_network("10.0.0.0/8"),      # Private
    ipaddress.ip_network("100.64.0.0/10"),   # CGNAT
    ipaddress.ip_network("127.0.0.0/8"),     # Loopback
    ipaddress.ip_network("169.254.0.0/16"),  # Link-local / cloud metadata
    ipaddress.ip_network("172.16.0.0/12"),   # Private
    ipaddress.ip_network("192.0.0.0/24"),    # IETF protocol
    ipaddress.ip_network("192.0.2.0/24"),    # TEST-NET-1
    ipaddress.ip_network("192.168.0.0/16"),  # Private
    ipaddress.ip_network("198.18.0.0/15"),   # Benchmark
    ipaddress.ip_network("198.51.100.0/24"), # TEST-NET-2
    ipaddress.ip_network("203.0.113.0/24"),  # TEST-NET-3
    ipaddress.ip_network("224.0.0.0/4"),     # Multicast
    ipaddress.ip_network("240.0.0.0/4"),     # Reserved / future
]


def _resolve_and_validate(host: str) -> str:
    """Resolve hostname and validate IP is not internal/blocked.

    Returns the resolved IP or raises ValueError.
    """
    try:
        addr = socket.gethostbyname(host)
    except socket.gaierror as e:
        raise ValueError(f"DNS resolution failed for {host}: {e}") from e

    ip = ipaddress.ip_address(addr)
    for net in _BLOCKED_NETWORKS:
        if ip in net:
            raise ValueError(
                f"SSRF blocked: {host} resolves to {addr} "
                f"which is in blocked range {net}"
            )
    return addr


# Max redirect hops before aborting
_MAX_REDIRECTS: int = 10

# ═══════════════════════════════════════════════════════════════════════════
# Layer 1 Anti-Bot Headers (full evasion stack, not just 1 UA)
# ═══════════════════════════════════════════════════════════════════════════
DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Sec-Ch-Ua": '"Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Platform": '"Linux"',
    "Connection": "keep-alive",
}

# ═══════════════════════════════════════════════════════════════════════════
# Patterns that indicate the page is a bot-detection challenge, not content
# ═══════════════════════════════════════════════════════════════════════════
BOT_CHALLENGE_PATTERNS: list[str] = [
    "Just a moment...",         # Cloudflare Turnstile
    "_cf_chl_opt",              # Cloudflare challenge options
    "cf-challenge",
    "challenge-platform",
    "Enable JavaScript and cookies to continue",
    "unusual traffic from your computer",
    "verify you are a human",
    "/cdn-cgi/challenge-platform",
    "id=\"challenge-error-text\"",
]


def _detect_bot_challenge(html: str) -> Optional[str]:
    """Return the challenge type if the page is a bot wall, None otherwise."""
    html_lower = html.lower()
    for pattern in BOT_CHALLENGE_PATTERNS:
        if pattern.lower() in html_lower:
            return "Cloudflare/bot challenge page detected"
    return None


def _build_headers(overrides: Optional[dict[str, str]] = None) -> dict[str, str]:
    """Merge default anti-bot headers with per-request overrides."""
    headers = dict(DEFAULT_HEADERS)
    if overrides:
        headers.update(overrides)
    return headers


# ═══════════════════════════════════════════════════════════════════════════
# Pydantic Input Models
# ═══════════════════════════════════════════════════════════════════════════


class FetchUrlInput(BaseModel):
    """Input for fetch_url_content tool."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    url: str = Field(
        ...,
        min_length=8,
        pattern=r"^https?://.+",
        description="Target URL (must start with http:// or https://)",
    )
    include_tables: bool = Field(
        default=True,
        description="Extract HTML tables as formatted Markdown tables",
    )
    include_images: bool = Field(
        default=True,
        description="Extract image URLs with alt text and dimensions",
    )
    max_table_rows: int = Field(
        default=100,
        ge=1,
        le=500,
        description="Max rows to extract per table (prevents huge output)",
    )
    custom_headers: Optional[dict[str, str]] = Field(
        default=None,
        description=(
            "Additional/override request headers merged with defaults. "
            'Example: {"Authorization": "Bearer token", "Origin": "https://example.com"}'
        ),
    )
    proxy: Optional[str] = Field(
        default=None,
        description=(
            "Proxy URL for IP rotation — supports http, https, socks5. "
            "Example: 'socks5://127.0.0.1:9050' or 'http://user:pass@proxy:8080'"
        ),
    )
    max_retries: int = Field(
        default=1,
        ge=0,
        le=3,
        description="Retry count on transient failures, with exponential backoff",
    )
    use_cache: bool = Field(
        default=False,
        description=(
            "Try Google Cache first (webcache.googleusercontent.com). "
            "Falls back to live fetch if cached version is stale/missing. "
            "Bypasses Cloudflare — use this when the live URL returns a challenge page."
        ),
    )


class ExtractLinksInput(BaseModel):
    """Input for extract_links tool."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    url: str = Field(
        ...,
        min_length=8,
        pattern=r"^https?://.+",
        description="Target URL (must start with http:// or https://)",
    )
    filter_text: str = Field(
        default="",
        description="Only return links whose URL or anchor text contains this keyword",
    )
    internal_only: bool = Field(
        default=False,
        description="Restrict to links on the same domain as the target URL",
    )
    max_links: int = Field(
        default=500, ge=1, le=2000, description="Cap on returned links (token protection)"
    )
    custom_headers: Optional[dict[str, str]] = Field(
        default=None,
        description="Additional/override request headers merged with defaults",
    )
    proxy: Optional[str] = Field(
        default=None,
        description="Proxy URL for IP rotation",
    )
    max_retries: int = Field(
        default=1, ge=0, le=3, description="Retry count with exponential backoff"
    )
    use_cache: bool = Field(
        default=False,
        description="Try Google Cache first, fall back to live fetch",
    )


# ═══════════════════════════════════════════════════════════════════════════
# Shared Helpers
# ═══════════════════════════════════════════════════════════════════════════


def _google_cache_url(url: str) -> str:
    """Build the Google Web Cache URL for a given target."""
    return f"https://webcache.googleusercontent.com/search?q=cache:{url}"


def _detect_and_format_json(raw_text: str, max_length: int = 100000) -> Optional[str]:
    """Sniff content for JSON and pretty-print it if detected.

    Returns formatted JSON text, or None if the content is not JSON.
    This allows JSON API responses to bypass trafilatura (which only handles HTML).
    """
    stripped = raw_text.strip()
    if not stripped or stripped[0] not in ("{", "["):
        return None

    try:
        parsed = json.loads(stripped)
    except (json.JSONDecodeError, ValueError):
        return None

    # Pretty-print and truncate if needed
    formatted = json.dumps(parsed, indent=2, ensure_ascii=False)
    if len(formatted) > max_length:
        formatted = formatted[:max_length] + "\n\n... (content truncated to save context)"

    return formatted


def _fetch_with_redirect_control(
    url: str,
    headers: Optional[dict[str, str]] = None,
    proxy: Optional[str] = None,
    max_retries: int = 1,
    timeout: int = 30,
) -> requests.Response:
    """Fetch a URL with manual redirect following, validating each hop."""
    if headers is None:
        headers = _build_headers()

    proxies: dict[str, str] | None = None
    if proxy:
        proxies = {"http": proxy, "https": proxy}

    current_url = url
    redirect_count = 0

    # ── Validate initial URL before any HTTP request ──
    initial_parsed = urllib.parse.urlparse(current_url)
    _resolve_and_validate(initial_parsed.hostname)  # type: ignore[arg-type]

    # ── Initial fetch with retry ──
    last_exc: Optional[Exception] = None
    for attempt in range(max_retries + 1):
        try:
            if attempt > 0:
                delay = 2**attempt  # 2s, 4s, 8s
                time.sleep(delay)
            resp = requests.get(
                current_url,
                headers=headers,
                proxies=proxies,
                timeout=timeout,
                impersonate="chrome131",
                allow_redirects=False,
            )
            break
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_exc = e
            if attempt == max_retries:
                raise
            continue
        except requests.exceptions.RequestException:
            # Non-retryable (e.g. 400-level errors, invalid URL)
            raise
    else:
        # All retries exhausted
        raise last_exc  # type: ignore[misc]

    # ── Manual redirect loop with SSRF validation ──
    while resp.status_code in (301, 302, 303, 307, 308):
        location = resp.headers.get("Location", "")
        if not location:
            break

        # Resolve relative redirects
        next_url = urllib.parse.urljoin(current_url, location)

        # Validate redirect target — must not resolve to internal IP
        parsed = urllib.parse.urlparse(next_url)
        _resolve_and_validate(parsed.hostname)  # type: ignore[arg-type]

        redirect_count += 1
        if redirect_count > _MAX_REDIRECTS:
            raise ValueError(f"Too many redirects ({_MAX_REDIRECTS})")

        current_url = next_url
        # One shot per redirect hop — no retry
        resp = requests.get(
            current_url,
            headers=headers,
            proxies=proxies,
            timeout=timeout,
            impersonate="chrome131",
            allow_redirects=False,
        )

    # ── Validate final URL's IP (defense-in-depth) ──
    final_parsed = urllib.parse.urlparse(resp.url or current_url)
    _resolve_and_validate(final_parsed.hostname)  # type: ignore[arg-type]

    return resp


def _fetch_with_cache(
    url: str,
    use_cache: bool,
    headers: Optional[dict[str, str]] = None,
    proxy: Optional[str] = None,
    max_retries: int = 1,
) -> tuple[str, str]:
    """
    Fetch content, optionally via Google Cache.

    Returns (html_content, final_url).
    """
    if not use_cache:
        resp = _fetch_with_redirect_control(
            url, headers=headers, proxy=proxy, max_retries=max_retries
        )
        return resp.text, resp.url

    # Try cache first
    cache_url = _google_cache_url(url)
    try:
        cache_resp = _fetch_with_redirect_control(
            cache_url,
            headers=headers,
            proxy=proxy,
            max_retries=0,  # one shot for cache
        )
        cache_html = cache_resp.text
        # Verify cache didn't return a challenge itself, and contains actual content
        if (
            not _detect_bot_challenge(cache_html)
            and len(cache_html) > 2000
            and ("web scraping" not in cache_html.lower() or "<html" in cache_html[:200])
        ):
            return cache_html, cache_resp.url
    except (requests.exceptions.RequestException, ValueError):
        pass  # Cache miss or SSRF block → fall through to live

    # Fallback: live fetch
    resp = _fetch_with_redirect_control(
        url, headers=headers, proxy=proxy, max_retries=max_retries
    )
    return resp.text, resp.url


def _extract_tables(soup: BeautifulSoup, max_rows: int) -> list[str]:
    """Extract and format HTML tables as Markdown."""
    extracted_tables: list[str] = []
    tables = soup.find_all("table")

    for table in tables:
        rows = table.find_all("tr")
        if not rows:
            continue

        headers: list[str] = []
        thead = table.find("thead")

        if thead:
            headers = [th.get_text(strip=True) for th in thead.find_all(["th", "td"])]
        else:
            th_elements = rows[0].find_all("th")
            if th_elements:
                headers = [th.get_text(strip=True) for th in th_elements]
                rows = rows[1:]

        if not headers:
            max_cols = max(
                (len(r.find_all(["td", "th"])) for r in rows), default=0
            )
            if max_cols == 0:
                continue
            headers = [f"Col {i + 1}" for i in range(max_cols)]

        md_table: list[str] = []
        md_table.append("| " + " | ".join(headers) + " |")
        md_table.append("|" + "|".join(["---" for _ in headers]) + "|")

        for row_count, row in enumerate(rows):
            if row_count >= max_rows:
                break
            cols = row.find_all(["td", "th"])
            col_data = [c.get_text(strip=True).replace("|", "\\|") for c in cols]
            col_data += [""] * (len(headers) - len(col_data))
            md_table.append(
                "| " + " | ".join(col_data[: len(headers)]) + " |"
            )

        extracted_tables.append("\n".join(md_table))

    return extracted_tables


def _extract_images(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Extract image metadata from the page."""
    extracted_images: list[str] = []

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


# ═══════════════════════════════════════════════════════════════════════════
# Tools
# ═══════════════════════════════════════════════════════════════════════════


@mcp.tool(
    name="page_scrape_fetch_url_content",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
def fetch_url_content(params: FetchUrlInput) -> str:  # type: ignore[no-untyped-def]
    """
    Fetch and extract clean content from a URL with anti-bot evasion.

    **TRIGGER CONDITION:** Use when you need structured content (text, tables,
    images) from a URL. Handles Cloudflare Turnstile, bot walls, and IP-based
    rate limits through headers, proxy rotation, and Google Cache fallback.

    **SEQUENCE GUIDANCE:** Use as standalone content extraction. For site
    mapping, call page_scrape_extract_links first, then feed individual URLs
    into this tool. No prerequisites — the tool auto-detects bot walls.

    **CONSTRAINT WARNING:**
      - URL must start with http:// or https://.
      - Content truncated at ~100,000 characters.
      - Network timeout at 30 seconds per attempt.
      - max_retries up to 3 (exponential backoff: 2s, 4s, 8s).
      - use_cache=True hits Google Cache first, bypassing Cloudflare.

    **OUTPUT EXPECTATION:** Four-section structured Markdown:
      1. Metadata (URL, status, element counts, source: live/cache)
      2. Main content extracted by Trafilatura
      3. Tables formatted as Markdown (if enabled)
      4. Images listed with metadata (if enabled)

    **ANTI-BOT FEATURES:**
      - Full browser header stack (7 headers, not just User-Agent)
      - Cloudflare challenge detection → returns diagnostic error
      - Proxy support for IP rotation
      - Google Cache as Layer 4 bypass
      - Retry with exponential backoff on transient failures

    **ERROR RECOVERY:**
      - If content is missing → try use_cache=True
      - If Cloudflare detected → set use_cache=True to bypass via Google Cache
      - If timeouts persist → reduce max_table_rows or set include_tables=False
      - If 429/rate limited → pass a proxy for IP rotation
    """
    if not params.url.startswith(("http://", "https://")):
        return "Error: Invalid URL format. URL must start with http:// or https://"

    headers = _build_headers(params.custom_headers)

    try:
        html_content, final_url = _fetch_with_cache(
            params.url,
            use_cache=params.use_cache,
            headers=headers,
            proxy=params.proxy,
            max_retries=params.max_retries,
        )

        # ── Diagnose: is this a bot challenge page? ──
        challenge = _detect_bot_challenge(html_content)
        if challenge:
            return (
                f"Error: {challenge} at {params.url}\n\n"
                "The page returned a bot-detection challenge instead of content.\n"
                "Recommendations:\n"
                "  1. Retry with use_cache=True (hits Google Cache, bypasses Cloudflare)\n"
                "  2. Pass custom_headers with additional auth/cookie headers\n"
                "  3. Pass a proxy for IP rotation (your current IP may be flagged)\n"
                "  4. Try an alternative data source (Play Store, Trustpilot, Reddit)\n"
                "  5. Use a stealth browser (Playwright + playwright-stealth) for JS challenges"
            )

        # ── 1. Primary text extraction ──
        # Detect JSON responses first (trafilatura only handles HTML)
        json_content = _detect_and_format_json(html_content)
        if json_content is not None:
            extracted_text = json_content
        else:
            extracted_text = trafilatura.extract(
                html_content,
                include_links=True,
                include_images=False,
                include_tables=False,
            )
            if not extracted_text:
                extracted_text = (
                    "No primary content could be cleanly extracted "
                    "(page might be JS-rendered or heavily gated)."
                )

            if len(extracted_text) > 100000:
                extracted_text = (
                    extracted_text[:100000]
                    + "\n\n... (content truncated to save context)"
                )

        # ── 2. Tables and images via BeautifulSoup ──
        soup = BeautifulSoup(html_content, "lxml")
        total_tables = len(soup.find_all("table"))
        total_images = len(soup.find_all("img"))

        markdown_tables = (
            _extract_tables(soup, params.max_table_rows)
            if params.include_tables
            else []
        )
        image_list = (
            _extract_images(soup, final_url) if params.include_images else []
        )

        # ── 3. Assemble output ──
        output: list[str] = [
            "### Section 1: Page Metadata",
            f"- Canonical URL: {final_url}",
            f"- Elements Detected: {total_tables} tables, {total_images} images",
            "",
            "---",
            "",
            "### Section 2: Main Content",
            extracted_text,
            "",
            "---",
            "",
        ]

        if params.include_tables:
            output.append("### Section 3: Tables (if found)")
            if markdown_tables:
                for t in markdown_tables:
                    output.append("```markdown\n" + t + "\n```\n")
            else:
                output.append("Tables found: 0")
            output.extend(["", "---", ""])

        if params.include_images:
            output.append("### Section 4: Images (if found)")
            if image_list:
                output.append("\n\n".join(image_list))
            else:
                output.append("Images found: 0")
            output.append("\n---")

        return "\n".join(output)

    except requests.exceptions.Timeout:
        return "Error: Request timed out after 30 seconds (all retries exhausted)."
    except requests.exceptions.ConnectionError as e:
        return (
            f"Error: Connection failed — {e}\n"
            "Check network/proxy, or try use_cache=True to bypass the target server."
        )
    except requests.exceptions.RequestException as e:
        return f"Error: Network request failed — {e}"
    except ValueError as e:
        # SSRF blocks and redirect limit violations surface here
        return f"Error: SSRF blocked — {e}"
    except Exception as e:
        return f"Error: An unexpected error occurred — {e}"


@mcp.tool(
    name="page_scrape_extract_links",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def extract_links(params: ExtractLinksInput) -> str:  # type: ignore[no-untyped-def]
    """
    Extract and catalog all hyperlinks from a URL for site mapping.

    **TRIGGER CONDITION:** Use when you need to map a page's link structure,
    discover referenced URLs, or prepare for crawling. Ideal for site audits,
    API endpoint discovery, and finding related pages.

    **SEQUENCE GUIDANCE:** Call after identifying a target URL. Use
    `filter_text` to narrow results (e.g., "/api/", "github.com"). Set
    `internal_only=True` for same-domain links only. Feed resulting URLs
    to page_scrape_fetch_url_content for deeper extraction.

    **CONSTRAINT WARNING:**
      - URL must start with http:// or https://.
      - JavaScript-rendered links (SPAs) may return fewer links than visible.
      - max_links caps output — increase only when needed.
      - Relative URLs are auto-resolved to absolute.

    **OUTPUT EXPECTATION:** Deduplicated, sorted absolute URLs with anchor
    text, grouped by domain. Internal links are tagged. Summary includes
    total/unique link counts and domain count.

    **ANTI-BOT FEATURES:** Same as fetch_url_content — full header stack,
    proxy support, Google Cache, retry with backoff.

    **ERROR RECOVERY:**
      - If no links found → try without filter_text
      - If Cloudflare detected → set use_cache=True
      - If few links on SPA → the page requires a JS browser (browser_navigate)
    """
    if not params.url.startswith(("http://", "https://")):
        return "Error: Invalid URL format. URL must start with http:// or https://"

    headers = _build_headers(params.custom_headers)

    try:
        html_content, final_url = _fetch_with_cache(
            params.url,
            use_cache=params.use_cache,
            headers=headers,
            proxy=params.proxy,
            max_retries=params.max_retries,
        )

        # Diagnose bot challenge
        challenge = _detect_bot_challenge(html_content)
        if challenge:
            return (
                f"Error: {challenge} at {params.url}\n\n"
                "Recommendations:\n"
                "  1. Retry with use_cache=True\n"
                "  2. Pass a proxy for IP rotation\n"
                "  3. Use browser_navigate for JS-heavy pages"
            )

        soup = BeautifulSoup(html_content, "lxml")
        parsed_base = urllib.parse.urlparse(final_url)

        seen: set[str] = set()
        links: list[dict[str, object]] = []

        for tag in soup.find_all("a", href=True):
            href = str(tag["href"]).strip()
            if not href or href.startswith(
                ("#", "mailto:", "tel:", "javascript:", "data:")
            ):
                continue

            abs_url = urllib.parse.urljoin(final_url, href)
            parsed = urllib.parse.urlparse(abs_url)
            if parsed.scheme not in ("http", "https"):
                continue

            is_internal = parsed.netloc == parsed_base.netloc
            if params.internal_only and not is_internal:
                continue

            text = tag.get_text(strip=True)[:120]

            if params.filter_text:
                kw = params.filter_text.lower()
                if kw not in abs_url.lower() and kw not in text.lower():
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
            qualifier = (
                f" matching '{params.filter_text}'" if params.filter_text else ""
            )
            return f"No links found{qualifier} on {params.url}."

        total = len(links)
        links = links[: params.max_links]
        truncated = total > params.max_links

        # Group by domain
        by_domain: dict[str, list[dict[str, object]]] = {}
        for link in links:
            domain = str(link["domain"])
            by_domain.setdefault(domain, []).append(link)

        output: list[str] = [
            f"### Links extracted from: {params.url}",
            (
                f"- Total unique links: {total}"
                + (f" (showing first {params.max_links})" if truncated else "")
            ),
            f"- Domains represented: {len(by_domain)}",
            (f"- Filter applied: '{params.filter_text}'" if params.filter_text else ""),
            "",
        ]

        for domain, domain_links in sorted(by_domain.items()):
            tag_label = "(internal)" if domain_links[0]["internal"] else ""
            output.append(
                f"**{domain}** {tag_label} — {len(domain_links)} link(s)"
            )
            for link in domain_links:
                label = (
                    f' "{link["text"]}"'
                    if str(link["text"])
                    else ""
                )
                output.append(f"  - {link['url']}{label}")
            output.append("")

        return "\n".join(line for line in output if line is not None)

    except requests.exceptions.Timeout:
        return "Error: Request timed out after 30 seconds."
    except requests.exceptions.RequestException as e:
        return f"Error: Network request failed — {e}"
    except ValueError as e:
        return f"Error: SSRF blocked — {e}"
    except Exception as e:
        return f"Error: An unexpected error occurred — {e}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
