"""Live smoke test for mcp-romm write path.

Exercises the ACTUAL server.py tool code against a live RomM instance.
Requires ROMM_URL / ROMM_USERNAME / ROMM_PASSWORD in the environment.

Goal: favorite "Ocarina of Time" on N64 and verify it stuck.
Run: python smoke_test.py
"""

import asyncio
import sys

import server


def _platform_is_n64(p: dict) -> bool:
    slug = (p.get("slug") or "").lower()
    name = (p.get("display_name") or p.get("name") or "").lower()
    return slug in ("n64", "nintendo-64") or "nintendo 64" in name or slug == "n64"


async def main() -> int:
    print("=== romm_status ===")
    print(await server.romm_status())

    print("\n=== find N64 platform ===")
    platforms = await server._get("platforms")
    n64 = next((p for p in platforms if isinstance(p, dict) and _platform_is_n64(p)), None)
    if not n64:
        print("FAIL: no N64 platform found. Platforms:",
              [p.get("slug") for p in platforms if isinstance(p, dict)])
        return 1
    n64_id = n64["id"]
    print(f"N64 platform: {n64.get('display_name') or n64.get('name')} (id={n64_id})")

    print("\n=== search Ocarina of Time on N64 ===")
    raw = await server._get("roms", params={
        "search_term": "Ocarina of Time", "platform_ids": n64_id,
        "limit": 25, "offset": 0, "order_by": "name", "order_dir": "asc",
    }, long_timeout=True)
    items = raw.get("items", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])
    if not items:
        print("FAIL: no OoT match on N64.")
        return 1
    for r in items:
        print(f"  - {r.get('name')} (id={r.get('id')}) [{r.get('platform_slug')}]")
    # Prefer an exact-ish match, else first result.
    target = next((r for r in items if "ocarina" in (r.get("name") or "").lower()), items[0])
    rom_id = target["id"]
    print(f"Target: {target.get('name')} (id={rom_id})")

    print("\n=== favorite it (romm_favorite) ===")
    print(await server.romm_favorite(rom_id, favorite=True))

    print("\n=== verify via favourites collection ===")
    fav = await server._favorite_collection()
    if not fav:
        print("FAIL: no favourites collection after favoriting.")
        return 1
    rom_ids = fav.get("rom_ids") or []
    print(f"Favourites collection '{fav.get('name')}' (id={fav.get('id')}) "
          f"now has {len(rom_ids)} ROMs.")
    if rom_id in rom_ids:
        print(f"PASS: ROM {rom_id} (Ocarina of Time / N64) is favorited.")
        return 0
    print(f"FAIL: ROM {rom_id} not in favourites rom_ids={sorted(rom_ids)[:20]}...")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
