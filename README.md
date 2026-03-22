# RomM MCP Server

An [MCP](https://modelcontextprotocol.io) server for [RomM](https://github.com/rommapp/romm) — the self-hosted retro game library manager. Browse platforms, search ROMs, view metadata, manage collections, and track saves through any MCP-compatible AI assistant.

[![mcp-romm MCP server](https://glama.ai/mcp/servers/lodordev/mcp-romm/badges/card.svg)](https://glama.ai/mcp/servers/lodordev/mcp-romm)

## Tools

| Tool | Description |
|------|-------------|
| `romm_status` | Check server configuration and reachability |
| `romm_platforms` | List platforms with ROM counts and sizes |
| `romm_library_items` | Browse ROMs with filtering and pagination |
| `romm_get_item` | Full ROM detail — metadata, saves, user status |
| `romm_search` | Search ROMs by name |
| `romm_stats` | Library-wide statistics |
| `romm_collections` | List user-curated collections |
| `romm_saves` | List save files by ROM or platform |
| `romm_user_profile` | Browse by status (now playing, backlog, completed, etc.) |
| `romm_scan_library` | Trigger a background library rescan |

## Setup

### Prerequisites

- Python 3.10+
- A running [RomM](https://github.com/rommapp/romm) instance
- A RomM user account (username + password)

### Install

```bash
pip install fastmcp httpx
```

Or clone and install:

```bash
git clone https://github.com/lodordev/romm-mcp.git
cd romm-mcp
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
      "args": ["/path/to/romm-mcp/server.py"],
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
      "args": ["/path/to/romm-mcp/server.py"],
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
- "Show me the saves for Super Metroid"
- "What's in my favorites?"

## Auth

The server uses OAuth2 password grant to authenticate with RomM. Tokens are automatically refreshed when they expire. If a request gets a 401, the server re-authenticates and retries once.

No tokens or credentials are stored on disk — they live in memory for the duration of the server process.

## License

MIT