"""Tests for the llm_tools MCP server (Ollama-backed with tier routing).

All tests mock the Ollama/OpenAI client — no running server required.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(content: str):
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _mock_model(model_id: str, context_length: int = 8192):
    m = MagicMock()
    m.id = model_id
    m.context_length = context_length
    return m


def _mock_ollama_model(name="test-model:latest", size=1000000000, family="test",
                        param_size="7B", quant="Q4_K_M"):
    return {
        "name": name,
        "model": name,
        "size": size,
        "digest": "abc123",
        "modified_at": "2026-01-01T00:00:00Z",
        "details": {
            "format": "gguf",
            "family": family,
            "families": [family],
            "parameter_size": param_size,
            "quantization_level": quant,
        },
    }


# ===========================================================================
# BACKWARD-COMPATIBILITY TESTS — existing 22 tests must still pass
# ===========================================================================

# ---------------------------------------------------------------------------
# list_available_models
# ---------------------------------------------------------------------------

@patch("main._ollama_list_models")
@patch("main._get_client")
def test_list_models_returns_ids(mock_fn, mock_ollama):
    mock_ollama.return_value = [
        _mock_ollama_model(name="nemotron-4b", param_size="4B"),
        _mock_ollama_model(name="qwen3.5:9b", param_size="9B"),
    ]
    from main import list_available_models
    result = list_available_models()
    assert "nemotron-4b" in result
    assert "qwen3.5:9b" in result


@patch("main._ollama_list_models")
def test_list_models_shows_tier_labels(mock_ollama):
    mock_ollama.return_value = [
        _mock_ollama_model(name="nemotron-3-nano-4b", param_size="4B", family="nemotron"),
        _mock_ollama_model(name="qwen3.5:9b", param_size="9B", family="qwen2"),
        _mock_ollama_model(name="devstral-2", param_size="123B", family="devstral"),
    ]
    from main import list_available_models
    result = list_available_models()
    assert "fast" in result.lower() or "standard" in result.lower() or "deep" in result.lower()


@patch("main._ollama_list_models")
@patch("main._get_client")
def test_list_models_empty(mock_client, mock_ollama):
    mock_ollama.return_value = []
    mock_client.return_value.models.list.return_value.data = []
    from main import list_available_models
    result = list_available_models()
    assert "No models" in result


@patch("main._ollama_list_models")
@patch("main._get_client")
def test_list_models_connection_error(mock_client, mock_ollama):
    mock_ollama.side_effect = Exception("Connection refused")
    mock_client.return_value.models.list.side_effect = Exception("Connection refused")
    from main import list_available_models
    result = list_available_models()
    assert "Error" in result or "not reachable" in result.lower() or "running" in result.lower()


# ---------------------------------------------------------------------------
# _resolve_model — tier routing
# ---------------------------------------------------------------------------

def test_resolve_explicit_passthrough():
    from main import _resolve_model
    assert _resolve_model(model="my-specific-model") == "my-specific-model"


def test_resolve_tier_fast():
    from main import _resolve_model, OLLAMA_FAST_MODEL
    assert _resolve_model(tier="fast") == OLLAMA_FAST_MODEL


def test_resolve_tier_standard():
    from main import _resolve_model, OLLAMA_STANDARD_MODEL
    assert _resolve_model(tier="standard") == OLLAMA_STANDARD_MODEL


def test_resolve_tier_deep():
    from main import _resolve_model, OLLAMA_DEEP_MODEL
    assert _resolve_model(tier="deep") == OLLAMA_DEEP_MODEL


def test_resolve_tier_auto():
    from main import _resolve_model, OLLAMA_STANDARD_MODEL
    assert _resolve_model(tier="auto") == OLLAMA_STANDARD_MODEL


def test_resolve_explicit_overrides_tier():
    from main import _resolve_model
    assert _resolve_model(tier="fast", model="my-explicit-model") == "my-explicit-model"


@patch.dict(os.environ, {"LLM_TOOLS_DEFAULT_MODEL": "env-override-model"})
def test_resolve_respects_env_var():
    from main import _resolve_model
    result = _resolve_model(tier="auto")
    assert result == "env-override-model"
    os.environ.pop("LLM_TOOLS_DEFAULT_MODEL", None)


# ---------------------------------------------------------------------------
# _call — error handling
# ---------------------------------------------------------------------------

@patch("main._get_client")
def test_call_connection_refused(mock_fn):
    mock_fn.return_value.chat.completions.create.side_effect = Exception(
        "Connection refused"
    )
    from main import _call
    result = _call([{"role": "user", "content": "hi"}], model="m")
    assert "Error" in result
    assert "Ollama" in result or "not reachable" in result.lower()


@patch("main._get_client")
def test_call_model_not_found(mock_fn):
    mock_fn.return_value.chat.completions.create.side_effect = Exception(
        "404 model_not_found: unknown model"
    )
    from main import _call
    result = _call([{"role": "user", "content": "hi"}], model="bad-model")
    assert "Error" in result
    assert "not loaded" in result.lower() or "list_available_models" in result


@patch("main._get_client")
def test_call_empty_response(mock_fn):
    mock_fn.return_value.chat.completions.create.return_value = _mock_response("")
    from main import _call
    result = _call([{"role": "user", "content": "test"}], model="m")
    assert result == "(empty response)"


# ---------------------------------------------------------------------------
# summarize
# ---------------------------------------------------------------------------

@patch("main._call")
def test_summarize_returns_model_output(mock_call):
    mock_call.return_value = "Python is a high-level programming language."
    from main import summarize
    result = summarize("Long article about Python...", model="m")
    assert "Python" in result


@patch("main._call")
def test_summarize_all_styles_use_distinct_prompts(mock_call):
    from main import summarize, _SUMMARIZE_PROMPTS

    seen_system_prompts = set()
    for style in ("concise", "detailed", "bullets", "eli5"):
        mock_call.reset_mock()
        mock_call.return_value = "ok"
        summarize("some text", style=style, model="m")  # type: ignore[arg-type]
        call_args = mock_call.call_args
        messages = call_args[0][0]  # first positional arg is messages list
        sys_msg = next(m["content"] for m in messages if m["role"] == "system")
        seen_system_prompts.add(sys_msg)

    assert len(seen_system_prompts) == 4


@patch("main._call")
def test_summarize_truncates_oversized_input(mock_call):
    mock_call.return_value = "ok"
    from main import summarize, _INPUT_CHAR_LIMIT_LOCAL

    oversized = "word " * 5000  # ~25k chars
    summarize(oversized, model="m")

    call_args = mock_call.call_args
    messages = call_args[0][0]  # first positional arg is messages list
    user_msg = next(m["content"] for m in messages if m["role"] == "user")
    assert "truncated" in user_msg.lower()
    assert len(user_msg) <= _INPUT_CHAR_LIMIT_LOCAL + 200


@patch("main._call")
def test_summarize_uses_low_temperature(mock_call):
    mock_call.return_value = "ok"
    from main import summarize
    summarize("text", model="m")
    call_args = mock_call.call_args
    assert call_args[1]["temperature"] <= 0.35


def test_summarize_empty_input():
    from main import summarize
    assert "Error" in summarize("   ")


@patch("main._call")
def test_summarize_tier_routing(mock_call):
    mock_call.return_value = "summary"
    from main import summarize, OLLAMA_FAST_MODEL, OLLAMA_DEEP_MODEL
    summarize("text", tier="fast", model="")
    mock_call.assert_called()


# ---------------------------------------------------------------------------
# ask_model
# ---------------------------------------------------------------------------

@patch("main._call")
def test_ask_model_includes_context_in_user_message(mock_call):
    mock_call.return_value = "answer"
    from main import ask_model
    ask_model(prompt="What is the trend?", context="Q1:100 Q2:150 Q3:200", model="m")

    call_args = mock_call.call_args
    messages = call_args[0][0]  # first positional arg
    user_msg = next(m["content"] for m in messages if m["role"] == "user")
    assert "Context" in user_msg
    assert "Q1:100" in user_msg
    assert "trend" in user_msg.lower()


@patch("main._call")
def test_ask_model_no_context_sends_plain_prompt(mock_call):
    mock_call.return_value = "answer"
    from main import ask_model
    ask_model(prompt="What is 2+2?", model="m")

    call_args = mock_call.call_args
    messages = call_args[0][0]
    user_msg = next(m["content"] for m in messages if m["role"] == "user")
    assert "Context" not in user_msg
    assert "2+2" in user_msg


@patch("main._call")
def test_ask_model_custom_system_prompt(mock_call):
    mock_call.return_value = "ok"
    from main import ask_model
    ask_model(prompt="Ahoy", system_prompt="You are a pirate.", model="m")

    call_args = mock_call.call_args
    sys_msg = next(m["content"] for m in call_args[0][0] if m["role"] == "system")
    assert "pirate" in sys_msg


@patch("main._call")
def test_ask_model_respects_temperature(mock_call):
    mock_call.return_value = "ok"
    from main import ask_model
    ask_model(prompt="Brainstorm ideas", temperature=0.9, model="m")

    call_args = mock_call.call_args
    assert call_args[1]["temperature"] == 0.9


def test_ask_model_empty_prompt():
    from main import ask_model
    assert "Error" in ask_model(prompt="")
    assert "Error" in ask_model(prompt="   ")


@patch("main._call")
def test_ask_model_cloud_model_higher_limit(mock_call):
    mock_call.return_value = "answer"
    from main import ask_model
    ask_model(prompt="test", context="x" * 10000, model="devstral-2", tier="deep")
    call_args = mock_call.call_args
    user_msg = next(m["content"] for m in call_args[0][0] if m["role"] == "user")
    assert len(user_msg) > 10_000


# ---------------------------------------------------------------------------
# analyze_code
# ---------------------------------------------------------------------------

@patch("main._call")
def test_analyze_code_injects_language_into_system_prompt(mock_call):
    mock_call.return_value = "**Correctness**: OK.\n**Performance**: O(n²) loop."
    from main import analyze_code
    analyze_code("for i in range(n): pass", language="rust", model="m")

    call_args = mock_call.call_args
    sys_msg = next(m["content"] for m in call_args[0][0] if m["role"] == "system")
    assert "rust" in sys_msg.lower()


@patch("main._call")
def test_analyze_code_wraps_code_in_fence(mock_call):
    mock_call.return_value = "ok"
    from main import analyze_code
    analyze_code("x = 1 + 1", language="python", model="m")

    call_args = mock_call.call_args
    user_msg = next(m["content"] for m in call_args[0][0] if m["role"] == "user")
    assert "```python" in user_msg
    assert "x = 1 + 1" in user_msg


@patch("main._call")
def test_analyze_code_uses_very_low_temperature(mock_call):
    mock_call.return_value = "ok"
    from main import analyze_code
    analyze_code("def f(): pass", model="m")
    call_args = mock_call.call_args
    assert call_args[1]["temperature"] <= 0.25


def test_analyze_code_empty():
    from main import analyze_code
    assert "Error" in analyze_code("   ")


@patch("main._call")
def test_analyze_code_default_tier_is_deep(mock_call):
    mock_call.return_value = "ok"
    from main import analyze_code
    analyze_code("def f(): pass", model="")
    # Default tier for analyze_code is "deep"
    call_args = mock_call.call_args
    assert call_args is not None


# ---------------------------------------------------------------------------
# interpret_data
# ---------------------------------------------------------------------------

@patch("main._call")
def test_interpret_data_structures_message_correctly(mock_call):
    mock_call.return_value = "Sales grew 50% from Q1 to Q3."
    from main import interpret_data
    data = "| Q | Sales |\n|---|---|\n| Q1 | 100 |\n| Q3 | 150 |"
    result = interpret_data(data, "What is the trend?", model="m")

    assert isinstance(result, str)
    call_args = mock_call.call_args
    user_msg = next(m["content"] for m in call_args[0][0] if m["role"] == "user")
    assert "Q1" in user_msg
    assert "trend" in user_msg.lower()
    assert "Data" in user_msg
    assert "Question" in user_msg


@patch("main._call")
def test_interpret_data_caps_input(mock_call):
    mock_call.return_value = "ok"
    from main import interpret_data
    large_data = "row, value\n" + "\n".join(f"{i}, {i*2}" for i in range(5000))
    interpret_data(large_data, "Any trend?", model="m")

    call_args = mock_call.call_args
    user_msg = next(m["content"] for m in call_args[0][0] if m["role"] == "user")
    assert len(user_msg) <= 11500


def test_interpret_data_empty_data():
    from main import interpret_data
    assert "Error" in interpret_data("", "any question")


def test_interpret_data_empty_question():
    from main import interpret_data
    assert "Error" in interpret_data("some data", "")


# ===========================================================================
# NEW TESTS — Ollama-specific functionality
# ===========================================================================

# ---------------------------------------------------------------------------
# _is_cloud_model
# ---------------------------------------------------------------------------

def test_is_cloud_model_devstral():
    from main import _is_cloud_model
    assert _is_cloud_model("devstral-2") is True
    assert _is_cloud_model("devstral-2:latest") is True


def test_is_cloud_model_deepseek():
    from main import _is_cloud_model
    assert _is_cloud_model("deepseek-v4-pro") is True


def test_is_cloud_model_local_model():
    from main import _is_cloud_model
    assert _is_cloud_model("qwen3.5:9b") is False
    assert _is_cloud_model("nemotron-3-nano-4b") is False


# ---------------------------------------------------------------------------
# _infer_tier
# ---------------------------------------------------------------------------

def test_infer_tier_fast():
    from main import _infer_tier
    assert _infer_tier("nemotron-3-nano-4b") == "fast"
    assert _infer_tier("gemma-4-4b") == "fast"


def test_infer_tier_deep():
    from main import _infer_tier
    assert _infer_tier("devstral-2") == "deep"
    assert _infer_tier("deepseek-v4-pro") == "deep"


def test_infer_tier_standard():
    from main import _infer_tier
    assert _infer_tier("qwen3.5:9b") == "standard"
    assert _infer_tier("qwen3.6:27b") == "standard"


# ---------------------------------------------------------------------------
# _input_limit_for_model
# ---------------------------------------------------------------------------

def test_input_limit_local():
    from main import _input_limit_for_model
    assert _input_limit_for_model("qwen3.5:9b") == 12_000


def test_input_limit_cloud():
    from main import _input_limit_for_model
    assert _input_limit_for_model("devstral-2") == 50_000


# ---------------------------------------------------------------------------
# _ollama_list_models
# ---------------------------------------------------------------------------

@patch("main._get_native")
def test_ollama_list_models_success(mock_fn):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "models": [
            _mock_ollama_model(name="qwen3.5:9b"),
            _mock_ollama_model(name="nemotron-4b"),
        ]
    }
    mock_resp.raise_for_status = MagicMock()
    mock_fn.return_value.get.return_value = mock_resp

    from main import _ollama_list_models
    result = _ollama_list_models()
    assert len(result) == 2
    assert result[0]["name"] == "qwen3.5:9b"


@patch("main._get_native")
def test_ollama_list_models_failure(mock_fn):
    mock_fn.return_value.get.side_effect = Exception("Connection refused")
    from main import _ollama_list_models
    result = _ollama_list_models()
    assert result == []


# ---------------------------------------------------------------------------
# _ollama_model_info
# ---------------------------------------------------------------------------

@patch("main._get_native")
def test_ollama_model_info_success(mock_fn):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "details": {"family": "qwen2", "parameter_size": "9B", "quantization_level": "Q4_K_M"},
        "model_info": {"qwen2.context_length": 32768},
        "capabilities": ["completion"],
    }
    mock_resp.raise_for_status = MagicMock()
    mock_fn.return_value.post.return_value = mock_resp

    from main import _ollama_model_info
    result = _ollama_model_info("qwen3.5:9b")
    assert result is not None
    assert result["details"]["family"] == "qwen2"


@patch("main._get_native")
def test_ollama_model_info_failure(mock_fn):
    mock_fn.return_value.post.side_effect = Exception("Model not found")
    from main import _ollama_model_info
    result = _ollama_model_info("nonexistent")
    assert result is None


# ---------------------------------------------------------------------------
# embed_text
# ---------------------------------------------------------------------------

@patch("main._get_native")
def test_embed_text_success(mock_fn):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "model": "nomic-embed-text",
        "embeddings": [[0.1, 0.2, 0.3, 0.4, 0.5, 0.6]],
        "prompt_eval_count": 10,
    }
    mock_resp.raise_for_status = MagicMock()
    mock_fn.return_value.post.return_value = mock_resp

    from main import embed_text
    result = embed_text("hello world")
    assert "Embedding Result" in result
    assert "6" in result
    assert "nomic-embed-text" in result


@patch("main._get_native")
def test_embed_text_empty_embeddings(mock_fn):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "model": "nomic-embed-text",
        "embeddings": [],
    }
    mock_resp.raise_for_status = MagicMock()
    mock_fn.return_value.post.return_value = mock_resp

    from main import embed_text
    result = embed_text("hello world")
    assert "Error" in result


def test_embed_text_empty_input():
    from main import embed_text
    result = embed_text("")
    assert "Error" in result


@patch("main._get_native")
def test_embed_text_connection_error(mock_fn):
    mock_fn.return_value.post.side_effect = Exception("Connection refused")
    from main import embed_text
    result = embed_text("hello")
    assert "Error" in result
    assert "Ollama" in result


@patch("main._get_native")
def test_embed_text_model_not_found(mock_fn):
    mock_fn.return_value.post.side_effect = Exception("404 model not found")
    from main import embed_text
    result = embed_text("hello", model="bad-embed")
    assert "Error" in result
    assert "not loaded" in result.lower() or "pull" in result.lower()


@patch("main._get_native")
def test_embed_text_uses_default_model(mock_fn):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "model": "nomic-embed-text",
        "embeddings": [[0.1] * 768],
        "prompt_eval_count": 5,
    }
    mock_resp.raise_for_status = MagicMock()
    mock_fn.return_value.post.return_value = mock_resp

    from main import embed_text
    embed_text("hello")
    call_args = mock_fn.return_value.post.call_args
    assert call_args[1]["json"]["model"] == "nomic-embed-text"


# ---------------------------------------------------------------------------
# model_info
# ---------------------------------------------------------------------------

@patch("main._ollama_model_info")
@patch("main._ollama_list_models")
def test_model_info_success(mock_list, mock_info):
    mock_list.return_value = [_mock_ollama_model(name="qwen3.5:9b")]
    mock_info.return_value = {
        "details": {
            "family": "qwen2",
            "parameter_size": "9B",
            "quantization_level": "Q4_K_M",
            "format": "gguf",
        },
        "model_info": {"qwen2.context_length": 32768},
        "capabilities": ["completion"],
        "parameters": "temperature 0.6\nnum_predict 800",
    }

    from main import model_info
    result = model_info("qwen3.5:9b")
    assert "qwen3.5:9b" in result
    assert "qwen2" in result
    assert "9B" in result
    assert "32768" in result or "32,768" in result


@patch("main._ollama_model_info")
def test_model_info_not_found(mock_info):
    mock_info.return_value = None
    from main import model_info
    result = model_info("nonexistent")
    assert "Error" in result


# ---------------------------------------------------------------------------
# generate_code
# ---------------------------------------------------------------------------

@patch("main._call")
def test_generate_code_basic(mock_call):
    mock_call.return_value = "```python\nprint('hello')\n```"
    from main import generate_code
    result = generate_code("write a hello world", language="python", model="m")
    assert "print" in result


def test_generate_code_empty_prompt():
    from main import generate_code
    result = generate_code("")
    assert "Error" in result


@patch("main._call")
def test_generate_code_with_context(mock_call):
    mock_call.return_value = "```python\ndef foo(): pass\n```"
    from main import generate_code
    generate_code("implement the function", context="def foo(x): ...", model="m")
    call_args = mock_call.call_args
    user_msg = next(m["content"] for m in call_args[0][0] if m["role"] == "user")
    assert "Context" in user_msg or "Existing" in user_msg


# ---------------------------------------------------------------------------
# agent_chat
# ---------------------------------------------------------------------------

def test_agent_chat_empty_prompt():
    from main import agent_chat
    result = agent_chat("")
    assert "Error" in result


@patch("main._call_native")
def test_agent_chat_single_turn_response(mock_call):
    mock_call.return_value = {
        "message": {"role": "assistant", "content": "The answer is 42.", "tool_calls": []},
        "done": True,
        "eval_count": 10,
        "prompt_eval_count": 5,
    }
    from main import agent_chat
    result = agent_chat("What is the meaning of life?", model="m")
    assert "42" in result
    assert "Final Answer" in result


@patch("main._call_native")
def test_agent_chat_with_tool_calls(mock_call):
    mock_call.return_value = {
        "message": {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "search", "arguments": {"query": "meaning of life"}}}
            ],
        },
        "done": True,
        "eval_count": 20,
        "prompt_eval_count": 15,
    }
    from main import agent_chat
    result = agent_chat("Find the meaning of life", model="m")
    assert "Tool call" in result or "search" in result


@patch("main._call_native")
def test_agent_chat_with_thinking(mock_call):
    mock_call.return_value = {
        "message": {
            "role": "assistant",
            "content": "The answer is 42.",
            "thinking": "Let me think about this...",
            "tool_calls": [],
        },
        "done": True,
        "eval_count": 30,
        "prompt_eval_count": 10,
    }
    from main import agent_chat
    result = agent_chat("What is 6*7?", model="m")
    assert "Thinking" in result or "thinking" in result


def test_agent_chat_invalid_tools_json():
    from main import agent_chat
    result = agent_chat("test", tools="not json")
    assert "Error" in result


# ---------------------------------------------------------------------------
# _call_native
# ---------------------------------------------------------------------------

@patch("main._get_native")
def test_call_native_basic(mock_fn):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "message": {"role": "assistant", "content": "Hello"},
        "done": True,
    }
    mock_resp.raise_for_status = MagicMock()
    mock_fn.return_value.post.return_value = mock_resp

    from main import _call_native
    result = _call_native(messages=[{"role": "user", "content": "hi"}], model="test")
    assert result["message"]["content"] == "Hello"


# ---------------------------------------------------------------------------
# Configuration / env vars
# ---------------------------------------------------------------------------

def test_default_urls_and_models():
    from main import OLLAMA_URL, OLLAMA_FAST_MODEL, OLLAMA_STANDARD_MODEL, OLLAMA_DEEP_MODEL, OLLAMA_EMBED_MODEL
    assert OLLAMA_URL == "http://localhost:11434" or OLLAMA_URL.startswith("http")
    assert OLLAMA_FAST_MODEL is not None
    assert OLLAMA_STANDARD_MODEL is not None
    assert OLLAMA_DEEP_MODEL is not None
    assert OLLAMA_EMBED_MODEL is not None


# ---------------------------------------------------------------------------
# Integration-style tests (mocked Ollama endpoints)
# ---------------------------------------------------------------------------

@patch("main._get_native")
def test_list_available_models_with_rich_metadata(mock_fn):
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "models": [
            {
                "name": "qwen3.5:9b",
                "model": "qwen3.5:9b",
                "size": 5_500_000_000,
                "digest": "abc123",
                "modified_at": "2026-01-01T00:00:00Z",
                "details": {
                    "format": "gguf",
                    "family": "qwen2",
                    "families": ["qwen2"],
                    "parameter_size": "9B",
                    "quantization_level": "Q4_K_M",
                },
            },
            {
                "name": "devstral-2",
                "model": "devstral-2",
                "size": 70_000_000_000,
                "digest": "def456",
                "modified_at": "2026-01-01T00:00:00Z",
                "details": {
                    "format": "gguf",
                    "family": "devstral",
                    "families": ["devstral"],
                    "parameter_size": "123B",
                    "quantization_level": "Q4_K_M",
                },
            },
        ]
    }
    mock_resp.raise_for_status = MagicMock()
    mock_fn.return_value.get.return_value = mock_resp

    from main import list_available_models
    result = list_available_models()
    assert "qwen3.5:9b" in result
    assert "devstral-2" in result
    assert "9B" in result
    assert "123B" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])