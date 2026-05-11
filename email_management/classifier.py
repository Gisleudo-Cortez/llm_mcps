"""Email classification via local ollama models with tiered escalation.

Tier order: qwen3:4b → gemma4:latest → deepseek-v4-flash:cloud → keyword fallback
Cloud fallback only used if allow_cloud_fallback=True in config.
"""

import json
import re
import subprocess
from dataclasses import dataclass
from typing import List, Optional

try:
    from .config import ClassificationConfig, ModelTier
except ImportError:
    from config import ClassificationConfig, ModelTier


CLASSIFICATION_PROMPT = """You are an email classifier. Analyze this email and output ONLY a JSON object (no other text) with these fields:
- label: one of [receipt, financial, work, personal, newsletter, spam, urgent, unsorted]
- confidence: a number from 0.0 to 1.0
- needs_review: true if confidence < 0.7, otherwise false

EMAIL:
From: {sender}
To: {recipient}
Subject: {subject}
Body preview: {body_preview}

JSON:"""


# Keyword patterns for last-resort classification (all models failed)
KEYWORD_RULES = [
    (["invoice", "receipt", "order confirmation", "payment received", "purchase"], "financial"),
    (["meeting", "schedule", "calendar", "agenda", "standup", "sprint"], "work"),
    (["unsubscribe", "newsletter", "weekly digest", "monthly roundup"], "newsletter"),
    (["urgent", "asap", "deadline", "critical", "immediate action"], "urgent"),
    (["viagra", "casino", "won", "winner", "prize", "click here", "limited offer"], "spam"),
    (["dear valued", "your account", "verify your", "security alert", "suspicious"], "spam"),
]


@dataclass
class ClassificationResult:
    label: str
    confidence: float
    needs_review: bool
    model_used: str
    error: Optional[str] = None


def _run_ollama(model: str, prompt: str, timeout: int = 30) -> str:
    """Run ollama with a prompt, return raw output."""
    result = subprocess.run(
        ["ollama", "run", model],
        input=prompt,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"ollama {model} failed (exit {result.returncode}): {result.stderr.strip()}"
        )

    return result.stdout.strip()


def _check_model_available(model: str) -> bool:
    """Check if an ollama model is available."""
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return model in result.stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def _parse_json(response: str) -> dict:
    """Extract JSON object from potentially messy ollama output."""
    match = re.search(r'\{[^{}]*\}', response, re.DOTALL)
    if match:
        return json.loads(match.group())
    return json.loads(response)


def _keyword_classify(sender: str, subject: str, body: str) -> ClassificationResult:
    """Fallback classification using keyword patterns."""
    text = f"{subject} {body}".lower()
    sender_domain = sender.split("@")[-1].lower() if "@" in sender else ""

    for keywords, label in KEYWORD_RULES:
        for kw in keywords:
            if kw in text:
                return ClassificationResult(
                    label=label,
                    confidence=0.4,
                    needs_review=True,
                    model_used="keyword-fallback",
                )

    # Check if sender domain suggests work
    if sender_domain and not any(d in sender_domain for d in ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com"]):
        return ClassificationResult(
            label="work",
            confidence=0.3,
            needs_review=True,
            model_used="keyword-fallback",
        )

    return ClassificationResult(
        label="unsorted",
        confidence=0.2,
        needs_review=True,
        model_used="keyword-fallback",
    )


def classify_email(
    sender: str,
    recipient: str,
    subject: str,
    body: str,
    classification_config: ClassificationConfig,
) -> ClassificationResult:
    """Classify an email using tiered models.

    Tries tiers in order. Skips cloud tiers if allow_cloud_fallback=False.
    Falls back to keyword classification if all tiers fail.
    """
    body_preview = body[:classification_config.max_preview_chars]

    prompt = CLASSIFICATION_PROMPT.format(
        sender=sender,
        recipient=recipient,
        subject=subject,
        body_preview=body_preview,
    )

    last_error = None

    for tier in classification_config.tiers:
        # Skip cloud tiers if not allowed
        if tier.provider == "cloud" and not classification_config.allow_cloud_fallback:
            continue

        # Check model availability first
        if not _check_model_available(tier.model):
            continue

        try:
            response = _run_ollama(tier.model, prompt, timeout=tier.timeout)
            data = _parse_json(response)

            label = data.get("label", "unsorted")
            confidence = float(data.get("confidence", 0.5))
            needs_review = data.get("needs_review", confidence < classification_config.confidence_threshold)

            # If confidence is high enough and model doesn't flag for review, accept
            if confidence >= classification_config.confidence_threshold and not needs_review:
                return ClassificationResult(
                    label=label,
                    confidence=confidence,
                    needs_review=False,
                    model_used=tier.model,
                )

            # Low confidence — continue to next tier for a second opinion
            if tier.provider == "cloud":
                # Cloud is the last tier that's allowed; accept its answer even if low confidence
                return ClassificationResult(
                    label=label,
                    confidence=confidence,
                    needs_review=True,
                    model_used=tier.model,
                )

        except (json.JSONDecodeError, KeyError, ValueError, RuntimeError, subprocess.TimeoutExpired) as e:
            last_error = str(e)
            continue

    # All tiers exhausted — keyword fallback
    return _keyword_classify(sender, subject, body)
