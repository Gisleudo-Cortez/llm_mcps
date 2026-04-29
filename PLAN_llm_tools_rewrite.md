# PLAN: llm_tools Ollama Rewrite (Phase 1.1–1.4)

## Tool: llm_tools (complete rewrite)

---

## 1.1 — Client & Model Resolution Rewrite

### Changes to `llm_tools/main.py`

**Replace:**
- `LM_STUDIO_BASE` env var → `OLLAMA_URL` env var (default `http://localhost:11434`)
- Single `_client` (OpenAI) → dual client system:
  - `_get_client()` → OpenAI client pointing to `OLLAMA_URL/v1` (for backward-compatible chat completions)
  - `_get_native()` → `httpx.Client` pointing to `OLLAMA_URL` (for native Ollama API: /api/chat, /api/embed, /api/tags, /api/show)
- `_resolve_model(model)` → `_resolve_model(tier, model)` with tier routing:
  - `tier="fast"` → `OLLAMA_FAST_MODEL` env (default: `nemotron-3-nano-4b`)
  - `tier="standard"` → `OLLAMA_STANDARD_MODEL` env (default: `qwen3.5:9b`)
  - `tier="deep"` → `OLLAMA_DEEP_MODEL` env (default: `devstral-2`)
  - `tier="auto"` → heuristic: input < 500 chars + simple task → fast; < 5000 or reasoning → standard; else → deep
  - Explicit `model` parameter always wins over tier

**New env vars:**
- `OLLAMA_URL` → default `http://localhost:11434`
- `OLLAMA_FAST_MODEL` → default `nemotron-3-nano-4b`
- `OLLAMA_STANDARD_MODEL` → default `qwen3.5:9b`
- `OLLAMA_DEEP_MODEL` → default `devstral-2`
- `OLLAMA_EMBED_MODEL` → default `nomic-embed-text`

**New internal helpers:**
- `_get_native()` → lazy `httpx.Client(base_url=OLLAMA_URL, timeout=120.0)`
- `_resolve_model(tier, model)` → tier-aware model selection
- `_ollama_list_models()` → `GET /api/tags` → parsed model list
- `_ollama_model_info(model_name)` → `POST /api/show` → capabilities, context length, family, size

### Expectations
- Backward compatibility: `LM_STUDIO_URL` still works as fallback (mapped to OLLAMA_URL internally)
- All 22 existing tests still pass (they mock `_get_client`)
- New `_resolve_model` returns correct model per tier
- `_get_native()` connects to Ollama at `localhost:11434`
- Error messages reference Ollama, not LM Studio

---

## 1.2 — Modify Existing Tools

### Changes per tool:

| Tool | Change | Details |
|------|--------|---------|
| `list_available_models` | Use `/api/tags` instead of OpenAI `models.list()` | Show model name, parameter_size, quantization, family, size. Add tier labels based on name matching. |
| `summarize` | Add `tier: str = "auto"` param | Pass tier to `_resolve_model`. Cloud models get higher input limit (50k chars). |
| `ask_model` | Add `tier: str = "auto"` param | Same pattern. Increase context limit for cloud models. |
| `analyze_code` | Add `tier: str = "deep"` param (default changes to deep for code review quality) | Cloud devstral-2 by default for best analysis. |
| `interpret_data` | Add `tier: str = "standard"` param | Standard tier default for data tasks. |

### Preserved behaviors:
- All existing params remain with same defaults (except new `tier` param)
- Existing test mocks still work (`_get_client` still used for chat completions)
- Input char limits preserved; cloud models get higher limits

### Expectations:
- All 22 existing tests pass unchanged (backward compat)
- `tier` param is optional, defaults preserve existing behavior
- `list_available_models` shows richer metadata from `/api/tags`

---

## 1.3 — New Tools

### `embed_text(text, model)`
- Calls `POST /api/embed` with `OLLAMA_EMBED_MODEL`
- Returns: dimension count, first 5 values preview, model name, token count
- Purpose: enable RAG tools and memory tools to use Ollama embeddings

### `model_info(model)`
- Calls `POST /api/show` for specified model
- Returns: capabilities, context length, family, parameter size, quantization
- Purpose: let agents know what a model can do before selecting it

### `generate_code(prompt, language, context, model, tier)`
- Uses cloud devstral-2 by default for code generation
- Returns generated code wrapped in language fence
- Purpose: structured code generation (separate from ask_model)

### `agent_chat(messages, tools, model, tier, max_steps)`
- Multi-turn agent loop using `/api/chat` with native tool calling
- Each step: send messages + tools → check response for tool_calls → if tool_calls, format results → append → repeat
- Max `max_steps` iterations to prevent infinite loops
- Returns final assistant message content + reasoning trace if present
- Purpose: let Ollama orchestrate multi-step tool-using workflows

### Expectations:
- Each new tool has at least 3 unit tests
- `embed_text` works with Ollama embedding models
- `model_info` parses `/api/show` response correctly
- `generate_code` defaults to tier="deep"
- `agent_chat` handles tool call loop correctly

---

## 1.4 — Dependencies & Tests

### `pyproject.toml` changes:
- Add `httpx>=0.28.0`
- Update description from "LM Studio" to "Ollama"
- Keep `openai>=1.0.0` (still used for `/v1/chat/completions` backward compat)

### Test plan:
- Run all 22 existing tests → must pass
- Add new tests for: `_resolve_model` tier routing, `_get_native`, `_ollama_list_models`, `_ollama_model_info`, `embed_text`, `model_info`, `generate_code`, `agent_chat`
- Integration test: `ollama list` to verify models are available (skipped if Ollama not running)

---

## Implementation Order:
1. Rewrite internals (_get_client, _get_native, _resolve_model) — commit
2. Modify existing tools (add tier param, update list_available_models) — commit
3. Add new tools (embed_text, model_info, generate_code, agent_chat) — commit
4. Update pyproject.toml, run tests, fix issues — commit