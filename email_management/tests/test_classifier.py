"""Test async classifier without himalaya or server dependencies."""

import pytest
import pytest_asyncio

from async_classifier import (
    ClassificationConfig,
    ClassificationResult,
    ModelTier,
    _keyword_classify,
    classify_email_async,
    classify_batch,
)


@pytest.fixture
def keyword_only_config() -> ClassificationConfig:
    """Config with no valid local/cloud tiers to force keyword fallback."""
    return ClassificationConfig(
        tiers=[],  # No valid tiers
        max_concurrent=2,
    )


@pytest.fixture
def local_tier_config() -> ClassificationConfig:
    """Config with a nonexistent local model — will fall through to keyword."""
    return ClassificationConfig(
        tiers=[
            ModelTier(model="nonexistent-model:999", provider="local", timeout=1),
        ],
        max_concurrent=2,
    )


@pytest.mark.asyncio
async def test_keyword_classify_spam() -> None:
    """Test keyword classifier on a spam-like email."""
    result = _keyword_classify(
        sender="spam@casino.com",
        subject="You won a prize! Click here now!",
        body="Limited offer, click here to claim your winnings.",
    )
    assert isinstance(result, ClassificationResult)
    assert result.label == "spam"
    assert result.model_used == "keyword-fallback"
    assert result.needs_review is True


@pytest.mark.asyncio
async def test_keyword_classify_financial() -> None:
    """Test keyword classifier on a receipt email."""
    result = _keyword_classify(
        sender="noreply@amazon.com",
        subject="Your order confirmation #12345",
        body="Thank you for your purchase. Receipt attached.",
    )
    assert result.label == "financial"
    assert result.confidence == 0.4


@pytest.mark.asyncio
async def test_classify_email_async_keyword_fallback(keyword_only_config: ClassificationConfig) -> None:
    """Test that classify_email_async falls through to keyword when no tiers work."""
    result = await classify_email_async(
        sender="sender@example.com",
        recipient="me@example.com",
        subject="Test email",
        body="This is a test email with no strong keywords.",
        classification_config=keyword_only_config,
    )
    assert isinstance(result, ClassificationResult)
    assert result.model_used == "keyword-fallback"
    assert result.latency_ms == 0.0


@pytest.mark.asyncio
async def test_classify_batch_empty() -> None:
    """Test batch classification with empty list."""
    config = ClassificationConfig(tiers=[], max_concurrent=2)
    results = await classify_batch(emails=[], classification_config=config)
    assert results == []


@pytest.mark.asyncio
async def test_classify_batch_single() -> None:
    """Test batch classification with one email."""
    config = ClassificationConfig(tiers=[], max_concurrent=2)
    results = await classify_batch(
        emails=[{
            "sender": "test@example.com",
            "recipient": "me@example.com",
            "subject": "Invoice for your order",
            "body": "Please find the attached invoice.",
        }],
        classification_config=config,
    )
    assert len(results) == 1
    assert results[0].label == "financial"


@pytest.mark.asyncio
async def test_classify_batch_multiple() -> None:
    """Test batch classification with multiple emails."""
    config = ClassificationConfig(tiers=[], max_concurrent=2)
    emails = [
        {"sender": "a@example.com", "recipient": "me@example.com", "subject": "Invoice", "body": "Receipt"},
        {"sender": "b@example.com", "recipient": "me@example.com", "subject": "Meeting tomorrow", "body": "Agenda"},
        {"sender": "c@example.com", "recipient": "me@example.com", "subject": "Hello", "body": "Personal note"},
    ]
    results = await classify_batch(emails=emails, classification_config=config)
    assert len(results) == 3
    labels = [r.label for r in results]
    assert "financial" in labels
    assert "work" in labels
    assert labels.count("work") >= 2


@pytest.mark.asyncio
async def test_parse_json_valid() -> None:
    """Test JSON parsing from model response."""
    from async_classifier import _parse_json
    raw = '{"label": "work", "confidence": 0.85, "needs_review": false}'
    result = _parse_json(raw)
    assert result == {"label": "work", "confidence": 0.85, "needs_review": False}


@pytest.mark.asyncio
async def test_parse_json_with_noise() -> None:
    """Test JSON parsing when model outputs markdown or extra text."""
    from async_classifier import _parse_json
    raw = 'Here is the JSON:\n\n```json\n{"label": "spam", "confidence": 0.92, "needs_review": false}\n```'
    result = _parse_json(raw)
    assert result == {"label": "spam", "confidence": 0.92, "needs_review": False}
