# RomM MCP Server

An [MCP](https://modelcontextprotocol.io) server for [RomM](https://github.com/rommapp/romm) — the self-hosted retro game library manager. 58 tools: 34 read-only for browsing platforms, searching ROMs, viewing metadata, collections (regular, smart, and virtual), saves, soundtracks, play activity, and tasks — plus 24 write tools for play status, play sessions, favorites, notes, collection management, music playlists, and emulator-streaming sessions, through any MCP-compatible AI assistant.

Tracks RomM **5.0 through 5.2**. Features added after 5.0 are version-gated against `/api/heartbeat` and explain themselves on an older server instead of returning a bare 404. Every tool carries MCP annotations (`readOnlyHint` / `destructiveHint` / `idempotentHint`), so a client can tell a browse from a delete without guessing.

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
| `romm_search_by_hash` | Identify a ROM by file hash (CRC, MD5, SHA1, or RetroAchievements) |
| `romm_filters` | Available filter values (genres, regions, languages, tags) |
| `romm_collections` | List user-curated collections |
| `romm_collection_detail` | List ROMs in a specific collection |
| `romm_smart_collections` | List auto-generated smart collections |
| `romm_saves` | List save files by ROM or platform |
| `romm_user_profile` | Browse by status (now playing, backlog, completed, etc.) |
| `romm_rom_notes` | View notes on a ROM |
| `romm_firmware` | List BIOS/firmware files per platform |
| `romm_devices` | List registered devices |
| `romm_tasks` | List registered tasks (schedule, manual-run availability) and running status |
| `romm_scan_library` | Trigger a background library rescan (blocked over REST on RomM 5.0 — see Known issues) |
| `romm_activity` | Recent play activity feed — who played what, when (5.0+) |
| `romm_play_sessions` | List recorded play sessions with durations (5.0+) |
| `romm_virtual_collections` | Automatic groupings by genre/franchise/company/etc. (5.0+) |
| `romm_virtual_collection_detail` | List ROMs in a virtual collection (5.0+) |
| `romm_smart_collection_detail` | A smart collection's rules and matching ROMs (5.0+) |
| `romm_whoami` | Authenticated account, role, and effective permissions |
| `romm_metadata_search` | Search metadata providers (IGDB etc.) for ROM matches (5.0+) |
| `romm_music_tracks` | Browse the soundtrack library — filterable, paginated game music |
| `romm_music_facets` | Distinct soundtrack artists / albums / genres / years with counts |
| `romm_music_favorites` | Your favourite soundtrack tracks (5.1+) |
| `romm_music_playlists` | List music playlists — yours plus other users' public ones (5.1+) |
| `romm_music_playlist` | A playlist's details and its tracks, in playlist order (5.1+) |
| `romm_streaming_config` | Emulator-streaming setup: enabled?, platforms, save-slot capabilities (5.1+) |
| `romm_streaming_sessions` | Active streaming sessions (admin-only server-side, 5.1+) |
| `romm_platform_folders` | Platform folders on disk RomM has never scanned (5.1+) |
| `romm_random_rom` | Pick one ROM at random — "what should I play?" (5.2+) |

### Write

These modify **your own** user data, collections, and music playlists. They cannot alter ROM files, platforms, firmware, other users, or RomM save files.

The exception is the emulator-streaming cluster, which starts and stops a **server-side emulator container** and can write that emulator's own save states. Those are separate from RomM's battery-save files, which no tool here touches.

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
| `romm_log_play_session` | Record a play session on a ROM (5.0+) |
| `romm_delete_play_session` | Delete one of your play sessions (permanent, 5.0+) |
| `romm_create_smart_collection` | Create a smart collection — a saved filter that auto-matches ROMs (5.0+) |
| `romm_update_smart_collection` | Edit a smart collection's name/description/rules (5.0+) |
| `romm_delete_smart_collection` | Delete a smart collection — the saved filter only (permanent, 5.0+) |
| `romm_music_favorite` | Add or remove soundtrack tracks from your favourites (5.1+) |
| `romm_create_music_playlist` | Create a music playlist (5.1+) |
| `romm_update_music_playlist` | Edit a playlist's name / description / visibility (5.1+) |
| `romm_delete_music_playlist` | Delete a playlist — the grouping only, not the audio (permanent, 5.1+) |
| `romm_music_playlist_tracks` | Add, remove, or reorder tracks in a playlist (5.1+) |
| `romm_streaming_start` | Launch a ROM in a server-side emulator and get the stream URL (5.1+) |
| `romm_streaming_state` | Save or load an emulator save state mid-game (5.1+) |
| `romm_streaming_audio` | Set volume, or set/toggle mute, on a running session (5.1+) |
| `romm_streaming_stop` | Stop a session — optionally saving state first (5.1+) |

### Not implemented on purpose

RomM 5.1 also added `DELETE /api/roms/{id}/files/{id}` (delete a ROM's files) and `PUT /api/config/scan` (rewrite scan settings). Both are outside this server's boundary — it does not delete library content or rewrite server configuration — so neither has a tool, and neither scope is requested.

## Setup

### Prerequisites

- Python 3.10+
- A running [RomM](https://github.com/rommapp/romm) instance (v5.0–v5.2; most read tools also work on v4.4+)
- An **enabled** RomM user account — admin is not required (see [Auth](#auth))

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
| `ROMM_MCP_TRANSPORT` | No | `stdio` | `stdio` (client spawns the process) or `http` (serve remote MCP clients) |
| `ROMM_MCP_HOST` | No | `127.0.0.1` | Bind address for `http` transport |
| `ROMM_MCP_PORT` | No | `8765` | Port for `http` transport |

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

### Remote (HTTP) transport

By default the server speaks stdio and is spawned by the client. To host it as
a shared network service instead — e.g. one instance on your homelab that all
your machines use — set:

```bash
ROMM_MCP_TRANSPORT=http ROMM_MCP_HOST=0.0.0.0 ROMM_MCP_PORT=8765 python server.py
```

Then point clients at it, e.g. Claude Code:

```bash
claude mcp add --transport http romm http://your-host:8765/mcp
```

The HTTP transport has no authentication of its own — bind it to a trusted
network only (LAN, VPN, or an overlay network like Tailscale), never the open
internet. Note that claude.ai web/mobile connectors connect from Anthropic's
cloud and cannot reach private-network URLs.

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
- "Pick me something random to play on the SNES"
- "What soundtrack albums do I have by Nobuo Uematsu?"
- "Make a playlist called 'Focus' and add those tracks"
- "Launch Shadow of the Colossus in the streaming emulator"

## Security

- **Least privilege.** The OAuth2 token requests only the read scopes the tools use plus `roms.user.write` and `collections.write`, and negotiates for the two optional tiers (`tasks.run`, and `playlists.read`/`playlists.write` on 5.1+). It deliberately does **not** request `roms.write`, `platforms.write`, `firmware.write`, `assets.write`, `users.write`, or `me.write` — no tool uses them.
- **Bounded write surface.** Write tools change only your own user data (play status, favorites, notes, music favourites and playlists) and your own collections. No tool edits ROM files, platforms, firmware, other users, or uploads/deletes RomM save files.
- **Emulator streaming is the one broader surface.** `romm_streaming_*` starts and stops a server-side emulator container and can write that emulator's save states. `romm_streaming_stop` kills a running emulator and is annotated destructive. Nothing here touches RomM's own save files.
- **Destructive ops are labeled.** Every tool that permanently removes data says so in its description and carries `destructiveHint`. Collection and playlist deletes remove the grouping, never the ROMs or audio.
- **No disk writes.** Credentials and tokens are held in memory only, never written to disk.
- **TLS by default.** Certificate verification is enabled by default (`ROMM_TLS_VERIFY=true`).
- **Auto-retry.** If a token expires mid-session, the server re-authenticates transparently.

## Auth

The server uses OAuth2 password grant to authenticate with RomM. Tokens are automatically refreshed when they expire, and a 401 triggers one transparent re-authenticate-and-retry.

**Scopes are negotiated, not assumed.** RomM's token endpoint rejects the *entire* request with `403 Insufficient scope` if any single requested scope falls outside the account's set — so asking for a scope your server or account doesn't have would take down every tool, not just the ones that need it. On first authentication this server asks for everything it wants; if that is refused it establishes that the core set works, then probes each optional tier once and drops the ones refused. The result is cached for the process, so token expiry doesn't re-run the probe. `romm_status` reports what was granted and what was dropped, and any tool needing a dropped tier says so plainly.

This matters in two common cases:

- **RomM 5.0 servers**, where `playlists.*` does not exist at all.
- **Least-privilege service accounts**, which typically lack the admin-tier `tasks.run`.

**Note:** The read and write tools operate on your own library and user data, so an ordinary **enabled** RomM user account is sufficient — admin is not required. (`romm_scan_library` needs an account permitted to run tasks; `romm_streaming_sessions` is admin-only on RomM's side and says so.)

**RomM 5.0 role change:** RomM 5.0 collapsed the old `viewer`/`editor`/`admin` roles into `user`/`admin` and moved fine-grained authorization to a permissions system (legacy roles are coerced to `user` on upgrade). If a tool unexpectedly gets a 403 on a 5.0 instance, check the account's effective permissions (`GET /api/permissions/me`) in the RomM admin UI.

## Version support

| RomM | Status |
|------|--------|
| 4.4–4.9 | Most read tools work; anything marked 5.0+ does not |
| 5.0 | Fully exercised, live e2e (see Known issues for 5.0.0 server bugs) |
| 5.1 | Music favourites/playlists, emulator streaming, unscanned platform folders |
| 5.2 | Random ROM pick |

The 5.1 and 5.2 tools were built against RomM's tagged source (routes, request
bodies, response shapes, and scope requirements read from the 5.1.0 and 5.2.0
tags) and are covered by unit tests, but have **not yet been exercised against
a live 5.1/5.2 instance**. The API surface is purely additive from 5.0.0 to
5.2.0 — 26 routes added, none removed or changed — so nothing that worked on
5.0 is at risk.

## Known issues

All three are RomM 5.0.0 server-side issues, found by running this server's
live e2e suite (`smoke_test.py`) against a 5.0.0 instance:

- **`romm_filters` times out.** `GET /api/roms/filters` in RomM 5.0.0 executes
  a query with a cartesian product (RomM's log flags it at
  `roms_handler.py:2159`); the request hangs until the client timeout, and the
  abandoned query keeps running server-side at high CPU. Avoid calling
  `romm_filters` on 5.0.0 until this is fixed upstream — every call strands
  another runaway database query.
- **Note listing 500s once any note exists.** RomM 5.0.0's
  `GET /api/roms/{id}/notes` fails serialization (`UserNoteSchema` validation
  in `endpoints/roms/notes.py`) whenever the ROM has at least one note.
  Creating and deleting notes work; `romm_rom_notes` (and the read-back after
  `romm_add_note`) will error until fixed upstream.
- **Library scans can't be triggered over REST.** RomM 5.0.0 flags
  `scan_library` as `manual_run: false`, so `POST /api/tasks/run/scan_library`
  is rejected. `romm_scan_library` reports this instead of failing; scans run
  on the configured schedule or from the web UI.

## License

MIT. See [CHANGELOG.md](CHANGELOG.md) for release history.
