import json
import os
from datetime import datetime

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Memory & Notes")

MEMORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memories.json")


def _load() -> dict:
    if os.path.exists(MEMORY_FILE):
        with open(MEMORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save(data: dict) -> None:
    with open(MEMORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


@mcp.tool()
def remember(key: str, value: str, category: str = "general") -> str:
    """
    Store a named memory that persists across sessions.

    **TRIGGER CONDITION:** Use whenever you learn something the user wants recalled later —
    preferences, project context, credentials hints, decisions, recurring patterns. Also use
    when the user says "remember that..." or "save this for later".

    **SEQUENCE GUIDANCE:** Use short, descriptive keys (e.g. "db_path_project_x",
    "user_prefers_dark_theme"). Use `category` to group related memories (e.g. "project",
    "preference", "credential_hint"). Overwriting an existing key replaces the old value.

    **OUTPUT EXPECTATION:** Confirms the key was saved with its category and timestamp.
    """
    key = key.strip()
    if not key:
        return "Error: key cannot be empty."

    data = _load()
    data[key] = {
        "value": value,
        "category": category,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    _save(data)
    return f"Remembered `{key}` [{category}]."


@mcp.tool()
def recall(query: str) -> str:
    """
    Search memories by key substring or value content and return all matches.

    **TRIGGER CONDITION:** Use when you need to retrieve something the user asked to save,
    or when context suggests a previously stored fact is relevant (project paths, preferences,
    past decisions). Also use when the user says "do you remember..." or "what did we set for X".

    **SEQUENCE GUIDANCE:** Use a short keyword — the search checks both key names and
    value text. Use `list_memories` instead if you want to browse everything by category.

    **OUTPUT EXPECTATION:** Returns all matching memories with their key, category,
    value, and last-updated timestamp. Returns a clear message if nothing matches.
    """
    query = query.strip().lower()
    data = _load()

    if not data:
        return "No memories stored yet. Use `remember` to save something."

    matches = {
        k: v
        for k, v in data.items()
        if query in k.lower() or query in str(v.get("value", "")).lower()
    }

    if not matches:
        return f"No memories match '{query}'."

    lines = [f"### Memories matching '{query}'\n"]
    for key, entry in sorted(matches.items()):
        lines.append(
            f"**{key}** [{entry.get('category', '?')}] *(updated {entry.get('updated_at', '?')})*\n"
            f"> {entry.get('value', '')}\n"
        )
    return "\n".join(lines)


@mcp.tool()
def list_memories(category: str = "") -> str:
    """
    List all stored memories, optionally filtered to a single category.

    **TRIGGER CONDITION:** Use at the start of a session to surface relevant context,
    or when the user asks to see what's been saved.

    **SEQUENCE GUIDANCE:** Call without arguments for a full overview. Pass `category`
    (e.g. "project", "preference") to narrow the list.

    **OUTPUT EXPECTATION:** Returns all memories grouped by category with keys, values,
    and timestamps. Returns a helpful message if the store is empty.
    """
    data = _load()

    if not data:
        return "No memories stored yet. Use `remember` to save something."

    filtered = {
        k: v
        for k, v in data.items()
        if not category or v.get("category", "") == category
    }

    if not filtered:
        return f"No memories found in category '{category}'."

    # Group by category
    by_category: dict[str, list[tuple[str, dict]]] = {}
    for key, entry in sorted(filtered.items()):
        cat = entry.get("category", "general")
        by_category.setdefault(cat, []).append((key, entry))

    lines = ["### Stored Memories\n"]
    for cat, entries in sorted(by_category.items()):
        lines.append(f"**[{cat}]**")
        for key, entry in entries:
            lines.append(
                f"- `{key}` *(updated {entry.get('updated_at', '?')})*: {entry.get('value', '')}"
            )
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
def forget(key: str) -> str:
    """
    Delete a stored memory by its exact key.

    **TRIGGER CONDITION:** Use when the user says "forget X", when a saved value is
    outdated, or before `remember`-ing a replacement to keep the store clean.

    **OUTPUT EXPECTATION:** Confirms deletion, or reports that the key was not found.
    """
    key = key.strip()
    data = _load()

    if key not in data:
        return f"No memory found with key '{key}'. Use `list_memories` to see available keys."

    del data[key]
    _save(data)
    return f"Forgot `{key}`."


if __name__ == "__main__":
    mcp.run(transport="stdio")
