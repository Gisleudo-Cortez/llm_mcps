import os
import re

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("awesome_lists_mcp")

AWESOME_README = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../../..", "Downloads/git/awesome/readme.md")
)


def _load_readme() -> str:
    if not os.path.isfile(AWESOME_README):
        return ""
    with open(AWESOME_README, encoding="utf-8") as f:
        return f.read()


def _parse_sections(text: str) -> dict[str, list[dict]]:
    """Parse the awesome readme into top-level sections with their link items."""
    sections: dict[str, list[dict]] = {}
    current_section: str | None = None

    section_re = re.compile(r"^## (.+)$")
    # Matches: - [Name](URL) - optional description
    item_re = re.compile(r"^\s*-\s+\[([^\]]+)\]\(([^)]+)\)(?:\s*[-–]\s*(.+))?")

    for line in text.splitlines():
        section_match = section_re.match(line)
        if section_match:
            current_section = section_match.group(1).strip()
            sections.setdefault(current_section, [])
            continue

        if current_section and current_section != "Contents":
            item_match = item_re.match(line)
            if item_match:
                sections[current_section].append(
                    {
                        "name": item_match.group(1).strip(),
                        "url": item_match.group(2).strip(),
                        "description": (item_match.group(3) or "").strip(),
                    }
                )

    return sections


@mcp.tool(
    name="awesome_search_awesome_lists",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
}
)
def search_awesome_lists(query: str) -> str:
    """
    Search the sindresorhus/awesome meta-list for curated resource collections.

    **TRIGGER CONDITION:** Use when looking for authoritative, community-curated resource
    collections on a topic — programming languages, frameworks, tools, or any subject area.
    The awesome ecosystem contains hundreds of high-quality lists maintained by domain experts.

    **SEQUENCE GUIDANCE:** Start with a broad keyword (e.g., "python", "security",
    "machine learning"). Review matching list names and descriptions. Use the returned
    GitHub URLs to fetch specific lists with `fetch_url_content` (page_scrape server).

    **CONSTRAINT WARNING:** Search is case-insensitive substring matching over list names
    and descriptions. Very generic queries (e.g., "web") return many results — use more
    specific terms for focused results.

    **OUTPUT EXPECTATION:** Returns matching awesome list entries grouped by category,
    each with name, URL, and description. Ideal for resource discovery and research.

    *Examples:*
    - search_awesome_lists("rust") → Rust-related awesome lists
    - search_awesome_lists("self-hosted") → self-hosting resources
    - search_awesome_lists("interview") → interview prep lists
    """
    text = _load_readme()
    if not text:
        return "Error: Awesome readme not found. Ensure the repo is cloned at ~/Downloads/git/awesome/."

    query_lower = query.strip().lower()
    if not query_lower:
        return "Error: Search query cannot be empty."

    sections = _parse_sections(text)
    results: list[str] = []

    for section, items in sections.items():
        matches = [
            item
            for item in items
            if query_lower in item["name"].lower()
            or query_lower in item["description"].lower()
        ]
        if matches:
            results.append(f"### {section}")
            for item in matches:
                desc = f" — {item['description']}" if item["description"] else ""
                results.append(f"- **{item['name']}**: {item['url']}{desc}")

    if not results:
        return (
            f"No awesome lists found matching '{query}'. "
            "Try broader keywords or use `list_awesome_categories` to browse all topics."
        )

    total = sum(
        1
        for section_lines in results
        if section_lines.startswith("- ")
    )
    header = f"### Search results for: '{query}' ({total} lists found)\n"
    return header + "\n".join(results)


@mcp.tool(
    name="awesome_list_awesome_categories",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
}
)
def list_awesome_categories() -> str:
    """
    List all top-level categories in the sindresorhus/awesome meta-list with item counts.

    **TRIGGER CONDITION:** Use when you want to explore available topics before searching,
    or when discovering what subject areas are covered in the awesome ecosystem.

    **SEQUENCE GUIDANCE:** Call first to see all available categories and their list counts.
    Then use `get_awesome_category` to drill into a specific topic, or `search_awesome_lists`
    for cross-category keyword search.

    **OUTPUT EXPECTATION:** Returns all category names with item counts. Ideal for
    exploration and planning targeted searches.
    """
    text = _load_readme()
    if not text:
        return "Error: Awesome readme not found. Ensure the repo is cloned at ~/Downloads/git/awesome/."

    sections = _parse_sections(text)
    lines = ["### Awesome List Categories\n"]

    for section, items in sections.items():
        if section in ("Contents", "Related"):
            continue
        lines.append(f"- **{section}** — {len(items)} lists")

    return "\n".join(lines)


@mcp.tool(
    name="awesome_get_awesome_category",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
}
)
def get_awesome_category(category: str) -> str:
    """
    Get all curated lists within a specific awesome category.

    **TRIGGER CONDITION:** Use after `list_awesome_categories` to drill into a specific
    topic area and see every curated list it contains with descriptions and links.

    **SEQUENCE GUIDANCE:** Pass the exact category name from `list_awesome_categories`
    (case-insensitive match applied). After finding a relevant list, fetch its contents
    using `fetch_url_content` from the page_scrape server.

    **CONSTRAINT WARNING:** Category name must roughly match one from `list_awesome_categories`.
    Uses case-insensitive exact matching — typos will return an error with available options.

    **OUTPUT EXPECTATION:** Returns all lists in the category with names, URLs, and
    descriptions. Ideal for comprehensive topic discovery within a domain.

    *Examples:*
    - get_awesome_category("Security") → all security-related awesome lists
    - get_awesome_category("Programming Languages") → language-specific lists
    """
    text = _load_readme()
    if not text:
        return "Error: Awesome readme not found. Ensure the repo is cloned at ~/Downloads/git/awesome/."

    if not category.strip():
        return "Error: Category name cannot be empty."

    sections = _parse_sections(text)

    matched = next(
        (s for s in sections if s.lower() == category.strip().lower()),
        None,
    )

    if not matched:
        available = ", ".join(
            f'"{s}"' for s in sections if s not in ("Contents", "Related")
        )
        return f"Category '{category}' not found.\n\nAvailable categories: {available}"

    items = sections[matched]
    if not items:
        return f"Category '{matched}' exists but contains no items."

    lines = [f"### {matched} ({len(items)} awesome lists)\n"]
    for item in items:
        desc = f" — {item['description']}" if item["description"] else ""
        lines.append(f"- **{item['name']}**: {item['url']}{desc}")

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="stdio")
