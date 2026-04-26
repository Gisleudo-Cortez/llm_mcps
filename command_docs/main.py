import re
import subprocess
from typing import Literal

import requests
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Command Docs")

CHEAT_SH_BASE = "https://cheat.sh"

_session = requests.Session()
_session.headers.update(
    {
        "User-Agent": "curl/7.0",
        "Accept": "text/plain",
    }
)


def run_command(cmd_list: list[str], max_chars: int = 50000) -> str:
    """Execute a shell command safely without shell=True, with timeout and truncation."""
    try:
        result = subprocess.run(
            cmd_list,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=30,
        )
        output = result.stdout if result.returncode == 0 else result.stderr
        if not output.strip():
            return "Command executed successfully, but returned no output."
        if len(output) > max_chars:
            return output[:max_chars] + f"\n\n... [Output truncated at {max_chars} characters]"
        return output
    except FileNotFoundError:
        return f"Error: The command '{cmd_list[0]}' was not found. Please ensure it is installed."
    except subprocess.TimeoutExpired:
        return f"Error: Command '{' '.join(cmd_list)}' timed out after 30 seconds."
    except Exception as e:
        return f"Unexpected error: {str(e)}"


@mcp.tool()
def man_lookup(command: str) -> str:
    """
    Retrieve full manual page documentation for terminal commands using `man`.

    **TRIGGER CONDITION:** Use when you need comprehensive command documentation,
    flag descriptions, or complete option reference. Best for unfamiliar commands or
    verifying exact syntax of flags.

    **SEQUENCE GUIDANCE:** Provide exact command name (e.g., "ls", "grep", "systemctl").
    Combine with `tldr_lookup` for quick practical examples, or `cheat_sh_lookup` for
    curated community one-liners. Man pages are authoritative — prefer them for edge cases.

    **CONSTRAINT WARNING:** Command names must be alphanumeric with hyphens/underscores/dots.
    If a page doesn't exist the tool returns an error immediately — check spelling first.
    Some commands have multiple sections (e.g., printf(1) vs printf(3)).

    **OUTPUT EXPECTATION:** Returns the full man page content including SYNOPSIS, DESCRIPTION,
    OPTIONS, EXAMPLES, and SEE ALSO sections. May be verbose for complex commands.

    *Typical workflow:* man_lookup("tar") → tldr_lookup("tar") for quick usage examples.
    """
    if not re.match(r"^[a-zA-Z0-9_.@-]+$", command):
        return "Error: Invalid command name. Use alphanumeric characters with hyphens, underscores, or dots."
    if not command.strip():
        return "Error: Command name cannot be empty."

    output = run_command(["man", "-P", "cat", command])
    if "No manual entry" in output or "can't open" in output.lower():
        return f"Error: No manual page found for '{command}'. Check spelling or ensure the package is installed."
    return output


@mcp.tool()
def tldr_lookup(command: str) -> str:
    """
    Retrieve simplified cheat sheet with practical command examples using `tldr`.

    **TRIGGER CONDITION:** Use when you need quick, practical usage examples rather than
    exhaustive documentation. Ideal for recalling common patterns or learning new commands
    through real-world examples rather than reading full man pages.

    **SEQUENCE GUIDANCE:** Provide the exact command name (e.g., "tar", "docker", "git").
    TLDR pages cover the most common 80% of usage. For advanced flags or complete reference,
    follow up with `man_lookup`. For curated recipes, try `cheat_sh_lookup`.

    **CONSTRAINT WARNING:** Not all commands have TLDR pages — the tool returns a clear
    message if none exists. Examples are community-contributed and may not cover edge cases.
    Focuses on practical usage rather than complete flag enumeration.

    **OUTPUT EXPECTATION:** Returns simplified cheat sheet with concrete command invocations,
    grouped by use case. Ideal for quick reference and workflow acceleration.

    *Typical workflow:* tldr_lookup("docker") → man_lookup("docker") for advanced options.
    """
    if not re.match(r"^[a-zA-Z0-9_.@-]+$", command):
        return "Error: Invalid command name. Use alphanumeric characters with hyphens, underscores, or dots."
    if not command.strip():
        return "Error: Command name cannot be empty."

    output = run_command(["tldr", command])
    if "No documentation" in output or "not found" in output.lower():
        return f"No TLDR page found for '{command}'. Try man_lookup or cheat_sh_lookup instead."
    return output


@mcp.tool()
def cheat_sh_lookup(
    command: str,
    query: str = "",
    style: Literal["plain", "color"] = "plain",
) -> str:
    """
    Fetch concise, curated command cheat sheets from cheat.sh (cht.sh).

    **TRIGGER CONDITION:** Use when you need community-curated command examples, recipes,
    or language-specific snippets. Excellent complement to man pages and tldr — especially
    for finding real-world one-liners and patterns not covered by official docs.

    **SEQUENCE GUIDANCE:** Use `command` for the tool/language name (e.g., "git", "awk",
    "python"). Add `query` to search for specific use cases (e.g., command="git",
    query="rebase interactive"). Supports programming language queries too:
    command="python", query="list comprehension".

    **CONSTRAINT WARNING:** Requires internet connectivity to reach cheat.sh. Command must
    be alphanumeric with hyphens/underscores/dots. Query characters outside alphanumeric,
    spaces, and hyphens are stripped. Responses may be large for generic queries — use
    `query` parameter to narrow scope.

    **OUTPUT EXPECTATION:** Returns formatted cheat sheet with commented examples and
    explanations. Typically more opinionated and practical than man pages.

    *Examples:*
    - cheat_sh_lookup("tar") → common tar patterns
    - cheat_sh_lookup("git", "rebase") → git rebase recipes
    - cheat_sh_lookup("python", "dictionary") → Python dict one-liners
    - cheat_sh_lookup("awk", "print column") → awk field extraction
    """
    if not re.match(r"^[a-zA-Z0-9_.@-]+$", command):
        return "Error: Invalid command name. Use alphanumeric characters with hyphens, underscores, or dots."

    path = command
    if query.strip():
        safe_query = re.sub(r"[^\w\s-]", "", query).strip().replace(" ", "+")
        if safe_query:
            path = f"{command}/{safe_query}"

    url = f"{CHEAT_SH_BASE}/{path}"
    params: dict = {}
    if style == "plain":
        params["T"] = ""  # ?T strips ANSI color codes

    try:
        resp = _session.get(url, params=params, timeout=15)
        resp.raise_for_status()
        text = resp.text.strip()
        if not text or "Unknown topic" in text:
            return f"No cheat.sh entry found for '{path}'. Try man_lookup or tldr_lookup instead."
        if len(text) > 50000:
            text = text[:50000] + "\n\n... [Response truncated at 50k chars]"
        return text
    except requests.exceptions.Timeout:
        return "Error: cheat.sh request timed out after 15 seconds."
    except requests.exceptions.RequestException as e:
        return f"Error fetching from cheat.sh: {str(e)}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
