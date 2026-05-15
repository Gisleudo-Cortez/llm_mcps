"""Pytest configuration for email_management MCP server tests."""

import sys
from pathlib import Path

# Add project root to path so `from async_classifier` imports work
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
