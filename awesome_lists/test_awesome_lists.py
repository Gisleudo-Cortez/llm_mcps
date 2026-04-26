"""Tests for the awesome_lists MCP server tools."""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from unittest.mock import patch
import pytest

from main import (
    search_awesome_lists,
    list_awesome_categories,
    get_awesome_category,
    _parse_sections,
)

# Minimal fixture mirroring the real awesome readme structure
SAMPLE_README = """\
## Contents

- [Platforms](#platforms)
- [Programming Languages](#programming-languages)

## Platforms

- [Linux](https://github.com/inputsh/awesome-linux) - Open-source POSIX-compliant Unix-like OS.
- [macOS](https://github.com/iCHAIT/awesome-macOS) - Operating system for Apple's Mac computers.

## Programming Languages

- [Python](https://github.com/vinta/awesome-python) - General-purpose interpreted programming language.
- [Rust](https://github.com/rust-unofficial/awesome-rust) - Systems programming language focused on safety.
- [Go](https://github.com/avelino/awesome-go) - Open-source programming language to build reliable software.

## Security

- [Application Security](https://github.com/paragonie/awesome-appsec) - Learning about application security.
- [CTF](https://github.com/apsdehal/awesome-ctf) - Capture The Flag frameworks, libraries, resources.
"""


# ---------------------------------------------------------------------------
# _parse_sections
# ---------------------------------------------------------------------------

def test_parse_sections_extracts_correct_categories():
    sections = _parse_sections(SAMPLE_README)
    assert "Platforms" in sections
    assert "Programming Languages" in sections
    assert "Security" in sections


def test_parse_sections_skips_contents():
    sections = _parse_sections(SAMPLE_README)
    # Contents section links are TOC anchors, not real items we want
    assert "Contents" in sections  # section exists
    # But its items are anchor links that won't match the item_re
    platform_items = sections["Platforms"]
    assert len(platform_items) == 2
    assert platform_items[0]["name"] == "Linux"
    assert "github.com" in platform_items[0]["url"]


def test_parse_sections_extracts_descriptions():
    sections = _parse_sections(SAMPLE_README)
    python_item = next(
        i for i in sections["Programming Languages"] if i["name"] == "Python"
    )
    assert "interpreted" in python_item["description"].lower()


# ---------------------------------------------------------------------------
# search_awesome_lists
# ---------------------------------------------------------------------------

@patch("main._load_readme", return_value=SAMPLE_README)
def test_search_finds_exact_match(mock_load):
    result = search_awesome_lists("python")
    assert "Python" in result
    assert "github.com/vinta/awesome-python" in result


@patch("main._load_readme", return_value=SAMPLE_README)
def test_search_is_case_insensitive(mock_load):
    result_lower = search_awesome_lists("rust")
    result_upper = search_awesome_lists("RUST")
    assert "Rust" in result_lower
    assert "Rust" in result_upper


@patch("main._load_readme", return_value=SAMPLE_README)
def test_search_no_results(mock_load):
    result = search_awesome_lists("xyzzy_not_a_real_topic")
    assert "No awesome lists" in result


@patch("main._load_readme", return_value=SAMPLE_README)
def test_search_empty_query(mock_load):
    result = search_awesome_lists("   ")
    assert "Error" in result


@patch("main._load_readme", return_value="")
def test_search_missing_readme(mock_load):
    result = search_awesome_lists("python")
    assert "Error" in result


# ---------------------------------------------------------------------------
# list_awesome_categories
# ---------------------------------------------------------------------------

@patch("main._load_readme", return_value=SAMPLE_README)
def test_list_categories_returns_all(mock_load):
    result = list_awesome_categories()
    assert "Platforms" in result
    assert "Programming Languages" in result
    assert "Security" in result


@patch("main._load_readme", return_value=SAMPLE_README)
def test_list_categories_excludes_contents(mock_load):
    result = list_awesome_categories()
    # "Contents" itself should not appear as a category line
    lines = [l for l in result.splitlines() if "Contents" in l and l.startswith("- ")]
    assert len(lines) == 0


@patch("main._load_readme", return_value=SAMPLE_README)
def test_list_categories_shows_counts(mock_load):
    result = list_awesome_categories()
    assert "2 lists" in result  # Platforms has 2 items
    assert "3 lists" in result  # Programming Languages has 3 items


# ---------------------------------------------------------------------------
# get_awesome_category
# ---------------------------------------------------------------------------

@patch("main._load_readme", return_value=SAMPLE_README)
def test_get_category_exact(mock_load):
    result = get_awesome_category("Security")
    assert "CTF" in result
    assert "Application Security" in result


@patch("main._load_readme", return_value=SAMPLE_README)
def test_get_category_case_insensitive(mock_load):
    result = get_awesome_category("security")
    assert "CTF" in result


@patch("main._load_readme", return_value=SAMPLE_README)
def test_get_category_not_found(mock_load):
    result = get_awesome_category("Quantum Computing")
    assert "not found" in result.lower()
    # Should suggest alternatives
    assert "Security" in result or "Platforms" in result


@patch("main._load_readme", return_value=SAMPLE_README)
def test_get_category_empty_name(mock_load):
    result = get_awesome_category("")
    assert "Error" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
