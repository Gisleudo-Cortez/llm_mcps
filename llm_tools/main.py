"""
MCP server for offloading specific tasks to local LLMs via Ollama.

Three tools only — things the calling agent cannot do more cheaply itself:
  summarize   → cheap text compression via local models instead of burning main context
  embed_text  → vector embeddings via Ollama (net-new capability: the agent cannot embed)
  model_info  → discover model capabilities for smart tier selection

Tier routing:
  fast     → qwen3:4b (~3GB) for light summarization, near-instant
  standard → qwen3:14b (~9GB) for heavier compression
  deep     → deepseek-v4-pro:cloud for complex or long-form summarization

Env vars:
  OLLAMA_URL            → default http://localhost:11434
  OLLAMA_FAST_MODEL     → default qwen3:4b
  OLLAMA_STANDARD_MODEL → default qwen3:14b
  OLLAMA_DEEP_MODEL     → default deepseek-v4-pro:cloud
  OLLAMA_EMBED_MODEL    → default nomic-embed-text
  LLM_TOOLS_DEFAULT_MODEL → override all tier resolution
  LM_STUDIO_URL         → legacy alias for OLLAMA_URL (backward compat)
"""

import os
import re
from typing import Literal

import httpx
from mcp.server.fastmcp import FastMCP
from openai import OpenAI

mcp = FastMCP("llm_mcp")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")

OLLAMA_FAST_MODEL = os.getenv("OLLAMA_FAST_MODEL", "qwen3:4b")
OLLAMA_STANDARD_MODEL = os.getenv("OLLAMA_STANDARD_MODEL", "granite4.1:8b")
OLLAMA_DEEP_MODEL = os.getenv("OLLAMA_DEEP_MODEL", "deepseek-v4-pro:cloud")
OLLAMA_EMBED_MODEL = os.getenv("OLLAMA_EMBED_MODEL", "nomic-embed-text")

# Input char limits — larger for cloud models with big context windows
_INPUT_CHAR_LIMIT_LOCAL = 12_000
_INPUT_CHAR_LIMIT_CLOUD = 50_000

# ---------------------------------------------------------------------------
# System prompt library
# ---------------------------------------------------------------------------

_SUMMARIZE_PROMPTS: dict[str, str] = {
    "concise": (
        "You are a precise summarization engine. "
        "Summarize the user's text in 2-3 clear, dense sentences that capture the core message. "
        "No preamble. No 'here is a summary of'. Output only the summary."
    ),
    "detailed": (
        "You are a precise summarization engine. "
        "Summarize the user's text in 2-3 paragraphs, preserving important nuance, "
        "key arguments, and supporting details. No preamble. Output only the summary."
    ),
    "bullets": (
        "You are a precise summarization engine. "
        "Extract the 5-7 most important points from the user's text as concise bullet points. "
        "Each bullet starts with '- ' and fits on one line. No preamble, no closing statement."
    ),
    "eli5": (
        "You are a clear, patient teacher. "
        "Explain the user's text as if to someone with absolutely no background in the subject. "
        "Use simple language, short sentences, and concrete everyday analogies. "
        "Avoid all jargon. No preamble."
    ),
}

# ---------------------------------------------------------------------------
# Internal helpers — client management
# ---------------------------------------------------------------------------

_openai_client: OpenAI | None = None
_native_client: httpx.Client | None = None
_model_name_cache: list[str] | None = None


def _get_client() -> OpenAI:
    """Return a module-level OpenAI client pointing to Ollama's /v1 endpoint."""
    global _openai_client
    if _openai_client is None:
        base = OLLAMA_URL.rstrip("/") + "/v1"
        _openai_client = OpenAI(base_url=base, api_key="ollama")
    return _openai_client


def _get_native() -> httpx.Client:
    """Return a module-level httpx client for Ollama's native API (/api/embed, /api/show, etc.)."""
    global _native_client
    if _native_client is None:
        _native_client = httpx.Client(base_url=OLLAMA_URL, timeout=120.0)
    return _native_client


def _is_cloud_model(model: str) -> bool:
    """Detect Ollama Max cloud-hosted models. Cloud models use the ':cloud' tag."""
    if model.lower().endswith(":cloud"):
        return True
    # Fallback: bare names for known cloud-only model families (no local GGUF versions)
    cloud_prefixes = ("devstral", "deepseek-v4", "gemini-3")
    return any(model.lower().startswith(p) for p in cloud_prefixes)


def _input_limit_for_model(model: str) -> int:
    return _INPUT_CHAR_LIMIT_CLOUD if _is_cloud_model(model) else _INPUT_CHAR_LIMIT_LOCAL


# ---------------------------------------------------------------------------
# Internal helpers — model resolution
# ---------------------------------------------------------------------------

_Tier = Literal["fast", "standard", "deep", "auto"]


def _resolve_model(tier: _Tier = "auto", model: str = "") -> str:
    """
    Resolve to a concrete model ID.

    Priority: explicit model arg > LLM_TOOLS_DEFAULT_MODEL env var > tier-based selection.
    """
    if model and model not in ("auto", ""):
        return model

    env_default = os.getenv("LLM_TOOLS_DEFAULT_MODEL", "")
    if env_default:
        return env_default

    tier_map = {
        "fast": OLLAMA_FAST_MODEL,
        "standard": OLLAMA_STANDARD_MODEL,
        "deep": OLLAMA_DEEP_MODEL,
        "auto": OLLAMA_STANDARD_MODEL,
    }
    return tier_map.get(tier, OLLAMA_STANDARD_MODEL)


def _ollama_list_models() -> list[dict]:
    """Fetch model list from Ollama's /api/tags endpoint."""
    try:
        resp = _get_native().get("/api/tags")
        resp.raise_for_status()
        return resp.json().get("models", [])
    except Exception:
        return []


def _get_available_model_names() -> list[str]:
    """Cached list of model names from Ollama /api/tags. Populated on first call."""
    global _model_name_cache
    if _model_name_cache is None:
        models = _ollama_list_models()
        _model_name_cache = [m.get("name", "") for m in models] if models else []
    return _model_name_cache


def _resolve_model_with_fallback(tier: _Tier = "auto", model: str = "") -> str:
    """
    Like _resolve_model but checks live Ollama availability and falls back if needed.

    Fallback chain: requested → deep → standard → fast → first available model.
    Cloud models (:cloud suffix) bypass the local availability check.
    """
    requested = _resolve_model(tier, model)
    if _is_cloud_model(requested):
        return requested

    available = _get_available_model_names()
    if not available or requested in available:
        return requested

    for candidate in (OLLAMA_DEEP_MODEL, OLLAMA_STANDARD_MODEL, OLLAMA_FAST_MODEL):
        if candidate and (_is_cloud_model(candidate) or candidate in available):
            return candidate

    if available:
        return available[0]
    return requested


def _ollama_model_info(model_name: str) -> dict | None:
    """Fetch model details from Ollama's /api/show endpoint."""
    try:
        resp = _get_native().post("/api/show", json={"model": model_name})
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Internal helpers — API calls
# ---------------------------------------------------------------------------


def _call(
    messages: list[dict],
    model: str = "",
    tier: _Tier = "auto",
    temperature: float = 0.4,
    max_tokens: int = 800,
) -> str:
    """Execute one chat completion via Ollama's OpenAI-compatible endpoint and return the text."""
    resolved = _resolve_model_with_fallback(tier, model)
    try:
        resp = _get_client().chat.completions.create(
            model=resolved,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "(empty response)").strip()
    except Exception as e:
        err = str(e)
        if "connection" in err.lower() or "refused" in err.lower() or "connect" in err.lower():
            return (
                f"Error: Ollama is not reachable at {OLLAMA_URL}. "
                "Ensure Ollama is running, then retry. "
                "Default URL: http://localhost:11434"
            )
        if "404" in err or "model_not_found" in err.lower() or "not found" in err.lower():
            return (
                f"Error: Model '{resolved}' is not loaded. "
                "Call `llm_model_info` to see what is currently available."
            )
        return f"Error from LLM API: {err}"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool(
    name="llm_summarize",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
def summarize(
    text: str,
    style: Literal["concise", "detailed", "bullets", "eli5"] = "concise",
    model: str = "auto",
    tier: _Tier = "auto",
    max_tokens: int = 600,
) -> str:
    """
    Condense any text block using a local LLM with four distinct output styles.

    **TRIGGER CONDITION:** Use after retrieving any large block of text — web articles,
    document sections, search results, log output, book chapters — when the raw content
    is too verbose for the main context window. This is the compression step.

    **SEQUENCE GUIDANCE:** Choose `style` based on downstream use:
    - "concise"  → 2-3 sentence digest; quick insight extraction (default)
    - "detailed" → 2-3 paragraph summary; preserving nuance
    - "bullets"  → 5-7 key points; document scanning, checklists
    - "eli5"     → plain-language explanation; learning new concepts

    **MODEL TIER:** Defaults to "auto" (standard). Use `tier="fast"` for cheap, sub-second
    compression of short texts; `tier="deep"` for long or complex documents.

    Omit `model` or pass "auto" to use tier-based routing. The tool falls back to
    the first available model if the requested one isn't loaded.

    **CONSTRAINT WARNING:** Input is capped at 12,000 characters for local models
    and 50,000 for cloud models — content beyond that is truncated. For very long
    documents, call in sections. Requires Ollama running with a model loaded.

    **OUTPUT EXPECTATION:** Returns the summarized text in the requested style with no
    preamble. Approximate output: concise ~100 tokens, bullets ~250, detailed ~450.

    *Why this tool exists:* Running the main agent's context window over raw 500-line
    web pages is wasteful. A local qwen3:4b can produce a 5-bullet summary at negligible
    cost. This is the compression step before the main agent reasons over the content.
    """
    if not text.strip():
        return "Error: text cannot be empty."

    resolved = _resolve_model(tier, model)
    limit = _input_limit_for_model(resolved)

    clipped = text[:limit]
    if len(text) > limit:
        clipped += f"\n\n[... input truncated at {limit} chars ...]"

    # Adaptive token budget: eli5 and detailed generate longer output than concise/bullets
    if max_tokens <= 600:
        style_budget = {"concise": 600, "bullets": 600, "detailed": 1000, "eli5": 1200}
        effective_max = style_budget.get(style, 600)
    else:
        effective_max = max_tokens

    messages = [
        {"role": "system", "content": _SUMMARIZE_PROMPTS[style]},
        {"role": "user", "content": clipped},
    ]

    result = _call(messages, model=model, tier=tier, temperature=0.3, max_tokens=effective_max)

    # Empty response detection: small models (qwen3:4b) sometimes produce no output
    # for structured styles (bullets, eli5). Escalate to the next tier up.
    if not result or result == "(empty response)":
        if tier == "deep":
            return "Error: Summarization produced empty output on deep tier. Try a different model."
        # Fast → Standard, Auto → Standard, Standard → Deep
        fallback_tier: _Tier = "standard" if tier in ("fast", "auto") else "deep"
        result = _call(messages, tier=fallback_tier, temperature=0.3, max_tokens=effective_max)
        if result and result != "(empty response)":
            return f"{result}\n\n*(auto-fallback: tier={tier} → {fallback_tier} due to empty response)*"
        return "Error: Summarization produced empty output on both initial and fallback tiers."

    return result


@mcp.tool(
    name="llm_embed_text",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
def embed_text(
    text: str,
    model: str = "auto",
) -> str:
    """
    Generate vector embeddings for text via Ollama's embedding endpoint.

    **TRIGGER CONDITION:** Use when you need vector representations of text for
    semantic similarity comparison, clustering search results, or feeding into
    rag_tools with Ollama-native embeddings. This is a net-new capability —
    the main agent cannot generate embeddings itself.

    **SEQUENCE GUIDANCE:** Default model: `nomic-embed-text` (768-dim, 8192-token context).
    Alternative: `mxbai-embed-large` (1024-dim, higher quality). For documents longer
    than the embedding model's context window, chunk first with rag_tools.

    **MODEL TIER:** Uses the embedding model, not a chat model.

    **CONSTRAINT WARNING:** Long texts may be truncated by the embedding model's
    context window. The tool returns metadata (dimensions, first 5 values, token count),
    not the full vector — the full vector stays in Ollama for downstream consumption.

    **OUTPUT EXPECTATION:** Returns embedding dimension count, a preview of the first 5
    values, the model used, and the token count processed.

    *Use cases:*
    - Compute cosine similarity between two document embeddings
    - Cluster semantically related search results before presenting
    - Feed Ollama-native embeddings into rag_tools for consistent vector space
    """
    if not text.strip():
        return "Error: text cannot be empty."

    embed_model = model if model and model not in ("auto", "") else OLLAMA_EMBED_MODEL

    try:
        resp = _get_native().post(
            "/api/embed",
            json={"model": embed_model, "input": text},
        )
        resp.raise_for_status()
        data = resp.json()

        embeddings = data.get("embeddings", [])
        if not embeddings or not embeddings[0]:
            return "Error: No embeddings returned from Ollama."

        vec = embeddings[0]
        dim = len(vec)
        preview = [round(v, 6) for v in vec[:5]]
        prompt_tokens = data.get("prompt_eval_count", "?")

        return (
            "### Embedding Result\n\n"
            f"- **Model**: `{data.get('model', embed_model)}`\n"
            f"- **Dimensions**: {dim}\n"
            f"- **Tokens processed**: {prompt_tokens}\n"
            f"- **First 5 values**: {preview}\n"
        )
    except Exception as e:
        err = str(e)
        if "connection" in err.lower() or "refused" in err.lower():
            return (
                f"Error: Ollama is not running at {OLLAMA_URL}. "
                "Start Ollama and ensure an embedding model is loaded."
            )
        if "404" in err or "not found" in err.lower():
            return (
                f"Error: Embedding model '{embed_model}' is not loaded. "
                f"Pull it with: ollama pull {embed_model}"
            )
        return f"Error generating embeddings: {err}"


@mcp.tool(
    name="llm_model_info",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
def model_info(
    model: str = "",
) -> str:
    """
    Show detailed capabilities and metadata for a loaded Ollama model.

    **TRIGGER CONDITION:** Use before selecting a model for summarization or embedding
    tasks — to check context length, whether it supports vision, what family it belongs to.
    Essential for capability-aware tier selection.

    **SEQUENCE GUIDANCE:** Call before any summarization task to verify the target model
    is loaded and has sufficient context length for your input. Use the capabilities
    field to decide if a model is suitable:
    - `completion` → text generation (all chat models)
    - `vision` → can process images (multimodal models)
    - `embedding` → can generate embeddings

    **MODEL TIER:** N/A — this tool queries Ollama metadata, does not call a model.

    **OUTPUT EXPECTATION:** Returns model name, family, parameter size, quantization,
    context length, capabilities, and template info.

    *Use case:* Before calling `llm_summarize` on a 20k-character document, check
    the model's context length to confirm it won't truncate.
    """
    if not model or model in ("auto", ""):
        models = _ollama_list_models()
        if models:
            model = models[0]["name"]
        else:
            return "Error: No models loaded. Pass a specific model name."

    info = _ollama_model_info(model)
    if not info:
        return (
            f"Error: Could not retrieve info for model '{model}'. "
            "Ensure the model is loaded in Ollama."
        )

    details = info.get("details", {})
    model_info_data = info.get("model_info", {})
    capabilities = info.get("capabilities", [])
    params = info.get("parameters", "")

    # Extract context length from model_info
    context_length = "?"
    for key, val in model_info_data.items():
        if key.endswith(".context_length"):
            context_length = f"{val:,}"
            break

    lines = [
        f"### Model Info: `{model}`\n",
        f"- **Family**: {details.get('family', '?')}",
        f"- **Parameters**: {details.get('parameter_size', '?')}",
        f"- **Quantization**: {details.get('quantization_level', '?')}",
        f"- **Format**: {details.get('format', '?')}",
        f"- **Context Length**: {context_length}",
        f"- **Capabilities**: {', '.join(capabilities) if capabilities else 'unknown'}",
    ]

    if params:
        lines.append(f"- **Parameters (runtime)**: `{params.strip()}`")

    if info.get("template"):
        template_preview = info["template"][:200]
        lines.append(f"- **Template preview**: `{template_preview}...`")

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio")
