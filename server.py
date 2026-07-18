"""RomM MCP Server — browse and manage your retro game library with AI.

Single-file MCP server for RomM (https://github.com/rommapp/romm).
Provides 40 tools: 26 read-only for browsing, searching, and viewing metadata,
plus 14 write tools for play status, play sessions, favorites, notes, and
collections (regular and smart). Targets RomM 5.0+; most read tools degrade
gracefully on 4.4+ (tools marked "RomM 5.0+" need 5.0).

Write tools change only your own user data (play status, favorites, notes) and
your own collections. No tool modifies ROM files, platforms, firmware, other
users, or uploads/deletes save files. OAuth2 password grant with automatic
token refresh and 401 retry.

Tools:
  romm_status              — Check server configuration and reachability
  romm_platforms           — List platforms with ROM counts and sizes
  romm_library_items       — Browse ROMs with filtering and pagination
  romm_recent              — Recently added or updated ROMs
  romm_get_item            — Full ROM detail (metadata, saves, user status)
  romm_search              — Search ROMs by name
  romm_search_by_hash      — Identify a ROM by file hash (MD5, SHA1, CRC, or RetroAchievements)
  romm_stats               — Library-wide statistics
  romm_collections         — List user-curated collections
  romm_collection_detail   — List ROMs in a specific collection
  romm_smart_collections   — List auto-generated smart collections
  romm_saves               — List save files by ROM or platform
  romm_user_profile        — Browse by user status (now playing, backlog, etc.)
  romm_firmware            — List BIOS/firmware files per platform
  romm_devices             — List registered devices
  romm_rom_notes           — View notes on a ROM
  romm_filters             — Available filter values (genres, regions, etc.)
  romm_tasks               — Task registry + running task status
  romm_scan_library        — Trigger a background library rescan
  romm_activity            — Recent play activity feed (RomM 5.0+)
  romm_play_sessions       — List recorded play sessions (RomM 5.0+)
  romm_virtual_collections — Automatic metadata groupings (RomM 5.0+)
  romm_virtual_collection_detail — List ROMs in a virtual collection
  romm_smart_collection_detail — Smart collection rules + matching ROMs
  romm_whoami              — Authenticated account, role, and permissions
  romm_metadata_search     — Search metadata providers for ROM matches

Write tools (modify your user data and collections):
  romm_set_status          — Set play status, backlog, now-playing, rating, completion
  romm_favorite            — Add or remove a ROM from your favorites
  romm_log_play_session    — Record a play session (RomM 5.0+)
  romm_delete_play_session — Delete a play session (permanent)
  romm_add_note            — Add a note to a ROM
  romm_update_note         — Edit an existing note
  romm_delete_note         — Delete a note (permanent)
  romm_create_collection   — Create a new collection
  romm_add_to_collection   — Add ROMs to a collection
  romm_remove_from_collection — Remove ROMs from a collection
  romm_delete_collection   — Delete a collection (permanent)
  romm_create_smart_collection — Create a smart collection (saved filter)
  romm_update_smart_collection — Edit a smart collection
  romm_delete_smart_collection — Delete a smart collection (permanent)

Environment variables:
  ROMM_URL              — RomM instance URL (default: http://localhost:3000)
  ROMM_USERNAME         — RomM username (required)
  ROMM_PASSWORD         — RomM password (required)
  ROMM_REQUEST_TIMEOUT  — Default request timeout in seconds (default: 30)
  ROMM_REQUEST_TIMEOUT_LONG — Timeout for slow endpoints (default: 60)
  ROMM_TLS_VERIFY       — Verify TLS certificates (default: true)

Security:
  - Least privilege: the OAuth2 token requests only read scopes plus
    roms.user.write, collections.write, and tasks.run — exactly what the tools
    do. It cannot modify ROM files, platforms, firmware, or other users, and it
    cannot touch save files.
  - Write tools that permanently destroy data (romm_delete_note,
    romm_delete_collection) are clearly labeled as such.
  - Credentials are held in memory only, never written to disk.
  - TLS verification is enabled by default.
"""

from __future__ import annotations

import json as jsonlib
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from fastmcp import FastMCP

log = logging.getLogger("romm-mcp")


# ── Config ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Config:
    romm_url: str
    romm_username: str
    romm_password: str
    request_timeout: int
    request_timeout_long: int
    tls_verify: bool

    @classmethod
    def from_env(cls) -> Config:
        username = os.getenv("ROMM_USERNAME", "")
        password = os.getenv("ROMM_PASSWORD", "")
        if not username or not password:
            log.warning("ROMM_USERNAME/ROMM_PASSWORD not set — all tools will fail")
        return cls(
            romm_url=os.getenv("ROMM_URL", "http://localhost:3000").rstrip("/"),
            romm_username=username,
            romm_password=password,
            request_timeout=int(os.getenv("ROMM_REQUEST_TIMEOUT", "30")),
            request_timeout_long=int(os.getenv("ROMM_REQUEST_TIMEOUT_LONG", "60")),
            tls_verify=os.getenv("ROMM_TLS_VERIFY", "true").lower() in ("true", "1", "yes"),
        )

    @property
    def configured(self) -> bool:
        return bool(self.romm_url and self.romm_username and self.romm_password)


cfg = Config.from_env()
mcp = FastMCP("romm")


# ── OAuth2 token management ─────────────────────────────────────────────


@dataclass
class _TokenState:
    access_token: str = ""
    refresh_token: str = ""
    expires_at: float = 0.0


_token = _TokenState()

# Least privilege: every read scope the read tools need, plus the two write
# scopes the write tools actually use (roms.user.write for status/favorites/
# notes, collections.write for collections) and tasks.run for the rescan.
# Deliberately NOT requested: roms.write, platforms.write, firmware.write,
# assets.write, users.write, me.write, devices.write — no tool uses them.
_DEFAULT_SCOPES = (
    "me.read "
    "roms.read "
    "roms.user.read roms.user.write "
    "platforms.read "
    "assets.read "
    "devices.read "
    "firmware.read "
    "collections.read collections.write "
    "tasks.run"
)

_clients: dict[str, httpx.AsyncClient] = {}


def _get_client() -> httpx.AsyncClient:
    if cfg.romm_url not in _clients:
        _clients[cfg.romm_url] = httpx.AsyncClient(
            timeout=httpx.Timeout(cfg.request_timeout, connect=10.0),
            verify=cfg.tls_verify,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
        )
    return _clients[cfg.romm_url]


async def _acquire_token() -> str:
    """Get a valid access token, refreshing or re-authenticating as needed."""
    now = time.time()

    if _token.access_token and _token.expires_at > now + 60:
        return _token.access_token

    client = _get_client()

    # Try refresh first
    if _token.refresh_token:
        try:
            resp = await client.post(
                f"{cfg.romm_url}/api/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": _token.refresh_token,
                    "scope": _DEFAULT_SCOPES,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if resp.status_code == 200:
                data = resp.json()
                _token.access_token = data["access_token"]
                _token.expires_at = now + data.get("expires", 1800)
                return _token.access_token
        except Exception:
            log.debug("Token refresh failed, falling back to password grant")

    # Password grant
    try:
        resp = await client.post(
            f"{cfg.romm_url}/api/token",
            data={
                "grant_type": "password",
                "username": cfg.romm_username,
                "password": cfg.romm_password,
                "scope": _DEFAULT_SCOPES,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        data = resp.json()
        _token.access_token = data["access_token"]
        _token.refresh_token = data.get("refresh_token", "")
        _token.expires_at = now + data.get("expires", 1800)
        return _token.access_token
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"RomM auth failed ({e.response.status_code}): {e.response.text[:200]}")
    except Exception as e:
        raise RuntimeError(f"RomM auth failed: {e}")


# ── HTTP helpers ─────────────────────────────────────────────────────────


async def _request(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json: dict | None = None,
    data: dict | None = None,
    long_timeout: bool = False,
    auth_required: bool = True,
) -> dict | list:
    """Make an HTTP request to RomM API. Handles auth and 401 retry.

    Use `json` for JSON bodies, `data` for url-encoded form fields (RomM's
    collection-create endpoint takes Form fields rather than JSON).
    """
    client = _get_client()
    url = f"{cfg.romm_url}/api/{path.lstrip('/')}"
    req_timeout = cfg.request_timeout_long if long_timeout else cfg.request_timeout

    headers: dict[str, str] = {}
    if auth_required:
        token = await _acquire_token()
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = await client.request(
            method, url, headers=headers, params=params, json=json, data=data,
            timeout=req_timeout,
        )

        if resp.status_code == 401 and auth_required:
            _token.access_token = ""
            token = await _acquire_token()
            headers["Authorization"] = f"Bearer {token}"
            resp = await client.request(
                method, url, headers=headers, params=params, json=json, data=data,
                timeout=req_timeout,
            )

        resp.raise_for_status()
        if resp.status_code == 204 or not resp.text:
            return {}
        ct = resp.headers.get("content-type", "")
        if "json" not in ct:
            return {"_text": resp.text}
        return resp.json()
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"API error {e.response.status_code}: {e.response.text[:200]}")
    except httpx.TimeoutException:
        raise RuntimeError(f"Request timed out after {req_timeout}s")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Request failed: {e}")


async def _get(path: str, *, params: dict | None = None, long_timeout: bool = False,
               auth_required: bool = True) -> dict | list:
    return await _request("GET", path, params=params, long_timeout=long_timeout,
                          auth_required=auth_required)


async def _post(path: str, body: dict | None = None, *, params: dict | None = None,
                data: dict | None = None, long_timeout: bool = False) -> dict | list:
    return await _request("POST", path, params=params, json=body, data=data,
                          long_timeout=long_timeout)


async def _put(path: str, body: dict | None = None, *, params: dict | None = None) -> dict | list:
    return await _request("PUT", path, json=body, params=params)


async def _delete(path: str, body: dict | None = None) -> dict | list:
    return await _request("DELETE", path, json=body)


# ── Helpers ──────────────────────────────────────────────────────────────


def _fmt_size(size_bytes: int | float | None) -> str:
    if not size_bytes:
        return "0 B"
    size_bytes = int(size_bytes)
    if size_bytes >= 1024**3:
        return f"{size_bytes / 1024**3:.1f} GB"
    if size_bytes >= 1024**2:
        return f"{size_bytes / 1024**2:.0f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.0f} KB"
    return f"{size_bytes} B"


def _fmt_rom_line(rom: dict, index: int = 0) -> list[str]:
    """Format a ROM into display lines. Reused across multiple tools."""
    name = rom.get("name", "Unknown")
    platform = rom.get("platform_display_name") or rom.get("platform_slug", "?")
    size = rom.get("fs_size_bytes", 0)
    rom_id = rom.get("id", "?")
    summary = rom.get("summary", "")
    user = rom.get("rom_user", {}) or {}
    is_fav = user.get("is_favorite", False) if isinstance(user, dict) else False

    lines = []
    line = f"{index}. {name}" if index else name
    if platform:
        line += f" [{platform}]"
    if is_fav:
        line += " *"
    lines.append(line)
    if size:
        lines.append(f"   Size: {_fmt_size(size)}")
    if summary:
        short = summary[:120]
        if len(summary) > 120:
            short += "..."
        lines.append(f"   {short}")
    lines.append(f"   ID: {rom_id}")
    lines.append("")
    return lines


# ── Tools — Status & Stats ──────────────────────────────────────────────


@mcp.tool()
async def romm_status() -> str:
    """Check RomM MCP server configuration and reachability."""
    lines = ["RomM MCP Status:\n"]
    lines.append(f"  URL: {cfg.romm_url}")
    lines.append(f"  Username: {cfg.romm_username}")
    lines.append(f"  TLS verify: {cfg.tls_verify}")
    lines.append(f"  Timeouts: {cfg.request_timeout}s / {cfg.request_timeout_long}s (long)\n")

    if not cfg.configured:
        lines.append("  Status: NOT CONFIGURED (set ROMM_USERNAME + ROMM_PASSWORD)")
        return "\n".join(lines)

    try:
        data = await _get("heartbeat", auth_required=False)
        if isinstance(data, dict):
            system = data.get("SYSTEM", {})
            meta = data.get("METADATA_SOURCES", {})
            fs = data.get("FILESYSTEM", {})

            lines.append("  Connected: yes")
            lines.append(f"  Version: {system.get('VERSION', '?')}")
            lines.append(f"  IGDB enabled: {meta.get('IGDB_API_ENABLED', False)}")
            lines.append(f"  ScreenScraper enabled: {meta.get('SS_API_ENABLED', False)}")
            lines.append(f"  HLTB enabled: {meta.get('HLTB_API_ENABLED', False)}")
            platforms = fs.get("FS_PLATFORMS", [])
            lines.append(f"  Filesystem platforms: {len(platforms)}")
        else:
            lines.append("  Connected: yes (unexpected response format)")
    except Exception as e:
        lines.append(f"  Status: UNREACHABLE — {e}")

    return "\n".join(lines)


@mcp.tool()
async def romm_stats() -> str:
    """Get library statistics — platform count, ROM count, saves, total size."""
    data = await _get("stats")

    if not isinstance(data, dict):
        return "No stats available."

    lines = ["RomM Library Statistics:\n"]
    lines.append(f"  Platforms: {data.get('PLATFORMS', 0)}")
    lines.append(f"  ROMs: {data.get('ROMS', 0)}")
    lines.append(f"  Saves: {data.get('SAVES', 0)}")
    lines.append(f"  Save states: {data.get('STATES', 0)}")
    lines.append(f"  Screenshots: {data.get('SCREENSHOTS', 0)}")
    total = data.get("TOTAL_FILESIZE_BYTES", 0)
    if total:
        lines.append(f"  Total size: {_fmt_size(total)}")

    return "\n".join(lines)


# ── Tools — Platforms ────────────────────────────────────────────────────


@mcp.tool()
async def romm_platforms() -> str:
    """List platforms with ROM counts."""
    data = await _get("platforms")
    if not isinstance(data, list):
        return "No platforms found."

    platforms = sorted(data, key=lambda p: p.get("rom_count", 0), reverse=True)
    if not platforms:
        return "No platforms found."

    lines = [f"Platforms ({len(platforms)}):\n"]
    for p in platforms:
        name = p.get("display_name") or p.get("name", "Unknown")
        slug = p.get("slug", "?")
        count = p.get("rom_count", 0)
        size = p.get("fs_size_bytes", 0)
        pid = p.get("id", "?")

        line = f"  {name} ({slug})"
        line += f" — {count} ROM{'s' if count != 1 else ''}"
        if size:
            line += f", {_fmt_size(size)}"
        lines.append(line)
        lines.append(f"    ID: {pid}")

    return "\n".join(lines)


# ── Tools — ROM Browsing & Search ────────────────────────────────────────


@mcp.tool()
async def romm_library_items(
    platform_id: int = 0,
    search: str = "",
    favorite: bool = False,
    limit: int = 25,
    offset: int = 0,
    order_by: str = "name",
    order_dir: str = "asc",
) -> str:
    """Browse ROMs — filter by platform, search term, or favorites. Paginated.

    platform_id: Filter to a single platform (use romm_platforms to find IDs). 0 = all.
    search: Text search in ROM names.
    favorite: Show only favorites (default: false).
    limit: Items per page (default 25, max 100).
    offset: Skip this many items (default 0).
    order_by: Sort field — "name", "fs_size_bytes", "updated_at" (default: name).
    order_dir: "asc" or "desc" (default: asc).
    """
    limit = min(max(limit, 1), 100)
    params: dict = {
        "limit": limit,
        "offset": offset,
        "order_by": order_by,
        "order_dir": order_dir,
    }
    if platform_id:
        params["platform_ids"] = platform_id
    if search:
        params["search_term"] = search
    if favorite:
        params["favorite"] = True

    data = await _get("roms", params=params, long_timeout=True)

    items = []
    total = None
    if isinstance(data, dict):
        items = data.get("items", [])
        total = data.get("total")
    elif isinstance(data, list):
        items = data

    if not items:
        qualifier = f" matching \"{search}\"" if search else ""
        qualifier += f" on platform {platform_id}" if platform_id else ""
        return f"No ROMs found{qualifier}."

    if total is not None:
        lines = [f"ROMs (offset {offset}, showing {len(items)} of {total}):\n"]
    else:
        lines = [f"ROMs (offset {offset}, showing {len(items)}):\n"]
    for i, rom in enumerate(items, 1):
        lines.extend(_fmt_rom_line(rom, index=i + offset))

    return "\n".join(lines)


@mcp.tool()
async def romm_recent(limit: int = 20) -> str:
    """Recently added or updated ROMs.

    limit: Number of results (default 20, max 100).
    """
    limit = min(max(limit, 1), 100)
    params: dict = {
        "limit": limit,
        "offset": 0,
        "order_by": "updated_at",
        "order_dir": "desc",
    }

    data = await _get("roms", params=params, long_timeout=True)

    items = []
    if isinstance(data, dict):
        items = data.get("items", [])
    elif isinstance(data, list):
        items = data

    if not items:
        return "No ROMs found."

    lines = [f"Recently updated ROMs ({len(items)}):\n"]
    for i, rom in enumerate(items, 1):
        name = rom.get("name", "Unknown")
        platform = rom.get("platform_display_name") or rom.get("platform_slug", "?")
        updated = rom.get("updated_at", "")
        rom_id = rom.get("id", "?")

        line = f"  {i}. {name} [{platform}]"
        lines.append(line)
        if updated:
            lines.append(f"     Updated: {updated}")
        lines.append(f"     ID: {rom_id}")

    return "\n".join(lines)


@mcp.tool()
async def romm_get_item(rom_id: int) -> str:
    """Get full detail for a single ROM — metadata, user status, saves.

    rom_id: The ROM's ID (from romm_library_items or romm_search).
    """
    data = await _get(f"roms/{rom_id}")

    if not isinstance(data, dict) or "id" not in data:
        return f"ROM {rom_id} not found."

    name = data.get("name", "Unknown")
    slug = data.get("slug", "")
    platform = data.get("platform_display_name") or data.get("platform_slug", "?")
    summary = data.get("summary", "")
    size = data.get("fs_size_bytes", 0)
    regions = data.get("regions", [])
    languages = data.get("languages", [])
    tags = data.get("tags", [])
    alt_names = data.get("alternative_names", [])

    user = data.get("rom_user", {}) or {}
    last_played = user.get("last_played") if isinstance(user, dict) else None
    status = user.get("status") if isinstance(user, dict) else None

    # Favorite flag: 5.0 dropped rom_user.is_favorite — favorites are a special
    # collection. The is_favorite field embedded in user_collections serializes
    # as null even for the favorites collection (verified on 5.0.0), so resolve
    # the flagged collection and match by id. 4.x still sets rom_user.is_favorite.
    is_fav = user.get("is_favorite", False) if isinstance(user, dict) else False
    user_collections = data.get("user_collections", [])
    if not is_fav and isinstance(user_collections, list) and user_collections:
        try:
            fav = await _favorite_collection()
        except RuntimeError:
            fav = None
        if fav:
            is_fav = any(
                isinstance(c, dict) and c.get("id") == fav.get("id")
                for c in user_collections
            )

    # Inline note: 4.x kept a single note on rom_user; 5.0 moved notes to the
    # /notes endpoints and only flags has_notes here.
    note_raw = user.get("note_raw_markdown") if isinstance(user, dict) else None
    has_notes = bool(data.get("has_notes"))

    saves = data.get("user_saves", [])
    states = data.get("user_states", [])

    lines = [name]
    lines.append(f"  Platform: {platform}")
    if slug:
        lines.append(f"  Slug: {slug}")
    if regions:
        lines.append(f"  Regions: {', '.join(str(r) for r in regions)}")
    if languages:
        lines.append(f"  Languages: {', '.join(str(lang) for lang in languages)}")
    if tags:
        lines.append(f"  Tags: {', '.join(str(t) for t in tags)}")
    if alt_names:
        lines.append(f"  Also known as: {', '.join(str(n) for n in alt_names[:5])}")
    if size:
        lines.append(f"  Size: {_fmt_size(size)}")

    if is_fav:
        lines.append("  Favorite: yes")
    if status:
        lines.append(f"  Status: {status}")
    if last_played:
        lines.append(f"  Last played: {last_played}")
    if note_raw:
        lines.append(f"  Note: {note_raw[:200]}")
    elif has_notes:
        lines.append("  Notes: yes (view with romm_rom_notes)")

    if saves:
        lines.append(f"\n  Saves ({len(saves)}):")
        for s in saves[:10]:
            sname = s.get("file_name", "?")
            ssize = s.get("file_size_bytes", 0)
            lines.append(f"    - {sname} ({_fmt_size(ssize)})")

    if states:
        lines.append(f"\n  Save states ({len(states)}):")
        for s in states[:10]:
            sname = s.get("file_name", "?")
            ssize = s.get("file_size_bytes", 0)
            lines.append(f"    - {sname} ({_fmt_size(ssize)})")

    lines.append(f"\n  ID: {data['id']}")

    if summary:
        desc = summary[:400]
        if len(summary) > 400:
            desc += "..."
        lines.append(f"\n  Description: {desc}")

    return "\n".join(lines)


@mcp.tool()
async def romm_search(query: str, platform_id: int = 0, limit: int = 20) -> str:
    """Search ROMs by name across the library.

    query: Search term (required).
    platform_id: Filter to a single platform (0 = all).
    limit: Max results (default 20).
    """
    params: dict = {
        "search_term": query,
        "limit": min(limit, 100),
        "offset": 0,
        "order_by": "name",
        "order_dir": "asc",
    }
    if platform_id:
        params["platform_ids"] = platform_id

    data = await _get("roms", params=params, long_timeout=True)

    items = []
    total = None
    if isinstance(data, dict):
        items = data.get("items", [])
        total = data.get("total")
    elif isinstance(data, list):
        items = data

    if not items:
        return f"No ROMs found matching \"{query}\"."

    found = total if total is not None else len(items)
    lines = [f"Search results for \"{query}\" ({found} found, showing {len(items)}):\n"]
    for i, rom in enumerate(items, 1):
        name = rom.get("name", "Unknown")
        platform = rom.get("platform_display_name") or rom.get("platform_slug", "?")
        size = rom.get("fs_size_bytes", 0)
        rom_id = rom.get("id", "?")

        line = f"  {i}. {name} [{platform}]"
        if size:
            line += f" — {_fmt_size(size)}"
        lines.append(line)
        lines.append(f"     ID: {rom_id}")

    return "\n".join(lines)


@mcp.tool()
async def romm_search_by_hash(
    crc_hash: str = "",
    md5_hash: str = "",
    sha1_hash: str = "",
    ra_hash: str = "",
) -> str:
    """Identify a ROM by file hash. Provide at least one hash value.

    crc_hash: CRC32 hash string.
    md5_hash: MD5 hash string.
    sha1_hash: SHA1 hash string.
    ra_hash: RetroAchievements hash string (RomM 5.0+).
    """
    params: dict = {}
    if crc_hash:
        params["crc_hash"] = crc_hash.strip()
    if md5_hash:
        params["md5_hash"] = md5_hash.strip()
    if sha1_hash:
        params["sha1_hash"] = sha1_hash.strip()
    if ra_hash:
        params["ra_hash"] = ra_hash.strip()

    if not params:
        return "At least one hash value is required (crc_hash, md5_hash, sha1_hash, or ra_hash)."

    data = await _get("roms/by-hash", params=params)

    if not isinstance(data, dict) or "id" not in data:
        return "No ROM found matching the provided hash."

    name = data.get("name", "Unknown")
    platform = data.get("platform_display_name") or data.get("platform_slug", "?")
    rom_id = data.get("id", "?")
    size = data.get("fs_size_bytes", 0)

    lines = ["Match found:\n"]
    lines.append(f"  {name} [{platform}]")
    if size:
        lines.append(f"  Size: {_fmt_size(size)}")
    lines.append(f"  ID: {rom_id}")

    return "\n".join(lines)


@mcp.tool()
async def romm_filters() -> str:
    """Get available filter values for ROM browsing — genres, regions, languages, tags."""
    data = await _get("roms/filters", long_timeout=True)

    if not isinstance(data, dict):
        return "No filter data available."

    lines = ["Available ROM Filters:\n"]

    for key in ("genres", "franchises", "collections", "companies", "regions",
                "languages", "tags"):
        values = data.get(key, [])
        if values:
            display = ", ".join(str(v) for v in values[:30])
            if len(values) > 30:
                display += f"... (+{len(values) - 30} more)"
            lines.append(f"  {key.title()} ({len(values)}): {display}")

    if len(lines) == 1:
        return "No filters available (library may be empty)."

    return "\n".join(lines)


# ── Tools — Collections ─────────────────────────────────────────────────


@mcp.tool()
async def romm_collections() -> str:
    """List user-curated collections."""
    data = await _get("collections")

    if not isinstance(data, list) or not data:
        return "No collections found."

    lines = [f"Collections ({len(data)}):\n"]
    for c in data:
        name = c.get("name", "Unknown")
        desc = c.get("description", "")
        cid = c.get("id", "?")
        # RomM 5.0 returns rom_count; 4.x embedded the full roms list.
        rom_count = c.get("rom_count")
        if rom_count is None:
            roms = c.get("roms", [])
            rom_count = len(roms) if isinstance(roms, list) else 0

        lines.append(f"  {name} ({rom_count} ROM{'s' if rom_count != 1 else ''})")
        if desc:
            short = desc[:100]
            if len(desc) > 100:
                short += "..."
            lines.append(f"    {short}")
        lines.append(f"    ID: {cid}")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
async def romm_collection_detail(collection_id: int) -> str:
    """List ROMs in a specific collection.

    collection_id: The collection's ID (from romm_collections).
    """
    data = await _get(f"collections/{collection_id}")

    if not isinstance(data, dict) or "id" not in data:
        return f"Collection {collection_id} not found."

    name = data.get("name", "Unknown")
    desc = data.get("description", "")

    # RomM 5.0 no longer embeds the roms list in the collection — fetch the
    # members via the roms endpoint's collection_id filter. 4.x embedded them.
    roms = data.get("roms")
    rom_count = data.get("rom_count")
    if not isinstance(roms, list) or not roms:
        members = await _get(
            "roms",
            params={"collection_id": collection_id, "limit": 50, "offset": 0,
                    "order_by": "name", "order_dir": "asc"},
            long_timeout=True,
        )
        if isinstance(members, dict):
            roms = members.get("items", [])
            rom_count = members.get("total", rom_count)
        elif isinstance(members, list):
            roms = members
    if rom_count is None:
        rom_count = len(roms) if isinstance(roms, list) else 0

    lines = [f"{name}"]
    if desc:
        lines.append(f"  {desc[:200]}")
    lines.append(f"  ROMs: {rom_count}\n")

    if isinstance(roms, list):
        for i, rom in enumerate(roms[:50], 1):
            if isinstance(rom, dict):
                rom_name = rom.get("name") or rom.get("rom_name", "Unknown")
                platform = rom.get("platform_display_name") or rom.get("platform_slug", "")
                line = f"  {i}. {rom_name}"
                if platform:
                    line += f" [{platform}]"
                lines.append(line)
            else:
                lines.append(f"  {i}. ROM ID: {rom}")

        if rom_count > 50:
            lines.append(f"\n  ({rom_count - 50} more not shown)")

    return "\n".join(lines)


@mcp.tool()
async def romm_smart_collections() -> str:
    """List auto-generated smart collections (rule-based)."""
    data = await _get("collections/smart")

    if not isinstance(data, list) or not data:
        return "No smart collections found."

    lines = [f"Smart Collections ({len(data)}):\n"]
    for c in data:
        name = c.get("name", "Unknown")
        desc = c.get("description", "")
        cid = c.get("id", "?")
        count = c.get("rom_count")

        header = f"  {name}"
        if count is not None:
            header += f" ({count} ROM{'s' if count != 1 else ''})"
        lines.append(header)
        if c.get("filter_summary"):
            lines.append(f"    Rules: {c['filter_summary'][:120]}")
        if desc:
            short = desc[:100]
            if len(desc) > 100:
                short += "..."
            lines.append(f"    {short}")
        lines.append(f"    ID: {cid}")
        lines.append("")

    return "\n".join(lines)


# ── Tools — Saves & User Status ──────────────────────────────────────────


@mcp.tool()
async def romm_saves(rom_id: int = 0, platform_id: int = 0) -> str:
    """List save files. Filter by ROM or platform.

    rom_id: Filter to a specific ROM (0 = all).
    platform_id: Filter to a specific platform (0 = all).
    """
    params: dict = {}
    if rom_id:
        params["rom_id"] = rom_id
    if platform_id:
        params["platform_id"] = platform_id

    data = await _get("saves", params=params)

    if not isinstance(data, list) or not data:
        qualifier = ""
        if rom_id:
            qualifier += f" for ROM {rom_id}"
        if platform_id:
            qualifier += f" on platform {platform_id}"
        return f"No saves found{qualifier}."

    lines = [f"Saves ({len(data)}):\n"]
    for s in data[:50]:
        fname = s.get("file_name", "?")
        size = s.get("file_size_bytes", 0)
        # rom_name/platform_slug are 4.x-only; 5.0 saves carry rom_id + emulator/slot.
        rom_name = s.get("rom_name", "")
        platform = s.get("platform_slug", "")
        emulator = s.get("emulator", "")
        slot = s.get("slot")
        srom_id = s.get("rom_id")
        updated = s.get("updated_at", "")

        line = f"  - {fname}"
        if rom_name:
            line += f" ({rom_name})"
        elif srom_id:
            line += f" (ROM {srom_id})"
        if platform:
            line += f" [{platform}]"
        if emulator:
            line += f" [{emulator}]"
        if slot not in (None, ""):
            line += f" slot {slot}"
        if size:
            line += f" — {_fmt_size(size)}"
        lines.append(line)
        if updated:
            lines.append(f"    Updated: {updated}")

    if len(data) > 50:
        lines.append(f"\n  ({len(data) - 50} more saves not shown)")

    return "\n".join(lines)


@mcp.tool()
async def romm_user_profile(status_filter: str = "") -> str:
    """Browse ROMs by user status — favorites, now playing, backlogged, completed.

    status_filter: Filter by user status. Options: "now_playing", "backlog",
                   "wishlist", "completed", "retired", "" (shows favorites).
    """
    params: dict = {"limit": 50, "offset": 0, "order_by": "name", "order_dir": "asc"}

    if status_filter:
        params["statuses"] = status_filter
    else:
        params["favorite"] = True

    data = await _get("roms", params=params, long_timeout=True)

    items = []
    if isinstance(data, dict):
        items = data.get("items", [])
    elif isinstance(data, list):
        items = data

    label = status_filter.replace("_", " ").title() if status_filter else "Favorites"

    if not items:
        return f"No ROMs marked as {label}."

    lines = [f"{label} ({len(items)}):\n"]
    for i, rom in enumerate(items, 1):
        name = rom.get("name", "Unknown")
        platform = rom.get("platform_display_name") or rom.get("platform_slug", "?")
        rom_id = rom.get("id", "?")
        user = rom.get("rom_user", {}) or {}
        last_played = user.get("last_played") if isinstance(user, dict) else None

        line = f"  {i}. {name} [{platform}]"
        lines.append(line)
        if last_played:
            lines.append(f"     Last played: {last_played}")
        lines.append(f"     ID: {rom_id}")

    return "\n".join(lines)


@mcp.tool()
async def romm_rom_notes(rom_id: int) -> str:
    """View notes on a ROM.

    rom_id: The ROM's ID (from romm_library_items or romm_search).
    """
    data = await _get(f"roms/{rom_id}/notes")

    if not isinstance(data, list) or not data:
        return f"No notes found for ROM {rom_id}."

    lines = [f"Notes for ROM {rom_id} ({len(data)}):\n"]
    for n in data:
        title = n.get("title", "")
        content = n.get("content", "")
        created = n.get("created_at", "")
        updated = n.get("updated_at", "")
        note_id = n.get("id", "?")
        username = n.get("username", "")
        is_public = n.get("is_public", False)

        header = f"  [{note_id}]"
        if title:
            header += f" {title}"
        if username:
            header += f" — by {username}"
        if is_public:
            header += " (public)"
        lines.append(header)
        if content:
            short = content[:300]
            if len(content) > 300:
                short += "..."
            lines.append(f"      {short}")
        if created:
            line = f"    Created: {created}"
            if updated and updated != created:
                line += f" | Updated: {updated}"
            lines.append(line)
        lines.append("")

    return "\n".join(lines)


# ── Tools — Firmware & Devices ───────────────────────────────────────────


@mcp.tool()
async def romm_firmware(platform_id: int = 0) -> str:
    """List BIOS/firmware files. Optionally filter by platform.

    platform_id: Filter to a specific platform (0 = all).
    """
    params: dict = {}
    if platform_id:
        params["platform_id"] = platform_id

    data = await _get("firmware", params=params)

    if not isinstance(data, list) or not data:
        qualifier = f" for platform {platform_id}" if platform_id else ""
        return f"No firmware found{qualifier}."

    lines = [f"Firmware ({len(data)}):\n"]
    for fw in data[:50]:
        fname = fw.get("file_name", "?")
        size = fw.get("file_size_bytes", 0)
        platform = fw.get("platform_slug", "")
        fw_id = fw.get("id", "?")

        line = f"  - {fname}"
        if platform:
            line += f" [{platform}]"
        if size:
            line += f" — {_fmt_size(size)}"
        lines.append(line)
        lines.append(f"    ID: {fw_id}")

    if len(data) > 50:
        lines.append(f"\n  ({len(data) - 50} more not shown)")

    return "\n".join(lines)


@mcp.tool()
async def romm_devices() -> str:
    """List registered devices (handhelds, emulators, etc.)."""
    data = await _get("devices")

    if not isinstance(data, list) or not data:
        return "No devices registered."

    lines = [f"Devices ({len(data)}):\n"]
    for d in data:
        name = d.get("name") or d.get("hostname", "Unknown")
        # 4.x had a free-form "type"; 5.0 devices carry client/platform/sync info.
        device_type = d.get("type", "")
        client = d.get("client", "")
        platform = d.get("platform", "")
        last_seen = d.get("last_seen", "")
        sync_enabled = d.get("sync_enabled")
        device_id = d.get("id", "?")

        line = f"  - {name}"
        detail = device_type or " / ".join(x for x in (client, platform) if x)
        if detail:
            line += f" ({detail})"
        if sync_enabled is not None:
            line += f" — sync {'on' if sync_enabled else 'off'}"
        lines.append(line)
        if last_seen:
            lines.append(f"    Last seen: {last_seen}")
        lines.append(f"    ID: {device_id}")

    return "\n".join(lines)


# ── Tools — Tasks ────────────────────────────────────────────────────────


@mcp.tool()
async def romm_tasks() -> str:
    """List registered tasks (schedule, manual-run availability) and running task status."""
    lines: list[str] = []

    # Task registry — RomM 5.0+ (GET /api/tasks); 4.x doesn't have it.
    try:
        registry = await _get("tasks")
    except RuntimeError:
        registry = None
    if isinstance(registry, dict) and registry:
        lines.append("Registered tasks:")
        for group, tasks in registry.items():
            if not isinstance(tasks, list) or not tasks:
                continue
            lines.append(f"  [{group}]")
            for t in tasks:
                if not isinstance(t, dict):
                    continue
                name = t.get("name", "?")
                line = f"    {name} — {'enabled' if t.get('enabled') else 'disabled'}"
                if t.get("cron_string"):
                    line += f", cron {t['cron_string']}"
                line += (", manual run allowed" if t.get("manual_run")
                         else ", manual run not allowed")
                lines.append(line)
        lines.append("")

    data = await _get("tasks/status")

    if isinstance(data, dict) and data:
        lines.append("Task status:")
        for task_name, info in data.items():
            if isinstance(info, dict):
                status = info.get("status", "unknown")
                last_run = info.get("last_run", "")
                next_run = info.get("next_run", "")
                line = f"  {task_name}: {status}"
                if last_run:
                    line += f" (last: {last_run})"
                if next_run:
                    line += f" (next: {next_run})"
                lines.append(line)
            else:
                lines.append(f"  {task_name}: {info}")
    elif isinstance(data, list) and data:
        lines.append(f"Running/queued tasks ({len(data)}):")
        for t in data:
            if isinstance(t, dict):
                name = t.get("name", t.get("task_name", "Unknown"))
                status = t.get("status", "unknown")
                lines.append(f"  - {name}: {status}")
            else:
                lines.append(f"  - {t}")
    elif not lines:
        return "No task data available."
    else:
        lines.append("No tasks currently running.")

    return "\n".join(lines)


@mcp.tool()
async def romm_scan_library() -> str:
    """Trigger a library rescan to discover new ROMs and platforms.

    This is a background task — it returns immediately. New ROMs will appear
    in the library as the scan progresses.

    Note: RomM 5.0 marks scan_library as not manually runnable via the REST
    tasks API — on 5.0 instances this tool reports that instead of scanning.
    """
    try:
        data = await _post("tasks/run/scan_library", long_timeout=True)
        if isinstance(data, dict):
            job_id = data.get("task_id") or data.get("id", "")
            status = data.get("status", "started")
            return f"Library scan triggered (job: {job_id}, status: {status})."
        return "Library scan triggered."
    except RuntimeError as e:
        msg = str(e)
        if "cannot be run" in msg:
            return (
                "RomM refuses manually triggered library scans (scan_library is "
                "flagged manual_run=false on RomM 5.0). The library rescans on its "
                "configured schedule — check romm_tasks for the cron — or scan from "
                "the RomM web UI."
            )
        if "422" in msg or "not enabled" in msg.lower():
            return "Library scan task is not enabled in RomM settings. Enable scheduled rescan first."
        raise


# ── Tools — Write: play status & favorites ───────────────────────────────


_VALID_STATUSES = ("incomplete", "finished", "completed_100", "retired", "never_playing")


@mcp.tool()
async def romm_set_status(
    rom_id: int,
    status: str = "",
    backlogged: bool | None = None,
    now_playing: bool | None = None,
    rating: int = -1,
    completion: int = -1,
    mark_played: bool = False,
    clear_played: bool = False,
) -> str:
    """Set your personal play status on a ROM. Modifies only your own user data.

    Provide only the fields you want to change; omitted fields are left as-is.

    rom_id: The ROM's ID (from romm_search or romm_library_items).
    status: Play status — one of "incomplete", "finished", "completed_100",
            "retired", "never_playing". Empty = leave unchanged.
    backlogged: true/false to mark/unmark backlogged. Omit to leave unchanged.
    now_playing: true/false to mark/unmark as currently playing. Omit to leave unchanged.
    rating: 0-10 rating. -1 (default) = leave unchanged.
    completion: 0-100 percent complete. -1 (default) = leave unchanged.
    mark_played: Set the last-played timestamp to now.
    clear_played: Clear the last-played timestamp (mutually exclusive with mark_played).
    """
    if mark_played and clear_played:
        return "mark_played and clear_played are mutually exclusive."

    body: dict = {}
    if status:
        if status not in _VALID_STATUSES:
            return f"Invalid status '{status}'. Valid: {', '.join(_VALID_STATUSES)}."
        body["status"] = status
    if backlogged is not None:
        body["backlogged"] = backlogged
    if now_playing is not None:
        body["now_playing"] = now_playing
    if rating >= 0:
        if rating > 10:
            return "rating must be between 0 and 10."
        body["rating"] = rating
    if completion >= 0:
        if completion > 100:
            return "completion must be between 0 and 100."
        body["completion"] = completion

    if not body and not mark_played and not clear_played:
        return "Nothing to update — provide at least one field to change."

    params: dict = {}
    if mark_played:
        params["update_last_played"] = True
    if clear_played:
        params["remove_last_played"] = True

    data = await _put(f"roms/{rom_id}/props", body, params=params or None)

    if not isinstance(data, dict):
        return f"Updated ROM {rom_id}."

    parts = []
    if "status" in body:
        parts.append(f"status={data.get('status')}")
    if "backlogged" in body:
        parts.append(f"backlogged={data.get('backlogged')}")
    if "now_playing" in body:
        parts.append(f"now_playing={data.get('now_playing')}")
    if "rating" in body:
        parts.append(f"rating={data.get('rating')}")
    if "completion" in body:
        parts.append(f"completion={data.get('completion')}%")
    if mark_played:
        parts.append(f"last_played={data.get('last_played')}")
    if clear_played:
        parts.append("last_played cleared")

    detail = ", ".join(parts) if parts else "updated"
    return f"ROM {rom_id}: {detail}."


async def _favorite_collection() -> dict | None:
    """Return the user's favorites collection (is_favorite=True), or None."""
    data = await _get("collections")
    if isinstance(data, list):
        for c in data:
            if isinstance(c, dict) and c.get("is_favorite"):
                return c
    return None


@mcp.tool()
async def romm_favorite(rom_id: int, favorite: bool = True) -> str:
    """Add or remove a ROM from your Favorites.

    In RomM, favorites are a special collection. This finds (or creates) your
    favorites collection and adds/removes the ROM.

    rom_id: The ROM's ID.
    favorite: True to favorite (default), False to unfavorite.
    """
    fav = await _favorite_collection()

    if fav is None:
        if not favorite:
            return "You have no favorites collection yet — nothing to remove."
        created = await _post(
            "collections",
            data={"name": "Favourites", "description": ""},
            params={"is_favorite": True},
        )
        fav = created if isinstance(created, dict) and "id" in created else None
        if fav is None:
            return "Could not create a favorites collection."

    cid = fav["id"]
    if favorite:
        await _post(f"collections/{cid}/roms", {"rom_ids": [rom_id]})
        return f"Added ROM {rom_id} to Favorites."
    await _delete(f"collections/{cid}/roms", {"rom_ids": [rom_id]})
    return f"Removed ROM {rom_id} from Favorites."


# ── Tools — Write: notes ─────────────────────────────────────────────────


@mcp.tool()
async def romm_add_note(
    rom_id: int,
    title: str,
    content: str = "",
    tags: list[str] | None = None,
    is_public: bool = False,
) -> str:
    """Add a note to a ROM. The note belongs to you.

    rom_id: The ROM's ID.
    title: Note title (required).
    content: Note body in markdown (optional).
    tags: Optional list of tag strings.
    is_public: Whether other RomM users can see the note (default: false).
    """
    if not title.strip():
        return "title is required."
    body = {
        "title": title,
        "content": content,
        "is_public": is_public,
        "tags": tags or [],
    }
    data = await _post(f"roms/{rom_id}/notes", body)
    note_id = data.get("id", "?") if isinstance(data, dict) else "?"
    return f"Added note to ROM {rom_id} (note id: {note_id})."


@mcp.tool()
async def romm_update_note(
    rom_id: int,
    note_id: int,
    title: str = "",
    content: str = "",
    tags: list[str] | None = None,
    is_public: bool | None = None,
) -> str:
    """Edit an existing note. Only the fields you provide are changed.

    rom_id: The ROM's ID.
    note_id: The note's ID (from romm_rom_notes).
    title: New title (empty = unchanged).
    content: New body (empty = unchanged).
    tags: New tag list (omit to leave unchanged).
    is_public: New visibility (omit to leave unchanged).
    """
    body: dict = {}
    if title:
        body["title"] = title
    if content:
        body["content"] = content
    if tags is not None:
        body["tags"] = tags
    if is_public is not None:
        body["is_public"] = is_public

    if not body:
        return "Nothing to update — provide a field to change."

    await _put(f"roms/{rom_id}/notes/{note_id}", body)
    return f"Updated note {note_id} on ROM {rom_id}."


@mcp.tool()
async def romm_delete_note(rom_id: int, note_id: int) -> str:
    """Permanently delete a note. This cannot be undone.

    rom_id: The ROM's ID.
    note_id: The note's ID (from romm_rom_notes).
    """
    await _delete(f"roms/{rom_id}/notes/{note_id}")
    return f"Deleted note {note_id} from ROM {rom_id}."


# ── Tools — Write: collections ───────────────────────────────────────────


@mcp.tool()
async def romm_create_collection(name: str, description: str = "") -> str:
    """Create a new user collection.

    name: Collection name (required).
    description: Optional description.
    """
    if not name.strip():
        return "name is required."
    data = await _post("collections", data={"name": name, "description": description})
    if isinstance(data, dict) and "id" in data:
        return f"Created collection \"{data.get('name', name)}\" (id: {data['id']})."
    return f"Created collection \"{name}\"."


@mcp.tool()
async def romm_add_to_collection(collection_id: int, rom_ids: list[int]) -> str:
    """Add one or more ROMs to a collection (without replacing the existing list).

    collection_id: The collection's ID (from romm_collections).
    rom_ids: List of ROM IDs to add.
    """
    if not rom_ids:
        return "Provide at least one ROM ID."
    data = await _post(f"collections/{collection_id}/roms", {"rom_ids": rom_ids})
    count = len(data.get("rom_ids", [])) if isinstance(data, dict) else None
    suffix = f" Collection now has {count} ROMs." if count is not None else ""
    return f"Added {len(rom_ids)} ROM(s) to collection {collection_id}.{suffix}"


@mcp.tool()
async def romm_remove_from_collection(collection_id: int, rom_ids: list[int]) -> str:
    """Remove one or more ROMs from a collection (without deleting the collection).

    collection_id: The collection's ID (from romm_collections).
    rom_ids: List of ROM IDs to remove.
    """
    if not rom_ids:
        return "Provide at least one ROM ID."
    data = await _delete(f"collections/{collection_id}/roms", {"rom_ids": rom_ids})
    count = len(data.get("rom_ids", [])) if isinstance(data, dict) else None
    suffix = f" Collection now has {count} ROMs." if count is not None else ""
    return f"Removed {len(rom_ids)} ROM(s) from collection {collection_id}.{suffix}"


@mcp.tool()
async def romm_delete_collection(collection_id: int) -> str:
    """Permanently delete a collection. This cannot be undone.

    The ROMs themselves are not deleted — only the collection grouping.

    collection_id: The collection's ID (from romm_collections).
    """
    await _delete(f"collections/{collection_id}")
    return f"Deleted collection {collection_id}."


# ── Tools — Activity & Play Sessions (RomM 5.0+) ─────────────────────────


@mcp.tool()
async def romm_activity(rom_id: int = 0, limit: int = 20) -> str:
    """Recent play activity feed — who started playing what, when (RomM 5.0+).

    rom_id: Limit to one ROM's activity (0 = all).
    limit: Max entries shown (default 20).
    """
    path = f"activity/rom/{rom_id}" if rom_id else "activity"
    data = await _get(path)

    if not isinstance(data, list) or not data:
        qualifier = f" for ROM {rom_id}" if rom_id else ""
        return f"No play activity recorded{qualifier}."

    lines = [f"Play activity (showing {min(len(data), limit)} of {len(data)}):\n"]
    for a in data[:limit]:
        user = a.get("username", "?")
        rom = a.get("rom_name") or f"ROM {a.get('rom_id', '?')}"
        platform = a.get("platform_name") or a.get("platform_slug", "")
        started = a.get("started_at", "")

        line = f"  - {user} played {rom}"
        if platform:
            line += f" [{platform}]"
        lines.append(line)
        if started:
            lines.append(f"    Started: {started}")

    return "\n".join(lines)


@mcp.tool()
async def romm_play_sessions(rom_id: int = 0, limit: int = 20) -> str:
    """List your recorded play sessions — start time and duration (RomM 5.0+).

    rom_id: Filter to one ROM (0 = all).
    limit: Max sessions (default 20, max 100).
    """
    params: dict = {"limit": min(max(limit, 1), 100), "offset": 0}
    if rom_id:
        params["rom_id"] = rom_id

    data = await _get("play-sessions", params=params)

    if not isinstance(data, list) or not data:
        qualifier = f" for ROM {rom_id}" if rom_id else ""
        return f"No play sessions recorded{qualifier}."

    lines = [f"Play sessions ({len(data)}):\n"]
    for s in data:
        sid = s.get("id", "?")
        srom = s.get("rom_id", "?")
        start = s.get("start_time", "?")
        minutes = (s.get("duration_ms") or 0) / 60_000

        line = f"  [{sid}] ROM {srom} — {minutes:.0f} min, started {start}"
        if s.get("save_slot"):
            line += f", slot {s['save_slot']}"
        lines.append(line)

    return "\n".join(lines)


@mcp.tool()
async def romm_log_play_session(rom_id: int, duration_minutes: int,
                                ended_minutes_ago: int = 0) -> str:
    """Record a play session on a ROM (RomM 5.0+). Logs to your own history.

    rom_id: The ROM's ID.
    duration_minutes: How long the session lasted (1-1440).
    ended_minutes_ago: How many minutes ago it ended (default 0 = just now).
    """
    if not 1 <= duration_minutes <= 1440:
        return "duration_minutes must be between 1 and 1440."
    if ended_minutes_ago < 0:
        return "ended_minutes_ago must be >= 0."

    end = datetime.now(timezone.utc) - timedelta(minutes=ended_minutes_ago)
    start = end - timedelta(minutes=duration_minutes)
    body = {"sessions": [{
        "rom_id": rom_id,
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "duration_ms": duration_minutes * 60_000,
    }]}
    await _post("play-sessions", body)
    return (f"Logged a {duration_minutes}-minute play session on ROM {rom_id} "
            f"(ended {end.strftime('%Y-%m-%d %H:%M UTC')}).")


@mcp.tool()
async def romm_delete_play_session(session_id: int) -> str:
    """Permanently delete one of your play sessions. This cannot be undone.

    session_id: The session's ID (from romm_play_sessions).
    """
    await _delete(f"play-sessions/{session_id}")
    return f"Deleted play session {session_id}."


# ── Tools — Virtual & Smart Collections (RomM 5.0+) ──────────────────────


@mcp.tool()
async def romm_virtual_collections(collection_type: str = "collection",
                                   limit: int = 50) -> str:
    """List virtual collections — automatic groupings from metadata (RomM 5.0+).

    collection_type: Grouping to list — "collection", "genre", "franchise",
                     "company", "mode", or "all" (default "collection").
    limit: Max collections (default 50).
    """
    data = await _get("collections/virtual",
                      params={"type": collection_type,
                              "limit": min(max(limit, 1), 200)})

    if not isinstance(data, list) or not data:
        return f"No virtual collections of type \"{collection_type}\"."

    lines = [f"Virtual collections — {collection_type} ({len(data)}):\n"]
    for c in data:
        name = c.get("name", "Unknown")
        count = c.get("rom_count", 0)
        cid = c.get("id", "?")
        lines.append(f"  {name} ({count} ROM{'s' if count != 1 else ''})")
        lines.append(f"    ID: {cid}")

    return "\n".join(lines)


@mcp.tool()
async def romm_virtual_collection_detail(virtual_id: str) -> str:
    """List ROMs in a virtual collection.

    virtual_id: The virtual collection's ID string (from romm_virtual_collections).
    """
    data = await _get(f"collections/virtual/{virtual_id}")

    if not isinstance(data, dict) or "id" not in data:
        return f"Virtual collection {virtual_id} not found."

    name = data.get("name", "Unknown")
    rom_count = data.get("rom_count", 0)

    members = await _get(
        "roms",
        params={"virtual_collection_id": virtual_id, "limit": 50, "offset": 0,
                "order_by": "name", "order_dir": "asc"},
        long_timeout=True,
    )
    roms = members.get("items", []) if isinstance(members, dict) else members

    lines = [f"{name} ({data.get('type', 'virtual')})", f"  ROMs: {rom_count}\n"]
    if isinstance(roms, list):
        for i, rom in enumerate(roms[:50], 1):
            rom_name = rom.get("name", "Unknown")
            platform = rom.get("platform_display_name") or rom.get("platform_slug", "")
            line = f"  {i}. {rom_name}"
            if platform:
                line += f" [{platform}]"
            lines.append(line)
        if rom_count > 50:
            lines.append(f"\n  ({rom_count - 50} more not shown)")

    return "\n".join(lines)


@mcp.tool()
async def romm_smart_collection_detail(collection_id: int) -> str:
    """Show a smart collection's filter rules and matching ROMs.

    collection_id: The smart collection's ID (from romm_smart_collections).
    """
    data = await _get(f"collections/smart/{collection_id}")

    if not isinstance(data, dict) or "id" not in data:
        return f"Smart collection {collection_id} not found."

    name = data.get("name", "Unknown")
    desc = data.get("description", "")
    rom_count = data.get("rom_count", 0)

    lines = [name]
    if desc:
        lines.append(f"  {desc[:200]}")
    if data.get("filter_summary"):
        lines.append(f"  Rules: {data['filter_summary']}")
    elif data.get("filter_criteria"):
        lines.append(f"  Criteria: {jsonlib.dumps(data['filter_criteria'])[:200]}")
    lines.append(f"  ROMs: {rom_count}\n")

    members = await _get(
        "roms",
        params={"smart_collection_id": collection_id, "limit": 50, "offset": 0,
                "order_by": "name", "order_dir": "asc"},
        long_timeout=True,
    )
    roms = members.get("items", []) if isinstance(members, dict) else members
    if isinstance(roms, list):
        for i, rom in enumerate(roms[:50], 1):
            rom_name = rom.get("name", "Unknown")
            platform = rom.get("platform_display_name") or rom.get("platform_slug", "")
            line = f"  {i}. {rom_name}"
            if platform:
                line += f" [{platform}]"
            lines.append(line)
        if rom_count > 50:
            lines.append(f"\n  ({rom_count - 50} more not shown)")

    return "\n".join(lines)


# RomM stores filter_criteria as a schemaless dict and silently ignores unknown
# keys — a typo would create a filter that matches nothing it was meant to.
# These are the keys its smart-collection handler actually reads (RomM 5.0.0).
_SMART_FILTER_KEYS = {
    "platform_ids", "platform_id", "collection_id", "virtual_collection_id",
    "search_term", "matched", "favorite", "duplicate", "playable", "has_ra",
    "missing", "verified",
    "genres", "franchises", "collections", "companies", "age_ratings",
    "regions", "languages", "tags", "statuses", "metadata_providers",
    "genres_logic", "franchises_logic", "collections_logic", "companies_logic",
    "age_ratings_logic", "regions_logic", "languages_logic", "tags_logic",
    "statuses_logic", "metadata_providers_logic",
    "order_by", "order_dir",
}


def _validate_smart_criteria(criteria: dict | None) -> str | None:
    unknown = set(criteria or {}) - _SMART_FILTER_KEYS
    if unknown:
        return (f"Unknown filter_criteria key(s): {', '.join(sorted(unknown))}. "
                f"Valid keys: {', '.join(sorted(_SMART_FILTER_KEYS))}.")
    return None


@mcp.tool()
async def romm_create_smart_collection(name: str, description: str = "",
                                       filter_criteria: dict | None = None,
                                       is_public: bool = False) -> str:
    """Create a smart collection — a saved filter that auto-matches ROMs (RomM 5.0+).

    name: Collection name (required).
    description: Optional description.
    filter_criteria: Filter rules as a dict, using the same keys as the roms
                     list filters (e.g. {"platform_ids": [5], "genres": ["RPG"],
                     "search_term": "mario"}). Provide at least one rule.
    is_public: Visible to other users (default false).
    """
    if not name.strip():
        return "name is required."
    err = _validate_smart_criteria(filter_criteria)
    if err:
        return err
    data = await _post(
        "collections/smart",
        data={"name": name, "description": description,
              "filter_criteria": jsonlib.dumps(filter_criteria or {})},
        params={"is_public": is_public},
    )
    if isinstance(data, dict) and "id" in data:
        summary = data.get("filter_summary") or jsonlib.dumps(
            data.get("filter_criteria", {}))
        return (f"Created smart collection \"{data.get('name', name)}\" "
                f"(id: {data['id']}, matches {data.get('rom_count', '?')} ROMs, "
                f"rules: {summary[:150]}).")
    return f"Created smart collection \"{name}\"."


@mcp.tool()
async def romm_update_smart_collection(collection_id: int, name: str = "",
                                       description: str = "",
                                       filter_criteria: dict | None = None,
                                       is_public: bool | None = None) -> str:
    """Edit a smart collection. Only the fields you provide are changed.

    collection_id: The smart collection's ID (from romm_smart_collections).
    name: New name (empty = unchanged).
    description: New description (empty = unchanged).
    filter_criteria: New filter rules dict (omit to leave unchanged).
    is_public: New visibility (omit to leave unchanged).
    """
    err = _validate_smart_criteria(filter_criteria)
    if err:
        return err
    form: dict = {}
    if name:
        form["name"] = name
    if description:
        form["description"] = description
    if filter_criteria is not None:
        form["filter_criteria"] = jsonlib.dumps(filter_criteria)
    params = {"is_public": is_public} if is_public is not None else None

    if not form and params is None:
        return "Nothing to update — provide a field to change."

    await _request("PUT", f"collections/smart/{collection_id}",
                   data=form or None, params=params)
    return f"Updated smart collection {collection_id}."


@mcp.tool()
async def romm_delete_smart_collection(collection_id: int) -> str:
    """Permanently delete a smart collection. This cannot be undone.

    Only the saved filter is deleted — the ROMs it matched are untouched.

    collection_id: The smart collection's ID (from romm_smart_collections).
    """
    await _delete(f"collections/smart/{collection_id}")
    return f"Deleted smart collection {collection_id}."


# ── Tools — Identity & Metadata (RomM 5.0+) ──────────────────────────────


@mcp.tool()
async def romm_whoami() -> str:
    """Show the authenticated account — identity, role, and effective permissions."""
    me = await _get("users/me")

    lines = []
    if isinstance(me, dict) and me:
        lines.append(f"User: {me.get('username', '?')} (id {me.get('id', '?')})")
        if me.get("role"):
            lines.append(f"  Role: {me['role']}")
        if me.get("enabled") is not None:
            lines.append(f"  Enabled: {me['enabled']}")

    # Permissions engine is RomM 5.0+ — absent on 4.x.
    try:
        perms = await _get("permissions/me")
    except RuntimeError:
        perms = None
    if isinstance(perms, dict) and perms:
        if perms.get("is_admin"):
            lines.append("  Admin: yes (all permissions)")
        grants = perms.get("grants") or []
        if isinstance(grants, dict):
            grants = [k for k, v in grants.items() if v]
        # Live 5.0 returns grants as a list of objects, not strings — pull a
        # readable name from each rather than assuming they sort.
        names = []
        for g in grants:
            if isinstance(g, dict):
                label = str(g.get("action") or g.get("permission")
                            or g.get("name") or g.get("key") or g)
                scope = g.get("scope")
                if isinstance(scope, dict) and scope.get("kind") not in (None, "global"):
                    label += f" ({scope['kind']} {scope.get('id')})"
                names.append(label)
            else:
                names.append(str(g))
        if names:
            names.sort()
            shown = ", ".join(names[:25])
            if len(names) > 25:
                shown += f" (+{len(names) - 25} more)"
            lines.append(f"  Grants ({len(names)}): {shown}")
        hidden = perms.get("hidden")
        if isinstance(hidden, (list, dict)) and hidden:
            lines.append(f"  Hidden items: {len(hidden)}")

    return "\n".join(lines) if lines else "Could not read account info."


@mcp.tool()
async def romm_metadata_search(rom_id: int, search_term: str = "",
                               search_by: str = "name") -> str:
    """Search metadata providers (IGDB, MobyGames, ...) for matches for a ROM.

    Useful for identifying an unmatched ROM or checking alternate matches
    (RomM 5.0+). Read-only: it does not change the ROM's match.

    rom_id: The ROM to find matches for (required by RomM).
    search_term: Override the search text (default: the ROM's own name).
    search_by: "name" (default) or "id".
    """
    params: dict = {"rom_id": rom_id, "search_by": search_by}
    if search_term:
        params["search_term"] = search_term

    data = await _get("search/roms", params=params, long_timeout=True)

    if not isinstance(data, list) or not data:
        return "No metadata matches found."

    lines = [f"Metadata matches ({len(data)}):\n"]
    for m in data[:20]:
        name = m.get("name", "?")
        slug = m.get("slug", "")
        providers = [p for p in ("igdb", "moby", "launchbox", "flashpoint",
                                 "sgdb", "libretro")
                     if m.get(f"{p}_id")]
        line = f"  - {name}"
        if slug:
            line += f" ({slug})"
        if providers:
            line += f" — providers: {', '.join(providers)}"
        lines.append(line)
    if len(data) > 20:
        lines.append(f"\n  ({len(data) - 20} more not shown)")

    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
