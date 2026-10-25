# Changelog

## 1.4.0

RomM 5.3 and 5.4 parity — 34 new tools (92 total), three opt-in write tiers,
a read-only kill switch, and an output-hardening pass.

The RomM API stays purely additive from 5.2.0 to 5.4.1 (routes added, none
removed or changed), so every existing tool keeps working; the new ones are
version-gated and explain themselves on an older server.

### Added

- **Discovery (RomM 5.3+):** `romm_recommendations` (personal picks with
  reasons; `refresh` recomputes) and `romm_similar_roms`.
- **Memory cards (5.3+):** `romm_memory_cards`, `romm_memory_card` (with
  version history), `romm_rename_memory_card`,
  `romm_set_memory_card_visibility`, `romm_delete_memory_cards`.
  Uploading/downloading card images is deliberately not exposed.
- **Soundtracks (5.3+):** `romm_music_facets` gains `games`, `platforms` and
  `game-genres`; new `romm_music_stats`.
- **Streaming (5.3+/5.4+):** `romm_streaming_containers`,
  `romm_streaming_joinable`, `romm_streaming_join`, `romm_streaming_status`,
  `romm_streaming_swap_disc`, `romm_streaming_state_picks`.
- **Notifications (5.4+):** `romm_notifications`,
  `romm_mark_notifications_read`, `romm_dismiss_notifications`,
  `romm_send_notification` (own inbox only — recipient targeting is not
  exposed), `romm_notification_channels`, `romm_apprise_services`,
  `romm_create_notification_channel`, `romm_update_notification_channel`,
  `romm_delete_notification_channel`, `romm_test_notification_channel`,
  `romm_confirm_notification_channel`.
- **Devices (5.4+):** `romm_device_installs`, `romm_rom_installs`,
  `romm_install_on_device`, `romm_cancel_install`; `romm_devices` now marks
  online devices and install capability.
- **Saves and states:** `romm_states` (list save states), `romm_update_save`
  and `romm_update_state` (favourite / labels / rename, 5.4+);
  `romm_saves` shows favourite, labels, visibility and device-sync
  attribution.
- **Collections (5.4+):** `romm_set_collection_visibility`.
- **Audit (5.4+):** `romm_audit_events` — your own trail (everyone's for an
  admin), filterable by action, category, target, text and time window.
- **`romm_scan_library`** takes `scan_type` and `platform_ids` on 5.4+ via
  the new scan endpoint; older servers fall back to the task runner.
- **Config:** `ROMM_PASSWORD_FILE` (password from a file),
  `ROMM_READ_ONLY` (refuses every non-GET request inside the HTTP helper),
  `ROMM_WRITE_TIERS` (opt-in `notifications` → `me.write`, `devices` →
  `devices.write`, `assets` → `assets.write`; none requested by default).
  `romm_status` reports both.

### Security

- The three new write scopes are opt-in, per tier, and still negotiated —
  a tier the server or account refuses is dropped without taking down the
  rest, and the tools that need it say so.
- Free text from other users or external services (ROM names and
  summaries, notes, collection and playlist descriptions, notification
  bodies, labels, channel names, audit targets, usernames) is rendered
  through one helper that strips control, bidi, zero-width and Unicode tag
  characters, collapses newlines and caps length. Channel targets and
  secrets are masked and never echoed.
- Every caller-supplied URL path segment (platform slugs, device and
  request IDs, virtual-collection IDs) is percent-encoded, so an argument
  can't rewrite the route.
- `ROMM_READ_ONLY` also drops every `*.write` scope and `tasks.run` from
  the token request, not just the HTTP method.
- Webhook channels refuse loopback, private, link-local and bare internal
  hostnames; notification links must be a RomM path or http(s) URL.
- `ROMM_MCP_ALLOWED_HOSTS` turns on FastMCP's Host/Origin validation for
  the HTTP transport (DNS-rebinding defence); the README now states the
  browser exposure of an unguarded non-loopback bind.
- Independently reviewed (adversarial security read) before tagging; the
  findings above are the fixes.
- Destructive additions (`romm_delete_memory_cards`,
  `romm_dismiss_notifications`, `romm_delete_notification_channel`,
  `romm_cancel_install`) carry `destructiveHint`.

### Changed

- Verified live against RomM 5.4.1 as a least-privilege account (`smoke_test.py`:
  80 PASS). Shared memory cards need an `emulator` (RomM lists them per
  emulator); Apprise channels are admin-only server-side and the tools say
  so; a ROM whose platform has no streaming container gets a clear message
  from `romm_streaming_state_picks`.
- The ruff rule set is pinned in `pyproject.toml` so CI no longer drifts with
  ruff releases; the lint it keeps was fixed (exception chaining, ambiguous
  names, escaped invisibles in a test).
- `fastmcp` is now pinned `>=2.0.0,<4` — the server runs on both 2.x and
  3.x, and the upper bound keeps a rebuild from picking up a future major
  unreviewed.

## 1.3.0

RomM 5.1 and 5.2 coverage — 18 new tools (58 total), plus MCP annotations on
every tool and scope negotiation so a newer feature set can never take down an
older server.

The RomM API is purely additive from 5.0.0 to 5.2.0 (26 routes added, none
removed or changed), so nothing that worked before is affected.

### Added

- **Soundtracks (RomM 5.0+ / 5.1+):** `romm_music_tracks` (browse and filter
  the soundtrack library) and `romm_music_facets` (distinct artists, albums,
  genres, years with counts) work on 5.0+. On 5.1+: `romm_music_favorites`,
  `romm_music_favorite` (add/remove), `romm_music_playlists`,
  `romm_music_playlist` (details + tracks in order),
  `romm_create_music_playlist`, `romm_update_music_playlist`,
  `romm_delete_music_playlist`, and `romm_music_playlist_tracks`
  (add/remove/reorder).
- **Emulator streaming (RomM 5.1+):** `romm_streaming_config` (enabled?,
  platforms, per-platform save-slot capabilities), `romm_streaming_sessions`,
  `romm_streaming_start` (launch a ROM server-side, returns the stream URL),
  `romm_streaming_state` (save/load a state mid-game), `romm_streaming_audio`
  (volume, mute/toggle), and `romm_streaming_stop` (save-and-exit or plain
  release). This is the one cluster that reaches beyond your own account data:
  it starts and stops a server-side emulator container and can write that
  emulator's save states. It does not touch RomM's battery-save files.
- **Discovery:** `romm_random_rom` (RomM 5.2+) picks one ROM at random,
  optionally scoped to a platform or any collection type;
  `romm_platform_folders` (RomM 5.1+) lists platform folders on disk that RomM
  has never scanned — the usual reason a copied-in game doesn't appear.
- **MCP tool annotations on all 58 tools** — `readOnlyHint`,
  `destructiveHint`, `idempotentHint`, `openWorldHint` — so clients can
  distinguish a browse from a delete without parsing descriptions. 34 read-only,
  24 write, 6 flagged destructive.
- **Version gate.** New tools check the server version from `/api/heartbeat`
  (cached) and explain what they need on an older instance instead of
  returning a bare 404. The gate fails open when the version can't be read, so
  an unreadable heartbeat never refuses a call on a guess. `romm_status` now
  reports the version and a per-cluster capability list.

### Changed

- **Scopes are negotiated instead of assumed.** RomM rejects an entire token
  request with `403 Insufficient scope` when any one requested scope falls
  outside the account's set, so adding `playlists.*` naively would have broken
  *every* tool against a 5.0 server. Optional tiers (`tasks.run`, and the
  5.1-only `playlists.read`/`playlists.write`) are now probed once on first
  authentication and dropped if refused; the result is cached for the process
  so token expiry doesn't re-probe. This also fixes a latent problem: a
  least-privilege account without the admin-tier `tasks.run` previously failed
  authentication outright rather than losing just `romm_scan_library`.
  `romm_status` reports granted and dropped scopes.

### Not implemented (deliberate)

RomM 5.1 also added `DELETE /api/roms/{id}/files/{id}` and
`PUT /api/config/scan`. Deleting library files and rewriting server scan
configuration are both outside this server's boundary; no tool was added and
neither scope is requested.

### Verification

96 unit tests (up from 49), ruff clean. The 5.1/5.2 tools were built against
RomM's tagged source — routes, request bodies, response shapes and scope
requirements read from the 5.1.0 and 5.2.0 tags — and are **not yet verified
against a live 5.1/5.2 instance**.

## 1.2.1

### Added

- Optional HTTP transport for remote MCP clients: set `ROMM_MCP_TRANSPORT=http`
  (with `ROMM_MCP_HOST`/`ROMM_MCP_PORT`, default `127.0.0.1:8765`) to serve the
  MCP over Streamable HTTP instead of stdio — e.g. one shared instance on a
  homelab reachable from every machine. No auth of its own: bind to trusted
  networks only. Dockerfile documents the pattern and exposes 8765.

## 1.2.0

Full RomM 5.0 feature coverage — 12 new tools (40 total), all within the
existing OAuth scope set (no new permissions requested) and the same security
boundary: your own user data and collections only.

### Added

- **Play tracking (RomM 5.0+):** `romm_activity` (who played what, when),
  `romm_play_sessions` (your session history with durations),
  `romm_log_play_session` (record a session), `romm_delete_play_session`.
- **Virtual collections (RomM 5.0+):** `romm_virtual_collections` (automatic
  groupings by genre/franchise/collection/company/mode) and
  `romm_virtual_collection_detail`.
- **Smart collections (RomM 5.0+):** `romm_smart_collection_detail` (rules +
  matching ROMs), `romm_create_smart_collection`,
  `romm_update_smart_collection`, `romm_delete_smart_collection`. Filter
  criteria are validated against the key set RomM's handler actually reads —
  RomM itself silently ignores unknown keys, which would otherwise turn a typo
  into a filter that doesn't do what it says.
- **Identity:** `romm_whoami` — authenticated user, role, and (on 5.0) the
  effective permission grants from the new permissions engine.
- **Metadata:** `romm_metadata_search` — search IGDB/MobyGames/etc. for
  matches for a ROM (read-only; does not change the match).
- `romm_smart_collections` listing now shows ROM counts and rule summaries.

## 1.1.0

RomM 5.0 compatibility release, audited against a live 5.0.0 instance's
OpenAPI spec. All previously used endpoints, scopes, and the OAuth password
grant survive 5.0 unchanged; this release fixes the response-schema drift.

### Fixed (RomM 5.0 schema changes)

- `romm_collections` showed "0 ROMs" for every collection — 5.0 replaced the
  embedded `roms` list with `rom_count`/`rom_ids`. Now reads `rom_count`,
  falling back to the 4.x embedded list.
- `romm_collection_detail` listed no members on 5.0 — the collection detail no
  longer embeds ROMs. Members are now fetched via `GET /api/roms?collection_id=`
  (falls back to the 4.x embedded list).
- `romm_get_item` favorite flag — 5.0 dropped `rom_user.is_favorite`, and the
  `is_favorite` field embedded in `user_collections` is always null (verified
  live on 5.0.0). The flag is now derived by resolving the favorites collection
  and id-matching it in `user_collections` (falls back to the 4.x field).
- `romm_get_item` inline note — 5.0 dropped `rom_user.note_raw_markdown`; when
  `has_notes` is set, the output now points to `romm_rom_notes`.
- `romm_saves` and `romm_devices` displayed blank fields on 5.0 — saves now
  show emulator/slot/rom_id (5.0) alongside the 4.x rom_name/platform fields;
  devices show client/platform/sync state with the 4.x `type` fallback.

### Added

- `romm_search_by_hash` accepts `ra_hash` (RetroAchievements hash, RomM 5.0+).
- `romm_library_items` and `romm_search` report the library-wide `total` match
  count, not just the page size.
- `romm_tasks` now also lists the task registry (RomM 5.0's `GET /api/tasks`):
  each task's schedule, enabled state, and whether it can be run manually.
- `romm_scan_library` explains RomM 5.0's refusal of manually triggered scans
  (`scan_library` is `manual_run: false` there) instead of surfacing a raw 400.
- `smoke_test.py` is now a full live e2e suite: all 28 tools against a real
  instance, with self-cleaning write fixtures and a `ROMM_SMOKE_SKIP` knob for
  endpoints that are broken server-side. Running it against RomM 5.0.0 found
  three server-side bugs, documented in the README's Known issues section.

### Changed

- Documented prerequisites corrected: RomM 5.0+ (most read tools work on
  4.4+ — the `favorite` filter param only exists since 4.4.0), and an ordinary
  enabled user account (the old "admin role" claim was wrong). Documented RomM
  5.0's role collapse (`viewer`/`editor`/`admin` → `user`/`admin` + permissions
  engine) and its 403-troubleshooting implication.

## 1.0.0

First stable release. The server now both reads and writes: in addition to the
19 read tools, it can modify your own RomM user data and collections. Write
support has been smoke-tested end-to-end against a live RomM instance.

### Added

- **Write tools (9):**
  - `romm_set_status` — set play status (incomplete/finished/completed_100/
    retired/never_playing), backlog, now-playing, rating, completion, and the
    last-played timestamp.
  - `romm_favorite` — add/remove a ROM from your favorites (RomM models
    favorites as a collection; the tool finds or creates it).
  - `romm_add_note`, `romm_update_note`, `romm_delete_note` — manage your ROM notes.
  - `romm_create_collection`, `romm_add_to_collection`,
    `romm_remove_from_collection`, `romm_delete_collection` — manage collections.
- Test suite (`test_server.py`) and a CI workflow running ruff + pytest on 3.10 and 3.12.
- `[build-system]` and explicit `py-modules` in `pyproject.toml`.

### Changed

- **Least-privilege OAuth scopes.** The token now requests only the read scopes
  the tools use plus `roms.user.write`, `collections.write`, and `tasks.run`.
  Previously it requested a full write/edit scope set (`roms.write`,
  `platforms.write`, `users.write`, etc.) that no tool used.
- Security documentation rewritten to describe the actual write surface instead
  of claiming the server is fully read-only.

### Fixed

- `romm_rom_notes` read the wrong response fields (`raw_markdown`/`body`) and so
  showed no note text; it now reads `title`/`content` and shows author and
  visibility.

## 0.1.0

Initial release — 19 read-only tools for browsing platforms, searching ROMs,
viewing metadata, collections, saves, and tasks.
