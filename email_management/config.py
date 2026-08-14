"""Load and validate email-mcp server configuration."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml


CONFIG_DIR = Path.home() / ".config" / "email-mcp"
DEFAULT_CONFIG_PATH = CONFIG_DIR / "server_config.yaml"


@dataclass
class ModelTier:
    model: str
    provider: str = "local"  # "local" | "cloud"
    timeout: int = 30


@dataclass
class ClassificationConfig:
    tiers: List[ModelTier] = field(default_factory=list)
    allow_cloud_fallback: bool = False
    confidence_threshold: float = 0.7
    max_preview_chars: int = 500
    api_key: str = ""  # OpenRouter API key for cloud fallback
    max_concurrent: int = 4  # Max concurrent classification requests


@dataclass
class AccountConfig:
    name: str
    himalaya_account: str
    rules_file: str = ""
    folders: List[str] = field(default_factory=lambda: ["INBOX"])


@dataclass
class AttachmentConfig:
    download_dir: str = "/tmp/email-mcp-downloads"
    skip_existing: bool = True


@dataclass
class ServerConfig:
    accounts: List[AccountConfig] = field(default_factory=list)
    classification: ClassificationConfig = field(default_factory=ClassificationConfig)
    attachments: AttachmentConfig = field(default_factory=AttachmentConfig)


def _default_tiers() -> List[ModelTier]:
    return [
        ModelTier(model="qwen3:4b", provider="local", timeout=15),
        ModelTier(model="gemma4:latest", provider="local", timeout=30),
        ModelTier(model="deepseek-v4-flash:cloud", provider="cloud", timeout=30),
    ]


def load_config(path: Optional[str] = None) -> ServerConfig:
    """Load and validate server config from YAML."""
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    if not config_path.exists():
        raise FileNotFoundError(f"Config not found: {config_path}")

    with open(config_path) as f:
        raw = yaml.safe_load(f) or {}

    # Accounts
    accounts = []
    for a in raw.get("accounts", []):
        rules_file = a.get("rules_file", "")
        if not rules_file:
            rules_file = str(CONFIG_DIR / "accounts" / a["name"] / "routing_rules.yaml")
        accounts.append(AccountConfig(
            name=a["name"],
            himalaya_account=a.get("himalaya_account", a["name"]),
            rules_file=rules_file,
            folders=a.get("folders", ["INBOX"]),
        ))

    # Classification
    cls_raw = raw.get("classification", {})
    tiers = []
    for t in cls_raw.get("tiers", []):
        tiers.append(ModelTier(
            model=t["model"],
            provider=t.get("provider", "local"),
            timeout=t.get("timeout", 30),
        ))
    if not tiers:
        tiers = _default_tiers()

    # API key: prefer environment variable, fall back to YAML
    api_key = os.environ.get("OPENROUTER_API_KEY", "") or cls_raw.get("api_key", "")

    classification = ClassificationConfig(
        tiers=tiers,
        allow_cloud_fallback=cls_raw.get("allow_cloud_fallback", False),
        confidence_threshold=cls_raw.get("confidence_threshold", 0.7),
        max_preview_chars=cls_raw.get("max_preview_chars", 500),
        api_key=api_key,
        max_concurrent=cls_raw.get("max_concurrent", 4),
    )

    # Validate: cloud tiers require an API key
    has_cloud = any(t.provider == "cloud" for t in tiers)
    if has_cloud and classification.allow_cloud_fallback and not api_key:
        import warnings
        warnings.warn("Cloud fallback enabled but no API key found (set OPENROUTER_API_KEY env var)")

    # Attachments
    att_raw = raw.get("attachments", {})
    attachments = AttachmentConfig(
        download_dir=att_raw.get("download_dir", "/tmp/email-mcp-downloads"),
        skip_existing=att_raw.get("skip_existing", True),
    )

    return ServerConfig(
        accounts=accounts,
        classification=classification,
        attachments=attachments,
    )
