#!/usr/bin/env python3
"""MCP server for code formatting, linting, import sorting, spell checking, and auto-fixing.

Formatters: ruff, prettier, stylua, shfmt, fish_indent, gofumpt, rustfmt,
  sqlfluff, clang-format, google-java-format, taplo, ktlint
Linters: ruff check, eslint, shellcheck, fish --no-execute, sqlfluff lint,
  yamllint, mdl, ktlint
Import sorters: ruff (Python)
Spell checker: codespell
Auto-fixers: ruff --fix, eslint --fix, sqlfluff fix
Built-in (no subprocess): json, toml validation via Python stdlib.
"""

import json as _json
import os
import subprocess
import tempfile
import tomllib
from contextlib import contextmanager

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("code_check_mcp")


# ── Language configuration ───────────────────────────────────────────────────────

_CONFIGS: dict[str, dict] = {
    "python": {
        "ext": ".py",
        "fmt_cmd": ["ruff", "format", "{file}"],
        "fmt_inplace": True,
        "lint_cmd": ["ruff", "check", "--output-format", "concise", "{file}"],
        "fix_cmd": ["ruff", "check", "--fix", "{file}"],
        "fix_inplace": True,
        "sort_cmd": ["ruff", "check", "--select", "I", "--fix", "{file}"],
        "sort_inplace": True,
    },
    "javascript": {
        "ext": ".js",
        "fmt_cmd": ["prettier", "--write", "{file}"],
        "fmt_inplace": True,
        "lint_cmd": ["eslint", "{file}"],
        "fix_cmd": ["eslint", "--fix", "{file}"],
        "fix_inplace": True,
    },
    "typescript": {
        "ext": ".ts",
        "fmt_cmd": ["prettier", "--write", "{file}"],
        "fmt_inplace": True,
        "lint_cmd": ["eslint", "{file}"],
        "fix_cmd": ["eslint", "--fix", "{file}"],
        "fix_inplace": True,
    },
    "html": {
        "ext": ".html",
        "fmt_cmd": ["prettier", "--write", "{file}"],
        "fmt_inplace": True,
        "lint_cmd": None,
    },
    "css": {
        "ext": ".css",
        "fmt_cmd": ["prettier", "--write", "{file}"],
        "fmt_inplace": True,
        "lint_cmd": None,
    },
    "scss": {
        "ext": ".scss",
        "fmt_cmd": ["prettier", "--write", "{file}"],
        "fmt_inplace": True,
        "lint_cmd": None,
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
        "fix_cmd": ["sqlfluff", "fix", "--dialect", "ansi", "{file}"],
        "fix_inplace": True,
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
        "lint_cmd": ["mdl", "{file}"],
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
    # New: HTML/CSS aliases
    "htm": "html",
    "sass": "scss",
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


def _resolve_and_get_lang(language: str) -> str | None:
    """Resolve language, return None with error string for caller."""
    return _resolve_lang(language)


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
def code_check_format_code(code: str, language: str) -> str:
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
    lang = _resolve_lang(language)
    if lang is None:
        return f"[code_check] Unknown language '{language}'. Supported: {', '.join(sorted(_CONFIGS))}"

    cfg = _CONFIGS[lang]
    with _tmp_file(code, cfg["ext"]) as path:
        rc, stdout, stderr = _run(cfg["fmt_cmd"], path)
        if rc == -1:
            return f"[code_check] Formatter unavailable — {stderr}\n\n{code}"
        if rc != 0 and not cfg["fmt_inplace"]:
            err = (stderr or stdout).strip()
            return f"[code_check] Formatter error (exit {rc}) — {err}\n\n{code}"
        if cfg["fmt_inplace"]:
            with open(path, encoding="utf-8") as f:
                return f.read()
        return stdout if stdout else code


@mcp.tool(
    name="code_check_lint_code",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def code_check_lint_code(code: str, language: str) -> str:
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
    lang = _resolve_lang(language)
    if lang is None:
        return f"[code_check] Unknown language '{language}'. Supported: {', '.join(sorted(_CONFIGS))}"

    if lang in _BUILTIN_LINT:
        result = _BUILTIN_LINT[lang](code)
        return result if result else "No issues found."

    cfg = _CONFIGS[lang]
    if cfg.get("lint_cmd") is None:
        return f"[no linter] No standalone linter configured for {lang}."

    with _tmp_file(code, cfg["ext"]) as path:
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
def code_check_check_code(code: str, language: str) -> str:
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
    lang = _resolve_lang(language)
    if lang is None:
        return f"[code_check] Unknown language '{language}'. Supported: {', '.join(sorted(_CONFIGS))}"

    formatted = code_check_format_code(code=code, language=language)
    code_to_lint = code if formatted.startswith("[code_check]") else formatted
    lint_result = code_check_lint_code(code=code_to_lint, language=language)

    return f"=== FORMAT ({lang}) ===\n{formatted}\n\n=== LINT ===\n{lint_result}"


@mcp.tool(
    name="code_check_sort_imports",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def code_check_sort_imports(code: str, language: str) -> str:
    """Sort imports in source code using language-specific tools.

    **TRIGGER CONDITION**: Use when generated or edited code has imports in
    non-standard order (os before sys, third-party before stdlib, etc.).

    **SEQUENCE GUIDANCE**: Call before `format_code` so formatting sees
    properly-ordered imports. Only Python is currently supported.

    **CONSTRAINT WARNING**: Only Python (ruff isort rules). Returns original
    code unchanged for unsupported languages.

    **OUTPUT EXPECTATION**: Returns the code with imports sorted on success,
    or the original code with an error prefix on failure.
    """
    lang = _resolve_lang(language)
    if lang is None:
        return code  # silent no-op for unknown languages

    cfg = _CONFIGS[lang]
    if "sort_cmd" not in cfg:
        return f"[code_check] Import sorting not configured for {lang}."

    with _tmp_file(code, cfg["ext"]) as path:
        rc, stdout, stderr = _run(cfg["sort_cmd"], path)
        if rc == -1:
            return f"[code_check] Import sorter unavailable — {stderr}\n\n{code}"
        if cfg.get("sort_inplace"):
            with open(path, encoding="utf-8") as f:
                return f.read()
        return stdout if stdout else code


@mcp.tool(
    name="code_check_spell",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def code_check_spell(code: str, language: str) -> str:
    """Check source code comments and strings for common spelling errors.

    **TRIGGER CONDITION**: Use when you've written docstrings, comments, or
    string literals and want to catch typos like 'recieve', 'occured', etc.

    **SEQUENCE GUIDANCE**: Run after `format_code` as a final quality pass.
    codespell skips code identifiers and only checks comments/strings.

    **CONSTRAINT WARNING**: codespell must be installed (pacman -S codespell).
    Uses a built-in dictionary of common misspellings — not a full
    spell-checker. Returns "No issues found." when clean.

    **OUTPUT EXPECTATION**: Returns misspelling report or "No issues found."
    """
    lang = _resolve_lang(language)
    if lang is None:
        return f"[code_check] Unknown language '{language}'."

    cfg = _CONFIGS[lang]
    with _tmp_file(code, cfg["ext"]) as path:
        rc, stdout, stderr = _run(["codespell", "{file}"], path)
        if rc == -1:
            return f"[code_check] codespell unavailable — {stderr}"
        combined = "\n".join(filter(None, [stdout.strip(), stderr.strip()]))
        return combined if combined else "No spelling errors found."


@mcp.tool(
    name="code_check_fix_code",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def code_check_fix_code(code: str, language: str) -> str:
    """Auto-fix lint violations in source code (where supported).

    **TRIGGER CONDITION**: Use after `lint_code` reports fixable issues.
    Applies safe auto-fixes without changing code behavior.

    **SEQUENCE GUIDANCE**: The complete quality pipeline:
    1. `check_code` → see what's wrong
    2. `fix_code` → auto-fix what can be fixed
    3. `check_code` → verify remaining issues

    **CONSTRAINT WARNING**: Not all languages support auto-fixing. Currently
    supported: python (ruff --fix), javascript/typescript (eslint --fix),
    sql (sqlfluff fix). Returns original code with message for unsupported
    languages.

    **OUTPUT EXPECTATION**: Returns the auto-fixed code on success, or the
    original code with a message when fixing isn't available.
    """
    lang = _resolve_lang(language)
    if lang is None:
        return f"[code_check] Unknown language '{language}'."

    cfg = _CONFIGS[lang]
    if "fix_cmd" not in cfg:
        return f"[code_check] Auto-fix not configured for {lang}. Supported: {', '.join(sorted(k for k, v in _CONFIGS.items() if 'fix_cmd' in v))}\n\n{code}"

    with _tmp_file(code, cfg["ext"]) as path:
        rc, stdout, stderr = _run(cfg["fix_cmd"], path)
        if rc == -1:
            return f"[code_check] Fixer unavailable — {stderr}\n\n{code}"

        fixed = code
        if cfg.get("fix_inplace"):
            with open(path, encoding="utf-8") as f:
                fixed = f.read()

        # ruff --fix outputs what was fixed to stdout even in inplace mode
        fixes = (stdout.strip() or stderr.strip())
        header = ""
        if fixes:
            header = f"--- Applied fixes ---\n{fixes}\n\n--- Fixed code ---\n"
        else:
            header = "--- No fixes applied (code unchanged) ---\n"

        return header + fixed


if __name__ == "__main__":
    mcp.run(transport="stdio")
