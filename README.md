# RomM MCP Server

An [MCP](https://modelcontextprotocol.io) server for [RomM](https://github.com/rommapp/romm) — the self-hosted retro game library manager. 28 tools: 19 read-only for browsing platforms, searching ROMs, viewing metadata, collections, saves, and tasks — plus 9 write tools for setting play status, favoriting, notes, and managing collections, through any MCP-compatible AI assistant.

## Tools

### Read

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

### Write

These modify **your own** user data and collections. They cannot alter ROM files, platforms, firmware, other users, or save files.

| Tool | Description |
|------|-------------|
| `romm_set_status` | Set play status, backlog, now-playing, rating, completion, last-played |
| `romm_favorite` | Add or remove a ROM from your favorites |
| `romm_add_note` | Add a note to a ROM |
| `romm_update_note` | Edit an existing note |
| `romm_delete_note` | Delete a note (permanent) |
| `romm_create_collection` | Create a new collection |
| `romm_add_to_collection` | Add ROMs to a collection |
| `romm_remove_from_collection` | Remove ROMs from a collection |
| `romm_delete_collection` | Delete a collection — the grouping only, not the ROMs (permanent) |

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

And, with the write tools:

- "Mark Chrono Trigger as finished"
- "Favorite Super Metroid"
- "Add it to my backlog and rate it 9"
- "Make a collection called 'SNES RPGs' and add ROMs 10, 11, and 12"
- "Add a note to this ROM: 'glitch at the second boss, save often'"

## Security

- **Least privilege.** The OAuth2 token requests only the read scopes the tools use plus `roms.user.write`, `collections.write`, and `tasks.run`. It deliberately does **not** request `roms.write`, `platforms.write`, `firmware.write`, `assets.write`, `users.write`, or `me.write` — no tool uses them.
- **Bounded write surface.** Write tools change only your own user data (play status, favorites, notes) and your own collections. No tool edits ROM files, platforms, firmware, other users, or uploads/deletes save files.
- **Destructive ops are labeled.** `romm_delete_note` and `romm_delete_collection` permanently remove data and say so in their descriptions. (`romm_delete_collection` removes the grouping, not the ROMs.)
- **No disk writes.** Credentials and tokens are held in memory only, never written to disk.
- **TLS by default.** Certificate verification is enabled by default (`ROMM_TLS_VERIFY=true`).
- **Auto-retry.** If a token expires mid-session, the server re-authenticates transparently.

## Auth

The server uses OAuth2 password grant to authenticate with RomM. Tokens are scoped to the minimum permissions needed and automatically refreshed when they expire. If a request gets a 401, the server re-authenticates and retries once.

**Note:** The read and write tools operate on your own library and user data, so an ordinary **enabled** RomM user account is sufficient — admin is not required. (`romm_scan_library` does require an account permitted to run tasks.)

## License

MIT. See [CHANGELOG.md](CHANGELOG.md) for release history.
