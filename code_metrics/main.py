"""MCP server for code metrics: line counts, complexity, and maintainability.

Tools: tokei (LOC count by language), radon (Python cyclomatic complexity,
  maintainability index, raw metrics).
"""

import os
import subprocess
from collections.abc import Callable

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("code_metrics_mcp")


def _run(cmd: list[str], workdir: str, timeout: int = 60) -> tuple[int, str, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=workdir)
        return r.returncode, r.stdout, r.stderr
    except FileNotFoundError:
        return -1, "", f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -1, "", f"timed out after {timeout}s"


@mcp.tool(
    name="code_metrics_loc",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def code_metrics_loc(workdir: str) -> str:
    """Count lines of code per language in a directory using tokei.

    **TRIGGER CONDITION**: Use when exploring an unfamiliar codebase or
    auditing a project to understand its composition and scale.

    **SEQUENCE GUIDANCE**: Call before deeper analysis tools (radon) to
    understand the codebase structure first.

    **CONSTRAINT WARNING**: Requires tokei (pacman -S tokei). Automatically
    respects .gitignore. Output is capped at 500 lines.

    **OUTPUT EXPECTATION**: Table showing files, lines, code, comments, blanks
    per language, plus totals.
    """
    if not os.path.isdir(workdir):
        return f"[code_metrics] Directory not found: {workdir}"

    rc, stdout, stderr = _run(["tokei", "--output", "ascii"], workdir)
    if rc == -1:
        return f"[code_metrics] tokei unavailable — {stderr}"

    output = stdout.strip()
    if not output:
        return f"No source files found in {workdir}"

    # Truncate very long output
    lines = output.split("\n")
    if len(lines) > 500:
        lines = lines[:500]
        lines.append("... (output truncated at 500 lines)")
        output = "\n".join(lines)

    return output


@mcp.tool(
    name="code_metrics_complexity",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def code_metrics_complexity(workdir: str, metric: str = "cyclomatic") -> str:
    """Analyze Python code complexity using radon.

    **TRIGGER CONDITION**: Use when auditing Python code quality — identify
    functions that are too complex, hard to maintain, or need refactoring.

    **SEQUENCE GUIDANCE**: Run after `code_metrics_loc` to get an overview,
    then drill into complexity with this tool. Use `code_metrics_raw` for
    raw per-function lines-of-code stats.

    **CONSTRAINT WARNING**: Requires radon (uv tool install radon). Only
    works on Python code. Sorted by complexity descending (worst first).

    **OUTPUT EXPECTATION**: Per-function complexity scores with grades (A-F).
    A = simple, B = reasonable, C = needs review, D = refactor candidate,
    F = must refactor. Cyclomatic complexity > 10 is a warning flag.
    """
    if not os.path.isdir(workdir):
        return f"[code_metrics] Directory not found: {workdir}"

    if metric not in ("cyclomatic", "maintainability"):
        return f"[code_metrics] Unknown metric '{metric}'. Supported: cyclomatic, maintainability."

    cmd_map: dict[str, list[str]] = {
        "cyclomatic": ["radon", "cc", "--total-average", "--sort", "."],
        "maintainability": ["radon", "mi", "--sort", "."],
    }

    rc, stdout, stderr = _run(cmd_map[metric], workdir)
    if rc == -1:
        return f"[code_metrics] radon unavailable — {stderr}"

    output = stdout.strip()
    if not output:
        return f"No Python files found in {workdir}"

    # Truncate very long output
    lines = output.split("\n")
    if len(lines) > 300:
        lines = lines[:300]
        lines.append("... (output truncated at 300 lines)")
        output = "\n".join(lines)

    return output


@mcp.tool(
    name="code_metrics_raw",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def code_metrics_raw(workdir: str) -> str:
    """Show raw per-function metrics for Python code (LLOC, SLOC, comments).

    **TRIGGER CONDITION**: Use when you need per-function breakdown of lines
    of code — which functions are too long, have too few comments, etc.

    **SEQUENCE GUIDANCE**: Run after `code_metrics_complexity` for a complete
    picture: complexity + raw size metrics.

    **CONSTRAINT WARNING**: Requires radon. Only works on Python code.
    100+ LLOC per function suggests refactoring.

    **OUTPUT EXPECTATION**: Per-function table with LLOC (logical), SLOC
    (source), comments, multi-line strings, blank lines, and single comments.
    """
    if not os.path.isdir(workdir):
        return f"[code_metrics] Directory not found: {workdir}"

    rc, stdout, stderr = _run(["radon", "raw", "--summary", "."], workdir)
    if rc == -1:
        return f"[code_metrics] radon unavailable — {stderr}"

    output = stdout.strip()
    if not output:
        return f"No Python files found in {workdir}"

    lines = output.split("\n")
    if len(lines) > 300:
        lines = lines[:300]
        lines.append("... (output truncated at 300 lines)")
        output = "\n".join(lines)

    return output


if __name__ == "__main__":
    mcp.run(transport="stdio")
