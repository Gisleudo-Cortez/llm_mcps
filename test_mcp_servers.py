#!/usr/bin/env python3
"""Test harness for all 12 MCP servers — runs each in its own uv-managed venv."""

import subprocess
import sys
import os

BASE = "/home/nero/Documents/Estudos/07-tools-and-infrastructure/lms_mcp"

servers = sorted([d for d in os.listdir(BASE)
                  if os.path.isdir(os.path.join(BASE, d))
                  and os.path.isfile(os.path.join(BASE, d, "main.py"))])

def uv_run(server_dir, code, timeout=30):
    """Run Python code inside the server's own uv venv."""
    proc = subprocess.run(
        ["uv", "run", "python", "-c", code],
        cwd=server_dir,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "PYTHONPATH": server_dir},
    )
    return proc.returncode, proc.stdout, proc.stderr

# ──────────────────────────────────────────────────────────────────────────
print("=" * 70)
print("MCP SERVER FUNCTIONAL TESTS — uv-managed venvs")
print("=" * 70)

total_pass = 0
total_fail = 0

# ═══════════════════════════════════════════════════════════════════════════
# 1. current_date_time
# ═══════════════════════════════════════════════════════════════════════════
print("\n1. CURRENT_DATE_TIME")
print("-" * 70)
s = "current_date_time"; sd = os.path.join(BASE, s)
tests = [
    ("get_current_datetime default", """
from main import get_current_datetime, GetCurrentDatetimeInput
r = get_current_datetime(GetCurrentDatetimeInput())
assert "System Time Context" in r, r[:200]
print("PASS")
"""),
    ("get_current_datetime json", """
from main import get_current_datetime, GetCurrentDatetimeInput, _ResponseFormat
import json
r = get_current_datetime(GetCurrentDatetimeInput(response_format=_ResponseFormat.json))
d = json.loads(r)
assert "Unix Epoch" in d, r[:200]
print("PASS")
"""),
    ("invalid timezone runtime error", """
from main import get_current_datetime, GetCurrentDatetimeInput
r = get_current_datetime(GetCurrentDatetimeInput(timezone="Bad/Zone"))
assert "Error:" in r, r[:200]
print("PASS")
"""),
    ("extra field rejected", """
try:
    from main import GetCurrentDatetimeInput
    GetCurrentDatetimeInput(unknown_field="bad")
    print("FAIL: extra field accepted")
except Exception:
    print("PASS")
"""),
    ("calculate_relative_date", """
from main import calculate_relative_date, CalculateRelativeDateInput
r = calculate_relative_date(CalculateRelativeDateInput(days_offset=7))
assert "Relative Date Calculation" in r, r[:200]
print("PASS")
"""),
    ("get_api_date_range last_7_days", """
from main import get_api_date_range, GetApiDateRangeInput
r = get_api_date_range(GetApiDateRangeInput(preset="last_7_days"))
assert "API Date Range" in r, r[:200]
print("PASS")
"""),
    ("translate_timezone", """
from main import translate_timezone, TranslateTimezoneInput
r = translate_timezone(TranslateTimezoneInput(
    timestamp_str="2026-01-15T12:00:00Z", source_tz="UTC", target_tz="Asia/Tokyo"))
assert "Time Translation Result" in r, r[:200]
print("PASS")
"""),
    ("bad timestamp error", """
from main import translate_timezone, TranslateTimezoneInput
r = translate_timezone(TranslateTimezoneInput(timestamp_str="invalid", source_tz="UTC"))
assert "Error:" in r, r[:200]
print("PASS")
"""),
]
for name, code in tests:
    rc, out, err = uv_run(sd, code, timeout=15)
    ok = rc == 0 and "PASS" in out
    status = "PASS" if ok else "FAIL"
    total_pass += ok; total_fail += not ok
    print(f"  [{status}] {name}")
    if not ok:
        print(f"       stdout: {out[:150]}")
        print(f"       stderr: {err[:150]}")


# ═══════════════════════════════════════════════════════════════════════════
# 2. command_docs
# ═══════════════════════════════════════════════════════════════════════════
print("\n2. COMMAND_DOCS")
print("-" * 70)
s = "command_docs"; sd = os.path.join(BASE, s)
tests = [
    ("man_lookup ls", """
from main import command_docs_man_lookup, ManLookupInput
r = command_docs_man_lookup(ManLookupInput(command="ls"))
assert "Man Page" in r or "No manual" in r, r[:200]
print("PASS")
"""),
    ("tldr_lookup tar", """
from main import command_docs_tldr_lookup, TldrLookupInput
r = command_docs_tldr_lookup(TldrLookupInput(command="tar"))
assert "TLDR" in r or "No TLDR" in r, r[:200]
print("PASS")
"""),
    ("cheat_sh git clone", """
from main import command_docs_cheat_sh_lookup, CheatShLookupInput
r = command_docs_cheat_sh_lookup(CheatShLookupInput(command="git", query="clone"))
assert "cheat.sh" in r or "Error:" in r, r[:200]
print("PASS")
""", 20),
    ("dangerous command rejected", """
from pydantic import ValidationError
from main import ManLookupInput
try:
    ManLookupInput(command="ls; rm -rf /")
    print("FAIL")
except ValidationError:
    print("PASS")
"""),
    ("json format", """
from main import command_docs_man_lookup, ManLookupInput, _ResponseFormat
import json
r = command_docs_man_lookup(ManLookupInput(command="ls", response_format=_ResponseFormat.json))
d = json.loads(r)
assert "heading" in d and "content" in d, str(d)[:200]
print("PASS")
"""),
]
for item in tests:
    if len(item) == 2:
        name, code = item; to = 15
    else:
        name, code, to = item
    rc, out, err = uv_run(sd, code, timeout=to)
    ok = rc == 0 and "PASS" in out
    status = "PASS" if ok else "FAIL"
    total_pass += ok; total_fail += not ok
    print(f"  [{status}] {name}")
    if not ok:
        print(f"       stdout: {out[:150]}")
        print(f"       stderr: {err[:150]}")


# ═══════════════════════════════════════════════════════════════════════════
# 3. code_check
# ═══════════════════════════════════════════════════════════════════════════
print("\n3. CODE_CHECK")
print("-" * 70)
s = "code_check"; sd = os.path.join(BASE, s)
tests = [
    ("format python", """
from main import code_check_format_code, FormatCodeInput
r = code_check_format_code(FormatCodeInput(code="x = 1\\nif x==1:\\n    print(   \\"hello\\"   )\\n", language="python"))
# ruff formats spacing
assert "x == 1" in r or "x=1" in r, r[:200]
print("PASS")
"""),
    ("lint bad python", """
from main import code_check_lint_code, LintCodeInput
r = code_check_lint_code(LintCodeInput(code="def f(x,y): return x+y\\nx=1\\n", language="python"))
# Should not have the old --output-format error
assert "invalid value" not in r.lower(), r[:200]
print("PASS")
"""),
    ("check_code combined", """
from main import code_check_check_code, CheckCodeInput
r = code_check_check_code(CheckCodeInput(code="def add(a: int, b: int) -> int:\\n    return a + b\\n", language="python"))
assert "=== FORMAT" in r and "=== LINT" in r, r[:200]
print("PASS")
"""),
    ("unknown language", """
from main import code_check_format_code, FormatCodeInput
r = code_check_format_code(FormatCodeInput(code="test", language="brainfuck"))
assert "Unknown language" in r, r[:200]
print("PASS")
"""),
    ("alias py -> python", """
from main import code_check_lint_code, LintCodeInput
r = code_check_lint_code(LintCodeInput(code="x=1\\n", language="py"))
assert "Unknown language" not in r, r[:200]
print("PASS")
"""),
    ("extra field rejected", """
from pydantic import ValidationError
from main import FormatCodeInput
try:
    FormatCodeInput(code="x=1", language="python", extra="bad")
    print("FAIL")
except ValidationError:
    print("PASS")
"""),
    ("json validation builtin", """
from main import code_check_lint_code, LintCodeInput
r = code_check_lint_code(LintCodeInput(code='{"a": 1,}', language="json"))
assert "parse error" in r.lower() or "No issues" in r, r[:200]
print("PASS")
"""),
    ("good json clean", """
from main import code_check_lint_code, LintCodeInput
r = code_check_lint_code(LintCodeInput(code='{"a": 1}', language="json"))
assert "No issues" in r, r[:200]
print("PASS")
"""),
]
for name, code in tests:
    rc, out, err = uv_run(sd, code, timeout=15)
    ok = rc == 0 and "PASS" in out
    status = "PASS" if ok else "FAIL"
    total_pass += ok; total_fail += not ok
    print(f"  [{status}] {name}")
    if not ok:
        print(f"       stdout: {out[:150]}")
        print(f"       stderr: {err[:150]}")


# ═══════════════════════════════════════════════════════════════════════════
# 4. Other servers — Smoke test
# ═══════════════════════════════════════════════════════════════════════════
print("\n4. OTHER SERVERS — Smoke tests via uv venv")
print("-" * 70)

for s in servers:
    if s in ("current_date_time", "command_docs", "code_check"):
        continue
    sd = os.path.join(BASE, s)
    rc, out, err = uv_run(sd, """
import main
tools = list(main.mcp._tool_manager._tools.keys())
print(f"TOOLS_COUNT: {len(tools)}")
for t in tools:
    print(f"  {t}")
""", timeout=20)
    if rc != 0:
        total_fail += 1
        print(f"  [FAIL] {s} — import/load error")
        print(f"         stderr: {err[:200]}")
        continue
    tool_lines = [l.strip() for l in out.splitlines() if l.strip().startswith("  ")]
    count_line = [l for l in out.splitlines() if l.startswith("TOOLS_COUNT:")]
    tool_count = int(count_line[0].split(":")[1].strip()) if count_line else 0
    total_pass += 1
    print(f"  [PASS] {s:<25s} tools={tool_count}  {', '.join(tool_lines[:3])}{' ...' if tool_count > 3 else ''}")


print("\n" + "=" * 70)
print(f"FINAL RESULT: {total_pass} PASS, {total_fail} FAIL")
print("=" * 70)
