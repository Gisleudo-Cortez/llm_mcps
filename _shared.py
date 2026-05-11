"""Shared helpers used across multiple MCP servers in this collection."""

import subprocess


def run_command(cmd_list: list[str], max_chars: int = 50000) -> str:
    """Execute a shell command safely without using shell=True.

    Captures stdout and stderr, handles decoding errors, and truncates large outputs.
    Enforces a 30-second timeout to prevent hanging processes.
    """
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
            return (
                output[:max_chars]
                + f"\n\n... [Output truncated at {max_chars} characters to protect context]"
            )

        return output

    except FileNotFoundError:
        binary = cmd_list[0]
        return f"Error: The command '{binary}' was not found on the system. Please ensure it is installed."
    except subprocess.TimeoutExpired:
        return f"Error: Command '{' '.join(cmd_list)}' timed out after 30 seconds."
    except Exception as e:
        return f"Unexpected error executing command: {str(e)}"