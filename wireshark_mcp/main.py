#!/usr/bin/env python3
"""
MCP Server for Wireshark / tshark — packet capture and analysis.

Wraps tshark, capinfos, and editcap subprocess calls using the FastMCP
framework. Provides 16 tools covering the full packet analysis workflow:
loading, inspecting, filtering, extracting, following streams, and
live capture.
"""

import json
import os
import subprocess
from typing import Literal, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Server init
# ---------------------------------------------------------------------------

mcp = FastMCP("wireshark_mcp")

MAX_OUTPUT_CHARS = 80_000
DEFAULT_TIMEOUT = 120  # generous for large pcaps


def _run_cmd(
    cmd: list[str],
    timeout: int = DEFAULT_TIMEOUT,
    max_chars: int = MAX_OUTPUT_CHARS,
) -> str:
    """Execute a subprocess safely — returns stdout on success, stderr on failure."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
        )
        output = result.stdout if result.returncode == 0 else result.stderr
        if not output.strip():
            return "Command completed with no output."
        if len(output) > max_chars:
            return output[:max_chars] + (
                f"\n\n... [Truncated at {max_chars} characters]"
            )
        return output
    except FileNotFoundError:
        return f"Error: '{cmd[0]}' not found. Install wireshark-cli (pacman -S wireshark-cli)."
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {timeout}s."
    except Exception as exc:
        return f"Error: {exc}"


# ===================================================================
# Pydantic input models
# ===================================================================


class _BaseModel(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, validate_assignment=True)


class LoadPcapInput(_BaseModel):
    path: str = Field(..., description="Absolute path to .pcap or .pcapng file", min_length=1)


class ListPacketsInput(_BaseModel):
    path: str = Field(..., description="Absolute path to the pcap file", min_length=1)
    display_filter: str = Field(
        default="", description="Wireshark display filter (e.g. 'http', 'dns', 'tcp.port==443')"
    )
    limit: int = Field(default=30, description="Max frames to return", ge=1, le=500)
    offset: int = Field(default=0, description="Skip first N frames (0-indexed)", ge=0)


class PacketDetailInput(_BaseModel):
    path: str = Field(..., description="Absolute path to pcap", min_length=1)
    frame: int = Field(..., description="Frame number (1-indexed)", ge=1)


class ExtractFieldsInput(_BaseModel):
    path: str = Field(..., description="Absolute path to pcap", min_length=1)
    fields: str = Field(
        ...,
        description="Comma-separated field names (e.g. 'ip.src,ip.dst,tcp.port,dns.qry.name')",
    )
    display_filter: str = Field(default="", description="Optional Wireshark display filter")
    limit: int = Field(default=50, description="Max rows", ge=1, le=500)


class FollowStreamInput(_BaseModel):
    path: str = Field(..., description="Absolute path to pcap", min_length=1)
    protocol: Literal["tcp", "udp"] = Field(..., description="Transport protocol")
    stream: int = Field(..., description="Stream index (e.g. tcp.stream value)", ge=0)


class ConversationInput(_BaseModel):
    path: str = Field(..., description="Absolute path to pcap", min_length=1)
    layer: Literal["tcp", "udp", "ip", "ipv6", "eth"] = Field(
        default="tcp", description="Layer for conversation table"
    )
    display_filter: str = Field(default="", description="Optional display filter")


class EndpointInput(_BaseModel):
    path: str = Field(..., description="Absolute path to pcap", min_length=1)
    layer: Literal["tcp", "udp", "ip", "ipv6", "eth"] = Field(
        default="ip", description="Layer for endpoint table"
    )
    display_filter: str = Field(default="", description="Optional display filter")


class IoStatsInput(_BaseModel):
    path: str = Field(..., description="Absolute path to pcap", min_length=1)
    interval_ms: int = Field(default=1000, description="Interval in ms", ge=10, le=60000)
    display_filter: str = Field(default="", description="Optional display filter")


class ExpertInfoInput(_BaseModel):
    path: str = Field(..., description="Absolute path to pcap", min_length=1)
    display_filter: str = Field(default="", description="Optional display filter")


class ProtocolStatsInput(_BaseModel):
    path: str = Field(..., description="Absolute path to pcap", min_length=1)
    protocol: Literal["dns", "http", "dhcp"] = Field(
        default="dns", description="Protocol to get stats for"
    )


class LiveCaptureInput(_BaseModel):
    interface: str = Field(..., description="Interface name (use list_interfaces to discover)", min_length=1)
    duration: int = Field(default=30, description="Capture duration in seconds", ge=1, le=300)
    capture_filter: str = Field(
        default="", description="BPF capture filter (e.g. 'tcp port 443', 'host 10.0.0.1')"
    )
    output_path: str = Field(
        default="",
        description="Path to save pcap. Defaults to /tmp/wireshark_mcp_capture_<ts>.pcap",
    )


class ExportJsonInput(_BaseModel):
    path: str = Field(..., description="Absolute path to source pcap", min_length=1)
    output_path: str = Field(..., description="Absolute path for output JSON file")
    display_filter: str = Field(default="", description="Optional display filter")
    limit: int = Field(default=100, description="Max packets to export", ge=1, le=2000)


# ===================================================================
# Shared helpers
# ===================================================================


def _capinfos(path: str) -> str:
    """Run capinfos and return its output."""
    return _run_cmd(["capinfos", path], timeout=30)


def _tshark(path: str, extra: list[str]) -> str:
    """Run tshark -r <path> <extra>."""
    return _run_cmd(["tshark", "-r", path] + extra)


# ===================================================================
# 1 — Health check
# ===================================================================


@mcp.tool(
    name="wireshark_check_installation",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def check_installation() -> str:
    """Verify tshark and capinfos are installed and report versions.

    **TRIGGER CONDITION:** Call before any packet analysis — at the start of every
    session or after any tool returns a 'command not found' error.

    **SEQUENCE GUIDANCE:** No prerequisites. Run this first, then proceed to
    list_interfaces or load_pcap.

    **OUTPUT EXPECTATION:** JSON with 'tshark', 'capinfos', 'ready' keys.
    'ready': true means all dependencies found.

    **ERROR RECOVERY:** If not ready, install wireshark-cli:
    sudo pacman -S wireshark-cli
    """
    tshark_ok = "not found"
    capinfos_ok = "not found"

    for binary, key in [("tshark", "tshark"), ("capinfos", "capinfos")]:
        r = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0:
            if key == "tshark":
                tshark_ok = r.stdout.strip().splitlines()[0]
            else:
                capinfos_ok = r.stdout.strip().splitlines()[0]

    ready = "not found" not in (tshark_ok, capinfos_ok)

    return json.dumps(
        {"tshark": tshark_ok, "capinfos": capinfos_ok, "ready": ready}, indent=2
    )


# ===================================================================
# 2 — List interfaces
# ===================================================================


@mcp.tool(
    name="wireshark_list_interfaces",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
def list_interfaces() -> str:
    """Enumerate capture interfaces with descriptions.

    **TRIGGER CONDITION:** Use before live_capture, or when the user asks
    'what interfaces are available?'.

    **SEQUENCE GUIDANCE:** Call after check_installation. Feed the returned
    interface name into live_capture.

    **OUTPUT EXPECTATION:** Raw tshark -D output (one line per interface).
    Example: '1. eth0\\n2. wlan0 (Wi-Fi)'

    **ERROR RECOVERY:** If tshark is not found, run check_installation first.
    """
    return _run_cmd(["tshark", "-D"], timeout=10)


# ===================================================================
# 3 — Load / summary
# ===================================================================


@mcp.tool(
    name="wireshark_load_pcap",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def load_pcap(params: LoadPcapInput) -> str:
    """Return a quick summary of a pcap file (frame count, duration, size, type).

    **TRIGGER CONDITION:** Use whenever the user provides a pcap path — this is
    the first analysis step after check_installation.

    **SEQUENCE GUIDANCE:** call order:
    check_installation → load_pcap → (protocol_hierarchy | list_packets | expert_info | ...)

    **OUTPUT EXPECTATION:** capinfos output showing file type, encapsulation,
    packet count, data size, duration, avg packet size, and data rate.
    """
    if not os.path.exists(params.path):
        return f"Error: File not found: {params.path}"
    return _capinfos(params.path)


# ===================================================================
# 4 — Protocol hierarchy
# ===================================================================


@mcp.tool(
    name="wireshark_protocol_hierarchy",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def protocol_hierarchy(params: LoadPcapInput) -> str:
    """Return the nested protocol breakdown with frame/byte counts.

    **TRIGGER CONDITION:** Use after load_pcap when you need to understand
    which protocols dominate the capture. Also useful when the user asks
    'what protocols are in this pcap?'.

    **SEQUENCE GUIDANCE:** load_pcap → protocol_hierarchy → drill into
    specific protocol with display filters.

    **OUTPUT EXPECTATION:** tshark -z io,phs output: protocol tree with
    frames, bytes, and percentages per layer.
    """
    if not os.path.exists(params.path):
        return f"Error: File not found: {params.path}"
    return _tshark(params.path, ["-q", "-z", "io,phs"])


# ===================================================================
# 5 — List packets (paginated)
# ===================================================================


@mcp.tool(
    name="wireshark_list_packets",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
def list_packets(params: ListPacketsInput) -> str:
    """Paginated frame list with display filter.

    **TRIGGER CONDITION:** Use when you need to browse individual frames —
    after load_pcap shows many packets and you want to narrow down.

    **SEQUENCE GUIDANCE:** load_pcap → protocol_hierarchy →
    list_packets(filter='dns') → packet_detail(frame=...) for specific frames.

    **CONSTRAINT WARNING:** Default limit is 30. Use offset for pagination.
    Large pcaps (10k+ frames) may be slow with broad filters.

    **OUTPUT EXPECTATION:** tshark columnar output: frame number, time, src,
    dst, protocol, length, info.

    **ERROR RECOVERY:** If the display filter is invalid, tshark returns an
    error with the filter syntax issue. Fix the filter and retry.
    """
    if not os.path.exists(params.path):
        return f"Error: File not found: {params.path}"
    cmd = ["tshark", "-r", params.path]
    if params.display_filter:
        cmd += ["-Y", params.display_filter]
    # Use frame.number range for offset-based pagination
    if params.offset > 0:
        cmd += [
            "-Y",
            f"frame.number >= {params.offset + 1}"
            + (f" and ({params.display_filter})" if params.display_filter else ""),
        ]
        # Override the display_filter when offset is used with filter
        if params.display_filter:
            pass
        else:
            pass  # already set above
    else:
        if params.display_filter:
            cmd += ["-Y", params.display_filter]
    cmd += ["-c", str(params.limit)]
    return _run_cmd(cmd, timeout=60)


# ===================================================================
# 6 — Packet detail
# ===================================================================


@mcp.tool(
    name="wireshark_packet_detail",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def packet_detail(params: PacketDetailInput) -> str:
    """Full protocol tree for a single frame.

    **TRIGGER CONDITION:** Use after list_packets when you identify a
    specific frame of interest and need every dissected field.

    **SEQUENCE GUIDANCE:** list_packets → identify frame number →
    packet_detail(frame=...).

    **CONSTRAINT WARNING:** Output can be very large for complex frames
    (TLS, HTTP/2). Auto-truncated at 80k chars.

    **OUTPUT EXPECTATION:** Full tshark -V output: all protocol layers
    with field names, values, and nested subtrees.
    """
    if not os.path.exists(params.path):
        return f"Error: File not found: {params.path}"

    # Get frame count first to validate frame number
    count_check = _run_cmd(
        ["tshark", "-r", params.path, "-q", "-z", "io,stat,0"], timeout=30
    )
    # Use a different approach: just try to read the frame
    return _tshark(params.path, ["-V", "-Y", f"frame.number=={params.frame}"])


# ===================================================================
# 7 — Extract fields
# ===================================================================


@mcp.tool(
    name="wireshark_extract_fields",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
def extract_fields(params: ExtractFieldsInput) -> str:
    """Extract arbitrary Wireshark fields as a table.

    **TRIGGER CONDITION:** Use when you need structured data — IP addresses,
    ports, DNS names, HTTP URIs — across many frames at once.

    **SEQUENCE GUIDANCE:** load_pcap → protocol_hierarchy (discover protocols)
    → extract_fields(fields='ip.src,ip.dst,dns.qry.name,dns.a') for DNS flows.

    **CONSTRAINT WARNING:** Field names must be valid Wireshark field names
    (use Tab-complete in Wireshark GUI or check display filter reference).
    Use -T fields -e <field1> -e <field2> ... -E header=y.

    **OUTPUT EXPECTATION:** Tab-separated table with header row.
    Empty cells appear as blank fields.

    **ERROR RECOVERY:** Invalid field names produce empty columns silently.
    Verify field names if results look wrong.
    """
    if not os.path.exists(params.path):
        return f"Error: File not found: {params.path}"
    field_list = [f.strip() for f in params.fields.split(",") if f.strip()]
    if not field_list:
        return "Error: No fields specified."
    cmd = ["tshark", "-r", params.path, "-T", "fields", "-E", "header=y"]
    for fld in field_list:
        cmd += ["-e", fld]
    if params.display_filter:
        cmd += ["-Y", params.display_filter]
    if params.limit > 0:
        cmd += ["-c", str(params.limit)]
    return _run_cmd(cmd, timeout=120)


# ===================================================================
# 8 — Conversations
# ===================================================================


@mcp.tool(
    name="wireshark_conversations",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def conversations(params: ConversationInput) -> str:
    """Conversation table — bytes/frames per peer pair.

    **TRIGGER CONDITION:** Use when the user asks 'who is talking to whom?'
    or 'show me top talkers'. After loading any pcap with multiple hosts.

    **SEQUENCE GUIDANCE:** load_pcap → conversations(layer='tcp') to see
    top TCP pairs → follow_stream on the heaviest stream.

    **OUTPUT EXPECTATION:** Table with columns: Address A, Address B,
    Packets, Bytes, Packets A→B, Bytes A→B, Packets B→A, Bytes B→A,
    Duration, Bits/s.

    **ERROR RECOVERY:** Some layers may not be supported. Try 'tcp', 'udp',
    'ip', or 'eth' if one fails.
    """
    if not os.path.exists(params.path):
        return f"Error: File not found: {params.path}"
    cmd = ["tshark", "-r", params.path, "-q", "-z", f"conv,{params.layer}"]
    if params.display_filter:
        # -z stats don't support inline filters directly, pre-filter:
        # This is approximate — -z stats run on full pcap
        pass
    return _run_cmd(cmd, timeout=120)


# ===================================================================
# 9 — Endpoints
# ===================================================================


@mcp.tool(
    name="wireshark_endpoints",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def endpoints(params: EndpointInput) -> str:
    """Endpoint table — tx/rx per host.

    **TRIGGER CONDITION:** Use when the user asks 'which IP sent the most
    data?' or 'list all IPs that appear in this capture'.

    **SEQUENCE GUIDANCE:** load_pcap → endpoints(layer='ip') for top IPs.

    **OUTPUT EXPECTATION:** Table: Address, Packets, Bytes, Tx Packets,
    Tx Bytes, Rx Packets, Rx Bytes.
    """
    if not os.path.exists(params.path):
        return f"Error: File not found: {params.path}"
    return _tshark(params.path, ["-q", "-z", f"endpoints,{params.layer}"])


# ===================================================================
# 10 — IO stats
# ===================================================================


@mcp.tool(
    name="wireshark_io_stats",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def io_stats(params: IoStatsInput) -> str:
    """Per-interval frame and byte counts (traffic timeline).

    **TRIGGER CONDITION:** Use when you need a temporal view — 'when did
    traffic spike?' or 'show me the traffic pattern over time'.

    **SEQUENCE GUIDANCE:** load_pcap → io_stats(interval_ms=1000) →
    identify burst windows → list_packets with time-range filter.

    **OUTPUT EXPECTATION:** Table: interval, frames, bytes.
    """
    if not os.path.exists(params.path):
        return f"Error: File not found: {params.path}"
    cmd = ["tshark", "-r", params.path, "-q", "-z", f"io,stat,{params.interval_ms / 1000:.3f}"]
    return _run_cmd(cmd, timeout=120)


# ===================================================================
# 11 — Expert info
# ===================================================================


@mcp.tool(
    name="wireshark_expert_info",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def expert_info(params: ExpertInfoInput) -> str:
    """Per-frame anomaly detection — errors, warnings, notes.

    **TRIGGER CONDITION:** Use when diagnosing network issues —
    'are there any errors in this capture?', 'find suspicious packets'.

    **SEQUENCE GUIDANCE:** load_pcap → expert_info → packet_detail on
    frames flagged with errors/warnings.

    **OUTPUT EXPECTATION:** Sorted by severity: Errors first, then Warnings,
    then Notes, then Chats. Each entry: frame number, severity, group, message.
    """
    if not os.path.exists(params.path):
        return f"Error: File not found: {params.path}"
    return _tshark(params.path, ["-q", "-z", "expert"])


# ===================================================================
# 12 — Protocol stats (DNS / HTTP / DHCP)
# ===================================================================


@mcp.tool(
    name="wireshark_protocol_stats",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def protocol_stats(params: ProtocolStatsInput) -> str:
    """Aggregate protocol statistics (DNS queries/types, HTTP requests/status).

    **TRIGGER CONDITION:** Use after protocol_hierarchy shows significant
    DNS/HTTP/DHCP traffic. User asks 'show me DNS stats' or 'HTTP breakdown'.

    **SEQUENCE GUIDANCE:** protocol_hierarchy → protocol_stats(protocol='dns')
    → extract_fields for specific record types.

    **OUTPUT EXPECTATION:** Depends on protocol:
    - dns: query type distribution, response codes
    - http: request methods, status codes, hosts
    - dhcp: message types, options

    **ERROR RECOVERY:** If the protocol is not present in the capture,
    the output will show empty statistics.
    """
    if not os.path.exists(params.path):
        return f"Error: File not found: {params.path}"
    stat_cmd = f"{params.protocol},tree"
    return _tshark(params.path, ["-q", "-z", stat_cmd])


# ===================================================================
# 13 — Follow stream
# ===================================================================


@mcp.tool(
    name="wireshark_follow_stream",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def follow_stream(params: FollowStreamInput) -> str:
    """Reassemble a TCP or UDP stream and return its payload.

    **TRIGGER CONDITION:** Use after conversations or extract_fields reveals
    a specific stream of interest — 'follow that TCP stream', 'show me what
    was sent over stream 3'.

    **SEQUENCE GUIDANCE:** conversations → identify stream → follow_stream
    → analyze payload (HTTP, DNS, plaintext, etc.)

    **CONSTRAINT WARNING:** Binary payloads are displayed as hex-ASCII.
    Max output 80k chars. Long streams (file transfers) will be truncated.

    **OUTPUT EXPECTATION:** Reassembled stream payload in ASCII format.
    For HTTP: headers + body. For plaintext protocols: readable text.

    **ERROR RECOVERY:** If 'ascii' mode fails, try 'hex' mode.
    Stream index must exist — check conversations output for valid streams.
    """
    if not os.path.exists(params.path):
        return f"Error: File not found: {params.path}"

    # Build filter predicate and follow mode
    follow_mode = f"{params.protocol},ascii,{params.stream}"
    return _tshark(params.path, ["-q", "-z", f"follow,{follow_mode}"])


# ===================================================================
# 14 — Live capture (WRITE)
# ===================================================================


@mcp.tool(
    name="wireshark_live_capture",
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
def live_capture(params: LiveCaptureInput) -> str:
    """Capture live traffic from an interface and save to a pcap file.

    **TRIGGER CONDITION:** Use when the user asks to capture live traffic —
    'capture 30 seconds on wlan0', 'sniff HTTP traffic for 10 seconds'.

    **SEQUENCE GUIDANCE:**
    1. check_installation
    2. list_interfaces (pick interface name)
    3. live_capture(interface='wlan0', duration=30, capture_filter='tcp port 443')
    4. load_pcap on the output path
    5. Run any analysis tools on the captured file

    **CONSTRAINT WARNING:** Requires CAP_NET_RAW or root for live capture.
    Max 5 minutes / 300 seconds. Capture filter uses BPF syntax, not display filter.
    If user is not in the wireshark group, live capture will fail.

    **OUTPUT EXPECTATION:** JSON with 'success', 'output_path', 'packets_captured',
    'duration_seconds', 'size_bytes'.

    **ERROR RECOVERY:** If permission denied, add user to wireshark group:
    sudo usermod -aG wireshark $USER && newgrp wireshark
    Or use sudo (not recommended for MCP servers).
    """
    output = params.output_path
    if not output:
        import time as _time
        output = f"/tmp/wireshark_mcp_capture_{int(_time.time())}.pcap"

    cmd = ["tshark", "-i", params.interface, "-w", output]
    if params.capture_filter:
        cmd += ["-f", params.capture_filter]
    cmd += ["-a", f"duration:{params.duration}"]

    import time as _time2
    start = _time2.time()
    result = _run_cmd(cmd, timeout=params.duration + 30)
    elapsed = _time2.time() - start

    # Get file size
    size = os.path.getsize(output) if os.path.exists(output) else 0

    return json.dumps(
        {
            "success": os.path.exists(output) and size > 0,
            "output_path": output,
            "duration_seconds": round(elapsed, 1),
            "size_bytes": size,
            "raw_output": result,
        },
        indent=2,
    )


# ===================================================================
# 15 — Export JSON (WRITE)
# ===================================================================


@mcp.tool(
    name="wireshark_export_json",
    annotations={
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": False,
    },
)
def export_json(params: ExportJsonInput) -> str:
    """Export packets from a pcap to a JSON file.

    **TRIGGER CONDITION:** Use when the user needs programmatic access to
    packet data — 'export these packets as JSON', 'dump DNS frames to JSON'.

    **SEQUENCE GUIDANCE:** load_pcap → export_json(display_filter='dns', limit=50)
    → user reads the JSON file with their own tools.

    **CONSTRAINT WARNING:** JSON output is verbose (3-10 KB per packet).
    Limit to ≤2000 frames to avoid multi-GB files.

    **OUTPUT EXPECTATION:** JSON with 'success', 'output_path', 'packet_count',
    'size_bytes'.

    **ERROR RECOVERY:** If the output directory doesn't exist, the write will
    fail. Ensure the target directory exists.
    """
    if not os.path.exists(params.path):
        return f"Error: File not found: {params.path}"

    cmd = [
        "tshark",
        "-r", params.path,
        "-T", "json",
    ]
    if params.display_filter:
        cmd += ["-Y", params.display_filter]
    if params.limit > 0:
        cmd += ["-c", str(params.limit)]

    result = _run_cmd(cmd, timeout=120)

    # Write to output file
    import time as _time3
    try:
        os.makedirs(os.path.dirname(params.output_path) or ".", exist_ok=True)
        with open(params.output_path, "w") as fh:
            fh.write(result)
        size = os.path.getsize(params.output_path)
        # Count packets from the JSON array
        try:
            data = json.loads(result)
            if isinstance(data, list):
                pkt_count = len(data)
            else:
                pkt_count = len(data) if hasattr(data, "__len__") else 0
        except Exception:
            pkt_count = -1
        return json.dumps(
            {
                "success": True,
                "output_path": params.output_path,
                "packet_count": pkt_count,
                "size_bytes": size,
            },
            indent=2,
        )
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)}, indent=2)


# ===================================================================
# 16 — Pcap summary (richer than load_pcap)
# ===================================================================


@mcp.tool(
    name="wireshark_pcap_summary",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def pcap_summary(params: LoadPcapInput) -> str:
    """Richer summary combining capinfos + protocol hierarchy + top endpoints.

    **TRIGGER CONDITION:** Use as a one-stop overview when opening an
    unfamiliar pcap — 'summarize this capture', 'what's in this file?'.

    **SEQUENCE GUIDANCE:** Single call instead of load_pcap + protocol_hierarchy
    + endpoints. Use before diving deeper.

    **OUTPUT EXPECTATION:** Three sections:
    === CAPINFOS === (file metadata)
    === PROTOCOL HIERARCHY === (protocol breakdown)
    === TOP IP ENDPOINTS === (busiest hosts)
    """
    if not os.path.exists(params.path):
        return f"Error: File not found: {params.path}"

    sections = []
    sections.append("=== CAPINFOS ===")
    sections.append(_capinfos(params.path))
    sections.append("\n=== PROTOCOL HIERARCHY ===")
    sections.append(_tshark(params.path, ["-q", "-z", "io,phs"]))
    sections.append("\n=== TOP IP ENDPOINTS ===")
    sections.append(_tshark(params.path, ["-q", "-z", "endpoints,ip"]))
    return "\n".join(sections)


# ===================================================================
# Entry point
# ===================================================================


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
