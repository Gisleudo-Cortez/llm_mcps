"""Tests for the page_scrape MCP server tools."""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from unittest.mock import MagicMock, patch
import pytest

from main import (
    fetch_url_content, extract_links,
    _extract_tables, _extract_images,
    _resolve_and_validate, _BLOCKED_NETWORKS,
    _fetch_with_redirect_control,
    FetchUrlInput, ExtractLinksInput,
)
from bs4 import BeautifulSoup


# ── Mock helpers ────────────────────────────────────────────────────────────

def _make_mock_response(
    url="http://example.com",
    text="<html><body><p>hi</p></body></html>",
    status_code=200,
    headers=None,
):
    """Factory for mock curl_cffi response objects."""
    resp = MagicMock()
    resp.url = url
    resp.text = text
    resp.status_code = status_code
    resp.headers = headers or {}
    return resp


# ═══════════════════════════════════════════════════════════════════════════════
# _resolve_and_validate (SSRF layer)
# ═══════════════════════════════════════════════════════════════════════════════

def test_resolve_and_validate_blocks_loopback():
    with pytest.raises(ValueError, match="SSRF blocked"):
        _resolve_and_validate("127.0.0.1")


def test_resolve_and_validate_blocks_private_10():
    with pytest.raises(ValueError, match="SSRF blocked"):
        _resolve_and_validate("10.0.0.1")


def test_resolve_and_validate_blocks_192_168():
    with pytest.raises(ValueError, match="SSRF blocked"):
        _resolve_and_validate("192.168.1.1")


def test_resolve_and_validate_blocks_link_local():
    with pytest.raises(ValueError, match="SSRF blocked"):
        _resolve_and_validate("169.254.169.254")


def test_resolve_and_validate_blocks_metadata():
    """localhost should resolve to 127.0.0.1 which is blocked."""
    with pytest.raises(ValueError, match="SSRF blocked"):
        _resolve_and_validate("localhost")


def test_resolve_and_validate_allows_public():
    """8.8.8.8 is a public DNS server — must pass validation."""
    result = _resolve_and_validate("8.8.8.8")
    assert result == "8.8.8.8"


def test_blocked_networks_covers_all_private_ranges():
    """Sanity: every key private range is in the blocked list."""
    ranges = {str(n) for n in _BLOCKED_NETWORKS}
    assert "10.0.0.0/8" in ranges
    assert "127.0.0.0/8" in ranges
    assert "192.168.0.0/16" in ranges
    assert "172.16.0.0/12" in ranges
    assert "169.254.0.0/16" in ranges


# ═══════════════════════════════════════════════════════════════════════════════
# fetch_url_content
# ═══════════════════════════════════════════════════════════════════════════════

def test_fetch_invalid_url_schema():
    """URLs without http/https scheme hit Pydantic validation first."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="pattern"):
        FetchUrlInput(url="not-a-url")


def test_fetch_invalid_url_empty():
    """URLs shorter than 8 chars hit Pydantic min_length."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="at least 8"):
        FetchUrlInput(url="http://")


@patch("main._fetch_with_redirect_control")
def test_fetch_timeout(mock_fetch):
    from curl_cffi import requests as cffi_requests
    mock_fetch.side_effect = cffi_requests.exceptions.Timeout("timed out")
    result = fetch_url_content(FetchUrlInput(url="http://example.com"))
    assert "timed out" in result.lower()


@patch("main._fetch_with_redirect_control")
def test_fetch_connection_error(mock_fetch):
    from curl_cffi import requests as cffi_requests
    mock_fetch.side_effect = cffi_requests.exceptions.ConnectionError("refused")
    result = fetch_url_content(FetchUrlInput(url="http://example.com"))
    assert "Connection failed" in result


@patch("main._fetch_with_redirect_control")
def test_fetch_returns_sections(mock_fetch):
    mock_fetch.return_value = _make_mock_response(
        text="<html><head><title>Test</title></head><body><p>Hello world</p></body></html>"
    )
    result = fetch_url_content(FetchUrlInput(url="http://example.com"))
    assert "Page Metadata" in result
    assert "Main Content" in result


@patch("main._fetch_with_redirect_control")
def test_fetch_with_table_included(mock_fetch):
    mock_fetch.return_value = _make_mock_response(text="""
    <html><body>
    <table><thead><tr><th>Name</th><th>Age</th></tr></thead>
    <tbody><tr><td>Alice</td><td>30</td></tr></tbody>
    </table>
    </body></html>
    """)
    result = fetch_url_content(FetchUrlInput(url="http://example.com", include_tables=True))
    assert "Tables" in result


@patch("main._fetch_with_redirect_control")
def test_fetch_tables_disabled(mock_fetch):
    mock_fetch.return_value = _make_mock_response(text="""
    <html><body>
    <table><tr><td>hidden</td></tr></table>
    </body></html>
    """)
    result = fetch_url_content(FetchUrlInput(url="http://example.com", include_tables=False))
    assert "Tables" not in result


@patch("main._fetch_with_redirect_control")
def test_fetch_bot_challenge_detected(mock_fetch):
    mock_fetch.return_value = _make_mock_response(
        text="<html><body>Just a moment... Cloudflare</body></html>"
    )
    result = fetch_url_content(FetchUrlInput(url="http://example.com"))
    assert "bot-detection challenge" in result.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# _extract_tables helper
# ═══════════════════════════════════════════════════════════════════════════════

def test_extract_tables_basic():
    html = """
    <table>
      <thead><tr><th>Col A</th><th>Col B</th></tr></thead>
      <tbody><tr><td>1</td><td>2</td></tr></tbody>
    </table>
    """
    soup = BeautifulSoup(html, "lxml")
    tables = _extract_tables(soup, max_rows=10)
    assert len(tables) == 1
    assert "Col A" in tables[0]
    assert "Col B" in tables[0]
    assert "1" in tables[0]


def test_extract_tables_respects_max_rows():
    rows = "".join(f"<tr><td>{i}</td></tr>" for i in range(20))
    html = f"<table><thead><tr><th>N</th></tr></thead><tbody>{rows}</tbody></table>"
    soup = BeautifulSoup(html, "lxml")
    tables = _extract_tables(soup, max_rows=5)
    # header line + separator line + up to 5 data rows = 7
    assert tables[0].count("\n") <= 7


def test_extract_tables_empty():
    soup = BeautifulSoup("<p>no tables here</p>", "lxml")
    tables = _extract_tables(soup, max_rows=100)
    assert tables == []


# ═══════════════════════════════════════════════════════════════════════════════
# _extract_images helper
# ═══════════════════════════════════════════════════════════════════════════════

def test_extract_images_resolves_relative_url():
    html = '<img src="/images/logo.png" alt="Logo">'
    soup = BeautifulSoup(html, "lxml")
    images = _extract_images(soup, base_url="http://example.com/page")
    assert len(images) == 1
    assert "http://example.com/images/logo.png" in images[0]
    assert "Logo" in images[0]


def test_extract_images_skips_empty_src():
    html = '<img src="" alt="empty"> <img src="/real.png">'
    soup = BeautifulSoup(html, "lxml")
    images = _extract_images(soup, base_url="http://example.com")
    assert len(images) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# extract_links
# ═══════════════════════════════════════════════════════════════════════════════

def test_extract_links_invalid_url():
    """URL without http scheme hits Pydantic validation."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError, match="pattern"):
        ExtractLinksInput(url="ftp://not-http.com")


@patch("main._fetch_with_redirect_control")
def test_extract_links_basic(mock_fetch):
    mock_fetch.return_value = _make_mock_response(text="""
    <html><body>
      <a href="/page1">Page 1</a>
      <a href="https://external.com/path">External</a>
      <a href="#anchor">Skip</a>
      <a href="mailto:x@y.com">Mail</a>
    </body></html>
    """)
    result = extract_links(ExtractLinksInput(url="http://example.com"))
    assert "example.com" in result
    assert "external.com" in result
    assert "#anchor" not in result
    assert "mailto:" not in result


@patch("main._fetch_with_redirect_control")
def test_extract_links_internal_only(mock_fetch):
    mock_fetch.return_value = _make_mock_response(text="""
    <html><body>
      <a href="/internal">Internal</a>
      <a href="https://other.com/">External</a>
    </body></html>
    """)
    result = extract_links(ExtractLinksInput(
        url="http://example.com", internal_only=True
    ))
    assert "other.com" not in result
    assert "/internal" in result or "example.com/internal" in result


@patch("main._fetch_with_redirect_control")
def test_extract_links_filter_text(mock_fetch):
    mock_fetch.return_value = _make_mock_response(text="""
    <html><body>
      <a href="/api/users">API Users</a>
      <a href="/about">About</a>
    </body></html>
    """)
    result = extract_links(ExtractLinksInput(
        url="http://example.com", filter_text="/api/"
    ))
    assert "api/users" in result
    assert "/about" not in result


@patch("main._fetch_with_redirect_control")
def test_extract_links_deduplicates(mock_fetch):
    mock_fetch.return_value = _make_mock_response(text="""
    <html><body>
      <a href="/page">Link 1</a>
      <a href="/page">Link 2 (duplicate)</a>
    </body></html>
    """)
    result = extract_links(ExtractLinksInput(url="http://example.com"))
    assert result.count("example.com/page") == 1


@patch("main._fetch_with_redirect_control")
def test_extract_links_no_links(mock_fetch):
    mock_fetch.return_value = _make_mock_response(
        text="<html><body><p>No links here</p></body></html>"
    )
    result = extract_links(ExtractLinksInput(url="http://example.com"))
    assert "No links found" in result


@patch("main._fetch_with_redirect_control")
def test_extract_links_respects_max_links(mock_fetch):
    """Should cap output at max_links."""
    links = "".join(f'<a href="/p{i}">Page {i}</a>' for i in range(10))
    mock_fetch.return_value = _make_mock_response(
        text=f"<html><body>{links}</body></html>"
    )
    result = extract_links(ExtractLinksInput(url="http://example.com", max_links=3))
    # Only 3 links should appear
    assert result.count("example.com/p") == 3


# ═══════════════════════════════════════════════════════════════════════════════
# _detect_and_format_json
# ═══════════════════════════════════════════════════════════════════════════════

def test_detect_json_object():
    from main import _detect_and_format_json
    result = _detect_and_format_json('{"key": "value", "num": 42}')
    assert result is not None
    assert '"key"' in result
    assert '"num"' in result


def test_detect_json_array():
    from main import _detect_and_format_json
    result = _detect_and_format_json('[1, 2, 3]')
    assert result is not None
    assert "3" in result


def test_detect_json_html_returns_none():
    from main import _detect_and_format_json
    result = _detect_and_format_json("<html><body>hi</body></html>")
    assert result is None


def test_detect_json_plain_text_returns_none():
    from main import _detect_and_format_json
    result = _detect_and_format_json("Hello world")
    assert result is None


def test_detect_json_empty_returns_none():
    from main import _detect_and_format_json
    assert _detect_and_format_json("") is None


def test_detect_json_nested():
    from main import _detect_and_format_json
    result = _detect_and_format_json(
        '{"users": [{"name": "Alice"}, {"name": "Bob"}], "count": 2}'
    )
    assert result is not None
    assert "Alice" in result
    assert "Bob" in result


def test_detect_json_truncates_large_payload():
    from main import _detect_and_format_json
    result = _detect_and_format_json('[1]', max_length=5)
    assert result is not None
    assert "truncated" in result


# ═══════════════════════════════════════════════════════════════════════════════
# fetch_url_content handles JSON responses
# ═══════════════════════════════════════════════════════════════════════════════

@patch("main._fetch_with_redirect_control")
def test_fetch_json_api_response(mock_fetch):
    """JSON API responses bypass trafilatura and get pretty-printed."""
    mock_fetch.return_value = _make_mock_response(
        text='{"origin": "1.2.3.4"}',
        url="https://httpbin.org/ip",
    )
    result = fetch_url_content(FetchUrlInput(url="https://httpbin.org/ip"))
    assert '"origin"' in result
    assert "1.2.3.4" in result
    assert "No primary content" not in result


@patch("main._fetch_with_redirect_control")
def test_fetch_json_array_response(mock_fetch):
    """JSON array responses get pretty-printed."""
    mock_fetch.return_value = _make_mock_response(
        text='[{"id": 1}, {"id": 2}]',
        url="https://api.example.com/items",
    )
    result = fetch_url_content(FetchUrlInput(url="https://api.example.com/items"))
    assert '"id"' in result
    assert "No primary content" not in result


# ═══════════════════════════════════════════════════════════════════════════════
# SSRF error message uses "SSRF blocked" prefix
# ═══════════════════════════════════════════════════════════════════════════════

@patch("main._fetch_with_redirect_control")
def test_fetch_ssrf_blocked_clean_message(mock_fetch):
    """SSRF ValueError should surface as 'SSRF blocked', not 'unexpected parsing error'."""
    mock_fetch.side_effect = ValueError(
        "SSRF blocked: localhost resolves to 127.0.0.1 which is in blocked range 127.0.0.0/8"
    )
    result = fetch_url_content(FetchUrlInput(url="http://localhost:8080/"))
    assert "SSRF blocked" in result
    assert "unexpected" not in result.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# SSRF integration: redirect to internal IP is blocked
# ═══════════════════════════════════════════════════════════════════════════════

@patch("main.urllib.parse.urljoin")
@patch("main._resolve_and_validate")
@patch("main.requests.get")
def test_redirect_to_internal_blocked(mock_get, mock_validate, mock_urljoin):
    """A 301 redirect to 10.0.0.1 triggers SSRF block."""
    redirect_resp = _make_mock_response(
        url="http://safe.com",
        text="redirecting...",
        status_code=301,
        headers={"Location": "http://10.0.0.1/secret"},
    )
    mock_get.return_value = redirect_resp
    mock_urljoin.return_value = "http://10.0.0.1/secret"
    mock_validate.side_effect = ValueError(
        "SSRF blocked: 10.0.0.1 resolves to 10.0.0.1 which is in blocked range 10.0.0.0/8"
    )

    with pytest.raises(ValueError, match="SSRF blocked"):
        _fetch_with_redirect_control("http://safe.com")


@patch("main._resolve_and_validate")
@patch("main.requests.get")
def test_final_url_validated_even_without_redirect(mock_get, mock_validate):
    """Even non-redirect responses get their final URL validated."""
    mock_get.return_value = _make_mock_response(
        url="http://normal.com/page",
        text="<html><body>ok</body></html>",
    )
    # Should call _resolve_and_validate for the parsed host
    _fetch_with_redirect_control("http://normal.com/page")
    mock_validate.assert_called_with("normal.com")


@patch("main.urllib.parse.urljoin")
@patch("main._resolve_and_validate")
@patch("main.requests.get")
def test_too_many_redirects_raises(mock_get, mock_validate, mock_urljoin):
    """After _MAX_REDIRECTS hops, abort."""
    redirect_resp = _make_mock_response(
        status_code=301,
        headers={"Location": "http://next.example.com/"},
    )
    mock_get.return_value = redirect_resp
    mock_urljoin.return_value = "http://next.example.com/"

    with pytest.raises(ValueError, match="Too many redirects"):
        _fetch_with_redirect_control("http://start.example.com/")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
