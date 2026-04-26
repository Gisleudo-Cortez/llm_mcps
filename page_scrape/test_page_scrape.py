"""Tests for the page_scrape MCP server tools."""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from unittest.mock import MagicMock, patch
import pytest

from main import fetch_url_content, extract_links, _extract_tables, _extract_images
from bs4 import BeautifulSoup


# ---------------------------------------------------------------------------
# fetch_url_content
# ---------------------------------------------------------------------------

def test_fetch_invalid_url():
    result = fetch_url_content("not-a-url")
    assert "Error" in result
    assert "http" in result.lower()


@patch("main.session")
def test_fetch_timeout(mock_session):
    import requests
    mock_session.get.side_effect = requests.exceptions.Timeout()
    result = fetch_url_content("http://example.com")
    assert "timed out" in result.lower()


@patch("main.session")
def test_fetch_returns_sections(mock_session):
    mock_resp = MagicMock()
    mock_resp.url = "http://example.com"
    mock_resp.status_code = 200
    mock_resp.text = """
    <html><head><title>Test</title></head>
    <body><p>Hello world</p></body></html>
    """
    mock_resp.raise_for_status = MagicMock()
    mock_session.get.return_value = mock_resp

    result = fetch_url_content("http://example.com")
    assert "Page Metadata" in result
    assert "Main Content" in result


@patch("main.session")
def test_fetch_with_table(mock_session):
    mock_resp = MagicMock()
    mock_resp.url = "http://example.com"
    mock_resp.status_code = 200
    mock_resp.text = """
    <html><body>
    <table><thead><tr><th>Name</th><th>Age</th></tr></thead>
    <tbody><tr><td>Alice</td><td>30</td></tr></tbody>
    </table>
    </body></html>
    """
    mock_resp.raise_for_status = MagicMock()
    mock_session.get.return_value = mock_resp

    result = fetch_url_content("http://example.com", include_tables=True)
    assert "Tables" in result


# ---------------------------------------------------------------------------
# _extract_tables helper
# ---------------------------------------------------------------------------

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
    # Should have at most 5 data rows
    assert tables[0].count("\n") <= 7  # header + sep + 5 rows


def test_extract_tables_empty():
    soup = BeautifulSoup("<p>no tables here</p>", "lxml")
    tables = _extract_tables(soup, max_rows=100)
    assert tables == []


# ---------------------------------------------------------------------------
# _extract_images helper
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# extract_links
# ---------------------------------------------------------------------------

def test_extract_links_invalid_url():
    result = extract_links("ftp://not-http.com")
    assert "Error" in result


@patch("main.session")
def test_extract_links_basic(mock_session):
    mock_resp = MagicMock()
    mock_resp.url = "http://example.com"
    mock_resp.status_code = 200
    mock_resp.text = """
    <html><body>
      <a href="/page1">Page 1</a>
      <a href="https://external.com/path">External</a>
      <a href="#anchor">Skip</a>
      <a href="mailto:x@y.com">Mail</a>
    </body></html>
    """
    mock_resp.raise_for_status = MagicMock()
    mock_session.get.return_value = mock_resp

    result = extract_links("http://example.com")
    assert "example.com" in result
    assert "external.com" in result
    # anchor and mailto should be excluded
    assert "#anchor" not in result
    assert "mailto:" not in result


@patch("main.session")
def test_extract_links_internal_only(mock_session):
    mock_resp = MagicMock()
    mock_resp.url = "http://example.com"
    mock_resp.status_code = 200
    mock_resp.text = """
    <html><body>
      <a href="/internal">Internal</a>
      <a href="https://other.com/">External</a>
    </body></html>
    """
    mock_resp.raise_for_status = MagicMock()
    mock_session.get.return_value = mock_resp

    result = extract_links("http://example.com", internal_only=True)
    assert "other.com" not in result
    assert "/internal" in result or "example.com/internal" in result


@patch("main.session")
def test_extract_links_filter_text(mock_session):
    mock_resp = MagicMock()
    mock_resp.url = "http://example.com"
    mock_resp.status_code = 200
    mock_resp.text = """
    <html><body>
      <a href="/api/users">API Users</a>
      <a href="/about">About</a>
    </body></html>
    """
    mock_resp.raise_for_status = MagicMock()
    mock_session.get.return_value = mock_resp

    result = extract_links("http://example.com", filter_text="/api/")
    assert "api/users" in result
    assert "/about" not in result


@patch("main.session")
def test_extract_links_deduplicates(mock_session):
    mock_resp = MagicMock()
    mock_resp.url = "http://example.com"
    mock_resp.status_code = 200
    mock_resp.text = """
    <html><body>
      <a href="/page">Link 1</a>
      <a href="/page">Link 2 (duplicate)</a>
    </body></html>
    """
    mock_resp.raise_for_status = MagicMock()
    mock_session.get.return_value = mock_resp

    result = extract_links("http://example.com")
    # /page should appear only once as a URL
    assert result.count("example.com/page") == 1


@patch("main.session")
def test_extract_links_no_links(mock_session):
    mock_resp = MagicMock()
    mock_resp.url = "http://example.com"
    mock_resp.status_code = 200
    mock_resp.text = "<html><body><p>No links here</p></body></html>"
    mock_resp.raise_for_status = MagicMock()
    mock_session.get.return_value = mock_resp

    result = extract_links("http://example.com")
    assert "No links found" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
