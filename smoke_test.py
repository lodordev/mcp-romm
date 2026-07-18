"""Live end-to-end test for mcp-romm — every tool against a real RomM instance.

Exercises the ACTUAL server.py tool code (all 19 read tools and all 9 write
tools) over HTTP against a live RomM. Write tests are self-cleaning: they
operate on a throwaway collection and note, and capture-and-restore any user
props they touch. The only lasting side effect is romm_scan_library, which
triggers the same background quick-scan the scheduler runs.

A tool that raises is recorded as FAIL and the suite continues — one broken
endpoint must not abort the run. Set ROMM_SMOKE_SKIP to a comma-separated
list of tool names to skip entirely (e.g. endpoints with known server-side
bugs, where even a failing call has side effects — RomM 5.0.0's
/api/roms/filters strands a runaway DB query per call).

Requires ROMM_URL / ROMM_USERNAME / ROMM_PASSWORD in the environment.
Run: python smoke_test.py
"""

import asyncio
import os
import sys
import time

import server

PASS, FAIL = 0, 0
SKIP = {s.strip() for s in os.getenv("ROMM_SMOKE_SKIP", "").split(",") if s.strip()}


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


async def tool_out(name: str, coro) -> str | None:
    """Await a tool call; record FAIL (and continue) if it raises, None if skipped."""
    if name in SKIP:
        coro.close()
        print(f"SKIP  {name} — via ROMM_SMOKE_SKIP")
        return None
    try:
        return await coro
    except Exception as e:
        check(name, False, f"raised: {e}")
        return None


async def read_tools() -> dict:
    """Run all 19 read tools; return context (ids) for the write tests."""
    ctx: dict = {}

    out = await tool_out("romm_status", server.romm_status())
    if out is not None:
        check("romm_status", "Connected: yes" in out, out)
        ctx["version"] = next((line.split(":", 1)[1].strip() for line in out.splitlines()
                               if "Version:" in line), "?")

    out = await tool_out("romm_stats", server.romm_stats())
    if out is not None:
        check("romm_stats", looks_ok(out) and "ROMs:" in out, out)

    out = await tool_out("romm_platforms", server.romm_platforms())
    if out is not None:
        check("romm_platforms", looks_ok(out) and "ID:" in out, out)

    out = await tool_out("romm_library_items", server.romm_library_items(limit=5))
    if out is not None:
        check("romm_library_items", looks_ok(out) and "ID:" in out, out)

    out = await tool_out("romm_recent", server.romm_recent(limit=5))
    if out is not None:
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

    out = await tool_out("romm_get_item", server.romm_get_item(ctx["rom_a"]))
    if out is not None:
        check("romm_get_item", looks_ok(out) and f"ID: {ctx['rom_a']}" in out, out)

    name_word = (items[0].get("name") or "a").split()[0]
    out = await tool_out("romm_search", server.romm_search(name_word, limit=5))
    if out is not None:
        check("romm_search", looks_ok(out) and "found" in out, out)

    # Hash search: find a ROM that actually has an md5.
    detail = await server._get(f"roms/{ctx['rom_a']}")
    md5 = detail.get("md5_hash") if isinstance(detail, dict) else None
    if md5:
        out = await tool_out("romm_search_by_hash",
                             server.romm_search_by_hash(md5_hash=md5))
        if out is not None:
            check("romm_search_by_hash", "Match found" in out, out)
    else:
        out = await tool_out("romm_search_by_hash",
                             server.romm_search_by_hash(md5_hash="0" * 32))
        if out is not None:
            check("romm_search_by_hash (no-match path)", "No ROM found" in out, out)

    out = await tool_out("romm_filters", server.romm_filters())
    if out is not None:
        check("romm_filters", looks_ok(out), out)

    out = await tool_out("romm_collections", server.romm_collections())
    if out is not None:
        check("romm_collections", looks_ok(out), out)
        # Rom-count regression: on 5.0 a collection with members must not
        # read "0 ROMs".
        cols = await server._get("collections")
        nonempty = next((c for c in cols
                         if isinstance(c, dict) and c.get("rom_count")), None)
        if nonempty:
            check("romm_collections shows real counts",
                  f"({nonempty['rom_count']} ROM" in out, out)
            out2 = await tool_out("romm_collection_detail",
                                  server.romm_collection_detail(nonempty["id"]))
            if out2 is not None:
                check("romm_collection_detail lists members",
                      looks_ok(out2) and f"ROMs: {nonempty['rom_count']}" in out2
                      and "1." in out2, out2)
        else:
            print("SKIP  collection count/detail checks — no non-empty collection")

    out = await tool_out("romm_smart_collections", server.romm_smart_collections())
    if out is not None:
        check("romm_smart_collections", looks_ok(out), out)

    out = await tool_out("romm_saves", server.romm_saves())
    if out is not None:
        check("romm_saves", looks_ok(out), out)

    out = await tool_out("romm_user_profile (favorites)", server.romm_user_profile())
    if out is not None:
        check("romm_user_profile (favorites)", looks_ok(out), out)

    out = await tool_out("romm_user_profile (backlog)",
                         server.romm_user_profile(status_filter="backlog"))
    if out is not None:
        check("romm_user_profile (backlog)", looks_ok(out), out)

    out = await tool_out("romm_rom_notes", server.romm_rom_notes(ctx["rom_a"]))
    if out is not None:
        check("romm_rom_notes", looks_ok(out), out)

    out = await tool_out("romm_firmware", server.romm_firmware())
    if out is not None:
        check("romm_firmware", looks_ok(out), out)

    out = await tool_out("romm_devices", server.romm_devices())
    if out is not None:
        check("romm_devices", looks_ok(out), out)

    out = await tool_out("romm_tasks", server.romm_tasks())
    if out is not None:
        check("romm_tasks", looks_ok(out), out)

    return ctx


async def test_set_status(rom_a: int) -> None:
    before = await server._get(f"roms/{rom_a}")
    prior = (before.get("rom_user") or {}) if isinstance(before, dict) else {}
    try:
        out = await server.romm_set_status(rom_a, status="finished", rating=8,
                                           backlogged=True)
        check("romm_set_status", "status=finished" in out and "rating=8" in out, out)
        after = await server._get(f"roms/{rom_a}")
        au = (after.get("rom_user") or {}) if isinstance(after, dict) else {}
        check("romm_set_status persisted",
              au.get("status") == "finished" and au.get("rating") == 8
              and au.get("backlogged") is True, str(au))
    except Exception as e:
        check("romm_set_status", False, f"raised: {e}")
    finally:
        # Restore the exact prior values (None clears).
        try:
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
        except Exception as e:
            check("romm_set_status restored", False, f"raised: {e}")


async def test_favorite(rom_a: int) -> None:
    try:
        fav_before = await server._favorite_collection()
        was_fav = bool(fav_before and rom_a in (fav_before.get("rom_ids") or []))
        out = await server.romm_favorite(rom_a, favorite=True)
        check("romm_favorite add", "Added" in out, out)
        fav_now = await server._favorite_collection()
        check("romm_favorite membership verified",
              bool(fav_now) and rom_a in (fav_now.get("rom_ids") or []),
              str(fav_now)[:200])
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
    except Exception as e:
        check("romm_favorite", False, f"raised: {e}")


async def test_notes(rom_a: int) -> None:
    note_id = None
    try:
        out = await server.romm_add_note(rom_a, title="mcp-e2e note",
                                         content="throwaway", tags=["e2e"])
        check("romm_add_note", "note id:" in out, out)
        notes = await server._get(f"roms/{rom_a}/notes")
        if isinstance(notes, list):
            note_id = next((n.get("id") for n in notes
                            if n.get("title") == "mcp-e2e note"), None)
        check("romm_rom_notes shows new note", note_id is not None, str(notes)[:200])
        if note_id is not None:
            out = await server.romm_update_note(rom_a, note_id, content="updated body")
            check("romm_update_note", "Updated note" in out, out)
    except Exception as e:
        check("romm notes flow", False, f"raised: {e}")
    finally:
        if note_id is not None:
            try:
                out = await server.romm_delete_note(rom_a, note_id)
                check("romm_delete_note", "Deleted note" in out, out)
                notes = await server._get(f"roms/{rom_a}/notes")
                gone = not any(n.get("id") == note_id for n in notes) \
                    if isinstance(notes, list) else True
                check("note cleanup verified", gone, str(notes)[:200])
            except Exception as e:
                check("romm_delete_note", False, f"raised: {e}")


async def test_collections(rom_a: int, rom_b: int) -> None:
    cname = f"mcp-e2e-{int(time.time())}"
    cid = None
    try:
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
            check("collection detail shows members", "ROMs: 2" in detail_out,
                  detail_out)
            out = await server.romm_remove_from_collection(cid, [rom_b])
            check("romm_remove_from_collection", "Removed 1 ROM(s)" in out, out)
    except Exception as e:
        check("romm collections flow", False, f"raised: {e}")
    finally:
        if cid is not None:
            try:
                out = await server.romm_delete_collection(cid)
                check("romm_delete_collection", "Deleted collection" in out, out)
                cols = await server._get("collections")
                gone = not any(isinstance(c, dict) and c.get("id") == cid
                               for c in cols)
                check("collection cleanup verified", gone, str(cols)[:200])
            except Exception as e:
                check("romm_delete_collection", False, f"raised: {e}")


async def write_tools(ctx: dict) -> None:
    """Run all 9 write tools with capture-and-restore / throwaway fixtures."""
    await test_set_status(ctx["rom_a"])
    await test_favorite(ctx["rom_a"])
    await test_notes(ctx["rom_a"])
    await test_collections(ctx["rom_a"], ctx["rom_b"])

    # scan_library: real trigger, background, same as the nightly cron.
    out = await tool_out("romm_scan_library", server.romm_scan_library())
    if out is not None:
        check("romm_scan_library", "triggered" in out or "not enabled" in out, out)


async def main() -> int:
    print(f"mcp-romm live e2e — {server.cfg.romm_url}\n")
    ctx = await read_tools()
    print(f"\n-- write tools (RomM {ctx.get('version', '?')}) --\n")
    await write_tools(ctx)
    print(f"\n{PASS} passed, {FAIL} failed"
          + (f", skipped: {', '.join(sorted(SKIP))}" if SKIP else ""))
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
