# Email Management MCP Server

MCP server for email classification, attachment routing, and inbox processing. Built on [himlaya](https://github.com/soywod/himalaya) (IMAP) and local LLMs via Ollama. Zero cloud by default; optional OpenRouter fallback.

## Tools

| Tool | Purpose |
|------|---------|
| `check_connection` | Verify himalaya IMAP connectivity |
| `list_accounts` | Show configured accounts |
| `list_rules` | Show routing rules for an account |
| `classify_email_tool` | Classify a single email by ID |
| `filter_attachments` | Dry-run: classify + preview routing for emails with attachments |
| `route_attachments` | Classify, download, and route attachments to computed paths |
| `process_inbox` | Full pipeline — same as `route_attachments` (dry_run=false) for cron jobs |

## Architecture

```
main.py                 ← FastMCP server, async tools
├── async_classifier.py ← httpx.AsyncClient → Ollama HTTP / OpenRouter
├── config.py            ← YAML config loader (~/.config/email-mcp/server_config.yaml)
├── himalaya_wrapper.py  ← subprocess wrapper around himalaya CLI
└── router.py            ← YAML rule engine with glob matching
```

## Setup

1. Install himalaya and configure IMAP accounts in its config.toml.
2. Create `~/.config/email-mcp/server_config.yaml`:

```yaml
accounts:
  - name: personal
    himalaya_account: myemail
    folders:
      - INBOX

classification:
  tiers:
    - model: granite4.1:3b
      provider: local
      timeout: 15
    - model: google/gemma-4-31b-it:free
      provider: cloud
      timeout: 20
  allow_cloud_fallback: true
  api_key: "sk-or-xxx"      # OpenRouter key, optional
  confidence_threshold: 0.7
  max_concurrent: 4

attachments:
  download_dir: /tmp/email-mcp-downloads
  skip_existing: true
```

3. Create routing rules at `~/.config/email-mcp/accounts/{name}/routing_rules.yaml`:

```yaml
rules:
  - recipient: "*@mycompany.com"
    sender: "*"
    path: ~/Documents/email/work/{date}/
    label: work
    action: save
  - recipient: "*"
    sender: "*@newsletter.com"
    path: ~/Documents/email/newsletters/
    label: newsletter
    action: archive-no-save
```

## Commands

```sh
cd email_management
uv sync
uv run python main.py          # stdio transport
uv run pytest tests/ -v        # run tests
```

## Config notes

- `server_config.yaml` is zero-secrets — himalaya stores credentials.
- Attachment routing paths support `{sender_domain}`, `{date}`, `{label}`.
- Cloud fallback only fires if `allow_cloud_fallback: true` and an `api_key` is present.
- All classification tools are async — the old `asyncio.run()` pattern was removed.
