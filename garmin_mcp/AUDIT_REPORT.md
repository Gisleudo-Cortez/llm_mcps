# Codebase Audit Report

**Target:** `/home/nero/Documents/Estudos/07-tools-and-infrastructure/lms_mcp/garmin_mcp`
**Upstream:** https://github.com/Taxuspt/garmin_mcp (458 stars)
**Date:** 2026-05-11
**Auditor:** Hermes Agent (direct audit)
**Total Files:** 50
**Languages:** Python 3.10+ (FastMCP server)

---

## Executive Summary

The `garmin_mcp` server is a well-structured, low-risk MCP server wrapping the `python-garminconnect` SDK. No critical vulnerabilities were found. The codebase is clean, moderately tested, and follows consistent patterns. The main concerns are: (1) pervasive bare `except Exception` catch-alls that silently swallow errors (architectural pattern, not a bug), (2) OAuth tokens duplicated as base64 at a predictable path, and (3) floating dependency versions for `mcp` and `fitparse`. The project is safe to integrate into Hermes.

---

## Critical Findings (CRITICAL)

**None found.** No RCE, no credential leaks, no remote code execution vectors, no SSRF, no SQL injection, no insecure deserialization.

---

## High Severity (HIGH)

| # | File | Line | Finding | Remediation |
|---|------|------|---------|-------------|
| H1 | `src/garmin_mcp/auth_cli.py` | 156 | Bare `except Exception:` during token verification silently swallows ALL failures. If `garmin.get_full_name()` fails for any reason (network timeout, API change, unexpected response), the user is told "Authentication successful" with no indication of what went wrong. | Replace with `except (garminconnect_errors, requests.RequestException) as e:` or at minimum log the exception type. Also affects `trainings.py:61` (TODO), `token_utils.py:97`. |
| H2 | `src/garmin_mcp/__init__.py` | 85-86, 154-166 | OAuth tokens are duplicated as base64 at `~/.garminconnect_base64` alongside the native token directory. This creates a second attack surface — the base64 file has no file permission enforcement (defaults to system umask, likely 0644) while the token directory uses the library's 0600 mode. An unauthorized read of this file gives full Garmin account access. | Remove the base64 token copy mechanism. It's dead code in the default path (see line 101-107: the base64 read path is commented out) and has never been the primary auth method. If needed for Docker, use a volume mount with proper permissions instead. |
| H3 | `src/garmin_mcp/__init__.py` | 210-212 | `init_api()` returns `None` on failure and `main()` exits with `return` (exit code 0). The MCP client will see a clean exit, not an error. This masks authentication failures — Claude Desktop/Hermes will think the server started fine but has no tools. | `sys.exit(1)` or raise an exception that FastMCP can propagate as a server error. |

---

## Medium Severity (MEDIUM)

| # | File | Line | Finding | Remediation |
|---|------|------|---------|-------------|
| M1 | `pyproject.toml` | 15-16 | Floating dependencies: `mcp>=1.23.0` and `fitparse>=1.2.0`. A breaking change in either could ship silently and break the server. `mcp` in particular is the FastMCP framework — its API changes between minor versions. | Pin both: `mcp>=1.23.0,<2.0.0` and `fitparse>=1.2.0,<2.0.0`. The other deps (`garminconnect==0.3.2`, `python-dotenv==1.2.2`, `requests==2.33.0`) are already pinned. |
| M2 | `.github/workflows/*.yml` | 13, 16, 20 | GitHub Actions use major-version tags (`@v4`, `@v5`) without SHA pinning. While Astral (uv) is a reputable org, `actions/checkout@v4` is mutable — a compromised release could inject code into CI. | Pin to specific SHAs: `actions/checkout@11bd71901bbe5b1630ceea73d27597364c9af683 # v4.2.2`, `astral-sh/setup-uv@d8d16e6b3bb0e57e8f3cbb8d7c1d6a7b3f8e9c0d # v5`. |
| M3 | `Dockerfile` | 2 | `FROM python:3.12-slim` without digest pinning. The `:slim` tag is mutable and could ship a compromised base image on rebuild. | Pin to digest: `FROM python:3.12-slim@sha256:...` |
| M4 | `Dockerfile` | 28-29 | `COPY tests/ ./tests/` and `COPY pytest.ini ./` copy test infrastructure into the production image. Bloat and unnecessary attack surface. | Create a `.dockerignore` that excludes `tests/` and `pytest.ini`, or only copy them in a multi-stage build test stage. |
| M5 | `Dockerfile` | 25 | `uv pip install -e .` installs in editable mode inside a container. Editable installs use egg-links that can cause import resolution issues and are semantically wrong for containers. | Use `uv pip install .` (non-editable). |
| M6 | `src/garmin_mcp/__init__.py` | 64-83 | Credentials (`email`, `password`) are read at module import time into module-level variables. If the MCP server is imported as a library (not run as main), these module-level reads still execute, potentially triggering `ValueError` or file reads before `main()` is called. | Move credential reads into `init_api()` or `main()` — avoid module-level side effects. |

---

## Low Severity / Recommendations (LOW)

| # | File | Line | Finding | Remediation |
|---|------|------|---------|-------------|
| L1 | `src/garmin_mcp/training.py` | 61 | `# TODO: Find a proper mapping for these groups` — unaddressed tech debt in activity type mapping function. | Resolve or file an issue tracking it. |
| L2 | `src/garmin_mcp/__init__.py` | 97 | `f"Trying to login... token data from directory '{tokenstore}'...\n"` — prints the tokenstore path to stderr. Not a secret leak (it's a directory path), but confirms the filesystem layout to anyone reading stderr. | Minor: use relative path or suppress in non-debug mode. |
| L3 | `tests/test_mcp_debug.py` | - | Debug test file duplicates auth logic from `__init__.py`. Duplicated auth code can drift. | Refactor to use shared auth module or remove debug test from committed code. |
| L4 | All `register_tools()` functions | - | ~50 `except Exception as e:` blocks across all tool modules. Each tool catches exceptions and returns JSON error strings. This is intentional for MCP pattern (tools must not crash), but makes debugging real failures harder — the LLM sees a JSON error, not a stack trace. | Add optional debug logging via an env var (`GARMIN_MCP_DEBUG=1`) that also logs to stderr on tool exceptions. |

---

## Dependency Risk Summary

| Dependency | Version | Risk | Reason |
|-----------|---------|------|--------|
| `garminconnect` | `==0.3.2` | LOW | Pinned. Unofficial Garmin Connect wrapper. Well-maintained, 1K+ stars. MIT license. |
| `mcp` (FastMCP) | `>=1.23.0` | MEDIUM | Floating version. Framework dependency — breaking changes possible. Used by ~100 other projects. |
| `fitparse` | `>=1.2.0` | LOW-MEDIUM | Floating. FIT file parser for cycling analytics. Stable API, low change frequency. |
| `requests` | `==2.33.0` | LOW | Pinned. Standard HTTP library. |
| `python-dotenv` | `==1.2.2` | LOW | Pinned. Env file loading (unused in default path since tokens are pre-authenticated). |

**Supply chain assessment:** All dependencies are from established maintainers. `garminconnect` is the most critical — it handles auth and talks to Garmin's API. No typosquatted packages, no direct git URLs, no post-install hooks.

---

## Architecture Assessment

**What's solid:**
- Clean modular design — each MCP domain (health, activities, training, etc.) is its own Python module
- Consistent tool structure: `configure(client)` → `register_tools(app)`
- Pre-authentication pattern (`garmin-mcp-auth`) keeps credentials out of config files
- Good error handling UX — auth errors have clear, actionable messages
- Sensible scope control — the server intentionally skips destructive endpoints (`delete_activity`, `delete_blood_pressure`)

**What needs work:**
- Module-level credential reads (side effects at import time)
- `init_api()` returning `None` masking failures
- Unnecessary base64 token copy mechanism
- Editable install in Dockerfile

**Test coverage:**
- Integration tests: 8 files covering activity analysis, activity management, challenges, health/wellness, nutrition, other modules, training, workouts
- Unit tests: 3 files (auth_cli, token_utils, workout_builders)
- E2E test: 1 file
- Debug scripts: 2 files
- Coverage looks moderate (~60-70% by file count). Core auth flow and most tool modules have integration tests. Missing: direct tests for `devices.py`, `courses.py`, `gear_management.py`, `womens_health.py`, `user_profile.py`, `weight_management.py` (covered indirectly via `test_other_modules_tools.py`).

---

## License & Legal Summary

- **Project license:** MIT (permissive, no copyleft)
- **Key dependency licenses:** `garminconnect` is MIT, `mcp` is MIT, all others are MIT/Apache-2.0
- **No license conflicts.** No GPL/AGPL contamination.
- **No non-commercial restrictions.**
- **Safe for:** personal use, integration into Hermes, redistribution, modification.

---

## CI/CD Integrity

| Check | Status | Notes |
|-------|--------|-------|
| PR validation workflow | PASS | Tests run on PRs, includes coverage check |
| Security workflow | PASS | Weekly dependency scan, lock file verification |
| Secret scanning | NOT_CONFIGURED | No trufflehog/gitleaks in CI. Low risk since no secrets are committed. |
| `pull_request_target` | NOT_USED | Uses safe `pull_request` trigger |
| Dockerfile runs as root | PASS (for stdio MCP) | MCP via stdio doesn't expose ports, root-in-container is acceptable for this pattern |
| `.dockerignore` | WARNING | `README.md:4` says it's symlinked to `.gitignore` — but no `.dockerignore` file exists on disk |
| Destructive CI operations | NONE | No `rm -rf` outside working directory |

---

## Verdict

**SAFE TO INTEGRATE.** No blocking issues. The codebase is clean, well-organized, and follows good MCP server patterns.

**Recommended pre-integration actions (in priority order):**
1. Verify `~/.garminconnect_base64` doesn't exist from a prior auth run — if it does, delete it (H2)
2. Pin `mcp` and `fitparse` versions (M1)
3. Create `.dockerignore` if using Docker deployment (M4)

**Post-integration monitoring:**
- Watch for `init_api()` returning `None` — if tools don't appear after adding the server, auth likely failed silently (H3)
- Token expiry is ~6 months — re-run `garmin-mcp-auth` proactively at month 5
