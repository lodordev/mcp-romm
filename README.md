# RomM MCP Server

An [MCP](https://modelcontextprotocol.io) server for [RomM](https://github.com/rommapp/romm) — the self-hosted retro game library manager. 19 read-only tools for browsing platforms, searching ROMs, viewing metadata, managing collections, tracking saves, and monitoring tasks through any MCP-compatible AI assistant.

## Tools

| Tool | Description |
|------|-------------|
| `romm_status` | Check server configuration and reachability |
| `romm_stats` | Library-wide statistics (platforms, ROMs, saves, total size) |
| `romm_platforms` | List platforms with ROM counts and sizes |
| `romm_library_items` | Browse ROMs with filtering and pagination |
| `romm_recent` | Recently added or updated ROMs |
| `romm_get_item` | Full ROM detail — metadata, saves, user status |
| `romm_search` | Search ROMs by name |
| `romm_search_by_hash` | Identify a ROM by file hash (CRC, MD5, or SHA1) |
| `romm_filters` | Available filter values (genres, regions, languages, tags) |
| `romm_collections` | List user-curated collections |
| `romm_collection_detail` | List ROMs in a specific collection |
| `romm_smart_collections` | List auto-generated smart collections |
| `romm_saves` | List save files by ROM or platform |
| `romm_user_profile` | Browse by status (now playing, backlog, completed, etc.) |
| `romm_rom_notes` | View notes on a ROM |
| `romm_firmware` | List BIOS/firmware files per platform |
| `romm_devices` | List registered devices |
| `romm_tasks` | Check running/scheduled task status |
| `romm_scan_library` | Trigger a background library rescan |

## Setup

### Prerequisites

- Python 3.10+
- A running [RomM](https://github.com/rommapp/romm) instance (v4.0+)
- A RomM user account with admin role

### Install

```bash
pip install fastmcp httpx
```

Or clone and install:

```bash
git clone https://github.com/lodordev/mcp-romm.git
cd mcp-romm
pip install .
```

### Configure

Set environment variables:

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `ROMM_URL` | No | `http://localhost:3000` | Your RomM instance URL |
| `ROMM_USERNAME` | **Yes** | | RomM username |
| `ROMM_PASSWORD` | **Yes** | | RomM password |
| `ROMM_REQUEST_TIMEOUT` | No | `30` | Default request timeout (seconds) |
| `ROMM_REQUEST_TIMEOUT_LONG` | No | `60` | Timeout for slow endpoints |
| `ROMM_TLS_VERIFY` | No | `true` | Verify TLS certificates |

### Add to Claude Code

Add to your `.mcp.json`:

```json
{
  "mcpServers": {
    "romm": {
      "command": "python",
      "args": ["/path/to/mcp-romm/server.py"],
      "env": {
        "ROMM_URL": "http://your-romm-instance:3000",
        "ROMM_USERNAME": "your-username",
        "ROMM_PASSWORD": "your-password"
      }
    }
  }
}
```

### Add to Claude Desktop

Add to your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json` on macOS):

```json
{
  "mcpServers": {
    "romm": {
      "command": "python",
      "args": ["/path/to/mcp-romm/server.py"],
      "env": {
        "ROMM_URL": "http://your-romm-instance:3000",
        "ROMM_USERNAME": "your-username",
        "ROMM_PASSWORD": "your-password"
      }
    }
  }
}
```

## Examples

Once configured, you can ask your AI assistant things like:

- "What platforms do I have in RomM?"
- "Search for Zelda games"
- "Show me my backlog"
- "How many ROMs do I have total?"
- "What was recently added?"
- "Show me the saves for Super Metroid"
- "What's in my favorites?"
- "List my firmware files for PlayStation"
- "What tasks are running?"
- "What devices are registered?"

## Security

- **Read-only.** All 19 tools are read-only. The only mutation is `romm_scan_library`, which triggers an idempotent library rescan.
- **No disk writes.** Credentials and tokens are held in memory only, never written to disk.
- **Scoped tokens.** OAuth2 tokens request only the scopes needed for read operations.
- **TLS by default.** Certificate verification is enabled by default (`ROMM_TLS_VERIFY=true`).
- **Auto-retry.** If a token expires mid-session, the server re-authenticates transparently.

## Auth

The server uses OAuth2 password grant to authenticate with RomM. Tokens are scoped to the minimum permissions needed and automatically refreshed when they expire. If a request gets a 401, the server re-authenticates and retries once.

**Note:** Your RomM user must have the **admin** role for all tools to work. The user must also be **enabled** in the RomM admin panel.

## License

MIT
