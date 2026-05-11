from datetime import datetime, timedelta
from enum import Enum
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, ConfigDict, Field

mcp = FastMCP("date_time_mcp")


# ── Shared enums ───────────────────────────────────────────────────────────────

class _DatePreset(str, Enum):
    today = "today"
    yesterday = "yesterday"
    last_7_days = "last_7_days"
    last_30_days = "last_30_days"
    this_month = "this_month"
    this_year = "this_year"


class _ResponseFormat(str, Enum):
    markdown = "markdown"
    json = "json"


# ── Pydantic input models ──────────────────────────────────────────────────────

class GetCurrentDatetimeInput(BaseModel):
    """Input for retrieving the current date and time in a specific timezone."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    timezone: str = Field(
        default="America/Fortaleza",
        description="IANA timezone string (e.g., 'America/Fortaleza', 'Asia/Tokyo', 'UTC').",
        min_length=2,
        max_length=100,
    )
    response_format: _ResponseFormat = Field(
        default=_ResponseFormat.markdown,
        description="Output format: 'markdown' (human-readable) or 'json' (programmatic).",
    )


class CalculateRelativeDateInput(BaseModel):
    """Input for calculating a past or future date by a day offset."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    days_offset: int = Field(
        description="Number of days to add (future) or subtract (past) from today.",
        ge=-9999,
        le=9999,
    )
    timezone: str = Field(
        default="America/Fortaleza",
        description="IANA timezone string for the date calculation.",
        min_length=2,
        max_length=100,
    )
    response_format: _ResponseFormat = Field(
        default=_ResponseFormat.markdown,
        description="Output format: 'markdown' (human-readable) or 'json' (programmatic).",
    )


class GetApiDateRangeInput(BaseModel):
    """Input for generating strict start/end dates for API queries."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    preset: _DatePreset = Field(
        description="Preset date range (e.g., 'today', 'last_7_days').",
    )
    timezone: str = Field(
        default="America/Fortaleza",
        description="IANA timezone string for the date range.",
        min_length=2,
        max_length=100,
    )
    response_format: _ResponseFormat = Field(
        default=_ResponseFormat.markdown,
        description="Output format: 'markdown' (human-readable) or 'json' (programmatic).",
    )


class TranslateTimezoneInput(BaseModel):
    """Input for converting a timestamp from one timezone to another."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    timestamp_str: str = Field(
        description="ISO-8601 timestamp string (e.g., '2026-04-09T14:30:00+00:00').",
        min_length=5,
        max_length=100,
    )
    source_tz: str = Field(
        description="IANA timezone of the original timestamp.",
        min_length=2,
        max_length=100,
    )
    target_tz: str = Field(
        default="America/Fortaleza",
        description="IANA timezone to convert to.",
        min_length=2,
        max_length=100,
    )
    response_format: _ResponseFormat = Field(
        default=_ResponseFormat.markdown,
        description="Output format: 'markdown' (human-readable) or 'json' (programmatic).",
    )


# ── Shared formatters ────────────────────────────────────────────────────────

def _format_datetime_response(
    data: dict,
    heading: str,
    response_format: _ResponseFormat,
) -> str:
    """Render a response dict as markdown or JSON."""
    if response_format == _ResponseFormat.markdown:
        lines = [f"### {heading}"]
        for key, value in data.items():
            lines.append(f"- **{key}**: {value}")
        return "\n".join(lines)
    else:
        import json
        return json.dumps(data, indent=2)


# ── Tools ──────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="date_time_get_current_datetime",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def get_current_datetime(params: GetCurrentDatetimeInput) -> str:
    """Retrieves the current date and time for a specified timezone.

    **TRIGGER CONDITION:** Use when you need the current time in a specific timezone,
    especially when the user's timezone must be derived from context.

    **SEQUENCE GUIDANCE:** Typically called once at session start to establish time context.

    **CONSTRAINT WARNING:** Timezone must be a valid IANA identifier (e.g., 'Asia/Tokyo').

    **OUTPUT EXPECTATION:** Returns current date, time, day of week, ISO-8601 timestamp,
    and Unix epoch value.
    """
    try:
        tz = ZoneInfo(params.timezone)
        now = datetime.now(tz)

        data = {
            "Timezone": params.timezone,
            "Current Date & Time": now.strftime("%A, %B %d, %Y at %I:%M:%S %p"),
            "Day of the Week": now.strftime("%A"),
            "ISO-8601 Timestamp": now.isoformat(),
            "Unix Epoch": int(now.timestamp()),
        }
        return _format_datetime_response(data, "System Time Context", params.response_format)

    except ZoneInfoNotFoundError:
        return f"Error: Timezone '{params.timezone}' is invalid. Use standard IANA format (e.g., 'Europe/London')."
    except Exception as e:
        return f"Error retrieving current datetime: {str(e)}"


@mcp.tool(
    name="date_time_calculate_relative_date",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def calculate_relative_date(params: CalculateRelativeDateInput) -> str:
    """Calculate a past or future date based on a number of days offset from today.

    **TRIGGER CONDITION:** Use when you need a specific date relative to today —
    scheduling, deadlines, computing date ranges, or converting "3 days ago" into an
    actual date string.

    **SEQUENCE GUIDANCE:** Positive offsets for future, negative for past. Combine
    with `date_time_get_api_date_range` for start/end boundaries.

    **CONSTRAINT WARNING:** Offset is whole days only.

    **OUTPUT EXPECTATION:** Returns base date, offset, and target date in both
    human-readable and ISO-8601 formats.
    """
    try:
        tz = ZoneInfo(params.timezone)
        now = datetime.now(tz)
        target = now + timedelta(days=params.days_offset)

        data = {
            "Base Date": now.strftime("%Y-%m-%d") + f" ({params.timezone})",
            "Offset": f"{params.days_offset} days",
            "Target Date": target.strftime("%A, %B %d, %Y"),
            "Target ISO": target.date().isoformat(),
        }
        return _format_datetime_response(data, "Relative Date Calculation", params.response_format)

    except Exception as e:
        return f"Error calculating relative date: {str(e)}"


@mcp.tool(
    name="date_time_get_api_date_range",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def get_api_date_range(params: GetApiDateRangeInput) -> str:
    """Generates strict start and end dates for use in API queries.

    **TRIGGER CONDITION:** Use when querying APIs that require date ranges
    (news, logs, financial data).

    **SEQUENCE GUIDANCE:** Choose preset based on query intent. Combine with
    `date_time_get_current_datetime` to understand what "now" means.

    **OUTPUT EXPECTATION:** Returns start and end dates in YYYY-MM-DD, ISO-8601,
    and Unix Epoch formats.
    """
    try:
        tz = ZoneInfo(params.timezone)
        now = datetime.now(tz)

        start_date = end_date = now
        if params.preset == _DatePreset.today:
            start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        elif params.preset == _DatePreset.yesterday:
            start_date = (now - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = (now - timedelta(days=1)).replace(hour=23, minute=59, second=59, microsecond=999999)
        elif params.preset == _DatePreset.last_7_days:
            start_date = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = now
        elif params.preset == _DatePreset.last_30_days:
            start_date = (now - timedelta(days=30)).replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = now
        elif params.preset == _DatePreset.this_month:
            start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end_date = now
        elif params.preset == _DatePreset.this_year:
            start_date = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
            end_date = now

        data = {
            "Preset": params.preset.value,
            "Start (YYYY-MM-DD)": start_date.strftime("%Y-%m-%d"),
            "End (YYYY-MM-DD)": end_date.strftime("%Y-%m-%d"),
            "Start (ISO-8601)": start_date.isoformat(),
            "End (ISO-8601)": end_date.isoformat(),
            "Start (Unix)": int(start_date.timestamp()),
            "End (Unix)": int(end_date.timestamp()),
        }
        heading = f"API Date Range: {params.preset.value.replace('_', ' ').title()}"
        return _format_datetime_response(data, heading, params.response_format)

    except Exception as e:
        return f"Error generating API date range: {str(e)}"


@mcp.tool(
    name="date_time_translate_timezone",
    annotations={
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def translate_timezone(params: TranslateTimezoneInput) -> str:
    """Convert a timestamp from one timezone to another.

    **TRIGGER CONDITION:** Use when interpreting UTC or foreign timezone timestamps
    from APIs and logs.

    **SEQUENCE GUIDANCE:** Pass the ISO timestamp, its source TZ, and optionally a
    target TZ (defaults to America/Fortaleza). Supports 'Z' suffix notation.

    **CONSTRAINT WARNING:** Timestamp must be parseable in ISO-8601 format.

    **OUTPUT EXPECTATION:** Returns original time, translated time, and human-readable
    format.
    """
    try:
        source_zone = ZoneInfo(params.source_tz)
        target_zone = ZoneInfo(params.target_tz)

        clean_str = params.timestamp_str.replace("Z", "+00:00")
        parsed_time = datetime.fromisoformat(clean_str)

        if parsed_time.tzinfo is None:
            parsed_time = parsed_time.replace(tzinfo=source_zone)

        translated_time = parsed_time.astimezone(target_zone)

        data = {
            "Original": f"{parsed_time.strftime('%Y-%m-%d %H:%M:%S')} ({params.source_tz})",
            "Translated": f"{translated_time.strftime('%Y-%m-%d %H:%M:%S')} ({params.target_tz})",
            "Human Readable": translated_time.strftime("%A, %B %d at %I:%M %p"),
        }
        return _format_datetime_response(data, "Time Translation Result", params.response_format)

    except ValueError:
        return (
            "Error: Could not parse timestamp. "
            "Please ensure it is in a standard format like ISO-8601 (e.g., 2026-04-09T14:30:00)."
        )
    except Exception as e:
        return f"Error translating timezone: {str(e)}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
