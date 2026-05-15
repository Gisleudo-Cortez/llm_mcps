"""Async email classifier using Ollama HTTP API + OpenRouter fallback.

Replaces blocking subprocess calls with asyncio HTTP requests.
Key improvements:
- Model retention via keep_alive (30m)
- Concurrent classification via asyncio.gather
- Zero subprocess fork overhead
- Seamless cloud fallback with configurable API key
"""

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass
from typing import List, Optional

import httpx


try:
    from .config import ClassificationConfig, ModelTier
except ImportError:
    from config import ClassificationConfig, ModelTier


OLLAMA_URL = os.environ.get("OLLAMA_HOST", "http://localhost:11434") + "/api/generate"
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

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
    latency_ms: float = 0.0


# ── Keyword fallback ────────────────────────────────────────────────────

def _keyword_classify(sender: str, subject: str, body: str) -> ClassificationResult:
    text = f"{subject} {body}".lower()
    sender_domain = sender.split("@")[-1].lower() if "@" in sender else ""

    for keywords, label in KEYWORD_RULES:
        for kw in keywords:
            if kw in text:
                return ClassificationResult(
                    label=label, confidence=0.4, needs_review=True,
                    model_used="keyword-fallback", latency_ms=0.0,
                )

    if sender_domain and not any(d in sender_domain for d in ["gmail.com", "yahoo.com", "outlook.com", "hotmail.com"]):
        return ClassificationResult(
            label="work", confidence=0.3, needs_review=True,
            model_used="keyword-fallback", latency_ms=0.0,
        )

    return ClassificationResult(
        label="unsorted", confidence=0.2, needs_review=True,
        model_used="keyword-fallback", latency_ms=0.0,
    )


def _parse_json(response: str) -> dict:
    match = re.search(r'\{[^{}]*\}', response, re.DOTALL)
    if match:
        return json.loads(match.group())
    return json.loads(response)


# ── Async HTTP clients ──────────────────────────────────────────────────

async def _classify_ollama(
    client: httpx.AsyncClient,
    model: str,
    prompt: str,
    timeout: float = 30.0,
) -> tuple[str, float]:
    """Classify via Ollama HTTP API. Returns (raw_response, latency_ms)."""
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3},
        "keep_alive": "30m",
    }

    start = time.perf_counter()
    resp = await client.post(
        OLLAMA_URL,
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    latency_ms = (time.perf_counter() - start) * 1000

    return data.get("response", ""), latency_ms


async def _classify_openrouter(
    client: httpx.AsyncClient,
    model: str,
    prompt: str,
    api_key: str,
    timeout: float = 30.0,
) -> tuple[str, float]:
    if not api_key:
        raise RuntimeError("OpenRouter API key not configured")

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are an email classifier. Output ONLY JSON."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.3,
    }

    start = time.perf_counter()
    resp = await client.post(
        OPENROUTER_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://localhost",
            "X-Title": "Email Classifier",
        },
        json=payload,
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    latency_ms = (time.perf_counter() - start) * 1000

    content = data["choices"][0]["message"]["content"]
    return content, latency_ms


# ── Single email classification ─────────────────────────────────────────

async def classify_email_async(
    sender: str,
    recipient: str,
    subject: str,
    body: str,
    classification_config: ClassificationConfig,
    client: Optional[httpx.AsyncClient] = None,
) -> ClassificationResult:
    """Classify one email using tiered async models.

    Args:
        client: Optional shared httpx.AsyncClient. If None, a new client is created.
    """
    body_preview = body[:classification_config.max_preview_chars]
    prompt = CLASSIFICATION_PROMPT.format(
        sender=sender, recipient=recipient, subject=subject, body_preview=body_preview,
    )

    last_error = None
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=30.0)

    try:
        for tier in classification_config.tiers:
            if tier.provider == "cloud" and not classification_config.allow_cloud_fallback:
                continue

            try:
                if tier.provider == "cloud":
                    response, latency = await _classify_openrouter(
                        client, tier.model, prompt,
                        api_key=classification_config.api_key,
                        timeout=tier.timeout,
                    )
                else:
                    response, latency = await _classify_ollama(
                        client, tier.model, prompt, timeout=tier.timeout,
                    )

                data = _parse_json(response)
                label = data.get("label", "unsorted")
                confidence = float(data.get("confidence", 0.5))
                needs_review = data.get("needs_review", confidence < classification_config.confidence_threshold)

                if confidence >= classification_config.confidence_threshold and not needs_review:
                    return ClassificationResult(
                        label=label, confidence=confidence, needs_review=False,
                        model_used=tier.model, latency_ms=latency,
                    )

                if tier.provider == "cloud":
                    return ClassificationResult(
                        label=label, confidence=confidence, needs_review=True,
                        model_used=tier.model, latency_ms=latency,
                    )

            except Exception as e:
                last_error = str(e)
                continue

    finally:
        if own_client:
            await client.aclose()

    return _keyword_classify(sender, subject, body)


# ── Batch classification with concurrency ─────────────────────────────────

async def classify_batch(
    emails: list[dict],
    classification_config: ClassificationConfig,
) -> list[ClassificationResult]:
    """Classify multiple emails concurrently with semaphore-limited concurrency.

    Args:
        emails: List of dicts with keys sender, recipient, subject, body
    """
    max_concurrent = classification_config.max_concurrent
    sem = asyncio.Semaphore(max_concurrent)
    results: list[ClassificationResult] = [None] * len(emails)

    async with httpx.AsyncClient(
        limits=httpx.Limits(max_connections=20),
        timeout=30.0,
    ) as client:
        async def _task(idx: int, email: dict):
            async with sem:
                results[idx] = await classify_email_async(
                    sender=email["sender"],
                    recipient=email["recipient"],
                    subject=email["subject"],
                    body=email["body"],
                    classification_config=classification_config,
                    client=client,
                )

        await asyncio.gather(*[
            asyncio.create_task(_task(i, e)) for i, e in enumerate(emails)
        ])

    return results
