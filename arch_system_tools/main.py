import os
import re
import shutil
import sys
from typing import Literal

from mcp.server.fastmcp import FastMCP

# Import shared helper from parent directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _shared import run_command

# Initialize the MCP server
mcp = FastMCP("arch_mcp")


# --- Tool 1: Directory Listing (eza) ---
@mcp.tool(
    name="arch_list_directory",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def list_directory(path: str = ".") -> str:
    """
    List directory contents using 'eza' before navigating or inspecting files.

    **TRIGGER CONDITION:** Use this when you need to understand the structure of a
    directory before performing operations like reading files, searching contents,
    or running commands within that location.

    **SEQUENCE GUIDANCE:** Always call `list_directory` after receiving a new path
    from user input. Use the returned file list to determine which files exist before
    calling `read_file` or `search_contents`. For nested directories, you may need to
    call this recursively with different paths.

    **CONSTRAINT WARNING:** Avoid using on paths with excessive subdirectories (>1000 entries)
    as output may be truncated. If the path doesn't exist, check spelling before retrying.
    The tool returns plain text only—no colors or icons for clean LLM parsing.

    **OUTPUT EXPECTATION:** Returns formatted listing with file permissions, sizes, and names.
    Ideal for discovering available files, understanding directory structure, and planning
    subsequent operations like content search or file reading.

    *Typical workflow:* list_directory → read_file → search_contents (as needed)
    """
    if not os.path.exists(path):
        return f"Error: Path '{path}' does not exist."

    cmd = [
        "eza",
        "-al",
        "--color=never",  # Crucial: prevents ANSI escape code bloat
        "--group-directories-first",
        path,
    ]
    return run_command(cmd)


# --- Tool 2: Read File (cat) ---
@mcp.tool(
    name="arch_read_file",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def read_file(file_path: str, max_lines: int = 1000) -> str:
    """
    Extract text content from a file using 'cat' with optional line limiting.

    **TRIGGER CONDITION:** Use this when you need to inspect the contents of a known
    file (e.g., configuration files, source code, logs). Call after `list_directory`
    confirms the file exists at the specified path.

    **SEQUENCE GUIDANCE:** Always call `get_doc_metadata` first for large documents
    (>100 pages PDFs or >50MB files) to determine optimal `max_lines` settings. For
    very large files, read in chunks by calling multiple times with adjusted line offsets.

    **CONSTRAINT WARNING:** Avoid reading binary files (images, executables) as output
    will be garbled text. Never set `max_lines` below 50 for meaningful content analysis.
    If file doesn't exist or is a directory, the tool returns an error—verify path first.

    **OUTPUT EXPECTATION:** Returns clean text content truncated at max_lines if needed.
    Ideal for reading configuration files, source code inspection, log review, and
    extracting data from structured text files (CSV, JSON, YAML).

    *Error recovery:* If output is truncated, call again with higher `max_lines` or verify
    file type before attempting read operations.
    """
    if not os.path.isfile(file_path):
        return f"Error: File '{file_path}' does not exist or is a directory."

    cmd = ["cat", file_path]
    output = run_command(cmd)

    # Apply line-based truncation
    lines = output.splitlines()
    if len(lines) > max_lines:
        return (
            "\n".join(lines[:max_lines])
            + f"\n\n... [File truncated after {max_lines} lines]"
        )

    return output


# --- Tool 3: Search File Contents (ugrep) ---
@mcp.tool(
    name="arch_search_contents",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def search_contents(pattern: str, path: str = ".", max_matches: int = 100) -> str:
    """
    Find text patterns across files using 'ugrep' with regex support and binary filtering.

    **TRIGGER CONDITION:** Use this when you need to locate specific content within
    multiple files without knowing exact file names. Ideal for finding function definitions,
    configuration values, or error messages across a codebase or document collection.

    **SEQUENCE GUIDANCE:** Call after `list_directory` to understand the target directory structure.
    For large directories, use `path` parameter to narrow scope before searching. Use results
    from this tool to inform subsequent `read_file` calls on specific matching files.

    **CONSTRAINT WARNING:** Avoid using complex regex patterns that may cause excessive matches
    or performance issues. The tool automatically ignores binary files—don't expect matches in
    images, executables, or compressed archives. Set `max_matches` appropriately to balance
    completeness vs. context window usage.

    **OUTPUT EXPECTATION:** Returns matched lines with file paths and line numbers (1-indexed).
    Ideal for code exploration, finding configuration values, locating error messages, and
    discovering where specific terms appear across a project or system.

    *Typical workflow:* list_directory → search_contents(pattern="function_name") → read_file(matching_path)
    """
    if shutil.which("ugrep"):
        cmd = [
            "ugrep",
            "-rnI",  # r: recursive, n: line numbers, I: ignore binaries
            "--color=never",
            "-m",
            str(max_matches),
            pattern,
            path,
        ]
    else:
        cmd = [
            "grep",
            "-rn",
            "--color=never",
            "--binary-files=without-match",
            "-m",
            str(max_matches),
            pattern,
            path,
        ]
    return run_command(cmd)


# --- Tool 4: Package Management (pacman / paru) ---
@mcp.tool(
    name="arch_query_packages",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def query_packages(
    manager: Literal["pacman", "paru"],
    operation: Literal["search_repo", "search_local", "info_repo", "info_local"],
    query: str,
) -> str:
    """
    Safely query Arch Linux package databases for search and information lookups.

    **TRIGGER CONDITION:** Use this when you need to verify if a package is installed,
    check available versions in repositories, or find which package provides a specific tool.
    Call before attempting any installation operations to confirm availability first.

    **SEQUENCE GUIDANCE:** Always call `search_local` first to check if a package exists
    on your system. Use `info_local` for detailed installed package metadata. For checking
    upstream versions, use `search_repo` or `info_repo`. This tool is read-only—do not attempt
    installation or removal operations through this interface.

    **CONSTRAINT WARNING:** Do not use for installation or removal commands (pacman -S, pacman -R).
    These operations require explicit user confirmation and are intentionally excluded from MCP tools.
    Package names may be case-sensitive in some contexts—use exact naming when possible.

    **OUTPUT EXPECTATION:** Returns package search results with version numbers, descriptions,
    and installation status. Ideal for verifying tool availability, checking installed software,
    finding alternative packages, and understanding system dependencies before modifications.

    *Safety note:* All operations are read-only; no system changes can occur through this tool.
    """
    # Map high-level operations to safe package manager flags
    flag_map = {
        "search_repo": "-Ss",  # Search sync databases
        "search_local": "-Qs",  # Search local database
        "info_repo": "-Si",  # View package info (sync)
        "info_local": "-Qi",  # View package info (local)
    }

    flag = flag_map.get(operation)
    if not flag:
        return f"Error: Invalid operation '{operation}'."

    cmd = [manager, flag, query]
    return run_command(cmd)


# --- Tool 5: Git Operations Suite ---
@mcp.tool(
    name="arch_git_operations",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def git_operations(
    operation: Literal["status", "diff", "log", "show", "blame"],
    repo_path: str = ".",
    target: str = "",
    max_lines: int = 500,
) -> str:
    """
    Inspect Git repository state for code review, debugging, and commit preparation.

    **TRIGGER CONDITION:** Use this when you need to understand the current state of a
    Git repository before making changes, reviewing work, or preparing commits. Call after
    navigating to the project directory using `list_directory`.

    **SEQUENCE GUIDANCE:** Start with `operation="status"` for an overview of modified/untracked files.
    Use `operation="diff"` to see exact code changes in working directory vs. last commit.
    Use `operation="log"` for commit history review (limited to 10 most recent by default).
    Use `operation="show"` with `target=<commit_hash>` to inspect a specific commit's changes.
    Use `operation="blame"` with `target=<file_path>` to see which commit last changed each line.
    Always verify repo_path points to a valid Git repository before calling.

    **CONSTRAINT WARNING:** Do not attempt write operations (commit, push, branch creation) through this tool—
    it is read-only only. For large repositories with extensive diffs, the output may be truncated
    at `max_lines` for context protection. The repo_path must contain a `.git` directory or the call will fail.
    `show` and `blame` require a non-empty `target` parameter.

    **OUTPUT EXPECTATION:** Returns formatted Git state information: status shows modified files, diff shows code changes, log shows commit history, show displays a commit's full patch, blame annotates each line with its last commit.
    Ideal for code review preparation, debugging merge conflicts, understanding project history,
    and verifying repository state before making modifications.

    *Typical workflow:* git_operations(operation="status") → read_file(modified_file) → git_operations(operation="diff")
    """
    # Validate the directory is actually a Git repository
    if not os.path.isdir(os.path.join(repo_path, ".git")):
        # Note: This simple check misses worktrees or submodules, but is safe for standard repos
        return (
            f"Error: '{repo_path}' does not appear to be the root of a Git repository."
        )

    base_cmd = ["git", "-C", repo_path]

    if operation == "status":
        cmd = base_cmd + ["status", "--short", "--branch"]
    elif operation == "diff":
        cmd = base_cmd + ["diff", "--no-color"]
    elif operation == "log":
        cmd = base_cmd + ["log", "-n", "10", "--oneline", "--no-color"]
    elif operation == "show":
        if not target:
            return "Error: 'target' must be a commit hash for the 'show' operation."
        cmd = base_cmd + ["show", "--no-color", target]
    elif operation == "blame":
        if not target:
            return "Error: 'target' must be a file path for the 'blame' operation."
        cmd = base_cmd + ["blame", "--no-color", target]
    else:
        return f"Error: Unsupported git operation '{operation}'."

    output = run_command(cmd)

    # Apply line-based truncation to protect context (especially crucial for large diffs)
    lines = output.splitlines()
    if len(lines) > max_lines:
        return (
            "\n".join(lines[:max_lines])
            + f"\n\n... [Git output truncated after {max_lines} lines to protect context]"
        )

    return output


# --- Tool 6: Systemd Logs Explorer ---
@mcp.tool(
    name="arch_systemd_logs",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def systemd_logs(service: str = "", lines: int = 50, boot_only: bool = True) -> str:
    """
    Retrieve system and service logs using `journalctl` for debugging failures.

    **TRIGGER CONDITION:** Use this when services are failing, crashes occur, or you need
    to investigate system events. Call after identifying a suspect service name from `service_status`.

    **SEQUENCE GUIDANCE:** Start with `boot_only=True` for current session logs (less noise).
    Set `boot_only=False` if investigating issues that occurred in previous boot cycles. Use
    the `service` parameter to narrow results to a specific unit (e.g., "sshd", "docker").
    Always verify service name format before searching—invalid names return errors immediately.

    **CONSTRAINT WARNING:** Avoid setting `lines` above 500 without explicit confirmation; excessive logs can overwhelm context windows. Service names must follow systemd conventions (alphanumeric with hyphens/underscores/dots). For ongoing monitoring, consider combining with `service_status` for real-time state checking.

    **OUTPUT EXPECTATION:** Returns formatted log entries with ISO timestamps and service identifiers.
    Ideal for debugging failed services, investigating crashes, tracking system events, and
    understanding error messages from background processes.

    *Error recovery:* If no logs appear, verify the service name is correct using `service_status` first.
    """
    # Enforce a hard cap on lines to prevent context flooding, regardless of LLM request
    safe_lines = min(max(1, int(lines)), 500)

    cmd = [
        "journalctl",
        "--no-pager",  # Crucial: prevents journalctl from opening an interactive less session
        "--no-hostname",  # Saves tokens by omitting the hostname from every single line
        "-n",
        str(safe_lines),
        "--output=short-iso",  # Enforces clean, strictly formatted timestamps
    ]

    if boot_only:
        cmd.append("-b")  # Only show logs from the current boot cycle

    if service:
        # Security sanitisation: Ensure the service name is valid to prevent injection
        # Allows alphanumeric, hyphens, underscores, and dots (e.g., systemd-networkd.service)
        if not service.replace("-", "").replace("_", "").replace(".", "").isalnum():
            return "Error: Invalid service name format."
        cmd.extend(["-u", service])

    return run_command(cmd)


# --- Tool 7: Qalculate! (qalc) ---
@mcp.tool(
    name="arch_calculate",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def calculate(query: str) -> str:
    """
    Evaluate mathematical expressions and perform unit/currency conversions using 'qalc'.

    **TRIGGER CONDITION:** Use this when you need precise calculations, scientific computations,
    or unit conversions that exceed basic arithmetic. Ideal for data analysis, financial calculations,
    engineering computations, and scientific measurements.

    **SEQUENCE GUIDANCE:** Always provide complete expressions with proper operators and units.
    For complex calculations, break them into smaller steps and combine results manually if needed.
    The tool supports live exchange rates via the `-e` flag—ensure network connectivity for currency conversions.

    **CONSTRAINT WARNING:** Avoid extremely complex nested expressions that may timeout after 30 seconds.
    Network requests for live exchange rates will fail gracefully with a timeout error if unreachable.
    Ensure query syntax follows standard mathematical conventions to avoid parsing errors.

    **OUTPUT EXPECTATION:** Returns calculation results with proper formatting, unit conversions
    in multiple units, and error messages for invalid expressions. Ideal for scientific calculations,
    financial modeling, engineering computations, and data analysis requiring precision arithmetic.

    *Examples:* "100 USD to EUR", "sqrt(2) + pi^2", "5 km/h to m/s"
    """
    # Sanitize input
    clean_query = query.strip()
    if not clean_query:
        return "Error: Calculation query cannot be empty."

    # -e: Enables live exchange rate updates (network request).
    # If the network hangs, our run_command() helper will safely catch the 30s timeout.
    cmd = ["qalc", "-e", clean_query]

    return run_command(cmd)


# --- Tool 8: System Information Explorer ---
@mcp.tool(
    name="arch_get_system_info",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def get_system_info(
    target: Literal["os", "hardware", "desktop", "resources", "all"] = "all",
) -> str:
    """
    Retrieve system properties, hardware details, and real-time resource metrics.

    **TRIGGER CONDITION:** Use this when you need to understand your computing environment
    before debugging issues, optimizing performance, or preparing for specific workloads. Call
    at the start of a session to establish baseline system state.

    **SEQUENCE GUIDANCE:** Start with `target="all"` for comprehensive overview. Use specific targets
    (os, hardware, desktop) when you only need certain information—this reduces output size and token usage.
    For real-time monitoring, use `target="resources"` to check current memory/disk utilization.

    **CONSTRAINT WARNING:** The "all" target produces the most verbose output (~200-500 tokens).
    For quick checks or automated workflows, prefer specific targets to minimize context usage.
    Resource metrics are captured at call time only—this tool does not provide continuous monitoring (use `process_monitor` for that instead).

    **OUTPUT EXPECTATION:** Returns structured system information including OS details, CPU/GPU specs,
    desktop environment configuration, and real-time resource usage. Ideal for troubleshooting hardware issues,
    verifying system requirements, understanding platform capabilities, and preparing for performance-sensitive operations.

    *Typical workflow:* get_system_info(target="all") → process_monitor(sort_by="cpu") (if high CPU detected)
    """
    results = []

    # Fastfetch handles OS, Hardware, and Desktop targets elegantly in one pass.
    if target in ("os", "hardware", "desktop", "all"):
        results.append("=== SYSTEM & DESKTOP SUMMARY ===")
        # --logo none: Crucial for stripping the Arch ASCII art, which wastes hundreds of tokens
        # Output is automatically stripped of ANSI color codes by subprocess execution
        cmd = ["fastfetch", "--logo", "none"]
        results.append(run_command(cmd))

    # Native commands handle real-time, volatile resources safely
    if target in ("resources", "all"):
        results.append("=== SYSTEM RESOURCES (REAL-TIME) ===")

        results.append("--- MEMORY (MB) ---")
        # free -m: Current RAM and Swap usage in Megabytes
        results.append(run_command(["free", "-m"]))

        results.append("--- DISK USAGE ---")
        # df -h: Current disk space utilization in human-readable format
        results.append(run_command(["df", "-h"]))

    # Join all gathered metrics with double newlines for structured readability
    return "\n\n".join(results)


# --- Tool 9: Process & Task Monitor (ps) ---
@mcp.tool(
    name="arch_process_monitor",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def process_monitor(sort_by: Literal["cpu", "memory"] = "cpu", limit: int = 20) -> str:
    """
    Identify resource-intensive processes using `ps` with CPU or memory sorting.

    **TRIGGER CONDITION:** Use this when system performance degrades, high resource usage is detected via `get_system_info`,
    or you need to identify which applications are consuming the most resources. Call after noticing slow system response times.

    **SEQUENCE GUIDANCE:** Start with `sort_by="cpu"` for CPU-bound issues (common during compilation, rendering). Use `sort_by="memory"` when experiencing RAM exhaustion or swap activity. Always review top 20 processes first before investigating specific processes in detail.

    **CONSTRAINT WARNING:** Avoid setting `limit` above 50 without explicit confirmation; excessive process listings can overwhelm context windows. The tool returns only the header plus N rows for efficiency. For detailed per-process information, combine with other diagnostic tools (e.g., systemd logs for service processes).

    **OUTPUT EXPECTATION:** Returns sorted process list showing PID, user, CPU%, memory%, and command line.
    Ideal for identifying resource hogs, troubleshooting performance issues, monitoring background tasks,
    and understanding which processes are consuming system resources at any given moment.

    *Error recovery:* If no high-resource processes appear but system is slow, check `systemd_logs` for service failures.
    """
    sort_flag = "-%cpu" if sort_by == "cpu" else "-%mem"
    safe_limit = min(max(1, int(limit)), 50)  # Cap at 50 to protect context

    # Execute ps. We avoid shell piping (e.g., '| head') for security.
    cmd = ["ps", "-eo", "pid,user,%cpu,%mem,command", "--sort", sort_flag]
    output = run_command(cmd)

    # Handle the line truncation safely in Python
    lines = output.splitlines()
    if len(lines) > safe_limit + 1:  # +1 to include the header row
        return (
            "\n".join(lines[: safe_limit + 1])
            + f"\n... [Truncated to top {safe_limit} processes]"
        )
    return output


# --- Tool 10: Network Diagnostics (ss / ip) ---
@mcp.tool(
    name="arch_network_diagnostics",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def network_diagnostics(
    target: Literal["ports", "interfaces", "routes"] = "ports",
) -> str:
    """
    Diagnose network configuration using system networking tools (`ss`, `ip`).

    **TRIGGER CONDITION:** Use this when network connectivity issues occur, services fail to bind to ports,
    or you need to verify IP assignments and routing tables. Call after noticing connection failures or service unreachability.

    **SEQUENCE GUIDANCE:** Start with `target="ports"` to see listening services (common first step for server diagnostics).
    Use `target="interfaces"` to check IP addresses and network interface status. Use `target="routes"` when investigating routing issues or multi-homed configurations. Combine results from all three targets for complete network picture.

    **CONSTRAINT WARNING:** This tool queries the local system only—it does not test external connectivity (use `test_connectivity` for that). Results reflect current state at call time; changes may occur between calls if services start/stop or network conditions change.

    **OUTPUT EXPECTATION:** Returns formatted network information: ports show listening services and ports, interfaces display IP assignments and link status, routes show the routing table. Ideal for troubleshooting connection failures, verifying server configurations, debugging service binding issues, and understanding network topology.

    *Typical workflow:* network_diagnostics(target="ports") → test_connectivity(host="localhost:port") (if port listening)
    """
    if target == "ports":
        cmd = ["ss", "-tuln"]
    elif target == "interfaces":
        # -c=never ensures no ANSI color codes corrupt the LLM context
        cmd = ["ip", "-c=never", "a"]
    elif target == "routes":
        cmd = ["ip", "-c=never", "route"]
    else:
        return f"Error: Invalid network target '{target}'."

    return run_command(cmd)


# --- Tool 11: Service State Explorer (systemctl) ---
@mcp.tool(
    name="arch_service_status",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def service_status(service_name: str) -> str:
    """
    Check real-time status of systemd services for troubleshooting failures.

    **TRIGGER CONDITION:** Use this when a background service is failing, not starting, or behaving unexpectedly. Call after identifying the suspect service from `systemd_logs` or process monitoring.

    **SEQUENCE GUIDANCE:** Always verify service name format before calling (alphanumeric with hyphens/underscores/dots/@). For common services like sshd, docker, nginx, use exact systemd unit names (e.g., "sshd.service" or just "sshd"). Combine results with `systemd_logs` for complete failure diagnostics.

    **CONSTRAINT WARNING:** Avoid using special characters in service names beyond standard systemd conventions—invalid formats are rejected immediately to prevent injection attacks. This tool is read-only; it does not start/stop/restart services (use systemctl directly if needed). Service names may differ between systems—verify the correct unit name first.

    **OUTPUT EXPECTATION:** Returns current service state (active/inactive/failed), recent log entries, and process information. Ideal for troubleshooting failed services, verifying startup status, diagnosing crash loops, and understanding service health before making modifications.

    *Common service names:* sshd, docker, nginx, postgresql, mysql, systemd-networkd, NetworkManager

    *Error recovery:* If service not found, verify name using `systemctl list-units` manually or check documentation for correct unit naming convention.
    """
    # Sanitize service name to prevent command injection
    # Allows alphanumeric, dashes, underscores, dots, and '@' (for instanced services like getty@tty1)
    if (
        not service_name.replace("-", "")
        .replace("_", "")
        .replace(".", "")
        .replace("@", "")
        .isalnum()
    ):
        return "Error: Invalid service name format."

    cmd = ["systemctl", "status", service_name, "--no-pager", "-l"]
    return run_command(cmd)


# --- Tool 12: Container Fleet Status (docker) ---
@mcp.tool(
    name="arch_container_status",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def container_status(
    operation: Literal["list", "stats", "logs"] = "list",
    container_name: str = "",
) -> str:
    """
    Inspect Docker containers and their resource usage without running continuous monitoring.

    **TRIGGER CONDITION:** Use this when you need to verify container health, check resource allocation, or identify which containers are consuming system resources. Call after noticing performance issues that may be related to containerized applications.

    **SEQUENCE GUIDANCE:** Start with `operation="list"` for overview of all containers (running and stopped). Use `operation="stats"` when investigating high CPU/memory usage. Use `operation="logs"` with `container_name` to tail the last 100 lines of a container's stdout/stderr. This tool provides snapshots only—not continuous monitoring.

    **CONSTRAINT WARNING:** Requires Docker daemon running locally. `logs` and `stats` operations require a valid `container_name`. Container names may differ from image names—use `list` first to find the correct name.

    **OUTPUT EXPECTATION:** Returns formatted container information: list mode shows ID, names, status, and exposed ports; stats mode shows CPU%, memory usage, and network I/O; logs mode returns the last 100 timestamped log lines.

    *Prerequisites:* Docker installed and daemon running (`systemctl status docker`).

    *Typical workflow:* container_status(operation="list") → container_status(operation="logs", container_name="my-app")
    """
    if operation == "list":
        cmd = [
            "docker",
            "ps",
            "-a",
            "--format",
            "table {{.ID}}\t{{.Names}}\t{{.Status}}\t{{.Ports}}",
        ]
    elif operation == "stats":
        cmd = [
            "docker",
            "stats",
            "--no-stream",
            "--format",
            "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}",
        ]
    elif operation == "logs":
        if not container_name:
            return "Error: 'container_name' is required for the 'logs' operation."
        if not re.match(r"^[a-zA-Z0-9_.-]+$", container_name):
            return "Error: Invalid container name format."
        cmd = ["docker", "logs", "--tail", "100", "--timestamps", container_name]
    else:
        return f"Error: Invalid container operation '{operation}'."

    return run_command(cmd)


# --- Tool 14: Scheduled Tasks & Timers (systemctl) ---
@mcp.tool(
    name="arch_scheduled_tasks",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def scheduled_tasks(all_timers: bool = False) -> str:
    """
    List systemd timers for debugging scheduled background tasks and cron alternatives.

    **TRIGGER CONDITION:** Use this when automated jobs are failing, not executing as expected,
    or you need to understand what scheduled operations exist on the system. Call after noticing missing backups, failed cleanup jobs, or unexpected behavior from timed processes.

    **SEQUENCE GUIDANCE:** Start with `all_timers=False` (default) for active timers only—this provides cleaner output for typical troubleshooting. Use `all_timers=True` when investigating disabled or failed scheduled tasks that should be running. Combine results with `systemd_logs(service="timer_name")` to see execution history and error messages.

    **CONSTRAINT WARNING:** This tool shows systemd timers only—not traditional cron jobs (which require separate inspection via `/etc/crontab` and `/var/spool/cron/`). Timer names may differ from expected cron job names; cross-reference with service status for complete picture. Output includes next scheduled run times—useful for understanding timing issues.

    **OUTPUT EXPECTATION:** Returns list of configured timers with next execution times, last run timestamps, and timer unit names. Ideal for debugging failed automated jobs, understanding system automation, verifying backup schedules, and troubleshooting time-based process failures.

    *Typical workflow:* scheduled_tasks() → systemd_logs(service="backup.timer") (if backup job failing)
    """
    cmd = ["systemctl", "list-timers", "--no-pager"]
    if all_timers:
        cmd.append("--all")

    return run_command(cmd)


# --- Tool 15: Environment Variable Inspector (printenv) ---
@mcp.tool(
    name="arch_inspect_environment",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def inspect_environment(specific_var: str = "") -> str:
    """
    Inspect environment variables for debugging pathing, display, and build configuration issues.

    **TRIGGER CONDITION:** Use this when applications fail to start due to missing paths, display
    errors occur in GUI apps, or build systems report incorrect configurations. Call after encountering "command not found" errors or environment-related failures.

    **SEQUENCE GUIDANCE:** For targeted investigation, provide a specific variable name (e.g., "PATH", "DISPLAY", "HOME"). Use without arguments to dump all environment variables when troubleshooting complex configuration issues. Always verify variable values match expected paths and permissions before proceeding with application execution.

    **CONSTRAINT WARNING:** Variable names are case-sensitive—use exact casing as defined by the system. Special characters beyond alphanumerics and underscores are rejected for security reasons. When dumping all variables, output may be large (~50-100 lines)—consider filtering results manually or querying specific variables first.

    **OUTPUT EXPECTATION:** Returns environment variable values (specific query) or complete environment dump (no arguments). Ideal for debugging path resolution issues, verifying DISPLAY settings for GUI applications, checking build configuration variables, and troubleshooting application startup failures related to environment misconfiguration.

    *Common variables:* PATH, HOME, USER, DISPLAY, SHELL, PAGER, PYTHONPATH

    *Typical workflow:* inspect_environment(specific_var="PATH") → list_directory(path="$PATH") (to verify paths exist)
    """
    if specific_var:
        # Strict sanitization: ensure the variable name is safe (alphanumeric and underscores only)
        if not specific_var.replace("_", "").isalnum():
            return "Error: Invalid environment variable name."
        cmd = ["printenv", specific_var]
    else:
        cmd = ["printenv"]

    return run_command(cmd)


# --- Tool 16: Basic Connectivity & DNS Tester (ping / curl) ---
@mcp.tool(
    name="arch_test_connectivity",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def test_connectivity(host: str, method: Literal["ping", "http"] = "ping") -> str:
    """
    Test external network connectivity and DNS resolution for troubleshooting connection failures.

    **TRIGGER CONDITION:** Use this when services report connection errors, web pages fail to load,
    or API calls time out unexpectedly. Call after identifying a specific host/domain as the source of connectivity issues.

    **SEQUENCE GUIDANCE:** Start with `method="ping"` for basic reachability and DNS resolution testing (fast, ~4 ICMP requests). Use `method="http"` when verifying web server availability or HTTP endpoint accessibility (sends HEAD request via curl). For comprehensive diagnostics, run both methods to distinguish between network routing issues vs. application-layer failures.

    **CONSTRAINT WARNING:** Host names must be valid domain names or IP addresses—special characters are rejected for security reasons. Ping sends exactly 4 packets and times out after 30 seconds if unreachable (prevents infinite hangs). HTTP method adds 5-second timeout to prevent hanging on unresponsive servers. Firewall rules may block ICMP ping but allow HTTP—interpret results accordingly.

    **OUTPUT EXPECTATION:** Returns connectivity test results: ping shows round-trip times and packet loss statistics; HTTP returns status code, headers, or connection failure messages. Ideal for troubleshooting network issues, verifying DNS resolution, checking web server availability, debugging API endpoint failures, and validating connectivity before deploying services.

    *Typical workflow:* test_connectivity(host="example.com") → fetch_url_content(url="https://example.com") (if ping succeeds)
    """
    # Strict regex sanitization to prevent command injection via the host string
    if not re.match(r"^[a-zA-Z0-9.-]+$", host):
        return "Error: Invalid host format. Use domains (e.g., 'github.com') or IPs."

    if method == "ping":
        # -c 4 ensures it sends exactly 4 packets and exits, preventing infinite hangs
        cmd = ["ping", "-c", "4", host]
    elif method == "http":
        # -I sends a HEAD request, -s silences progress bars, --max-time prevents hanging on dropped packets
        # We prepend http:// to ensure curl routes it properly even if the user just provides 'google.com'
        if not host.startswith("http"):
            host = f"http://{host}"
        cmd = ["curl", "-I", "-s", "--max-time", "5", host]
    else:
        return f"Error: Invalid connectivity method '{method}'."

    return run_command(cmd)


if __name__ == "__main__":
    mcp.run(transport="stdio")
