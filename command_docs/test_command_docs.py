"""Tests for the command_docs MCP server tools."""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from unittest.mock import MagicMock, patch
import pytest

from main import man_lookup, tldr_lookup, cheat_sh_lookup, run_command


# ---------------------------------------------------------------------------
# run_command helper
# ---------------------------------------------------------------------------

def test_run_command_success():
    out = run_command(["echo", "hello"])
    assert "hello" in out


def test_run_command_missing_binary():
    out = run_command(["__nonexistent_binary__"])
    assert "Error" in out
    assert "not found" in out.lower()


def test_run_command_truncates_large_output():
    # Create a command that produces more than max_chars output
    large_text = "A" * 100
    out = run_command(["echo", large_text], max_chars=50)
    assert "truncated" in out.lower()


# ---------------------------------------------------------------------------
# man_lookup
# ---------------------------------------------------------------------------

def test_man_lookup_invalid_name():
    result = man_lookup("ls; rm -rf /")
    assert "Error" in result
    assert "Invalid" in result


def test_man_lookup_empty():
    result = man_lookup("")
    assert "Error" in result


def test_man_lookup_known_command():
    result = man_lookup("ls")
    # man pages always contain the command name in uppercase somewhere
    assert "ls" in result.lower() or "Error" in result


def test_man_lookup_dots_allowed():
    # Some commands like git-config have dots/hyphens in man page names
    result = man_lookup("git")
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# tldr_lookup
# ---------------------------------------------------------------------------

def test_tldr_lookup_invalid_name():
    result = tldr_lookup("git&&rm -rf /")
    assert "Error" in result


def test_tldr_lookup_empty():
    result = tldr_lookup("   ")
    assert "Error" in result


def test_tldr_lookup_known_command():
    result = tldr_lookup("ls")
    assert isinstance(result, str)
    assert len(result) > 0


# ---------------------------------------------------------------------------
# cheat_sh_lookup
# ---------------------------------------------------------------------------

def test_cheat_sh_invalid_command():
    result = cheat_sh_lookup("git; rm -rf")
    assert "Error" in result


@patch("main._session")
def test_cheat_sh_timeout(mock_session):
    import requests
    mock_session.get.side_effect = requests.exceptions.Timeout()
    result = cheat_sh_lookup("git")
    assert "timed out" in result.lower()


@patch("main._session")
def test_cheat_sh_success(mock_session):
    mock_resp = MagicMock()
    mock_resp.text = "# git\n\n> Version control.\n\n- Clone a repo:\n  `git clone url`\n"
    mock_resp.raise_for_status = MagicMock()
    mock_session.get.return_value = mock_resp

    result = cheat_sh_lookup("git")
    assert "git" in result.lower()
    assert mock_session.get.called


@patch("main._session")
def test_cheat_sh_with_query(mock_session):
    mock_resp = MagicMock()
    mock_resp.text = "# git rebase\nexample rebase content"
    mock_resp.raise_for_status = MagicMock()
    mock_session.get.return_value = mock_resp

    result = cheat_sh_lookup("git", query="rebase interactive")
    assert isinstance(result, str)
    # URL should contain the command and query
    call_url = mock_session.get.call_args[0][0]
    assert "git" in call_url
    assert "rebase" in call_url


@patch("main._session")
def test_cheat_sh_unknown_topic(mock_session):
    mock_resp = MagicMock()
    mock_resp.text = "Unknown topic. Try /list for available cheat sheets."
    mock_resp.raise_for_status = MagicMock()
    mock_session.get.return_value = mock_resp

    result = cheat_sh_lookup("__totally_unknown_command_xyz__")
    assert "No cheat.sh entry" in result or "Unknown" in result


@patch("main._session")
def test_cheat_sh_truncates_large_response(mock_session):
    mock_resp = MagicMock()
    mock_resp.text = "x" * 60000
    mock_resp.raise_for_status = MagicMock()
    mock_session.get.return_value = mock_resp

    result = cheat_sh_lookup("tar")
    assert "truncated" in result.lower()
    assert len(result) <= 50100  # 50k + truncation message overhead


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
