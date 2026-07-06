# Changelog

## 1.1.0

RomM 5.0 readiness + save-sync fleet visibility.

### Added

- **`romm_save_timeline`** — per-ROM save revision timeline: content hashes,
  per-device sync attribution (RomM 4.9+ device save sync), broken-record and
  sidecar flagging, and a divergence warning when two devices currently hold
  different save content.
- **`romm_states`** — list save states (suspend points) by ROM or platform.
- **`ROMM_API_TOKEN`** — authenticate with a RomM client API token (`rmm_...`)
  instead of username/password; the direction the RomM ecosystem is moving.
- `romm_status` reports server version + capability flags, the auth mode in
  use, and scope degradation.

### Changed

- RomM 5.0's permissions engine can reject the default scope request at token
  time; the server now degrades to a read-only scope set instead of failing
  every tool (write tools then surface the server's permission error).

### Notes

- Verified against the 5.0.0-beta.1 API surface: no endpoint this server uses
  changed; the removed `/api/raw/assets` route was never used here. Re-verify
  at 5.0 stable.

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
