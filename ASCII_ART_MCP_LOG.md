# ASCII Art MCP Server — Implementation Log

**Server name:** `ascii_art_mcp`
**Directory:** `lms_mcp/ascii_art_mcp/`
**Source tool:** `04-projects-by-language/python/ascii-art-tool/`
**Started:** 2026-05-17

---

## Architecture Decision Record

### Pipeline (decided during ascii-art-tool build)
```
User prompt
  → OpenRouter chat completions (google/gemini-2.5-flash-image, ~$0.04/call)
  → image bytes (base64 data-URI in choices[0].message.images[0].image_url.url)
  → PIL brightness mapping → ASCII string
```

Key finding: OpenRouter returns image data in `message.images[]`, NOT `message.content`
(non-standard field — content is null for image-output models).

LLM fallback (local Ollama gemma3:12b) is retained for `refine` and when
OPENROUTER_API_KEY is absent, but quality is significantly lower.

### Dependency strategy
MCP server declares `ascii-art-tool` as a uv path dependency pointing to
`../../../04-projects-by-language/python/ascii-art-tool` (editable).
No code duplication — updates to image_pipeline.py propagate automatically after `uv sync`.

---

## MCP Tools Exposed

| Tool | Input | Output | Backend |
|------|-------|--------|---------|
| `ascii_generate` | subject, width=80, style=None | ASCII string | OpenRouter image → PIL |
| `ascii_refine` | art, feedback, width=80 | ASCII string | Ollama LLM |
| `ascii_convert` | image_path, width=80 | ASCII string | PIL only (no API) |
| `ascii_list_styles` | — | style names + descriptions | static |

### Future tools (not yet implemented)
- `ascii_refine_image` — regenerate via image model with feedback prompt instead of LLM
- `ascii_batch` — generate multiple subjects, return dict
- `ascii_animate` — generate N frames for a simple animation (stretch)

---

## Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `OPENROUTER_API_KEY` | Yes (image backend) | Auth for gemini-2.5-flash-image |
| `OLLAMA_HOST` | No | Ollama server for LLM fallback (default localhost:11434) |

The server reads `OPENROUTER_API_KEY` from the environment.
Set it in your shell profile or pass via `claude mcp add --env`.

---

## File Structure

```
ascii_art_mcp/
├── main.py          ← FastMCP server (4 tools)
├── pyproject.toml   ← uv project, ascii-art-tool path dep
└── uv.lock          ← generated on first uv sync
```

---

## Registration Command

```bash
claude mcp add ascii-art-mcp --scope user \
  -- uv --directory /home/nero/Documents/Estudos/07-tools-and-infrastructure/lms_mcp/ascii_art_mcp \
  run python main.py
```

Verify registration:
```bash
claude mcp list
```

Test from Claude Code:
```
use the ascii_generate tool to draw a skull
```

---

## Setup Steps

```bash
cd /home/nero/Documents/Estudos/07-tools-and-infrastructure/lms_mcp/ascii_art_mcp
uv sync
export OPENROUTER_API_KEY=sk-or-...   # or add to shell profile
uv run python main.py                 # smoke-test: server starts, no errors
```

---

## Implementation Status

- [x] `main.py` — 4 tools implemented
- [x] `pyproject.toml` — path dep to ascii-art-tool, mcp dep
- [x] `uv sync` passes
- [x] `ascii_generate` end-to-end tested
- [x] `ascii_convert` end-to-end tested (PIL only)
- [x] `claude mcp add` registered — `ascii-art-mcp: ✓ Connected`
- [ ] Live test from Claude Code session (next session)

---

## Known Issues & Constraints

| Issue | Detail |
|-------|--------|
| Cost per generate | ~$0.04/call (Imagen 3 flat rate via Gemini 2.5 Flash Image) |
| `ascii_refine` quality | Uses local Ollama LLM — spatial reasoning still imperfect |
| Image model latency | ~5–10s per call (network + Imagen inference) |
| VRAM during refine | ascii-artist model (~8.1GB) must be loaded in Ollama |
| OpenRouter images field | Non-standard: image in `message.images[]` not `message.content` — may break if OpenRouter normalises their schema |

---

## Iteration Log

### 2026-05-17 — Initial implementation
- Built ascii-art-tool with two backends: image pipeline (OpenRouter) and LLM (Ollama)
- Discovered OpenRouter DOES have image generation models (via chat completions, not /images/generate)
- Models: gemini-2.5-flash-image, gpt-5-image, gemini-3.1-flash-image-preview
- Chose gemini-2.5-flash-image: cheapest, fast, good quality for ASCII source images
- PIL pipeline: grayscale → resize (with 0.45 aspect correction for terminal chars) → brightness ramp → ASCII
- Auto-detects dark vs light background, inverts ramp accordingly
- Wrapped as FastMCP server with 4 tools
