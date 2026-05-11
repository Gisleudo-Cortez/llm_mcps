#!/usr/bin/env python3
"""MCP server for code formatting and linting using the Neovim tool chain.

Supported formatters: ruff, prettier, stylua, shfmt, fish_indent, gofumpt,
  rustfmt, sqlfluff, clang-format, google-java-format, taplo, ktlint
Supported linters: ruff check, eslint, shellcheck, fish --no-execute,
  sqlfluff lint, yamllint, markdownlint, ktlint
Built-in (no subprocess): json, toml validation via Python stdlib.
"""

import json as _json
import os
import subprocess
import tempfile
import tomllib
from contextlib import contextmanager
from enum import Enum
from typing import Literal

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

mcp = FastMCP("code_check_mcp")


# ── Enums ────────────────────────────────────────────────────────────────────────

class _ResponseFormat(str, Enum):
    markdown = "markdown"
    json = "json"


# ── Pydantic input models ────────────────────────────────────────────────────────

class FormatCodeInput(BaseModel):
    """Input for formatting source code."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    code: str = Field(..., description="Source code to format.", min_length=1)
    language: str = Field(
        ...,
        description=(
            "Programming language. Supported: python, javascript, typescript, go, "
            "rust, lua, shell, bash, fish, sql, yaml, json, markdown, c, cpp, "
            "java, toml, kotlin and common aliases (py, js, ts, sh, yml, md, cc, "
            "cxx, h, hpp)."
        ),
        min_length=1,
        max_length=20,
    )


class LintCodeInput(BaseModel):
    """Input for linting source code."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    code: str = Field(..., description="Source code to lint.", min_length=1)
    language: str = Field(
        ...,
        description="Programming language. See format_code for supported values.",
        min_length=1,
        max_length=20,
    )


class CheckCodeInput(BaseModel):
    """Input for combined format + lint in one pass."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    code: str = Field(..., description="Source code to format and lint.", min_length=1)
    language: str = Field(
        ...,
        description="Programming language. See format_code for supported values.",
        min_length=1,
        max_length=20,
    )


# ── Language configuration ───────────────────────────────────────────────────────

# fmt_cmd / lint_cmd : arg list; {file} is replaced with the temp file path.
# fmt_inplace : True  → formatter writes file in-place (read back after).
#               False → formatter writes to stdout.
# lint_cmd : None  → no standalone linter configured for this language.

_CONFIGS: dict[str, dict] = {
    "python": {
        "ext": ".py",
        "fmt_cmd": ["ruff", "format", "{file}"],
        "fmt_inplace": True,
        "lint_cmd": ["ruff", "check", "--output-format", "concise", "{file}"],
    },
    "javascript": {
        "ext": ".js",
        "fmt_cmd": ["prettier", "--write", "{file}"],
        "fmt_inplace": True,
        "lint_cmd": ["eslint", "{file}"],
    },
    "typescript": {
        "ext": ".ts",
        "fmt_cmd": ["prettier", "--write", "{file}"],
        "fmt_inplace": True,
        "lint_cmd": ["eslint", "{file}"],
    },
    "go": {
        "ext": ".go",
        "fmt_cmd": ["gofumpt", "-w", "{file}"],
        "fmt_inplace": True,
        "lint_cmd": None,
    },
    "rust": {
        "ext": ".rs",
        "fmt_cmd": ["rustfmt", "{file}"],
        "fmt_inplace": True,
        "lint_cmd": None,
    },
    "lua": {
        "ext": ".lua",
        "fmt_cmd": [
            "stylua",
            "--column-width", "100",
            "--indent-width", "2",
            "--indent-type", "Spaces",
            "{file}",
        ],
        "fmt_inplace": True,
        "lint_cmd": None,
    },
    "shell": {
        "ext": ".sh",
        "fmt_cmd": ["shfmt", "-i", "2", "-ci", "-w", "{file}"],
        "fmt_inplace": True,
        "lint_cmd": ["shellcheck", "{file}"],
    },
    "bash": {
        "ext": ".sh",
        "fmt_cmd": ["shfmt", "-i", "2", "-ci", "-w", "{file}"],
        "fmt_inplace": True,
        "lint_cmd": ["shellcheck", "--shell=bash", "{file}"],
    },
    "fish": {
        "ext": ".fish",
        "fmt_cmd": ["fish_indent", "-w", "{file}"],
        "fmt_inplace": True,
        "lint_cmd": ["fish", "--no-execute", "{file}"],
    },
    "sql": {
        "ext": ".sql",
        "fmt_cmd": ["sqlfluff", "format", "--dialect", "ansi", "{file}"],
        "fmt_inplace": True,
        "lint_cmd": ["sqlfluff", "lint", "--dialect", "ansi", "{file}"],
    },
    "yaml": {
        "ext": ".yaml",
        "fmt_cmd": ["prettier", "--write", "{file}"],
        "fmt_inplace": True,
        "lint_cmd": ["yamllint", "{file}"],
    },
    "json": {
        "ext": ".json",
        "fmt_cmd": ["prettier", "--write", "{file}"],
        "fmt_inplace": True,
        "lint_cmd": None,
    },
    "markdown": {
        "ext": ".md",
        "fmt_cmd": ["prettier", "--write", "{file}"],
        "fmt_inplace": True,
        "lint_cmd": ["markdownlint", "{file}"],
    },
    "c": {
        "ext": ".c",
        "fmt_cmd": ["clang-format", "-i", "{file}"],
        "fmt_inplace": True,
        "lint_cmd": None,
    },
    "cpp": {
        "ext": ".cpp",
        "fmt_cmd": ["clang-format", "-i", "{file}"],
        "fmt_inplace": True,
        "lint_cmd": None,
    },
    "java": {
        "ext": ".java",
        "fmt_cmd": ["google-java-format", "--replace", "{file}"],
        "fmt_inplace": True,
        "lint_cmd": None,
    },
    "toml": {
        "ext": ".toml",
        "fmt_cmd": ["taplo", "fmt", "{file}"],
        "fmt_inplace": True,
        "lint_cmd": None,
    },
    "kotlin": {
        "ext": ".kt",
        "fmt_cmd": ["ktlint", "--format", "{file}"],
        "fmt_inplace": True,
        "lint_cmd": ["ktlint", "{file}"],
    },
}

# Language aliases → canonical key
_ALIASES: dict[str, str] = {
    "py": "python",
    "js": "javascript",
    "jsx": "javascript",
    "ts": "typescript",
    "tsx": "typescript",
    "sh": "shell",
    "zsh": "shell",
    "yml": "yaml",
    "md": "markdown",
    "cc": "cpp",
    "cxx": "cpp",
    "h": "c",
    "hpp": "cpp",
}


def _resolve_lang(language: str) -> str | None:
    """Resolve a language name or alias to the canonical internal key."""
    lang = language.lower().strip().lstrip(".")
    if lang in _CONFIGS:
        return lang
    return _ALIASES.get(lang)


# ── Helpers ──────────────────────────────────────────────────────────────────────

@contextmanager
def _tmp_file(code: str, ext: str):
    """Write code to a temporary file, yield its path, then clean up."""
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


def _run(cmd_template: list[str], file_path: str, timeout: int = 30) -> tuple[int, str, str]:
    """Execute a command template with {file} replaced by the actual path."""
    cmd = [arg.replace("{file}", file_path) for arg in cmd_template]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return -1, "", f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -1, "", f"timed out after {timeout}s"


def _json_lint(code: str) -> str:
    try:
        _json.loads(code)
        return ""
    except _json.JSONDecodeError as exc:
        return f"JSON parse error: {exc}"


def _toml_lint(code: str) -> str:
    try:
        tomllib.loads(code)
        return ""
    except tomllib.TOMLDecodeError as exc:
        return f"TOML parse error: {exc}"


_BUILTIN_LINT: dict[str, object] = {
    "json": _json_lint,
    "toml": _toml_lint,
}


# ── Tools ────────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="code_check_format_code",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def code_check_format_code(params: FormatCodeInput) -> str:
    """Format source code using the same formatter as the Neovim config.

    **TRIGGER CONDITION**: Use whenever you have generated or edited source
    code that should be properly formatted before being shown to the user or
    saved to a file.

    **SEQUENCE GUIDANCE**: Call before `lint_code` so the linter sees
    consistently-indented, clean code. For a combined pass, use `check_code`.

    **CONSTRAINT WARNING**: Formatter binary must be on PATH. Go (gofumpt) and
    Rust (rustfmt) may reject snippets that lack required boilerplate.

    **OUTPUT EXPECTATION**: Returns the formatted source code as a plain string
    on success, or the original code with an error prefix on failure.
    """
    lang = _resolve_lang(params.language)
    if lang is None:
        return f"[code_check] Unknown language '{params.language}'. Supported: {', '.join(sorted(_CONFIGS))}"

    cfg = _CONFIGS[lang]
    with _tmp_file(params.code, cfg["ext"]) as path:
        rc, stdout, stderr = _run(cfg["fmt_cmd"], path)
        if rc == -1:
            return f"[code_check] Formatter unavailable — {stderr}\n\n{params.code}"
        if rc != 0 and not cfg["fmt_inplace"]:
            err = (stderr or stdout).strip()
            return f"[code_check] Formatter error (exit {rc}) — {err}\n\n{params.code}"
        if cfg["fmt_inplace"]:
            with open(path, encoding="utf-8") as f:
                return f.read()
        return stdout if stdout else params.code


@mcp.tool(
    name="code_check_lint_code",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def code_check_lint_code(params: LintCodeInput) -> str:
    """Check source code for syntax errors and style violations.

    **TRIGGER CONDITION**: Use when you want to verify that generated or edited
    code has no syntax errors or obvious quality issues.

    **SEQUENCE GUIDANCE**: Run after `format_code` so style warnings don't
    obscure real errors. For a combined pass, use `check_code`.

    **CONSTRAINT WARNING**: Not all languages have a standalone linter that
    works on isolated snippets. Returns "[no linter]" when none is configured.

    **OUTPUT EXPECTATION**: Returns linter output as plain text. "No issues
    found." when clean.
    """
    lang = _resolve_lang(params.language)
    if lang is None:
        return f"[code_check] Unknown language '{params.language}'. Supported: {', '.join(sorted(_CONFIGS))}"

    if lang in _BUILTIN_LINT:
        result = _BUILTIN_LINT[lang](params.code)
        return result if result else "No issues found."

    cfg = _CONFIGS[lang]
    if cfg["lint_cmd"] is None:
        return f"[no linter] No standalone linter configured for {lang}."

    with _tmp_file(params.code, cfg["ext"]) as path:
        rc, stdout, stderr = _run(cfg["lint_cmd"], path)
        if rc == -1:
            return f"[code_check] Linter unavailable — {stderr}"
        combined = "\n".join(filter(None, [stdout.rstrip(), stderr.rstrip()]))
        return combined if combined else "No issues found."


@mcp.tool(
    name="code_check_check_code",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def code_check_check_code(params: CheckCodeInput) -> str:
    """Format code and lint it in a single pass, returning both results.

    **TRIGGER CONDITION**: Use as the primary quality gate on any generated
    code block. Prefer this over calling `format_code` + `lint_code` separately.

    **SEQUENCE GUIDANCE**: Call once per generated code block. If the LINT
    section reports issues, fix the code and call `check_code` again. Keep
    iterating until LINT shows "No issues found."

    **OUTPUT EXPECTATION**: Returns a two-section plain-text report:

        === FORMAT (<lang>) ===
        <formatted code or error>

        === LINT ===
        <issues or "No issues found." or "[no linter] ...">
    """
    lang = _resolve_lang(params.language)
    if lang is None:
        return f"[code_check] Unknown language '{params.language}'. Supported: {', '.join(sorted(_CONFIGS))}"

    formatted = code_check_format_code(
        FormatCodeInput(code=params.code, language=params.language)
    )
    code_to_lint = params.code if formatted.startswith("[code_check]") else formatted
    lint_result = code_check_lint_code(
        LintCodeInput(code=code_to_lint, language=params.language)
    )

    return f"=== FORMAT ({lang}) ===\n{formatted}\n\n=== LINT ===\n{lint_result}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
