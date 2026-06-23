"""Unit tests for the RomM MCP server.

No live RomM instance required — HTTP is stubbed at the _request boundary, so
these tests verify tool logic, request shaping, scope hygiene, and formatting.

Run:
    uv run pytest test_server.py -v
"""

import os

import pytest

# Ensure import doesn't depend on real credentials.
os.environ.setdefault("ROMM_USERNAME", "tester")
os.environ.setdefault("ROMM_PASSWORD", "secret")

import server  # noqa: E402


class _Recorder(list):
    """A list of recorded requests that also carries a FIFO `responses` queue."""
    responses: list


@pytest.fixture
def calls(monkeypatch):
    """Capture every outbound request and feed canned responses.

    Append to `calls.responses` to pop return values FIFO; otherwise {} is returned.
    """
    recorded = _Recorder()
    recorded.responses = []

    async def fake_request(method, path, *, params=None, json=None, data=None,
                           long_timeout=False, auth_required=True):
        recorded.append({
            "method": method, "path": path, "params": params,
            "json": json, "data": data,
        })
        return recorded.responses.pop(0) if recorded.responses else {}

    monkeypatch.setattr(server, "_request", fake_request)
    return recorded


# ── Scope hygiene ─────────────────────────────────────────────────────────


def test_scopes_request_only_needed_writes():
    scopes = set(server._DEFAULT_SCOPES.split())
    # The two write scopes the tools actually use, plus task running.
    assert "roms.user.write" in scopes
    assert "collections.write" in scopes
    assert "tasks.run" in scopes


def test_scopes_exclude_dangerous_writes():
    scopes = set(server._DEFAULT_SCOPES.split())
    for forbidden in ("roms.write", "platforms.write", "firmware.write",
                      "assets.write", "users.write", "me.write", "devices.write"):
        assert forbidden not in scopes, f"{forbidden} should not be requested"


# ── Formatting helpers ──────────────────────────────────────────────────────


@pytest.mark.parametrize("n,expected", [
    (0, "0 B"),
    (None, "0 B"),
    (512, "512 B"),
    (2048, "2 KB"),
    (5 * 1024**2, "5 MB"),
    (3 * 1024**3, "3.0 GB"),
])
def test_fmt_size(n, expected):
    assert server._fmt_size(n) == expected


# ── romm_set_status ─────────────────────────────────────────────────────────


async def test_set_status_rejects_bad_status(calls):
    out = await server.romm_set_status(5, status="bogus")
    assert "Invalid status" in out
    assert not calls  # never hit the API


async def test_set_status_mutually_exclusive_played(calls):
    out = await server.romm_set_status(5, mark_played=True, clear_played=True)
    assert "mutually exclusive" in out
    assert not calls


async def test_set_status_requires_a_field(calls):
    out = await server.romm_set_status(5)
    assert "Nothing to update" in out
    assert not calls


async def test_set_status_builds_body_and_params(calls):
    calls.responses.append({"status": "finished", "backlogged": True,
                            "last_played": "2026-06-23T00:00:00Z"})
    out = await server.romm_set_status(
        7, status="finished", backlogged=True, mark_played=True
    )
    req = calls[0]
    assert req["method"] == "PUT"
    assert req["path"] == "roms/7/props"
    assert req["json"] == {"status": "finished", "backlogged": True}
    assert req["params"] == {"update_last_played": True}
    assert "finished" in out


async def test_set_status_rating_bounds(calls):
    out = await server.romm_set_status(7, rating=99)
    assert "0 and 10" in out
    assert not calls


# ── romm_favorite ─────────────────────────────────────────────────────────


async def test_favorite_adds_to_existing_collection(calls):
    calls.responses.append([{"id": 3, "name": "Favourites", "is_favorite": True}])
    out = await server.romm_favorite(42, favorite=True)
    # First call lists collections, second adds the rom.
    assert calls[0]["path"] == "collections"
    assert calls[1]["method"] == "POST"
    assert calls[1]["path"] == "collections/3/roms"
    assert calls[1]["json"] == {"rom_ids": [42]}
    assert "Added ROM 42" in out


async def test_favorite_creates_collection_when_missing(calls):
    calls.responses.append([])  # no collections
    calls.responses.append({"id": 9, "name": "Favourites", "is_favorite": True})  # create
    calls.responses.append({})  # add rom
    out = await server.romm_favorite(42)
    assert calls[1]["method"] == "POST"
    assert calls[1]["path"] == "collections"
    assert calls[1]["params"] == {"is_favorite": True}
    assert calls[2]["path"] == "collections/9/roms"
    assert "Added ROM 42" in out


async def test_unfavorite_uses_delete(calls):
    calls.responses.append([{"id": 3, "is_favorite": True}])
    out = await server.romm_favorite(42, favorite=False)
    assert calls[1]["method"] == "DELETE"
    assert calls[1]["path"] == "collections/3/roms"
    assert calls[1]["json"] == {"rom_ids": [42]}
    assert "Removed ROM 42" in out


async def test_unfavorite_with_no_collection(calls):
    calls.responses.append([])
    out = await server.romm_favorite(42, favorite=False)
    assert "nothing to remove" in out.lower()


# ── notes ──────────────────────────────────────────────────────────────────


async def test_add_note_requires_title(calls):
    out = await server.romm_add_note(5, title="   ")
    assert "title is required" in out
    assert not calls


async def test_add_note_posts_body(calls):
    calls.responses.append({"id": 11})
    out = await server.romm_add_note(5, title="Boss tips", content="hi", tags=["wip"])
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == "roms/5/notes"
    assert calls[0]["json"] == {"title": "Boss tips", "content": "hi",
                                "is_public": False, "tags": ["wip"]}
    assert "note id: 11" in out


async def test_update_note_only_sends_changed(calls):
    calls.responses.append({})
    await server.romm_update_note(5, 11, content="new body")
    assert calls[0]["method"] == "PUT"
    assert calls[0]["path"] == "roms/5/notes/11"
    assert calls[0]["json"] == {"content": "new body"}


async def test_update_note_noop(calls):
    out = await server.romm_update_note(5, 11)
    assert "Nothing to update" in out
    assert not calls


async def test_delete_note(calls):
    calls.responses.append({})
    out = await server.romm_delete_note(5, 11)
    assert calls[0]["method"] == "DELETE"
    assert calls[0]["path"] == "roms/5/notes/11"
    assert "Deleted note 11" in out


# ── collections ──────────────────────────────────────────────────────────────


async def test_create_collection_uses_form_data(calls):
    calls.responses.append({"id": 4, "name": "RPGs"})
    out = await server.romm_create_collection("RPGs", description="best")
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == "collections"
    assert calls[0]["data"] == {"name": "RPGs", "description": "best"}
    assert calls[0]["json"] is None
    assert "id: 4" in out


async def test_add_to_collection(calls):
    calls.responses.append({"rom_ids": [1, 2, 3]})
    out = await server.romm_add_to_collection(4, [2, 3])
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == "collections/4/roms"
    assert calls[0]["json"] == {"rom_ids": [2, 3]}
    assert "now has 3 ROMs" in out


async def test_remove_from_collection(calls):
    calls.responses.append({"rom_ids": [1]})
    out = await server.romm_remove_from_collection(4, [2, 3])
    assert calls[0]["method"] == "DELETE"
    assert calls[0]["path"] == "collections/4/roms"
    assert "now has 1 ROMs" in out


async def test_add_to_collection_empty(calls):
    out = await server.romm_add_to_collection(4, [])
    assert "at least one" in out.lower()
    assert not calls


async def test_delete_collection(calls):
    calls.responses.append({})
    out = await server.romm_delete_collection(4)
    assert calls[0]["method"] == "DELETE"
    assert calls[0]["path"] == "collections/4"
    assert "Deleted collection 4" in out
