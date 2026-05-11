import os
import sqlite3
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("data_query_mcp")

_MAX_ROWS = 500
_MAX_CHARS = 80000


def _rows_to_markdown(columns: list[str], rows: list[tuple[Any, ...]]) -> str:
    header = "| " + " | ".join(str(c) for c in columns) + " |"
    sep = "|" + "|".join("---" for _ in columns) + "|"
    body = "\n".join(
        "| " + " | ".join(str(v).replace("|", "\\|") for v in row) + " |"
        for row in rows
    )
    return "\n".join([header, sep, body]) if rows else header + "\n" + sep


# ── SQLite tools ─────────────────────────────────────────────────────────────

@mcp.tool(
    name="data_query_query_sqlite",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
}
)
def query_sqlite(db_path: str, sql: str) -> str:
    """
    Execute a SQL query against a SQLite database file and return results as Markdown.

    **TRIGGER CONDITION:** Use when you need to query a local `.db` or `.sqlite` file.
    Call `list_sqlite_tables` first if you don't know the schema.

    **SEQUENCE GUIDANCE:** Recommended flow: `list_sqlite_tables` → `describe_table` →
    `query_sqlite`. For large result sets, add a `LIMIT` clause to avoid truncation.

    **CONSTRAINT WARNING:** Read-only queries only (SELECT). INSERT/UPDATE/DELETE/DROP
    are rejected. Results are capped at 500 rows; output is truncated at 80k chars.
    File path must point to a valid SQLite database.

    **OUTPUT EXPECTATION:** Returns a Markdown table of results, row count, and a
    truncation warning when applicable.
    """
    if not os.path.isfile(db_path):
        return f"Error: File '{db_path}' does not exist."

    # Block writes — only allow read statements
    normalized = sql.strip().upper()
    if not normalized.startswith("SELECT") and not normalized.startswith("WITH") and not normalized.startswith("EXPLAIN"):
        return "Error: Only SELECT/WITH/EXPLAIN queries are permitted."

    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        cur = con.execute(sql)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(_MAX_ROWS + 1)
        truncated = len(rows) > _MAX_ROWS
        rows = rows[:_MAX_ROWS]
        con.close()

        if not columns:
            return "Query executed successfully (no rows returned)."

        table = _rows_to_markdown(columns, [tuple(r) for r in rows])
        result = f"### Results ({len(rows)} row{'s' if len(rows) != 1 else ''})\n\n{table}"
        if truncated:
            result += f"\n\n*Output capped at {_MAX_ROWS} rows. Add a LIMIT clause to your query.*"

        if len(result) > _MAX_CHARS:
            result = result[:_MAX_CHARS] + "\n\n... [truncated at 80k chars]"

        return result

    except sqlite3.OperationalError as e:
        return f"SQL Error: {str(e)}"
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(
    name="data_query_list_sqlite_tables",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
}
)
def list_sqlite_tables(db_path: str) -> str:
    """
    List all tables in a SQLite database with their row counts.

    **TRIGGER CONDITION:** Use at the start of any SQLite workflow to discover available
    tables before writing queries. Call before `describe_table` or `query_sqlite`.

    **OUTPUT EXPECTATION:** Returns table names with row counts, sorted alphabetically.
    """
    if not os.path.isfile(db_path):
        return f"Error: File '{db_path}' does not exist."

    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        cur = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row[0] for row in cur.fetchall()]

        if not tables:
            return "No tables found in this database."

        lines = [f"### Tables in `{os.path.basename(db_path)}`\n"]
        for table in tables:
            # Validate table name before using in query (defense-in-depth)
            if not all(c.isalnum() or c in ("_", "-") for c in table):
                lines.append(f"- **{table}** — (skipped: invalid name)")
                continue
            count = con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            lines.append(f"- **{table}** — {count:,} rows")

        con.close()
        return "\n".join(lines)

    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool(
    name="data_query_describe_table",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
}
)
def describe_table(db_path: str, table_name: str) -> str:
    """
    Show the schema (column names, types, constraints) for a SQLite table.

    **TRIGGER CONDITION:** Use after `list_sqlite_tables` to understand a table's structure
    before writing queries against it.

    **OUTPUT EXPECTATION:** Returns column names, types, nullable flags, and default values
    in a Markdown table, plus the raw CREATE TABLE statement.
    """
    if not os.path.isfile(db_path):
        return f"Error: File '{db_path}' does not exist."

    if not all(c.isalnum() or c in ("_", "-") for c in table_name):
        return "Error: Invalid table name."

    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

        cols = con.execute(f'PRAGMA table_info("{table_name}")').fetchall()
        if not cols:
            return f"Table '{table_name}' not found or has no columns."

        schema_table = _rows_to_markdown(
            ["cid", "name", "type", "notnull", "dflt_value", "pk"],
            [tuple(c) for c in cols],
        )

        ddl_row = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
        ).fetchone()
        ddl = ddl_row[0] if ddl_row else "DDL not available."

        con.close()
        return f"### Schema: `{table_name}`\n\n{schema_table}\n\n**DDL:**\n```sql\n{ddl}\n```"

    except Exception as e:
        return f"Error: {str(e)}"


# ── DuckDB tool ───────────────────────────────────────────────────────────────

@mcp.tool(
    name="data_query_query_duckdb",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False
}
)
def query_duckdb(sql: str) -> str:
    """
    Execute SQL with DuckDB — queries CSV, Parquet, JSON, and SQLite files directly
    without importing them first.

    **TRIGGER CONDITION:** Use when you need to run analytics SQL over flat files (CSV,
    Parquet, JSON) or when you want DuckDB's advanced functions (PIVOT, window functions,
    `read_csv_auto`, `read_parquet`). Ideal for ad-hoc data analysis.

    **SEQUENCE GUIDANCE:** Reference files directly in SQL:
    - CSV: `SELECT * FROM read_csv_auto('/path/file.csv') LIMIT 10`
    - Parquet: `SELECT * FROM read_parquet('/path/file.parquet')`
    - JSON: `SELECT * FROM read_json_auto('/path/file.json')`
    - SQLite: `ATTACH '/path/db.sqlite' AS db; SELECT * FROM db.table_name`

    **CONSTRAINT WARNING:** Results capped at 500 rows and 80k chars. DuckDB must be
    installed (`pip install duckdb`). Write operations (CREATE TABLE, INSERT) are allowed
    but default to in-memory only — nothing is persisted unless you ATTACH a file.

    **OUTPUT EXPECTATION:** Returns a Markdown table of results with row count.
    """
    try:
        import duckdb
    except ImportError:
        return (
            "Error: DuckDB is not installed. Run `uv add duckdb` in the data_query directory."
        )

    try:
        con = duckdb.connect(database=":memory:")
        rel = con.execute(sql)
        columns = [d[0] for d in rel.description] if rel.description else []
        rows = rel.fetchmany(_MAX_ROWS + 1)
        truncated = len(rows) > _MAX_ROWS
        rows = rows[:_MAX_ROWS]
        con.close()

        if not columns:
            return "Query executed successfully (no rows returned)."

        table = _rows_to_markdown(columns, rows)
        result = f"### Results ({len(rows)} row{'s' if len(rows) != 1 else ''})\n\n{table}"
        if truncated:
            result += f"\n\n*Output capped at {_MAX_ROWS} rows. Add LIMIT to your query.*"

        if len(result) > _MAX_CHARS:
            result = result[:_MAX_CHARS] + "\n\n... [truncated at 80k chars]"

        return result

    except Exception as e:
        return f"DuckDB Error: {str(e)}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
