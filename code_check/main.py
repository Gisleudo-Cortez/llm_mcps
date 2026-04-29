#!/usr/bin/env python3
"""MCP server for code formatting and linting.

Mirrors the tool chain used in the Neovim config (conform.nvim + nvim-lint):
  formatters : ruff, prettier, stylua, shfmt, fish_indent, gofumpt, rustfmt,
               sqlfluff, clang-format, google-java-format, taplo, ktlint
  linters    : ruff check, eslint, shellcheck, fish --no-execute, sqlfluff lint,
               yamllint, markdownlint, ktlint
               json / toml: validated via Python stdlib (no subprocess)
"""
import json
import os
import subprocess
import tempfile
import tomllib
from contextlib import contextmanager

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Code Check Server")

# ── Per-language config ───────────────────────────────────────────────────────
# fmt_cmd / lint_cmd : arg list; {file} is replaced with the temp file path.
# fmt_inplace        : True  → formatter writes the file in-place (read back after).
#                      False → formatter writes formatted code to stdout.
# lint_cmd           : None  → no standalone linter for this language.

_CONFIGS: dict[str, dict] = {
    "python": {
        "ext": ".py",
        "fmt_cmd": ["ruff", "format", "{file}"],
        "fmt_inplace": True,
        "lint_cmd": ["ruff", "check", "--output-format", "text", "{file}"],
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
        # gofumpt is a strict superset of gofmt; -w writes in-place
        "fmt_cmd": ["gofumpt", "-w", "{file}"],
        "fmt_inplace": True,
        # go vet needs a proper module — no standalone linter here
        "lint_cmd": None,
    },
    "rust": {
        "ext": ".rs",
        "fmt_cmd": ["rustfmt", "{file}"],
        "fmt_inplace": True,
        # clippy requires a Cargo project — no standalone linter here
        "lint_cmd": None,
    },
    "lua": {
        "ext": ".lua",
        # Match nvim stylua settings from formatting.lua
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
        # Match nvim shfmt settings: 2-space indent, case-indent
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
        # Match nvim sqlfluff dialect setting
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
        "lint_cmd": None,  # validated via _BUILTIN_LINT["json"]
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
        "lint_cmd": None,  # clang-tidy needs compile_commands.json
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
        "lint_cmd": None,  # validated via _BUILTIN_LINT["toml"]
    },
    "kotlin": {
        "ext": ".kt",
        "fmt_cmd": ["ktlint", "--format", "{file}"],
        "fmt_inplace": True,
        "lint_cmd": ["ktlint", "{file}"],
    },
}

# Common language aliases / extensions → canonical key
_ALIASES: dict[str, str] = {
    "py":    "python",
    "js":    "javascript",
    "jsx":   "javascript",
    "ts":    "typescript",
    "tsx":   "typescript",
    "sh":    "shell",
    "zsh":   "shell",
    "yml":   "yaml",
    "md":    "markdown",
    "cc":    "cpp",
    "cxx":   "cpp",
    "h":     "c",
    "hpp":   "cpp",
}


def _resolve_lang(language: str) -> str | None:
    lang = language.lower().strip().lstrip(".")
    if lang in _CONFIGS:
        return lang
    return _ALIASES.get(lang)


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
        json.loads(code)
        return ""
    except json.JSONDecodeError as exc:
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


# ── MCP tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
def format_code(code: str, language: str) -> str:
    """Format source code using the same formatter as the Neovim config.

    **TRIGGER CONDITION**: Use whenever you have generated or edited source code
    that should be properly formatted before being shown to the user or saved to
    a file. Works on snippets, functions, or complete files.

    **SEQUENCE GUIDANCE**: Call before lint_code so the linter sees
    consistently-indented, clean code. For a combined format + lint pass, use
    check_code instead to avoid writing the file twice.

    **CONSTRAINT WARNING**: The formatter binary must be on PATH (same tools
    installed for Neovim via Mason). Returns the original code with an error
    prefix if the tool is missing. Go (gofumpt) and Rust (rustfmt) may reject
    snippets that lack required boilerplate (package declaration, module setup).

    **OUTPUT EXPECTATION**: Returns the formatted source code as a plain string
    on success. On failure, returns the original code prefixed with an error line.

    Supported languages (and aliases):
      python (py), javascript (js, jsx), typescript (ts, tsx), go, rust,
      lua, shell (sh, zsh), bash, fish, sql, yaml (yml), json, markdown (md),
      c (h), cpp (cc, cxx, hpp), java, toml, kotlin
    """
    lang = _resolve_lang(language)
    if lang is None:
        return (
            f"[code_check] Unknown language '{language}'. "
            f"Supported: {', '.join(sorted(_CONFIGS))}"
        )

    cfg = _CONFIGS[lang]
    with _tmp_file(code, cfg["ext"]) as path:
        rc, stdout, stderr = _run(cfg["fmt_cmd"], path)
        if rc == -1:
            return f"[code_check] Formatter unavailable — {stderr}\n\n{code}"
        if rc != 0 and not cfg["fmt_inplace"]:
            # stdout-mode formatter failed
            err = (stderr or stdout).strip()
            return f"[code_check] Formatter error (exit {rc}) — {err}\n\n{code}"
        if cfg["fmt_inplace"]:
            with open(path, encoding="utf-8") as f:
                return f.read()
        return stdout if stdout else code


@mcp.tool()
def lint_code(code: str, language: str) -> str:
    """Check source code for syntax errors and style violations.

    **TRIGGER CONDITION**: Use when you want to verify that generated or edited
    code has no syntax errors or obvious quality issues before presenting it.

    **SEQUENCE GUIDANCE**: Run after format_code so that pure style warnings
    don't obscure real errors. For a combined pass use check_code.

    **CONSTRAINT WARNING**: Not all languages have a standalone linter that
    works on isolated snippets. Languages without one (Go, Rust, C/C++, Java,
    Lua) return "[no linter]". ESLint for JS/TS requires a config file in the
    working directory — without one it will report a config error.

    **OUTPUT EXPECTATION**: Returns linter output as a plain-text string.
    "No issues found." when the linter exits clean.
    "[no linter] ..." when no linter is configured for the language.

    Linter coverage:
      python → ruff check (E, F, I, B, UP, SIM rules)
      javascript / typescript → eslint
      shell / bash → shellcheck
      fish → fish --no-execute
      sql → sqlfluff lint (dialect: ansi)
      yaml → yamllint
      json → json.loads (Python stdlib, no subprocess)
      markdown → markdownlint
      toml → tomllib.loads (Python stdlib, no subprocess)
      kotlin → ktlint
    """
    lang = _resolve_lang(language)
    if lang is None:
        return (
            f"[code_check] Unknown language '{language}'. "
            f"Supported: {', '.join(sorted(_CONFIGS))}"
        )

    # Built-in linters (stdlib, no subprocess)
    if lang in _BUILTIN_LINT:
        result = _BUILTIN_LINT[lang](code)
        return result if result else "No issues found."

    cfg = _CONFIGS[lang]
    if cfg["lint_cmd"] is None:
        return f"[no linter] No standalone linter configured for {lang}."

    with _tmp_file(code, cfg["ext"]) as path:
        rc, stdout, stderr = _run(cfg["lint_cmd"], path)
        if rc == -1:
            return f"[code_check] Linter unavailable — {stderr}"
        combined = "\n".join(filter(None, [stdout.rstrip(), stderr.rstrip()]))
        return combined if combined else "No issues found."


@mcp.tool()
def check_code(code: str, language: str) -> str:
    """Format code and lint it in a single pass, returning both results.

    **TRIGGER CONDITION**: Use as the primary quality gate on any generated
    code block. Prefer this over calling format_code + lint_code separately
    unless you need only one of the two results.

    **SEQUENCE GUIDANCE**: Call once per generated code block. If the LINT
    section reports issues, fix the code and call check_code again. Keep
    iterating until LINT shows "No issues found."

    **CONSTRAINT WARNING**: Both the formatter and linter must be installed on
    PATH. Missing tools are reported inline per section rather than raising an
    error. Snippet limitations (no Go module, no Rust crate, no ESLint config)
    apply — see format_code and lint_code for details.

    **OUTPUT EXPECTATION**: Returns a two-section plain-text report:

        === FORMAT (python) ===
        <formatted code or error>

        === LINT ===
        <issues or "No issues found." or "[no linter] ...">
    """
    lang = _resolve_lang(language)
    if lang is None:
        return (
            f"[code_check] Unknown language '{language}'. "
            f"Supported: {', '.join(sorted(_CONFIGS))}"
        )

    formatted = format_code(code, language)
    # Lint the formatted code when formatting succeeded; fall back to original
    code_to_lint = code if formatted.startswith("[code_check]") else formatted
    lint_result = lint_code(code_to_lint, language)

    return f"=== FORMAT ({lang}) ===\n{formatted}\n\n=== LINT ===\n{lint_result}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
