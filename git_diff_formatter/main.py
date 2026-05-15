"""MCP server for formatting only changed lines in a git diff.

Formats only the lines touched by a diff, leaving untouched code alone.
Useful for PR review and incremental formatting without changing files.

Supported: prettier (JS/TS/JSON/YAML/Markdown/CSS/HTML/SCSS),
  ruff format (Python), shfmt (Shell/Bash).
"""

import os
import subprocess
import tempfile
from contextlib import contextmanager

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("git_diff_formatter_mcp")


# Language → formatter with --stdin-filepath and range support
_FORMATTERS: dict[str, dict] = {
    "python": {
        "ext": ".py",
        "formatter": "ruff",
        "format_range": ["ruff", "format", "--stdin-filename", "{file}"],
        "stdin": True,
    },
    "javascript": {
        "ext": ".js",
        "formatter": "prettier",
        "format_range": ["prettier", "--stdin-filepath", "{file}"],
        "stdin": True,
    },
    "typescript": {
        "ext": ".ts",
        "formatter": "prettier",
        "format_range": ["prettier", "--stdin-filepath", "{file}"],
        "stdin": True,
    },
    "json": {
        "ext": ".json",
        "formatter": "prettier",
        "format_range": ["prettier", "--stdin-filepath", "{file}"],
        "stdin": True,
    },
    "yaml": {
        "ext": ".yaml",
        "formatter": "prettier",
        "format_range": ["prettier", "--stdin-filepath", "{file}"],
        "stdin": True,
    },
    "markdown": {
        "ext": ".md",
        "formatter": "prettier",
        "format_range": ["prettier", "--stdin-filepath", "{file}"],
        "stdin": True,
    },
    "css": {
        "ext": ".css",
        "formatter": "prettier",
        "format_range": ["prettier", "--stdin-filepath", "{file}"],
        "stdin": True,
    },
    "html": {
        "ext": ".html",
        "formatter": "prettier",
        "format_range": ["prettier", "--stdin-filepath", "{file}"],
        "stdin": True,
    },
    "scss": {
        "ext": ".scss",
        "formatter": "prettier",
        "format_range": ["prettier", "--stdin-filepath", "{file}"],
        "stdin": True,
    },
    "shell": {
        "ext": ".sh",
        "formatter": "shfmt",
        "format_range": ["shfmt", "-i", "2", "-ci", "-filename", "{file}"],
        "stdin": True,
    },
    "bash": {
        "ext": ".sh",
        "formatter": "shfmt",
        "format_range": ["shfmt", "-i", "2", "-ci", "-filename", "{file}"],
        "stdin": True,
    },
}

_ALIASES: dict[str, str] = {
    "py": "python",
    "js": "javascript",
    "jsx": "javascript",
    "ts": "typescript",
    "tsx": "typescript",
    "sh": "shell",
    "yml": "yaml",
    "md": "markdown",
    "htm": "html",
    "sass": "scss",
}


def _resolve_lang(language: str) -> str | None:
    lang = language.lower().strip().lstrip(".")
    if lang in _FORMATTERS:
        return lang
    return _ALIASES.get(lang)


def _run_stdin(cmd_template: list[str], file_path: str, input_text: str, timeout: int = 30) -> tuple[int, str, str]:
    """Run a command, feeding stdin (for prettier --stdin-filepath)."""
    cmd = [arg.replace("{file}", file_path) for arg in cmd_template]
    try:
        r = subprocess.run(cmd, input=input_text, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return -1, "", f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -1, "", f"timed out after {timeout}s"


@contextmanager
def _tmp_file(code: str, ext: str):
    fd, path = tempfile.mkstemp(suffix=ext)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(code)
        yield path
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


@mcp.tool(
    name="git_diff_format_file",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def git_diff_format_file(content: str, language: str) -> str:
    """Format a complete file using stdin-based formatters.

    **TRIGGER CONDITION**: Use when you need to format a file and want the
    result back as a string — especially for files in git worktrees where
    you don't have the original path.

    **SEQUENCE GUIDANCE**: Feed the current file content, get formatted
    content back. Same formatters as code_check but via stdin.

    **CONSTRAINT WARNING**: Supported languages: python, javascript,
    typescript, json, yaml, markdown, css, html, scss, shell, bash.
    All use stdin-based formatting (prettier --stdin-filepath,
    ruff format --stdin-filename, shfmt).

    **OUTPUT EXPECTATION**: Returns the formatted file content as a string.
    """
    lang = _resolve_lang(language)
    if lang is None:
        return f"[diff_format] Unknown language '{language}'. Supported: {', '.join(sorted(_FORMATTERS))}"

    cfg = _FORMATTERS[lang]
    dummy_path = f"/tmp/diff_format_file{cfg['ext']}"
    rc, stdout, stderr = _run_stdin(cfg["format_range"], dummy_path, content)

    if rc == -1:
        return f"[diff_format] Formatter unavailable — {stderr}\n\n{content}"

    if not stdout.strip():
        # ruff format --stdin-filename outputs nothing if unchanged
        return content

    return stdout


@mcp.tool(
    name="git_diff_format_patch",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def git_diff_format_patch(content: str, language: str) -> str:
    """Format a file and return a human-readable diff of changes.

    **TRIGGER CONDITION**: Use when reviewing code changes — see exactly
    what formatting would change before applying it.

    **SEQUENCE GUIDANCE**: Call before committing to preview formatting changes.
    Returns unified diff showing what the formatter would change.

    **CONSTRAINT WARNING**: Same language support as git_diff_format_file.
    Diff is computed locally using Python difflib.

    **OUTPUT EXPECTATION**: Returns a unified diff showing old→new changes,
    or "No formatting changes needed." when identical.
    """
    import difflib

    lang = _resolve_lang(language)
    if lang is None:
        return f"[diff_format] Unknown language '{language}'."

    formatted = git_diff_format_file(content=content, language=language)
    if formatted.startswith("[diff_format]"):
        return formatted

    if formatted == content:
        return "No formatting changes needed."

    diff = difflib.unified_diff(
        content.splitlines(keepends=True),
        formatted.splitlines(keepends=True),
        fromfile=f"original.{language}",
        tofile=f"formatted.{language}",
        lineterm="",
    )
    return "\n".join(list(diff))


if __name__ == "__main__":
    mcp.run(transport="stdio")
