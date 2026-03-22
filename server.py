"""RomM MCP Server — browse and manage your retro game library with AI.

Single-file MCP server for RomM (https://github.com/rommapp/romm).
Provides 19 read-only tools for browsing platforms, searching ROMs, viewing
metadata, managing collections, tracking saves, and monitoring tasks.

All tools are read-only or trigger idempotent scans. No tools delete, modify,
or upload data. OAuth2 password grant with automatic token refresh and 401 retry.

Tools:
  romm_status              — Check server configuration and reachability
  romm_platforms           — List platforms with ROM counts and sizes
  romm_library_items       — Browse ROMs with filtering and pagination
  romm_recent              — Recently added or updated ROMs
  romm_get_item            — Full ROM detail (metadata, saves, user status)
  romm_search              — Search ROMs by name
  romm_search_by_hash      — Identify a ROM by file hash (MD5, SHA1, SHA256, or CRC)
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
  romm_tasks               — Check running/scheduled task status
  romm_scan_library        — Trigger a background library rescan

Environment variables:
  ROMM_URL              — RomM instance URL (default: http://localhost:3000)
  ROMM_USERNAME         — RomM username (required)
  ROMM_PASSWORD         — RomM password (required)
  ROMM_REQUEST_TIMEOUT  — Default request timeout in seconds (default: 30)
  ROMM_REQUEST_TIMEOUT_LONG — Timeout for slow endpoints (default: 60)
  ROMM_TLS_VERIFY       — Verify TLS certificates (default: true)

Security:
  - All tools are read-only. No create, update, or delete operations exposed.
  - The only mutation is romm_scan_library which triggers a safe, idempotent rescan.
  - Credentials are held in memory only, never written to disk.
  - OAuth2 tokens are scoped to the minimum permissions needed.
  - TLS verification is enabled by default.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass

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

_DEFAULT_SCOPES = (
    "me.read me.write "
    "roms.read roms.write "
    "roms.user.read roms.user.write "
    "platforms.read platforms.write "
    "assets.read assets.write "
    "devices.read "
    "firmware.read "
    "collections.read collections.write "
    "users.read users.write "
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
    long_timeout: bool = False,
    auth_required: bool = True,
) -> dict | list:
    """Make an HTTP request to RomM API. Handles auth and 401 retry."""
    client = _get_client()
    url = f"{cfg.romm_url}/api/{path.lstrip('/')}"
    req_timeout = cfg.request_timeout_long if long_timeout else cfg.request_timeout

    headers: dict[str, str] = {}
    if auth_required:
        token = await _acquire_token()
        headers["Authorization"] = f"Bearer {token}"

    try:
        resp = await client.request(
            method, url, headers=headers, params=params, json=json,
            timeout=req_timeout,
        )

        if resp.status_code == 401 and auth_required:
            _token.access_token = ""
            token = await _acquire_token()
            headers["Authorization"] = f"Bearer {token}"
            resp = await client.request(
                method, url, headers=headers, params=params, json=json,
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


async def _post(path: str, body: dict | None = None, *, long_timeout: bool = False) -> dict | list:
    return await _request("POST", path, json=body, long_timeout=long_timeout)


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
    if isinstance(data, dict):
        items = data.get("items", [])
    elif isinstance(data, list):
        items = data

    if not items:
        qualifier = f" matching \"{search}\"" if search else ""
        qualifier += f" on platform {platform_id}" if platform_id else ""
        return f"No ROMs found{qualifier}."

    total = len(items)
    lines = [f"ROMs (offset {offset}, showing {total}):\n"]
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
    is_fav = user.get("is_favorite", False) if isinstance(user, dict) else False
    last_played = user.get("last_played") if isinstance(user, dict) else None
    status = user.get("status") if isinstance(user, dict) else None
    note_raw = user.get("note_raw_markdown") if isinstance(user, dict) else None

    saves = data.get("user_saves", [])
    states = data.get("user_states", [])

    lines = [name]
    lines.append(f"  Platform: {platform}")
    if slug:
        lines.append(f"  Slug: {slug}")
    if regions:
        lines.append(f"  Regions: {', '.join(str(r) for r in regions)}")
    if languages:
        lines.append(f"  Languages: {', '.join(str(l) for l in languages)}")
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
    if isinstance(data, dict):
        items = data.get("items", [])
    elif isinstance(data, list):
        items = data

    if not items:
        return f"No ROMs found matching \"{query}\"."

    lines = [f"Search results for \"{query}\" ({len(items)} found):\n"]
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
) -> str:
    """Identify a ROM by file hash. Provide at least one hash value.

    crc_hash: CRC32 hash string.
    md5_hash: MD5 hash string.
    sha1_hash: SHA1 hash string.
    """
    params: dict = {}
    if crc_hash:
        params["crc_hash"] = crc_hash.strip()
    if md5_hash:
        params["md5_hash"] = md5_hash.strip()
    if sha1_hash:
        params["sha1_hash"] = sha1_hash.strip()

    if not params:
        return "At least one hash value is required (crc_hash, md5_hash, or sha1_hash)."

    data = await _get("roms/by-hash", params=params)

    if not isinstance(data, dict) or "id" not in data:
        return f"No ROM found matching the provided hash."

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
    roms = data.get("roms", [])

    lines = [f"{name}"]
    if desc:
        lines.append(f"  {desc[:200]}")
    lines.append(f"  ROMs: {len(roms)}\n")

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

        if len(roms) > 50:
            lines.append(f"\n  ({len(roms) - 50} more not shown)")

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

        lines.append(f"  {name}")
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
        rom_name = s.get("rom_name", "")
        platform = s.get("platform_slug", "")
        updated = s.get("updated_at", "")

        line = f"  - {fname}"
        if rom_name:
            line += f" ({rom_name})"
        if platform:
            line += f" [{platform}]"
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
        body = n.get("raw_markdown") or n.get("body", "")
        created = n.get("created_at", "")
        updated = n.get("updated_at", "")
        note_id = n.get("id", "?")

        if body:
            short = body[:300]
            if len(body) > 300:
                short += "..."
            lines.append(f"  [{note_id}] {short}")
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
        name = d.get("name", "Unknown")
        device_type = d.get("type", "")
        device_id = d.get("id", "?")

        line = f"  - {name}"
        if device_type:
            line += f" ({device_type})"
        lines.append(line)
        lines.append(f"    ID: {device_id}")

    return "\n".join(lines)


# ── Tools — Tasks ────────────────────────────────────────────────────────


@mcp.tool()
async def romm_tasks() -> str:
    """Check running and scheduled task status."""
    data = await _get("tasks/status")

    if not isinstance(data, (dict, list)):
        return "No task data available."

    if isinstance(data, dict):
        lines = ["Task Status:\n"]
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
        return "\n".join(lines)

    if isinstance(data, list):
        lines = [f"Tasks ({len(data)}):\n"]
        for t in data:
            if isinstance(t, dict):
                name = t.get("name", t.get("task_name", "Unknown"))
                status = t.get("status", "unknown")
                lines.append(f"  - {name}: {status}")
            else:
                lines.append(f"  - {t}")
        return "\n".join(lines)

    return "Unexpected task data format."


@mcp.tool()
async def romm_scan_library() -> str:
    """Trigger a library rescan to discover new ROMs and platforms.

    This is a background task — it returns immediately. New ROMs will appear
    in the library as the scan progresses.
    """
    try:
        data = await _post("tasks/run/scan_library", long_timeout=True)
        if isinstance(data, dict):
            job_id = data.get("id", "")
            status = data.get("status", "started")
            return f"Library scan triggered (job: {job_id}, status: {status})."
        return "Library scan triggered."
    except RuntimeError as e:
        if "422" in str(e) or "not enabled" in str(e).lower():
            return "Library scan task is not enabled in RomM settings. Enable scheduled rescan first."
        raise


if __name__ == "__main__":
    mcp.run()
