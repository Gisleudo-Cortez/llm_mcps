"""Tests for the llm_tools MCP server.

All tests mock the OpenAI client — LM Studio does not need to be running.
"""

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


# ---------------------------------------------------------------------------
# list_available_models
# ---------------------------------------------------------------------------

@patch("main._get_client")
def test_list_models_returns_ids(mock_fn):
    mock_fn.return_value.models.list.return_value.data = [
        _mock_model("nemotron-4b", 4096),
        _mock_model("qwen3-9b", 32768),
    ]
    from main import list_available_models
    result = list_available_models()
    assert "nemotron-4b" in result
    assert "qwen3-9b" in result


@patch("main._get_client")
def test_list_models_shows_context_length(mock_fn):
    mock_fn.return_value.models.list.return_value.data = [_mock_model("m1", 8192)]
    from main import list_available_models
    result = list_available_models()
    assert "8,192" in result or "8192" in result


@patch("main._get_client")
def test_list_models_empty(mock_fn):
    mock_fn.return_value.models.list.return_value.data = []
    from main import list_available_models
    result = list_available_models()
    assert "No models" in result


@patch("main._get_client")
def test_list_models_connection_error(mock_fn):
    mock_fn.side_effect = Exception("Connection refused to localhost:1234")
    from main import list_available_models
    result = list_available_models()
    assert "Error" in result
    assert "running" in result.lower() or "not reachable" in result.lower()


# ---------------------------------------------------------------------------
# _resolve_model
# ---------------------------------------------------------------------------

@patch("main._get_client")
def test_resolve_explicit_passthrough(mock_fn):
    from main import _resolve_model
    assert _resolve_model("my-specific-model") == "my-specific-model"
    mock_fn.assert_not_called()


@patch("main._get_client")
def test_resolve_auto_picks_first_loaded(mock_fn):
    mock_fn.return_value.models.list.return_value.data = [_mock_model("first-loaded")]
    from main import _resolve_model
    assert _resolve_model("auto") == "first-loaded"
    assert _resolve_model("") == "first-loaded"


@patch.dict(os.environ, {"LLM_TOOLS_DEFAULT_MODEL": "env-override-model"})
def test_resolve_respects_env_var():
    from main import _resolve_model
    # Env var takes priority over auto-discovery (no network call needed)
    result = _resolve_model("auto")
    assert result == "env-override-model"


@patch("main._get_client")
def test_resolve_falls_back_to_hardcoded(mock_fn):
    mock_fn.return_value.models.list.side_effect = Exception("no models")
    # Remove env var if set
    os.environ.pop("LLM_TOOLS_DEFAULT_MODEL", None)
    from main import _resolve_model
    result = _resolve_model("auto")
    assert isinstance(result, str)
    assert len(result) > 0


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
    assert "LM Studio" in result


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

@patch("main._get_client")
def test_summarize_returns_model_output(mock_fn):
    mock_fn.return_value.chat.completions.create.return_value = _mock_response(
        "Python is a high-level programming language."
    )
    from main import summarize
    result = summarize("Long article about Python...", model="m")
    assert "Python" in result


@patch("main._get_client")
def test_summarize_all_styles_use_distinct_prompts(mock_fn):
    mock_fn.return_value.chat.completions.create.return_value = _mock_response("ok")
    from main import summarize, _SUMMARIZE_PROMPTS

    seen_system_prompts = set()
    for style in ("concise", "detailed", "bullets", "eli5"):
        summarize("some text", style=style, model="m")  # type: ignore[arg-type]
        call = mock_fn.return_value.chat.completions.create.call_args
        sys_msg = next(
            m["content"] for m in call[1]["messages"] if m["role"] == "system"
        )
        seen_system_prompts.add(sys_msg)

    # Each style must produce a different system prompt
    assert len(seen_system_prompts) == 4


@patch("main._get_client")
def test_summarize_truncates_oversized_input(mock_fn):
    mock_fn.return_value.chat.completions.create.return_value = _mock_response("ok")
    from main import summarize, _INPUT_CHAR_LIMIT

    oversized = "word " * 5000  # ~25k chars
    summarize(oversized, model="m")

    call = mock_fn.return_value.chat.completions.create.call_args
    user_msg = next(m["content"] for m in call[1]["messages"] if m["role"] == "user")
    # Truncation marker should be present and total content bounded
    assert "truncated" in user_msg.lower()
    assert len(user_msg) <= _INPUT_CHAR_LIMIT + 200


@patch("main._get_client")
def test_summarize_uses_low_temperature(mock_fn):
    mock_fn.return_value.chat.completions.create.return_value = _mock_response("ok")
    from main import summarize
    summarize("text", model="m")
    call = mock_fn.return_value.chat.completions.create.call_args
    assert call[1]["temperature"] <= 0.35


def test_summarize_empty_input():
    from main import summarize
    assert "Error" in summarize("   ")


# ---------------------------------------------------------------------------
# ask_model
# ---------------------------------------------------------------------------

@patch("main._get_client")
def test_ask_model_includes_context_in_user_message(mock_fn):
    mock_fn.return_value.chat.completions.create.return_value = _mock_response("answer")
    from main import ask_model
    ask_model(prompt="What is the trend?", context="Q1:100 Q2:150 Q3:200", model="m")

    call = mock_fn.return_value.chat.completions.create.call_args
    user_msg = next(m["content"] for m in call[1]["messages"] if m["role"] == "user")
    assert "Context" in user_msg
    assert "Q1:100" in user_msg
    assert "trend" in user_msg.lower()


@patch("main._get_client")
def test_ask_model_no_context_sends_plain_prompt(mock_fn):
    mock_fn.return_value.chat.completions.create.return_value = _mock_response("answer")
    from main import ask_model
    ask_model(prompt="What is 2+2?", model="m")

    call = mock_fn.return_value.chat.completions.create.call_args
    user_msg = next(m["content"] for m in call[1]["messages"] if m["role"] == "user")
    # No context section injected when context is empty
    assert "Context" not in user_msg
    assert "2+2" in user_msg


@patch("main._get_client")
def test_ask_model_custom_system_prompt(mock_fn):
    mock_fn.return_value.chat.completions.create.return_value = _mock_response("ok")
    from main import ask_model
    ask_model(prompt="Ahoy", system_prompt="You are a pirate.", model="m")

    call = mock_fn.return_value.chat.completions.create.call_args
    sys_msg = next(m["content"] for m in call[1]["messages"] if m["role"] == "system")
    assert "pirate" in sys_msg


@patch("main._get_client")
def test_ask_model_respects_temperature(mock_fn):
    mock_fn.return_value.chat.completions.create.return_value = _mock_response("ok")
    from main import ask_model
    ask_model(prompt="Brainstorm ideas", temperature=0.9, model="m")

    call = mock_fn.return_value.chat.completions.create.call_args
    assert call[1]["temperature"] == 0.9


def test_ask_model_empty_prompt():
    from main import ask_model
    assert "Error" in ask_model(prompt="")
    assert "Error" in ask_model(prompt="   ")


# ---------------------------------------------------------------------------
# analyze_code
# ---------------------------------------------------------------------------

@patch("main._get_client")
def test_analyze_code_injects_language_into_system_prompt(mock_fn):
    mock_fn.return_value.chat.completions.create.return_value = _mock_response(
        "**Correctness**: OK.\n**Performance**: O(n²) loop."
    )
    from main import analyze_code
    analyze_code("for i in range(n): pass", language="rust", model="m")

    call = mock_fn.return_value.chat.completions.create.call_args
    sys_msg = next(m["content"] for m in call[1]["messages"] if m["role"] == "system")
    assert "rust" in sys_msg.lower()


@patch("main._get_client")
def test_analyze_code_wraps_code_in_fence(mock_fn):
    mock_fn.return_value.chat.completions.create.return_value = _mock_response("ok")
    from main import analyze_code
    analyze_code("x = 1 + 1", language="python", model="m")

    call = mock_fn.return_value.chat.completions.create.call_args
    user_msg = next(m["content"] for m in call[1]["messages"] if m["role"] == "user")
    assert "```python" in user_msg
    assert "x = 1 + 1" in user_msg


@patch("main._get_client")
def test_analyze_code_uses_very_low_temperature(mock_fn):
    mock_fn.return_value.chat.completions.create.return_value = _mock_response("ok")
    from main import analyze_code
    analyze_code("def f(): pass", model="m")
    call = mock_fn.return_value.chat.completions.create.call_args
    assert call[1]["temperature"] <= 0.25


def test_analyze_code_empty():
    from main import analyze_code
    assert "Error" in analyze_code("   ")


# ---------------------------------------------------------------------------
# interpret_data
# ---------------------------------------------------------------------------

@patch("main._get_client")
def test_interpret_data_structures_message_correctly(mock_fn):
    mock_fn.return_value.chat.completions.create.return_value = _mock_response(
        "Sales grew 50% from Q1 to Q3."
    )
    from main import interpret_data
    data = "| Q | Sales |\n|---|---|\n| Q1 | 100 |\n| Q3 | 150 |"
    result = interpret_data(data, "What is the trend?", model="m")

    assert isinstance(result, str)
    call = mock_fn.return_value.chat.completions.create.call_args
    user_msg = next(m["content"] for m in call[1]["messages"] if m["role"] == "user")
    assert "Q1" in user_msg
    assert "trend" in user_msg.lower()
    assert "Data" in user_msg
    assert "Question" in user_msg


@patch("main._get_client")
def test_interpret_data_caps_input(mock_fn):
    mock_fn.return_value.chat.completions.create.return_value = _mock_response("ok")
    from main import interpret_data
    large_data = "row, value\n" + "\n".join(f"{i}, {i*2}" for i in range(5000))
    interpret_data(large_data, "Any trend?", model="m")

    call = mock_fn.return_value.chat.completions.create.call_args
    user_msg = next(m["content"] for m in call[1]["messages"] if m["role"] == "user")
    assert len(user_msg) <= 11500  # 10k data + 1k question + overhead


def test_interpret_data_empty_data():
    from main import interpret_data
    assert "Error" in interpret_data("", "any question")


def test_interpret_data_empty_question():
    from main import interpret_data
    assert "Error" in interpret_data("some data", "")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
