"""
MCP server for delegating tasks to locally-running LLMs.

Targets LM Studio's OpenAI-compatible API (default: http://localhost:1234/v1).
Switch to Ollama by setting: LM_STUDIO_URL=http://localhost:11434/v1
Override the default model with: LLM_TOOLS_DEFAULT_MODEL=<model-id>
"""

import os
from typing import Literal

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("LLM Tools")

# Configurable backend — works with LM Studio (default) or Ollama
LM_STUDIO_BASE = os.getenv("LM_STUDIO_URL", "http://localhost:1234/v1")

# Hard cap on user-supplied text sent to local models.
# ~12k chars ≈ ~3k tokens, comfortably inside a 4B model's context window.
_INPUT_CHAR_LIMIT = 12_000

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

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_client = None


def _get_client():
    """Return a module-level OpenAI client (lazy init to avoid MCP handshake delays)."""
    global _client
    if _client is None:
        from openai import OpenAI
        _client = OpenAI(base_url=LM_STUDIO_BASE, api_key="lm-studio")
    return _client


def _resolve_model(model: str) -> str:
    """
    Resolve 'auto'/empty string to a concrete model ID.
    Priority: explicit arg > LLM_TOOLS_DEFAULT_MODEL env var > first loaded model > hardcoded fallback.
    """
    if model and model not in ("auto", ""):
        return model

    env_default = os.getenv("LLM_TOOLS_DEFAULT_MODEL", "")
    if env_default:
        return env_default

    try:
        data = _get_client().models.list().data
        if data:
            return data[0].id
    except Exception:
        pass

    return "nvidia/nemotron-3-nano-4b"


def _call(
    messages: list[dict],
    model: str,
    temperature: float = 0.4,
    max_tokens: int = 800,
) -> str:
    """Execute one chat completion and return the text, or a formatted error string."""
    try:
        resp = _get_client().chat.completions.create(
            model=_resolve_model(model),
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return (resp.choices[0].message.content or "(empty response)").strip()
    except Exception as e:
        err = str(e)
        if "connection" in err.lower() or "refused" in err.lower() or "connect" in err.lower():
            return (
                f"Error: LM Studio is not reachable at {LM_STUDIO_BASE}. "
                "Ensure LM Studio is running with a model loaded, then retry. "
                "For Ollama set LM_STUDIO_URL=http://localhost:11434/v1"
            )
        if "404" in err or "model_not_found" in err.lower() or "not found" in err.lower():
            return (
                f"Error: Model '{model}' is not loaded. "
                "Call `list_available_models` to see what is currently available."
            )
        return f"Error from LLM API: {err}"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def list_available_models() -> str:
    """
    List all models currently loaded in LM Studio (or Ollama) and ready for inference.

    **TRIGGER CONDITION:** Call this BEFORE any other llm_tools call when you are unsure
    which model IDs are available, or when a previous call failed with a model-not-found error.

    **SEQUENCE GUIDANCE:** Use the returned model IDs as the `model` parameter in
    summarize, ask_model, analyze_code, and interpret_data.
    - 4B models (nemotron, gemma) → fast, sufficient for simple summarization
    - 9B reasoning models (qwen3.5) → code analysis, nuanced interpretation
    - 18B–27B models → complex synthesis, multi-document reasoning

    **OUTPUT EXPECTATION:** Returns model IDs with context window sizes.
    Pass the exact ID string as the `model` parameter.
    """
    try:
        data = _get_client().models.list().data
        if not data:
            return (
                "No models currently loaded. "
                "Open LM Studio, load a model, then retry."
            )
        lines = ["### Models available\n"]
        for m in data:
            ctx = getattr(m, "context_length", None)
            ctx_str = f" — {ctx:,} token context" if ctx else ""
            lines.append(f"- `{m.id}`{ctx_str}")
        lines.append(
            "\n*Pass the model ID as the `model` parameter in other llm_tools calls. "
            "Omit or pass \"auto\" to use the first listed model.*"
        )
        return "\n".join(lines)
    except Exception as e:
        err = str(e)
        if "connection" in err.lower() or "refused" in err.lower():
            return (
                f"Error: LM Studio is not running at {LM_STUDIO_BASE}. "
                "Start LM Studio and load a model first. "
                "To use Ollama instead, set LM_STUDIO_URL=http://localhost:11434/v1"
            )
        return f"Error listing models: {err}"


@mcp.tool()
def summarize(
    text: str,
    style: Literal["concise", "detailed", "bullets", "eli5"] = "concise",
    model: str = "auto",
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

    Omit `model` or pass "auto" to use the first available model. Call
    `list_available_models` first to see what is loaded.

    **CONSTRAINT WARNING:** Input is capped at 12,000 characters — content beyond that is
    truncated before sending. For longer documents, call this in sections using pagination
    from rag_tools/read_doc_content or page_scrape/fetch_url_content. Requires LM Studio
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

    clipped = text[:_INPUT_CHAR_LIMIT]
    if len(text) > _INPUT_CHAR_LIMIT:
        clipped += f"\n\n[... input truncated at {_INPUT_CHAR_LIMIT} chars ...]"

    messages = [
        {"role": "system", "content": _SUMMARIZE_PROMPTS[style]},
        {"role": "user", "content": clipped},
    ]
    return _call(messages, model, temperature=0.3, max_tokens=max_tokens)


@mcp.tool()
def ask_model(
    prompt: str,
    context: str = "",
    model: str = "auto",
    system_prompt: str = "",
    temperature: float = 0.7,
    max_tokens: int = 1000,
) -> str:
    """
    Send a free-form query to a local LLM, optionally grounding it in retrieved context.

    **TRIGGER CONDITION:** Use when you need the model to reason over content retrieved by
    other tools — document chunks, search snippets, query results, code output — without a
    pre-defined task format. This is the synthesis step in RAG workflows.

    **SEQUENCE GUIDANCE:** Put retrieved content in `context`, keep `prompt` focused on the
    question. This separation helps the model distinguish information from instruction.
    Supply a custom `system_prompt` to change the model's persona for specialized tasks
    (e.g., "You are a Rust expert" or "You are a statistics tutor").

    Temperature guidance:
    - 0.1–0.3: factual, deterministic (Q&A over documents, data queries)
    - 0.5–0.7: balanced (general questions, explanations)
    - 0.8–1.0: creative, exploratory (brainstorming, generative writing)

    **CONSTRAINT WARNING:** `context` is capped at 10,000 chars; `prompt` at 2,000 chars.
    Both are truncated silently before sending. Use a larger model (18B+) for complex
    synthesis involving multiple evidence sources.

    **OUTPUT EXPECTATION:** Returns the model's response. Quality scales with model size
    and context quality. For factual tasks, verify important claims against source tools.

    *Typical workflows:*
    - rag_tools/semantic_search → ask_model(context=chunks, prompt="What does it say about X?")
    - data_query/query_sqlite   → ask_model(context=table, prompt="What pattern do you see?")
    - local_searxng + page_scrape → ask_model(context=articles, prompt="Synthesize the main debate")
    """
    if not prompt.strip():
        return "Error: prompt cannot be empty."

    prompt_clipped = prompt[:2000]
    ctx_clipped = context[:10000] if context else ""

    user_content = prompt_clipped
    if ctx_clipped:
        user_content = f"### Context\n\n{ctx_clipped}\n\n### Question\n\n{prompt_clipped}"

    messages = [
        {"role": "system", "content": system_prompt.strip() or _DEFAULT_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    return _call(messages, model, temperature=temperature, max_tokens=max_tokens)


@mcp.tool()
def analyze_code(
    code: str,
    language: str = "python",
    model: str = "auto",
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
    sql, bash, javascript, typescript). Use a reasoning-capable model (9B+) for nuanced
    analysis — 4B models may miss subtle logic errors.

    **CONSTRAINT WARNING:** Code is capped at 12,000 characters. For large files, pass
    the most relevant section using arch_system_tools/read_file with max_lines.

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
    clipped = code[:_INPUT_CHAR_LIMIT]
    system = _CODE_REVIEW_PROMPT.format(language=lang)

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": f"```{lang}\n{clipped}\n```"},
    ]
    return _call(messages, model, temperature=0.2, max_tokens=max_tokens)


@mcp.tool()
def interpret_data(
    data: str,
    question: str,
    model: str = "auto",
    max_tokens: int = 800,
) -> str:
    """
    Ask a plain-language question about tabular or numerical data using a local LLM.

    **TRIGGER CONDITION:** Use after running a database query, executing Python analysis,
    or reading a CSV/data file — when you need insight beyond raw numbers. Bridges the gap
    between query results and actionable, human-readable conclusions.

    **SEQUENCE GUIDANCE:** Pass formatted data (markdown table, CSV rows, describe()
    output, or JSON) in `data`. Keep `question` specific and analytical:
    - "What is the trend between Q1 and Q3?" (good)
    - "Tell me about this data" (too vague — use summarize instead)

    For large result sets, use LIMIT in SQL queries or sample with python_repl before
    passing. A 9B+ reasoning model significantly improves multi-variable analysis.

    **CONSTRAINT WARNING:** `data` is capped at 10,000 characters. The model interprets
    the data as provided — it does not run new computations or access external sources.
    Verify surprising conclusions against the source query.

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

    messages = [
        {"role": "system", "content": _DATA_PROMPT},
        {
            "role": "user",
            "content": f"### Data\n\n{data[:10000]}\n\n### Question\n\n{question[:1000]}",
        },
    ]
    return _call(messages, model, temperature=0.4, max_tokens=max_tokens)


if __name__ == "__main__":
    mcp.run(transport="stdio")
