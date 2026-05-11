import os
import subprocess
import sys
import tempfile

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("python_repl_mcp")


@mcp.tool(
    name="repl_execute_python",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
}
)
def execute_python(code: str, timeout: int = 30) -> str:
    """
    Execute Python code in an isolated subprocess and return stdout/stderr.

    **TRIGGER CONDITION:** Use when you need to run calculations, transform data, test
    logic, parse files, generate plots, or do anything that benefits from actual code
    execution rather than reasoning alone.

    **SEQUENCE GUIDANCE:** Each call runs in a fresh interpreter—variables do NOT persist
    between calls. For multi-step workflows, put all required logic in a single call. Use
    `print()` to surface values; expressions alone produce no output.

    **CONSTRAINT WARNING:** Timeout is capped at 60 seconds regardless of input. Each call
    spawns a real subprocess—avoid tight infinite loops. The interpreter has access to all
    packages installed in the system Python (`sys.executable`). The subprocess receives only
    essential system environment variables (PATH, HOME, LANG, etc.) — API keys, database
    URLs, and other secrets from the parent process are intentionally excluded for security.
    Network access is allowed but unguarded—use with care.

    **OUTPUT EXPECTATION:** Returns combined stdout and stderr, truncated at 50k chars.
    Non-zero exit codes are reported alongside error output. Empty output means the code
    ran but produced no print statements.

    *Examples:*
    - `print(sum(range(1000)))` → arithmetic
    - `import pandas as pd; df = pd.read_csv('/path/file.csv'); print(df.describe())` → data analysis
    - `import json, pathlib; print(json.loads(pathlib.Path('/tmp/data.json').read_text()))` → file parsing
    """
    timeout = min(max(1, int(timeout)), 60)

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(code)
        tmp_path = f.name

    try:
        # Restrict environment to only essential system variables — no API keys, secrets, or credentials
        _SAFE_ENV_KEYS = frozenset({
            "PATH", "HOME", "USER", "SHELL", "TERM", "LANG", "LC_ALL", "LC_CTYPE",
            "PYTHONPATH", "PYTHONIOENCODING", "TMPDIR", "TEMP", "TMP",
            "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME",
        })
        safe_env = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS}

        result = subprocess.run(
            [sys.executable, tmp_path],
            capture_output=True,
            text=True,
            timeout=timeout,
            errors="replace",
            env=safe_env,
        )

        stdout = result.stdout.rstrip()
        stderr = result.stderr.rstrip()

        if result.returncode != 0:
            combined = f"Exit code {result.returncode}\n"
            if stdout:
                combined += stdout + "\n"
            if stderr:
                combined += "--- stderr ---\n" + stderr
        elif stdout and stderr:
            combined = stdout + "\n--- stderr ---\n" + stderr
        elif stdout:
            combined = stdout
        elif stderr:
            combined = "--- stderr ---\n" + stderr
        else:
            combined = f"Executed successfully (exit {result.returncode}, no output)."

        if len(combined) > 50000:
            combined = combined[:50000] + "\n... [output truncated at 50k chars]"

        return combined

    except subprocess.TimeoutExpired:
        return f"Error: Execution timed out after {timeout} seconds."
    except Exception as e:
        return f"Error: {str(e)}"
    finally:
        os.unlink(tmp_path)


@mcp.tool(
    name="repl_list_installed_packages",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
}
)
def list_installed_packages() -> str:
    """
    List all packages installed in the current Python environment.

    **TRIGGER CONDITION:** Use before `execute_python` when you're unsure whether a
    required library is available. Prevents import errors at execution time.

    **OUTPUT EXPECTATION:** Returns a sorted list of package names and versions, one per line.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "list", "--format=columns"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        return result.stdout if result.stdout else result.stderr
    except Exception as e:
        return f"Error listing packages: {str(e)}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
