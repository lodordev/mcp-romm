"""Live end-to-end test for mcp-romm — every tool against a real RomM instance.

Exercises the ACTUAL server.py tool code (all 19 read tools and all 9 write
tools) over HTTP against a live RomM. Write tests are self-cleaning: they
operate on a throwaway collection and note, and capture-and-restore any user
props they touch. The only lasting side effect is romm_scan_library, which
triggers the same background quick-scan the scheduler runs.

Requires ROMM_URL / ROMM_USERNAME / ROMM_PASSWORD in the environment.
Run: python smoke_test.py
"""

import asyncio
import sys
import time

import server

PASS, FAIL = 0, 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"PASS  {name}")
    else:
        FAIL += 1
        print(f"FAIL  {name} — {detail[:300]}")


def looks_ok(out: str) -> bool:
    return not any(s in out for s in ("API error", "UNREACHABLE", "Request failed",
                                      "timed out", "auth failed"))


async def read_tools() -> dict:
    """Run all 19 read tools; return context (ids) for the write tests."""
    ctx: dict = {}

    out = await server.romm_status()
    check("romm_status", "Connected: yes" in out, out)
    ctx["version"] = next((line.split(":", 1)[1].strip() for line in out.splitlines()
                           if "Version:" in line), "?")

    out = await server.romm_stats()
    check("romm_stats", looks_ok(out) and "ROMs:" in out, out)

    out = await server.romm_platforms()
    check("romm_platforms", looks_ok(out) and "ID:" in out, out)

    out = await server.romm_library_items(limit=5)
    check("romm_library_items", looks_ok(out) and "ID:" in out, out)

    out = await server.romm_recent(limit=5)
    check("romm_recent", looks_ok(out) and "ID:" in out, out)

    # Pick two victim ROMs for the write tests, and capture full detail.
    raw = await server._get("roms", params={"limit": 2, "offset": 0,
                                            "order_by": "name", "order_dir": "asc"},
                            long_timeout=True)
    items = raw.get("items", []) if isinstance(raw, dict) else raw
    if len(items) < 2:
        print("ABORT: need at least 2 ROMs in the library for write tests.")
        sys.exit(2)
    ctx["rom_a"], ctx["rom_b"] = items[0]["id"], items[1]["id"]

    out = await server.romm_get_item(ctx["rom_a"])
    check("romm_get_item", looks_ok(out) and f"ID: {ctx['rom_a']}" in out, out)

    name_word = (items[0].get("name") or "a").split()[0]
    out = await server.romm_search(name_word, limit=5)
    check("romm_search", looks_ok(out) and "found" in out, out)

    # Hash search: find a ROM that actually has an md5.
    detail = await server._get(f"roms/{ctx['rom_a']}")
    md5 = detail.get("md5_hash") if isinstance(detail, dict) else None
    if md5:
        out = await server.romm_search_by_hash(md5_hash=md5)
        check("romm_search_by_hash", "Match found" in out, out)
    else:
        out = await server.romm_search_by_hash(md5_hash="0" * 32)
        check("romm_search_by_hash (no-match path)", "No ROM found" in out, out)

    out = await server.romm_filters()
    check("romm_filters", looks_ok(out), out)

    out = await server.romm_collections()
    check("romm_collections", looks_ok(out), out)
    # Rom-count regression: on 5.0 every collection must not read "0 ROMs"
    # if the server reports collections with members.
    cols = await server._get("collections")
    nonempty = next((c for c in cols if isinstance(c, dict) and c.get("rom_count")), None)
    if nonempty:
        check("romm_collections shows real counts",
              f"({nonempty['rom_count']} ROM" in out, out)
        out2 = await server.romm_collection_detail(nonempty["id"])
        check("romm_collection_detail lists members",
              looks_ok(out2) and f"ROMs: {nonempty['rom_count']}" in out2
              and "1." in out2, out2)
    else:
        print("SKIP  collection count/detail checks — no non-empty collection")

    out = await server.romm_smart_collections()
    check("romm_smart_collections", looks_ok(out), out)

    out = await server.romm_saves()
    check("romm_saves", looks_ok(out), out)

    out = await server.romm_user_profile()
    check("romm_user_profile (favorites)", looks_ok(out), out)

    out = await server.romm_user_profile(status_filter="backlog")
    check("romm_user_profile (backlog)", looks_ok(out), out)

    out = await server.romm_rom_notes(ctx["rom_a"])
    check("romm_rom_notes", looks_ok(out), out)

    out = await server.romm_firmware()
    check("romm_firmware", looks_ok(out), out)

    out = await server.romm_devices()
    check("romm_devices", looks_ok(out), out)

    out = await server.romm_tasks()
    check("romm_tasks", looks_ok(out), out)

    return ctx


async def write_tools(ctx: dict) -> None:
    """Run all 9 write tools with capture-and-restore / throwaway fixtures."""
    rom_a, rom_b = ctx["rom_a"], ctx["rom_b"]

    # ── set_status: capture, mutate, verify, restore ──
    before = await server._get(f"roms/{rom_a}")
    prior = (before.get("rom_user") or {}) if isinstance(before, dict) else {}
    out = await server.romm_set_status(rom_a, status="finished", rating=8,
                                       backlogged=True)
    check("romm_set_status", "status=finished" in out and "rating=8" in out, out)
    after = await server._get(f"roms/{rom_a}")
    au = (after.get("rom_user") or {}) if isinstance(after, dict) else {}
    check("romm_set_status persisted",
          au.get("status") == "finished" and au.get("rating") == 8
          and au.get("backlogged") is True, str(au))
    # Restore the exact prior values (None clears).
    await server._put(f"roms/{rom_a}/props", {
        "status": prior.get("status"),
        "rating": prior.get("rating"),
        "backlogged": prior.get("backlogged"),
    })
    restored = await server._get(f"roms/{rom_a}")
    ru = (restored.get("rom_user") or {}) if isinstance(restored, dict) else {}
    check("romm_set_status restored",
          ru.get("status") == prior.get("status")
          and ru.get("rating") == prior.get("rating")
          and ru.get("backlogged") == prior.get("backlogged"), str(ru))

    # ── favorite: add, verify, remove (only if it wasn't one before) ──
    fav_before = await server._favorite_collection()
    was_fav = bool(fav_before and rom_a in (fav_before.get("rom_ids") or []))
    out = await server.romm_favorite(rom_a, favorite=True)
    check("romm_favorite add", "Added" in out, out)
    fav_now = await server._favorite_collection()
    check("romm_favorite membership verified",
          bool(fav_now) and rom_a in (fav_now.get("rom_ids") or []), str(fav_now)[:200])
    item_out = await server.romm_get_item(rom_a)
    check("romm_get_item shows favorite", "Favorite: yes" in item_out, item_out)
    if not was_fav:
        out = await server.romm_favorite(rom_a, favorite=False)
        check("romm_favorite remove (cleanup)", "Removed" in out, out)
        fav_after = await server._favorite_collection()
        check("romm_favorite cleanup verified",
              not fav_after or rom_a not in (fav_after.get("rom_ids") or []),
              str(fav_after)[:200])
    else:
        print("SKIP  unfavorite — ROM was already a favorite before the test")

    # ── notes: add, list, update, delete ──
    out = await server.romm_add_note(rom_a, title="mcp-e2e note",
                                     content="throwaway", tags=["e2e"])
    check("romm_add_note", "note id:" in out, out)
    note_id = None
    notes = await server._get(f"roms/{rom_a}/notes")
    if isinstance(notes, list):
        note_id = next((n.get("id") for n in notes
                        if n.get("title") == "mcp-e2e note"), None)
    check("romm_rom_notes shows new note", note_id is not None, str(notes)[:200])
    if note_id is not None:
        out = await server.romm_update_note(rom_a, note_id, content="updated body")
        check("romm_update_note", "Updated note" in out, out)
        out = await server.romm_delete_note(rom_a, note_id)
        check("romm_delete_note", "Deleted note" in out, out)
        notes = await server._get(f"roms/{rom_a}/notes")
        gone = not any(n.get("id") == note_id for n in notes) \
            if isinstance(notes, list) else True
        check("note cleanup verified", gone, str(notes)[:200])

    # ── collections: create, add, detail, remove, delete ──
    cname = f"mcp-e2e-{int(time.time())}"
    out = await server.romm_create_collection(cname, description="throwaway")
    check("romm_create_collection", "Created collection" in out, out)
    cols = await server._get("collections")
    cid = next((c.get("id") for c in cols
                if isinstance(c, dict) and c.get("name") == cname), None)
    check("collection exists", cid is not None, str(cols)[:200])
    if cid is not None:
        out = await server.romm_add_to_collection(cid, [rom_a, rom_b])
        check("romm_add_to_collection", "Added 2 ROM(s)" in out, out)
        detail_out = await server.romm_collection_detail(cid)
        check("collection detail shows members", "ROMs: 2" in detail_out, detail_out)
        out = await server.romm_remove_from_collection(cid, [rom_b])
        check("romm_remove_from_collection", "Removed 1 ROM(s)" in out, out)
        out = await server.romm_delete_collection(cid)
        check("romm_delete_collection", "Deleted collection" in out, out)
        cols = await server._get("collections")
        gone = not any(isinstance(c, dict) and c.get("id") == cid for c in cols)
        check("collection cleanup verified", gone, str(cols)[:200])

    # ── scan_library: real trigger, background, same as the nightly cron ──
    out = await server.romm_scan_library()
    check("romm_scan_library", "triggered" in out or "not enabled" in out, out)


async def main() -> int:
    print(f"mcp-romm live e2e — {server.cfg.romm_url}\n")
    ctx = await read_tools()
    print(f"\n-- write tools (RomM {ctx.get('version')}) --\n")
    await write_tools(ctx)
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
