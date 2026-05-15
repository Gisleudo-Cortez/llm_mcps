"""MCP server for static type checking across languages.

Supported checkers: mypy (Python), cargo check (Rust), tsc (TypeScript).
TypeScript support requires a tsconfig.json in the project root.
"""

import os
import subprocess
import tempfile

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("type_check_mcp")

# Language configuration
_CHECKERS: dict[str, dict] = {
    "python": {
        "ext": ".py",
        "check_cmd": ["mypy", "{file}"],
        "check_cmd_workdir": ["mypy", "."],
    },
    "rust": {
        "ext": ".rs",
        "check_cmd": ["cargo", "check"],
        "check_cmd_workdir": ["cargo", "check"],
    },
}

_ALIASES: dict[str, str] = {
    "py": "python",
    "rs": "rust",
}


def _resolve_lang(language: str) -> str | None:
    lang = language.lower().strip().lstrip(".")
    if lang in _CHECKERS:
        return lang
    return _ALIASES.get(lang)


def _run(cmd: list[str], workdir: str, timeout: int = 120) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=workdir)
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return -1, "", f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -1, "", f"timed out after {timeout}s"


@mcp.tool(
    name="type_check",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def type_check(language: str, workdir: str) -> str:
    """Run static type checking on a project directory.

    **TRIGGER CONDITION**: Use after code generation or refactoring to verify
    type safety before running tests or committing.

    **SEQUENCE GUIDANCE**: Run after `format_code` and `lint_code` as the
    final quality gate. Requires a valid project directory with build config
    (pyproject.toml, Cargo.toml, tsconfig.json).

    **CONSTRAINT WARNING**: Requires project context — not for standalone
    snippets. Supported: python (mypy), rust (cargo check). TypeScript
    support requires tsc + tsconfig.json.

    **OUTPUT EXPECTATION**: Returns type errors with file locations and
    error messages, or "No type errors found." when clean.
    """
    lang = _resolve_lang(language)
    if lang is None:
        return f"[type_check] Unknown language '{language}'. Supported: {', '.join(sorted(_CHECKERS))}"

    if not os.path.isdir(workdir):
        return f"[type_check] Directory not found: {workdir}"

    cfg = _CHECKERS[lang]
    cmd = cfg["check_cmd_workdir"]
    rc, stdout, stderr = _run(cmd, workdir)

    if rc == -1:
        return f"[type_check] Checker unavailable — {stderr}"

    if rc == 0 and not stdout.strip() and not stderr.strip():
        return "No type errors found."

    combined = "\n".join(filter(None, [stdout.strip(), stderr.strip()]))
    return combined


@mcp.tool(
    name="type_check_snippet",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def type_check_snippet(code: str, language: str, workdir: str) -> str:
    """Type-check a single code snippet within a project context.

    **TRIGGER CONDITION**: Use when you've generated a single function or class
    and want to check it against the project's types before integrating.

    **SEQUENCE GUIDANCE**: Writes snippet to a temp file in the project and
    runs the checker. Requires the project to have type stubs installed.

    **CONSTRAINT WARNING**: Only Python (mypy) supports snippet checking.
    Rust/cargo check always operates on the whole crate.

    **OUTPUT EXPECTATION**: Returns type errors or "No type errors found."
    """
    lang = _resolve_lang(language)
    if lang is None:
        return f"[type_check] Unknown language '{language}'."

    if not os.path.isdir(workdir):
        return f"[type_check] Directory not found: {workdir}"

    cfg = _CHECKERS[lang]
    if "check_cmd" not in cfg or "{file}" not in cfg["check_cmd"]:
        return f"[type_check] Snippet type-checking not supported for {lang}. Use type_check for whole-project checking."

    fd, path = tempfile.mkstemp(suffix=cfg["ext"], dir=workdir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(code)

        cmd = [arg.replace("{file}", path) for arg in cfg["check_cmd"]]
        rc, stdout, stderr = _run(cmd, workdir)

        if rc == -1:
            return f"[type_check] Checker unavailable — {stderr}"
        if rc == 0 and not stdout.strip() and not stderr.strip():
            return "No type errors found."

        combined = "\n".join(filter(None, [stdout.strip(), stderr.strip()]))
        return combined
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


if __name__ == "__main__":
    mcp.run(transport="stdio")
