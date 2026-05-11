"""
MCP server for delegating tasks to locally-running LLMs via Ollama.

Primary backend: Ollama (localhost:11434) with tiered model routing.
Also compatible with any OpenAI-compatible API via OLLAMA_URL/v1.

Model tiers:
  fast     → small local models (qwen3:4b ~3GB) for quick tasks, sub-second
  standard → local reasoning models (qwen3:14b ~9GB) for code, analysis, Q&A
  deep     → cloud models (deepseek-v4-pro:cloud, glm-5.1:cloud) for complex tasks

Env vars:
  OLLAMA_URL            → default http://localhost:11434
  OLLAMA_FAST_MODEL     → default qwen3:4b
  OLLAMA_STANDARD_MODEL → default qwen3:14b
  OLLAMA_DEEP_MODEL     → default deepseek-v4-pro:cloud
  OLLAMA_EMBED_MODEL    → default nomic-embed-text
  LM_STUDIO_URL         → legacy alias for OLLAMA_URL (backward compat)
  LLM_TOOLS_DEFAULT_MODEL → override all tier resolution
"""

import os
from typing import Literal

import httpx
from mcp.server.fastmcp import FastMCP
from openai import OpenAI

mcp = FastMCP("llm_mcp")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OLLAMA_URL = os.getenv(
    "OLLAMA_URL",
    os.getenv("LM_STUDIO_URL", "http://localhost:11434"),
)

OLLAMA_FAST_MODEL = os.getenv("OLLAMA_FAST_MODEL", "qwen3:4b")
OLLAMA_STANDARD_MODEL = os.getenv("OLLAMA_STANDARD_MODEL", "qwen3:14b")
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

_CODE_REVIEW_PROMPT = """\
You are an expert {language} code reviewer. Analyze the provided code and report on:

1. **Correctness** — logic errors, off-by-one errors, unhandled edge cases
2. **Performance** — algorithmic inefficiencies, redundant work, O(n²) patterns
3. **Security** — injection risks, hardcoded secrets, unsafe input handling
4. **Style** — naming clarity, structure, readability
5. **Top 3 improvements** — the highest-impact changes to make, in priority order

Be specific: name the function, line pattern, or variable. Skip praise. Output only the review."""

_DATA_PROMPT = """\
You are a quantitative data analyst. The user will provide data and a question.
Answer concisely, focusing on patterns, anomalies, trends, and actionable insights.
Ground every claim in the data — do not speculate beyond what is shown.
Be quantitative where possible."""

_DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful, precise assistant. Answer concisely and accurately. "
    "If additional context is provided in the user message, use it to inform your answer."
)

_CODE_GEN_PROMPT = """\
You are an expert {language} programmer. Generate clean, idiomatic, production-ready code.

Rules:
- Output ONLY the code inside a single ```{language} code fence
- No explanations before or after the code
- Include necessary imports
- Follow the language's best practices and conventions
- If context is provided, use it to inform the implementation
- Handle edge cases appropriately"""

# ---------------------------------------------------------------------------
# Internal helpers — client management
# ---------------------------------------------------------------------------

_openai_client: OpenAI | None = None
_native_client: httpx.Client | None = None


def _get_client() -> OpenAI:
    """Return a module-level OpenAI client pointing to Ollama's /v1 endpoint."""
    global _openai_client
    if _openai_client is None:
        base = OLLAMA_URL.rstrip("/") + "/v1"
        _openai_client = OpenAI(base_url=base, api_key="ollama")
    return _openai_client


def _get_native() -> httpx.Client:
    """Return a module-level httpx client for Ollama's native API (/api/chat, /api/embed, etc.)."""
    global _native_client
    if _native_client is None:
        _native_client = httpx.Client(base_url=OLLAMA_URL, timeout=120.0)
    return _native_client


# ---------------------------------------------------------------------------
# Internal helpers — model resolution
# ---------------------------------------------------------------------------

_Tier = Literal["fast", "standard", "deep", "auto"]


def _is_cloud_model(model: str) -> bool:
    """Detect Ollama Max cloud-hosted models. Cloud models use the ':cloud' tag."""
    if model.lower().endswith(":cloud"):
        return True
    # Fallback: bare names for known cloud-only model families (no local GGUF versions)
    cloud_prefixes = ("devstral", "deepseek-v4", "gemini-3")
    return any(model.lower().startswith(p) for p in cloud_prefixes)


def _input_limit_for_model(model: str) -> int:
    return _INPUT_CHAR_LIMIT_CLOUD if _is_cloud_model(model) else _INPUT_CHAR_LIMIT_LOCAL


def _resolve_model(tier: _Tier = "auto", model: str = "") -> str:
    """
    Resolve to a concrete model ID.

    Priority: explicit model arg > LLM_TOOLS_DEFAULT_MODEL env var > tier-based selection.
    Tier routing:
      fast     → OLLAMA_FAST_MODEL
      standard → OLLAMA_STANDARD_MODEL
      deep     → OLLAMA_DEEP_MODEL
      auto     → standard (safe default for most tasks)
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


def _resolve_model_with_fallback(tier: _Tier = "auto", model: str = "") -> str:
    """
    Like _resolve_model but checks live Ollama availability and falls back if needed.

    Fallback chain: requested → deep → standard → fast → first available model.
    Cloud models (:cloud suffix) bypass the local availability check.
    Used at call time in _call() and _call_native() for runtime resilience.
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

    return available[0] if available else requested


def _ollama_list_models() -> list[dict]:
    """Fetch model list from Ollama's /api/tags endpoint."""
    try:
        resp = _get_native().get("/api/tags")
        resp.raise_for_status()
        return resp.json().get("models", [])
    except Exception:
        return []


def _ollama_model_info(model_name: str) -> dict | None:
    """Fetch model details from Ollama's /api/show endpoint."""
    try:
        resp = _get_native().post("/api/show", json={"model": model_name})
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return None


_model_name_cache: list[str] | None = None


def _get_available_model_names() -> list[str]:
    """Cached list of model names from Ollama /api/tags. Populated on first call."""
    global _model_name_cache
    if _model_name_cache is None:
        models = _ollama_list_models()
        _model_name_cache = [m.get("name", "") for m in models] if models else []
    return _model_name_cache


def _infer_tier(model_name: str) -> str:
    """Infer a tier label from the model name for display purposes."""
    name = model_name.lower()
    # Cloud: Ollama Max models always carry :cloud tag
    if name.endswith(":cloud"):
        return "deep"
    # Fast: small models (sub-5B) — exclude larger models that happen to contain "4b" as substring
    if any(p in name for p in ("nano", "mini", ":4b", ":3b", ":2b", ":1b", ":0.5b", "-4b", "-3b")):
        if "14b" not in name and "27b" not in name and "34b" not in name:
            return "fast"
    # Legacy deep markers (non-cloud bare names)
    if any(p in name for p in ("devstral", "deepseek-v4", "gemini-3")):
        return "deep"
    return "standard"


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
                f"Default URL: http://localhost:11434"
            )
        if "404" in err or "model_not_found" in err.lower() or "not found" in err.lower():
            return (
                f"Error: Model '{resolved}' is not loaded. "
                "Call `list_available_models` to see what is currently available."
            )
        return f"Error from LLM API: {err}"


def _call_native(
    messages: list[dict],
    model: str = "",
    tier: _Tier = "auto",
    tools: list[dict] | None = None,
    temperature: float = 0.4,
    num_predict: int = 800,
    think: bool = False,
) -> dict:
    """Execute a chat completion via Ollama's native /api/chat endpoint."""
    resolved = _resolve_model_with_fallback(tier, model)
    payload: dict = {
        "model": resolved,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": num_predict},
    }
    if tools:
        payload["tools"] = tools
    if think:
        payload["think"] = True

    resp = _get_native().post("/api/chat", json=payload)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Tools — existing (modified)
# ---------------------------------------------------------------------------


@mcp.tool(
    name="llm_list_available_models",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
}
)
def list_available_models() -> str:
    """
    List all models currently loaded in Ollama with tier labels and metadata.

    **TRIGGER CONDITION:** Call this BEFORE any other llm_tools call when you are unsure
    which model IDs are available, or when a previous call failed with a model-not-found error.

    **SEQUENCE GUIDANCE:** Use the returned model IDs as the `model` parameter in
    summarize, ask_model, analyze_code, and interpret_data.
    - **fast tier** (4B models) → simple summarization, formatting, quick lookups
    - **standard tier** (9B-27B) → code analysis, nuanced interpretation, reasoning
    - **deep tier** (cloud) → complex code generation, multi-source synthesis, agentic tasks

    **MODEL TIER:** N/A — this tool does not call a model.

    **OUTPUT EXPECTATION:** Returns model IDs with parameter size, quantization, family,
    and inferred tier label. Pass the exact ID string as the `model` parameter.
    """
    try:
        models = _ollama_list_models()
        if not models:
            # Fallback to OpenAI-compatible endpoint
            try:
                data = _get_client().models.list().data
                if not data:
                    return (
                        "No models currently loaded. "
                        "Start Ollama and pull/load a model, then retry."
                    )
                lines = ["### Models available (OpenAI-compatible)\n"]
                for m in data:
                    ctx = getattr(m, "context_length", None)
                    ctx_str = f" — {ctx:,} token context" if ctx else ""
                    lines.append(f"- `{m.id}`{ctx_str}")
                lines.append(
                    "\n*Pass the model ID as the `model` parameter. "
                    "Omit or pass \"auto\" to use tier-based routing.*"
                )
                return "\n".join(lines)
            except Exception:
                return (
                    f"Error: Ollama is not running at {OLLAMA_URL}. "
                    "Start Ollama and load a model first."
                )

        lines = ["### Models available\n"]
        for m in models:
            name = m.get("name", "unknown")
            size_bytes = m.get("size", 0)
            details = m.get("details", {})
            param_size = details.get("parameter_size", "?")
            quant = details.get("quantization_level", "?")
            family = details.get("family", "?")
            tier_label = _infer_tier(name)

            size_str = f"{size_bytes / 1e9:.1f}GB" if size_bytes > 0 else ""
            meta = f" — {param_size} {quant} ({family})"
            if size_str:
                meta += f" {size_str}"
            lines.append(f"- `{name}` [**{tier_label}**]{meta}")

        lines.append(
            f"\n*Current tier defaults: fast=`{OLLAMA_FAST_MODEL}` "
            f"standard=`{OLLAMA_STANDARD_MODEL}` deep=`{OLLAMA_DEEP_MODEL}`.*\n"
            f"*Pass a model ID as the `model` parameter, or use `tier` "
            f"(fast/standard/deep/auto) for automatic routing.*"
        )
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing models: {e}"


@mcp.tool(
    name="llm_summarize",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
}
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
    is too verbose for direct consumption. This is the compression step in most workflows.

    **SEQUENCE GUIDANCE:** Choose `style` based on downstream use:
    - "concise"  → 2-3 sentence digest; quick fact-check or insight extraction (default)
    - "detailed" → 2-3 paragraph summary; study notes, preserving nuance
    - "bullets"  → 5-7 key points; meeting prep, document scanning, checklists
    - "eli5"     → plain-language explanation; learning new concepts or technical topics

    **MODEL TIER:** Defaults to "auto" (standard). Use `tier="fast"` for simple
    summarization of short texts; `tier="deep"` for long or complex documents
    requiring nuanced understanding.

    Omit `model` or pass "auto" to use tier-based routing. Call
    `list_available_models` first to see what is loaded.

    **CONSTRAINT WARNING:** Input is capped at 12,000 characters for local models
    and 50,000 for cloud models — content beyond that is truncated before sending.
    For longer documents, call this in sections using pagination from
    rag_tools/read_doc_content or page_scrape/fetch_url_content. Requires Ollama
    running with a model loaded.

    **OUTPUT EXPECTATION:** Returns the summarized text in the requested style with no
    preamble. Approximate output sizes: concise ~100 tokens, bullets ~250, detailed ~450.

    *Typical workflows:*
    - page_scrape/fetch_url_content → summarize(style="bullets")
    - rag_tools/read_doc_content    → summarize(style="detailed")
    - local_searxng/web_search      → summarize(style="concise")
    - arch_system_tools/systemd_logs → summarize(style="concise")
    """
    if not text.strip():
        return "Error: text cannot be empty."

    resolved = _resolve_model(tier, model)
    limit = _input_limit_for_model(resolved)

    clipped = text[:limit]
    if len(text) > limit:
        clipped += f"\n\n[... input truncated at {limit} chars ...]"

    messages = [
        {"role": "system", "content": _SUMMARIZE_PROMPTS[style]},
        {"role": "user", "content": clipped},
    ]
    return _call(messages, model=model, tier=tier, temperature=0.3, max_tokens=max_tokens)


@mcp.tool(
    name="llm_ask_model",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
}
)
def ask_model(
    prompt: str,
    context: str = "",
    model: str = "auto",
    tier: _Tier = "auto",
    system_prompt: str = "",
    temperature: float = 0.7,
    max_tokens: int = 1000,
) -> str:
    """
    Send a free-form query to an LLM, optionally grounding it in retrieved context.

    **TRIGGER CONDITION:** Use when you need the model to reason over content retrieved by
    other tools — document chunks, search snippets, query results, code output — without a
    pre-defined task format. This is the synthesis step in RAG workflows.

    **SEQUENCE GUIDANCE:** Put retrieved content in `context`, keep `prompt` focused on the
    question. This separation helps the model distinguish information from instruction.
    Supply a custom `system_prompt` to change the model's persona for specialized tasks
    (e.g., "You are a Rust expert" or "You are a statistics tutor").

    **MODEL TIER:** Defaults to "auto" (standard). Use `tier="fast"` for simple factual
    Q&A; `tier="deep"` for multi-source synthesis requiring extended reasoning.

    Temperature guidance:
    - 0.1–0.3: factual, deterministic (Q&A over documents, data queries)
    - 0.5–0.7: balanced (general questions, explanations)
    - 0.8–1.0: creative, exploratory (brainstorming, generative writing)

    **CONSTRAINT WARNING:** `context` is capped at 10,000 chars for local models
    (50,000 for cloud); `prompt` at 2,000 chars. Both are truncated silently before
    sending. Use a larger model (18B+) or `tier="deep"` for complex synthesis.

    **OUTPUT EXPECTATION:** Returns the model's response. Quality scales with model size
    and context quality. For factual tasks, verify important claims against source tools.

    *Typical workflows:*
    - rag_tools/semantic_search → ask_model(context=chunks, prompt="What does it say about X?")
    - data_query/query_sqlite   → ask_model(context=table, prompt="What pattern do you see?")
    - local_searxng + page_scrape → ask_model(context=articles, prompt="Synthesize the main debate")
    """
    if not prompt.strip():
        return "Error: prompt cannot be empty."

    resolved = _resolve_model(tier, model)
    ctx_limit = 50_000 if _is_cloud_model(resolved) else 10_000

    prompt_clipped = prompt[:2000]
    ctx_clipped = context[:ctx_limit] if context else ""

    user_content = prompt_clipped
    if ctx_clipped:
        user_content = f"### Context\n\n{ctx_clipped}\n\n### Question\n\n{prompt_clipped}"

    messages = [
        {"role": "system", "content": system_prompt.strip() or _DEFAULT_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    return _call(messages, model=model, tier=tier, temperature=temperature, max_tokens=max_tokens)


@mcp.tool(
    name="llm_analyze_code",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
}
)
def analyze_code(
    code: str,
    language: str = "python",
    model: str = "auto",
    tier: _Tier = "deep",
    max_tokens: int = 1200,
) -> str:
    """
    Review code for correctness, performance, security, and style issues.

    **TRIGGER CONDITION:** Use after reading or producing code — from files via
    arch_system_tools/read_file, output from python_repl, or code scraped from web pages.
    Valuable for understanding unfamiliar scripts, reviewing before execution, or
    learning better patterns.

    **SEQUENCE GUIDANCE:** Pass the complete function or class rather than isolated lines
    for meaningful analysis. Specify `language` for accurate review (python, rust, go,
    sql, bash, javascript, typescript). Use `tier="deep"` (default) for the best analysis
    quality — cloud devstral-2 or glm-5.1 are designed for code review.

    **MODEL TIER:** Defaults to "deep" for best code review quality. Use `tier="standard"`
    for quick checks on simple code.

    **CONSTRAINT WARNING:** Code is capped at 12,000 chars for local models (50,000 for
    cloud). For large files, pass the most relevant section using
    arch_system_tools/read_file with max_lines.

    **OUTPUT EXPECTATION:** Returns a structured review under five headings (Correctness,
    Performance, Security, Style, Top Improvements). References specific function names
    or patterns. Low temperature (0.2) is used to keep analysis deterministic.

    *Typical workflows:*
    - arch_system_tools/read_file → analyze_code(code, language="python")
    - python_repl/execute_python (failing code) → analyze_code(code, language="python")
    """
    if not code.strip():
        return "Error: code cannot be empty."

    lang = language.strip() or "code"
    resolved = _resolve_model(tier, model)
    limit = _input_limit_for_model(resolved)

    clipped = code[:limit]
    if len(code) > limit:
        clipped += f"\n\n[... input truncated at {limit} chars ...]"

    system = _CODE_REVIEW_PROMPT.format(language=lang)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"```{lang}\n{clipped}\n```"},
    ]
    return _call(messages, model=model, tier=tier, temperature=0.2, max_tokens=max_tokens)


@mcp.tool(
    name="llm_interpret_data",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
}
)
def interpret_data(
    data: str,
    question: str,
    model: str = "auto",
    tier: _Tier = "standard",
    max_tokens: int = 800,
) -> str:
    """
    Ask a plain-language question about tabular or numerical data using an LLM.

    **TRIGGER CONDITION:** Use after running a database query, executing Python analysis,
    or reading a CSV/data file — when you need insight beyond raw numbers. Bridges the gap
    between query results and actionable, human-readable conclusions.

    **SEQUENCE GUIDANCE:** Pass formatted data (markdown table, CSV rows, describe()
    output, or JSON) in `data`. Keep `question` specific and analytical:
    - "What is the trend between Q1 and Q3?" (good)
    - "Tell me about this data" (too vague — use summarize instead)

    **MODEL TIER:** Defaults to "standard" for data tasks. Use `tier="deep"` for
    complex multi-variable statistical analysis.

    **CONSTRAINT WARNING:** `data` is capped at 10,000 chars for local models (50,000 for
    cloud). The model interprets the data as provided — it does not run new computations
    or access external sources. Verify surprising conclusions against the source query.

    **OUTPUT EXPECTATION:** Returns plain-language analysis: patterns, anomalies, trends,
    and quantitative observations grounded in the provided data.

    *Typical workflows:*
    - data_query/query_sqlite  → interpret_data(data=table, question="Are there outliers?")
    - data_query/query_duckdb  → interpret_data(data=result, question="What drives this?")
    - python_repl (df.describe()) → interpret_data(data=stats, question="Key takeaways?")
    """
    if not data.strip():
        return "Error: data cannot be empty."
    if not question.strip():
        return "Error: question cannot be empty."

    resolved = _resolve_model(tier, model)
    data_limit = 50_000 if _is_cloud_model(resolved) else 10_000

    messages = [
        {"role": "system", "content": _DATA_PROMPT},
        {
            "role": "user",
            "content": f"### Data\n\n{data[:data_limit]}\n\n### Question\n\n{question[:1000]}",
        },
    ]
    return _call(messages, model=model, tier=tier, temperature=0.4, max_tokens=max_tokens)


# ---------------------------------------------------------------------------
# Tools — new
# ---------------------------------------------------------------------------


@mcp.tool(
    name="llm_embed_text",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
}
)
def embed_text(
    text: str,
    model: str = "auto",
) -> str:
    """
    Generate vector embeddings for text via Ollama's embedding endpoint.

    **TRIGGER CONDITION:** Use when you need vector representations of text for
    semantic search, similarity comparison, or RAG indexing. Complements rag_tools
    by providing Ollama-native embeddings (nomic-embed-text, mxbai-embed-large, etc.)
    instead of SentenceTransformers.

    **SEQUENCE GUIDANCE:** Call `list_available_models` first to verify an embedding
    model is loaded. Default model: `nomic-embed-text` (768-dim, 8192-token context).
    Alternative: `mxbai-embed-large` (1024-dim, higher quality).

    **MODEL TIER:** Uses the embedding model, not a chat model.

    **CONSTRAINT WARNING:** Very long texts may be truncated by the embedding model's
    context window. For documents, chunk first using rag_tools/chunk_and_preview.

    **OUTPUT EXPECTATION:** Returns embedding dimension count, a preview of the first 5
    values, the model used, and the token count processed.
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
            f"### Embedding Result\n\n"
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
                "Pull it with: ollama pull " + embed_model
            )
        return f"Error generating embeddings: {err}"


@mcp.tool(
    name="llm_model_info",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
}
)
def model_info(
    model: str = "",
) -> str:
    """
    Show detailed capabilities and metadata for a loaded Ollama model.

    **TRIGGER CONDITION:** Use before selecting a model for a task — to check if it
    supports tool calling, vision, what its context length is, or what family it belongs to.
    Essential for capability-aware model selection in automated workflows.

    **SEQUENCE GUIDANCE:** Call after `list_available_models` to inspect a specific model
    of interest. Use the capabilities field to decide if a model is suitable for your task:
    - `completion` → text generation (all chat models have this)
    - `vision` → can process images (multimodal models)
    - `embedding` → can generate embeddings

    **MODEL TIER:** N/A — this tool queries Ollama metadata, does not call a model.

    **OUTPUT EXPECTATION:** Returns model name, family, parameter size, quantization,
    context length, capabilities, and template info.
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


@mcp.tool(
    name="llm_generate_code",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True
}
)
def generate_code(
    prompt: str,
    language: str = "python",
    context: str = "",
    model: str = "auto",
    tier: _Tier = "deep",
    max_tokens: int = 2000,
) -> str:
    """
    Generate production-ready code using a powerful coding model.

    **TRIGGER CONDITION:** Use when you need to write new code from a description —
    implementing a function, creating a script, or generating boilerplate. Distinct from
    `ask_model` in that it uses a coding-specialized system prompt and defaults to the
    deep tier (cloud devstral-2) for best results.

    **SEQUENCE GUIDANCE:** Be specific in `prompt` — describe what the code should do,
    inputs, outputs, and constraints. Provide existing code or API signatures in
    `context` so the model can integrate with what already exists.

    **MODEL TIER:** Defaults to "deep" (cloud devstral-2 or glm-5.1). These models are
    specifically trained for code generation and tool use. Use `tier="standard"` for
    simpler tasks or when cloud is unavailable.

    **CONSTRAINT WARNING:** Generated code may contain bugs — always validate with
    `analyze_code` and test with `python_repl/execute_python`. `prompt` is capped at
    2,000 chars; `context` at 50,000 chars for cloud models.

    **OUTPUT EXPECTATION:** Returns the generated code in a ```language code fence.
    No explanations outside the fence.

    *Typical workflows:*
    - code_intel/get_definitions → generate_code(context=api, prompt="implement client")
    - analyze_code (finds bug) → generate_code(prompt="fix the off-by-one", context=code)
    """
    if not prompt.strip():
        return "Error: prompt cannot be empty."

    lang = language.strip() or "code"
    resolved = _resolve_model(tier, model)
    ctx_limit = 50_000 if _is_cloud_model(resolved) else 10_000

    prompt_clipped = prompt[:2000]
    ctx_clipped = context[:ctx_limit] if context else ""

    user_content = prompt_clipped
    if ctx_clipped:
        user_content = f"### Existing Code / Context\n\n{ctx_clipped}\n\n### Task\n\n{prompt_clipped}"

    system = _CODE_GEN_PROMPT.format(language=lang)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user_content},
    ]
    return _call(messages, model=model, tier=tier, temperature=0.2, max_tokens=max_tokens)


@mcp.tool(
    name="llm_agent_chat",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True
}
)
def agent_chat(
    prompt: str,
    tools: str = "",
    model: str = "auto",
    tier: _Tier = "deep",
    max_steps: int = 5,
    system_prompt: str = "",
) -> str:
    """
    Multi-turn agent loop with native Ollama tool calling.

    **TRIGGER CONDITION:** Use for complex, multi-step tasks that require the model to
    reason about what tools to call, call them, observe results, and decide on next steps.
    This is the agentic orchestration layer — the model itself decides which tools to use
    and in what order.

    **SEQUENCE GUIDANCE:** Describe the task clearly in `prompt`. Pass available tools as
    a JSON array in `tools` — each tool should have `name`, `description`, and `parameters`
    (JSON Schema). The model will decide which tools to call at each step.

    **MODEL TIER:** Defaults to "deep" (cloud deepseek-v4-pro). Local qwen3:14b and qwen3:4b
    also support tool calling via Ollama's native `/api/chat`. Use `tier="standard"` for
    offline use. Known caveat: qwen3:14b may fall back to plaintext `<tool_call>` tags
    instead of structured JSON when context history grows large — keep max_steps ≤ 5 or
    use the cloud tier for long sessions.

    **CONSTRAINT WARNING:** Each step counts toward `max_steps` (default 5). The loop stops
    when the model produces a final text response without tool calls, or max_steps is reached.
    Tool call results are simulated — this tool shows what the model WOULD call, but does
    not execute the tools (the calling agent must execute them).

    **OUTPUT EXPECTATION:** Returns the conversation trace showing model reasoning, tool
    calls attempted, and the final answer. If the model uses thinking mode, the reasoning
    trace is included.

    *Typical workflows:*
    - research(topic) → agent_chat with search+scrape tools
    - code_task(description) → agent_chat with code_intel+code_check tools
    """
    if not prompt.strip():
        return "Error: prompt cannot be empty."

    resolved = _resolve_model(tier, model)

    messages: list[dict] = []
    if system_prompt.strip():
        messages.append({"role": "system", "content": system_prompt.strip()})
    else:
        messages.append({
            "role": "system",
            "content": (
                "You are an intelligent agent with access to tools. "
                "Break down the user's task, call the appropriate tools, "
                "and provide a final answer. Think step by step."
            ),
        })
    messages.append({"role": "user", "content": prompt[:5000]})

    tools_list = None
    if tools.strip():
        import json
        try:
            tools_list = json.loads(tools)
            if not isinstance(tools_list, list):
                return "Error: `tools` must be a JSON array of tool definitions."
        except json.JSONDecodeError:
            return "Error: `tools` must be valid JSON."

    trace_lines = [f"### Agent Chat: `{resolved}`\n"]

    for step in range(max_steps):
        try:
            result = _call_native(
                messages=messages,
                model=resolved,
                tools=tools_list,
                temperature=0.3,
                num_predict=2000,
            )
        except Exception as e:
            return f"Error at step {step + 1}: {e}"

        msg = result.get("message", {})
        content = msg.get("content", "")
        thinking = msg.get("thinking", "")
        tool_calls = msg.get("tool_calls", [])

        # Log the step
        trace_lines.append(f"**Step {step + 1}:**")
        if thinking:
            trace_lines.append(f"  *Thinking:* {thinking[:500]}...")
        if tool_calls:
            for tc in tool_calls:
                fn = tc.get("function", {})
                trace_lines.append(
                    f"  🔧 Tool call: `{fn.get('name', '?')}`({fn.get('arguments', {})})"
                )
            # Append assistant message with tool calls to conversation history
            messages.append(msg)
            # For each tool call, add a placeholder tool result
            for tc in tool_calls:
                fn = tc.get("function", {})
                tool_name = fn.get("name", "unknown")
                tool_call_id = tc.get("id", "")
                messages.append({
                    "role": "tool",
                    "content": f"[Tool '{tool_name}' result not available — agent must execute tools externally]",
                    "tool_call_id": tool_call_id,
                })
        elif content:
            # Final answer — no more tool calls
            trace_lines.append(f"\n**Final Answer:**\n\n{content}")
            break
        else:
            trace_lines.append("  *(no content or tool calls — stopping)*")
            break

    else:
        trace_lines.append(f"\n*Reached max_steps={max_steps} without a final answer.*")

    # Include token stats if available
    eval_count = result.get("eval_count", 0)
    prompt_eval = result.get("prompt_eval_count", 0)
    if eval_count or prompt_eval:
        trace_lines.append(
            f"\n*Tokens: {prompt_eval} prompt + {eval_count} generated*"
        )

    return "\n".join(trace_lines)


if __name__ == "__main__":
    mcp.run(transport="stdio")