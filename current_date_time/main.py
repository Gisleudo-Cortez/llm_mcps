from mcp.server.fastmcp import FastMCP
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Literal

# Initialize the standalone MCP Server
mcp = FastMCP("System Utilities Server")


@mcp.tool()
def get_current_datetime(timezone: str = "America/Fortaleza") -> str:
    """
    Retrieves the current date and time for a specified timezone.
    Defaults to America/Fortaleza (UTC-3).

    Args:
        timezone: An IANA timezone string (e.g., 'America/Fortaleza', 'Asia/Tokyo', 'UTC').
    """
    try:
        tz = ZoneInfo(timezone)
        now = datetime.now(tz)

        iso_format = now.isoformat()
        human_format = now.strftime("%A, %B %d, %Y at %I:%M:%S %p")
        day_of_week = now.strftime("%A")
        unix_epoch = int(now.timestamp())

        response = (
            f"### System Time Context\n"
            f"- **Timezone**: {timezone}\n"
            f"- **Current Date & Time**: {human_format}\n"
            f"- **Day of the Week**: {day_of_week}\n"
            f"- **ISO-8601 Timestamp**: {iso_format}\n"
            f"- **Unix Epoch**: {unix_epoch}\n"
            f"\n*Note: Use this localized time for queries regarding 'now' or scheduling in this timezone.*"
        )
        return response
    except ZoneInfoNotFoundError:
        return f"Error: Timezone '{timezone}' is invalid. Please use standard IANA format (e.g., 'Europe/London')."
    except Exception as e:
        return f"Error retrieving current datetime: {str(e)}"


@mcp.tool()
def calculate_relative_date(
    days_offset: int, timezone: str = "America/Fortaleza"
) -> str:
    """
    Calculates a past or future date based on a number of days offset from today.
    Use positive numbers for the future, negative numbers for the past.
    """
    try:
        tz = ZoneInfo(timezone)
        now = datetime.now(tz)
        target_date = now + timedelta(days=days_offset)

        return (
            f"### Relative Date Calculation\n"
            f"- **Base Date**: {now.strftime('%Y-%m-%d')} ({timezone})\n"
            f"- **Offset**: {days_offset} days\n"
            f"- **Target Date**: {target_date.strftime('%A, %B %d, %Y')}\n"
            f"- **Target ISO**: {target_date.date().isoformat()}"
        )
    except Exception as e:
        return f"Error calculating relative date: {str(e)}"


@mcp.tool()
def get_api_date_range(
    preset: Literal[
        "today", "yesterday", "last_7_days", "last_30_days", "this_month", "this_year"
    ],
    timezone: str = "America/Fortaleza",
) -> str:
    """
    Generates strict start and end dates for use in API queries (news, stocks, logs).
    Outputs exact boundaries in ISO-8601, YYYY-MM-DD, and Unix Epoch formats.
    """
    try:
        tz = ZoneInfo(timezone)
        now = datetime.now(tz)

        # Default boundary initialization
        start_date = now
        end_date = now

        if preset == "today":
            start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end_date = now.replace(hour=23, minute=59, second=59, microsecond=999999)
        elif preset == "yesterday":
            start_date = (now - timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            end_date = (now - timedelta(days=1)).replace(
                hour=23, minute=59, second=59, microsecond=999999
            )
        elif preset == "last_7_days":
            start_date = (now - timedelta(days=7)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            end_date = now
        elif preset == "last_30_days":
            start_date = (now - timedelta(days=30)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            end_date = now
        elif preset == "this_month":
            start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            end_date = now
        elif preset == "this_year":
            start_date = now.replace(
                month=1, day=1, hour=0, minute=0, second=0, microsecond=0
            )
            end_date = now

        return (
            f"### API Date Range: {preset.replace('_', ' ').title()}\n"
            f"- **Start (YYYY-MM-DD)**: {start_date.strftime('%Y-%m-%d')}\n"
            f"- **End (YYYY-MM-DD)**: {end_date.strftime('%Y-%m-%d')}\n"
            f"- **Start (ISO-8601)**: {start_date.isoformat()}\n"
            f"- **End (ISO-8601)**: {end_date.isoformat()}\n"
            f"- **Start (Unix)**: {int(start_date.timestamp())}\n"
            f"- **End (Unix)**: {int(end_date.timestamp())}"
        )
    except Exception as e:
        return f"Error generating API date range: {str(e)}"


@mcp.tool()
def translate_timezone(
    timestamp_str: str, source_tz: str, target_tz: str = "America/Fortaleza"
) -> str:
    """
    Translates a time from one timezone to another.
    Crucial for converting UTC or EST timestamps from APIs/logs into the user's local time.
    """
    try:
        source_zone = ZoneInfo(source_tz)
        target_zone = ZoneInfo(target_tz)

        # Try to parse standard ISO format first
        try:
            # Handle Python's strictness with the 'Z' suffix for UTC
            clean_str = timestamp_str.replace("Z", "+00:00")
            parsed_time = datetime.fromisoformat(clean_str)

            # If the string didn't contain offset info, apply the source_tz
            if parsed_time.tzinfo is None:
                parsed_time = parsed_time.replace(tzinfo=source_zone)
        except ValueError:
            return "Error: Could not parse timestamp. Please ensure it is in a standard format like ISO-8601 (e.g., 2026-04-09T14:30:00)."

        # Convert to target timezone
        translated_time = parsed_time.astimezone(target_zone)

        return (
            f"### Time Translation Result\n"
            f"- **Original**: {parsed_time.strftime('%Y-%m-%d %H:%M:%S')} ({source_tz})\n"
            f"- **Translated**: {translated_time.strftime('%Y-%m-%d %H:%M:%S')} ({target_tz})\n"
            f"- **Human Readable**: {translated_time.strftime('%A, %B %d at %I:%M %p')}"
        )
    except Exception as e:
        return f"Error translating timezone: {str(e)}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
