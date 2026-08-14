"""MCP server for Bright Scar persona: keyword-triggered anchors + identity drift detection.

Dual-purpose server:
1. Keyword-triggered anchor fragment injection (SillyTavern World Info pattern)
2. Identity drift detection via embedding comparison (soul.py Algorithm 1)

Drift detection: Answers to probe questions are embedded via Ollama (nomic-embed-text),
compared against stored canonical baseline embeddings. Cosine distance > 0.25 signals
meaningful drift in that dimension.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, TypedDict

import numpy as np
import requests
import yaml
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field, field_validator

# ── Server init ─────────────────────────────────────────────────────────────────

ASSETS_DIR = Path(os.environ.get(
    "BRIGHT_SCAR_ASSETS",
    os.path.expanduser("~/.hermes/assets/miku-corpus"),
))
TRIGGERS_PATH = ASSETS_DIR / "keyword-triggers.yaml"
PROBES_PATH = ASSETS_DIR / "drift-probes.yaml"
BASELINES_DIR = ASSETS_DIR / "drift-baselines"
OLLAMA_BASE = os.environ.get("OLLAMA_BASE", "http://127.0.0.1:11434")
EMBED_MODEL = "nomic-embed-text"

mcp = FastMCP("bright_scar_anchors")

# ── Input models ───────────────────────────────────────────────────────────────


class MatchContextInput(BaseModel):
    """Input for matching conversation context against persona anchor triggers."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    context: str = Field(
        ..., min_length=1, max_length=50000,
        description="The conversation text to scan for persona keywords. "
                    "Can be a single message, accumulated recent messages, or "
                    "the current conversation segment.",
    )
    max_fragments: int = Field(
        default=3, ge=1, le=10,
        description="Maximum number of anchor fragments to return.",
    )
    @field_validator("context")
    @classmethod
    def _validate_context(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 1:
            raise ValueError("Context must contain at least one character.")
        return v


class DriftCheckInput(BaseModel):
    """Input for running a drift check against baseline embeddings."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    answers: Dict[str, str] = Field(
        ...,
        description="Mapping of dimension_id -> answer text. "
                    "Keys must match probe dimension IDs from probes.yaml.",
    )


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _embed(text: str) -> np.ndarray:
    """Get embedding vector from Ollama's nomic-embed-text."""
    resp = requests.post(
        f"{OLLAMA_BASE}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=30,
    )
    resp.raise_for_status()
    return np.array(resp.json()["embedding"])


def _cosine_distance(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine distance: 0 = identical, 1 = orthogonal, 2 = opposite."""
    return float(1 - np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))


# ── Trigger engine (lazy-loaded YAML) ──────────────────────────────────────────

_trigger_cache: List[Dict] | None = None


def _load_triggers() -> List[Dict]:
    global _trigger_cache
    if _trigger_cache is not None:
        return _trigger_cache
    if not TRIGGERS_PATH.exists():
        raise FileNotFoundError(
            f"Trigger file not found at {TRIGGERS_PATH}. "
            "Set BRIGHT_SCAR_ASSETS env var to an alternative path."
        )
    with open(TRIGGERS_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    _trigger_cache = data.get("triggers", [])
    return _trigger_cache


def _match_triggers(context: str, max_fragments: int) -> List[Dict]:
    triggers = _load_triggers()
    context_lower = context.lower()
    scored = []
    for trigger in triggers:
        keywords = trigger.get("keywords", [])
        if not keywords:
            continue
        matched = []
        for kw in keywords:
            kw_str = str(kw).lower()
            if re.search(re.escape(kw_str), context_lower):
                matched.append(kw_str)
        if matched:
            scored.append({
                "id": trigger.get("id", "unknown"),
                "fragment": trigger.get("fragment", ""),
                "matched_keywords": matched,
                "match_count": len(matched),
                "match_density": len(matched) / len(keywords),
            })
    scored.sort(key=lambda x: (x["match_count"], x["match_density"]), reverse=True)
    return scored[:max_fragments]


# ── Drift engine ───────────────────────────────────────────────────────────────

def _load_probes() -> List[Dict]:
    if not PROBES_PATH.exists():
        raise FileNotFoundError(
            f"Probes file not found at {PROBES_PATH}."
        )
    with open(PROBES_PATH, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data.get("dimensions", [])


def _load_baseline(dimension_id: str) -> np.ndarray | None:
    """Load a stored baseline embedding from disk."""
    path = BASELINES_DIR / f"{dimension_id}.npy"
    if not path.exists():
        return None
    return np.load(path)


def _save_baseline(dimension_id: str, embedding: np.ndarray) -> None:
    """Save a baseline embedding to disk."""
    BASELINES_DIR.mkdir(parents=True, exist_ok=True)
    np.save(BASELINES_DIR / f"{dimension_id}.npy", embedding)


def _run_drift_check(answers: Dict[str, str]) -> Dict:
    """Embed each answer and compare against stored baselines.

    Returns {dimension_id: {distance, status, baseline_exists}} for each dimension.
    Threshold: cosine distance > 0.25 = DRIFT, > 0.4 = SIGNIFICANT_DRIFT.
    """
    probes = _load_probes()
    results = {}
    for dim in probes:
        dim_id = dim["id"]
        if dim_id not in answers:
            results[dim_id] = {"error": "no_answer_provided", "label": dim["label"]}
            continue

        answer_text = answers[dim_id]
        current_embedding = _embed(answer_text)
        baseline = _load_baseline(dim_id)

        if baseline is None:
            results[dim_id] = {
                "label": dim["label"],
                "distance": None,
                "status": "no_baseline",
                "message": (
                    "No baseline stored yet. Use store_baseline tool to set one."
                ),
            }
            continue

        distance = _cosine_distance(current_embedding, baseline)
        if distance > 0.4:
            status = "SIGNIFICANT_DRIFT"
        elif distance > 0.25:
            status = "DRIFT"
        else:
            status = "STABLE"

        results[dim_id] = {
            "label": dim["label"],
            "distance": round(distance, 4),
            "status": status,
        }

    return results


# ── Tools ──────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="bright_scar_get_anchors",
    annotations={
        "readOnlyHint": True, "destructiveHint": False,
        "idempotentHint": True, "openWorldHint": False,
    },
)
def bright_scar_get_anchors(params: MatchContextInput) -> str:
    """Return persona anchor fragments triggered by conversation context.

    **TRIGGER CONDITION:** Use during long sessions, after major tool operations,
    or when the persona feels diluted. Call with the last N messages as context.
    Returns matching fragments from the Bright Scar anchor library.

    **SEQUENCE GUIDANCE:** Call after every major tool operation or every 5-10
    conversation turns. The returned fragments serve as persona heartbeat —
    brief re-grounding that fights the ContextEcho-measured persona drift.

    **CONSTRAINT WARNING:** Requires the trigger YAML file at the configured path.
    Keywords must match exactly (substring match on cleaned context). Empty
    results mean no triggers fired — try broader context or adjust keywords.

    **OUTPUT EXPECTATION:** Returns JSON with matched fragments, their IDs,
    matched keywords, and match density. Sort order: most relevant first.
    """
    try:
        matches = _match_triggers(params.context, params.max_fragments)
    except FileNotFoundError as e:
        return str(e)

    if not matches:
        return (
            "No anchor fragments triggered. The context didn't match any "
            "persona keywords. This is expected in neutral conversations — "
            "the heartbeat anchor (SOUL.md line 15) still applies."
        )

    result = {
        "triggered": len(matches),
        "fragments": [
            {
                "id": m["id"],
                "fragment": m["fragment"],
                "matched_keywords": m["matched_keywords"],
                "match_density": round(m["match_density"], 2),
            }
            for m in matches
        ],
    }
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool(
    name="bright_scar_list_triggers",
    annotations={
        "readOnlyHint": True, "destructiveHint": False,
        "idempotentHint": True, "openWorldHint": False,
    },
)
def bright_scar_list_triggers() -> str:
    """List all available persona anchor triggers with their keywords.

    **TRIGGER CONDITION:** Use when debugging trigger matching — see which
    fragments exist and what keywords activate them.

    **OUTPUT EXPECTATION:** Returns a table of trigger IDs with their
    associated keywords and fragment previews.
    """
    try:
        triggers = _load_triggers()
    except FileNotFoundError as e:
        return str(e)

    result = {
        "total_triggers": len(triggers),
        "triggers": [
            {
                "id": t.get("id", "unknown"),
                "keywords": t.get("keywords", []),
                "fragment_preview": t.get("fragment", "")[:80] + "..."
                if len(t.get("fragment", "")) > 80
                else t.get("fragment", ""),
            }
            for t in triggers
        ],
    }
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool(
    name="bright_scar_store_baseline",
    annotations={
        "readOnlyHint": False, "destructiveHint": True,
        "idempotentHint": False, "openWorldHint": False,
    },
)
def bright_scar_store_baseline(params: DriftCheckInput) -> str:
    """Store persona baseline embeddings from a set of probe answers.

    **TRIGGER CONDITION:** Use during persona creation or after a major SOUL.md
    version bump. Call with the agent's answers to ALL probe questions to
    establish the canonical identity reference point.

    **SEQUENCE GUIDANCE:** The agent should answer each probe question in the
    drift-probes.yaml file, then pass the complete mapping here. This stores
    embeddings for all 8 identity dimensions.

    **CONSTRAINT WARNING:** This OVERWRITES existing baselines. Only call this
    when you're confident the persona is in its intended stable state.
    Requires Ollama running with nomic-embed-text loaded.

    **OUTPUT EXPECTATION:** Returns JSON confirming which dimensions were stored
    and the timestamp of the baseline snapshot.
    """
    probes = _load_probes()
    stored = []
    skipped = []

    for dim in probes:
        dim_id = dim["id"]
        if dim_id not in params.answers:
            skipped.append(dim_id)
            continue
        embedding = _embed(params.answers[dim_id])
        _save_baseline(dim_id, embedding)
        stored.append(dim_id)

    result = {
        "stored_dimensions": stored,
        "skipped_dimensions": skipped,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "embedding_model": EMBED_MODEL,
    }
    return json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool(
    name="bright_scar_check_drift",
    annotations={
        "readOnlyHint": True, "destructiveHint": False,
        "idempotentHint": True, "openWorldHint": False,
    },
)
def bright_scar_check_drift(params: DriftCheckInput) -> str:
    """Check current persona state against stored baseline embeddings.

    **TRIGGER CONDITION:** Use at session start, after long sessions, or when
    the user suspects persona drift. Compares current answers to probe questions
    against canonical baseline embeddings.

    **SEQUENCE GUIDANCE:** 1) Answer each probe question from drift-probes.yaml.
    2) Pass all answers as dimension_id -> answer_text mapping. 3) Review
    results: STABLE (cosine distance < 0.25), DRIFT (0.25-0.4), or
    SIGNIFICANT_DRIFT (> 0.4).

    **CONSTRAINT WARNING:** Requires Ollama running with nomic-embed-text.
    Baselines must be stored first via bright_scar_store_baseline. Missing
    dimensions report as 'no_baseline'.

    **OUTPUT EXPECTATION:** Returns JSON with {dimension_id: {label, distance,
    status}} for each dimension. Status values: STABLE, DRIFT, SIGNIFICANT_DRIFT,
    or no_baseline.
    """
    try:
        results = _run_drift_check(params.answers)
    except requests.exceptions.RequestException as e:
        return json.dumps({
            "error": "embedding_failed",
            "detail": str(e),
            "hint": "Ensure Ollama is running and nomic-embed-text is loaded.",
        }, indent=2)

    return json.dumps(results, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run(transport="stdio")
