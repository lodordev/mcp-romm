"""Unit tests for the RomM MCP server.

No live RomM instance required — HTTP is stubbed at the _request boundary, so
these tests verify tool logic, request shaping, scope hygiene, and formatting.

Run:
    uv run pytest test_server.py -v
"""

import json
import os

import httpx
import pytest

# Ensure import doesn't depend on real credentials.
os.environ.setdefault("ROMM_USERNAME", "tester")
os.environ.setdefault("ROMM_PASSWORD", "secret")

import server


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


# ── transport selection ──────────────────────────────────────────────────────


def test_transport_defaults_to_stdio(monkeypatch):
    monkeypatch.delenv("ROMM_MCP_TRANSPORT", raising=False)
    assert server._transport_config() == ("stdio", {})


def test_transport_http_reads_host_port(monkeypatch):
    monkeypatch.setenv("ROMM_MCP_TRANSPORT", "HTTP")
    monkeypatch.setenv("ROMM_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("ROMM_MCP_PORT", "9000")
    assert server._transport_config() == (
        "http", {"host": "0.0.0.0", "port": 9000, "host_origin_protection": "auto"})


# ── play sessions, smart/virtual collections, identity (v1.2) ────────────────


async def test_log_play_session_builds_payload(calls):
    calls.responses.append({})
    out = await server.romm_log_play_session(42, 30)
    req = calls[0]
    assert req["method"] == "POST"
    assert req["path"] == "play-sessions"
    sessions = req["json"]["sessions"]
    assert len(sessions) == 1
    assert sessions[0]["rom_id"] == 42
    assert sessions[0]["duration_ms"] == 30 * 60_000
    assert sessions[0]["start_time"] < sessions[0]["end_time"]
    assert "Logged a 30-minute" in out


async def test_log_play_session_bounds(calls):
    out = await server.romm_log_play_session(42, 0)
    assert "between 1 and 1440" in out
    assert not calls


async def test_delete_play_session(calls):
    calls.responses.append({})
    out = await server.romm_delete_play_session(7)
    assert calls[0]["method"] == "DELETE"
    assert calls[0]["path"] == "play-sessions/7"
    assert "Deleted play session 7" in out


async def test_activity_rom_scoped_path(calls):
    calls.responses.append([{"username": "j", "rom_name": "Chrono Trigger",
                             "platform_name": "SNES",
                             "started_at": "2026-07-18T00:00:00Z"}])
    out = await server.romm_activity(rom_id=5)
    assert calls[0]["path"] == "activity/rom/5"
    assert "j played Chrono Trigger [SNES]" in out


async def test_virtual_collections_params(calls):
    calls.responses.append([{"id": "abc123", "name": "Mario", "rom_count": 9}])
    out = await server.romm_virtual_collections("franchise")
    assert calls[0]["params"]["type"] == "franchise"
    assert "Mario (9 ROMs)" in out
    assert "abc123" in out


async def test_create_smart_collection_encodes_criteria(calls):
    calls.responses.append({"id": 3, "name": "SNES RPGs", "rom_count": 12,
                            "filter_summary": "platform SNES"})
    out = await server.romm_create_smart_collection(
        "SNES RPGs", filter_criteria={"platform_ids": [5], "genres": ["RPG"]})
    req = calls[0]
    assert req["path"] == "collections/smart"
    assert json.loads(req["data"]["filter_criteria"]) == {
        "platform_ids": [5], "genres": ["RPG"]}
    assert req["params"] == {"is_public": False}
    assert "id: 3" in out


async def test_create_smart_collection_rejects_unknown_keys(calls):
    out = await server.romm_create_smart_collection(
        "X", filter_criteria={"platfrom_ids": [5]})
    assert "Unknown filter_criteria key" in out
    assert "platfrom_ids" in out
    assert not calls


async def test_update_smart_collection_noop(calls):
    out = await server.romm_update_smart_collection(3)
    assert "Nothing to update" in out
    assert not calls


async def test_whoami_formats_identity_and_grants(calls):
    calls.responses.append({"id": 1, "username": "romm", "role": "admin",
                            "enabled": True})
    calls.responses.append({"is_admin": True, "grants": ["roms.read"],
                            "hidden": []})
    out = await server.romm_whoami()
    assert "User: romm" in out
    assert "Admin: yes" in out


async def test_whoami_handles_object_grants(calls):
    # Live RomM 5.0 returns grants as a list of objects — must not sort dicts.
    calls.responses.append({"id": 1, "username": "romm"})
    calls.responses.append({"is_admin": False, "grants": [
        {"action": "rom.view", "scope": {"kind": "global", "id": None}},
        {"action": "collection.edit", "scope": {"kind": "collection", "id": 4}}],
        "hidden": []})
    out = await server.romm_whoami()
    assert "collection.edit (collection 4), rom.view" in out
    assert "Grants (2)" in out


async def test_metadata_search_params(calls):
    calls.responses.append([{"name": "Super Mario 64", "slug": "sm64",
                             "igdb_id": 1074, "moby_id": None}])
    out = await server.romm_metadata_search(42)
    assert calls[0]["params"] == {"rom_id": 42, "search_by": "name"}
    assert "Super Mario 64" in out
    assert "igdb" in out


# ── tasks & scan ─────────────────────────────────────────────────────────────


async def test_tasks_lists_registry_and_status(calls):
    calls.responses.append({"scheduled": [
        {"name": "scan_library", "enabled": True, "manual_run": False,
         "cron_string": "0 3 * * *"}]})
    calls.responses.append([])
    out = await server.romm_tasks()
    assert "scan_library" in out
    assert "cron 0 3 * * *" in out
    assert "manual run not allowed" in out
    assert "No tasks currently running" in out


async def test_scan_library_manual_run_blocked(monkeypatch):
    async def raise_400(*a, **kw):
        raise RuntimeError(
            "API error 400: {\"detail\":\"Task 'scan_library' cannot be run\"}")
    monkeypatch.setattr(server, "_request", raise_400)
    out = await server.romm_scan_library()
    assert "refuses manually triggered" in out


# ── romm_get_item favorite detection ────────────────────────────────────────


async def test_get_item_favorite_via_collection_membership_on_50(calls):
    # 5.0: rom_user has no is_favorite, and user_collections embeds
    # is_favorite as null — favorite-ness comes from id-matching the
    # favorites collection.
    calls.responses.append({
        "id": 14376, "name": "Super Metroid", "rom_user": {"status": None},
        "user_collections": [{"id": 10, "name": "Favorites", "is_favorite": None}],
    })
    calls.responses.append([{"id": 10, "name": "Favorites", "is_favorite": True}])
    out = await server.romm_get_item(14376)
    assert calls[1]["path"] == "collections"
    assert "Favorite: yes" in out


async def test_get_item_not_favorite_when_not_member(calls):
    calls.responses.append({
        "id": 5, "name": "Beyond Oasis", "rom_user": {},
        "user_collections": [{"id": 9, "name": "Action RPG", "is_favorite": None}],
    })
    calls.responses.append([{"id": 10, "name": "Favorites", "is_favorite": True}])
    out = await server.romm_get_item(5)
    assert "Favorite: yes" not in out


async def test_get_item_favorite_legacy_4x_field_skips_lookup(calls):
    calls.responses.append({
        "id": 5, "name": "Chrono Trigger",
        "rom_user": {"is_favorite": True},
    })
    out = await server.romm_get_item(5)
    assert len(calls) == 1  # no collections lookup needed
    assert "Favorite: yes" in out


# ── collections ──────────────────────────────────────────────────────────────


async def test_collections_uses_rom_count_on_50(calls):
    calls.responses.append([{"id": 1, "name": "RPGs", "rom_count": 12}])
    out = await server.romm_collections()
    assert "RPGs (12 ROMs)" in out


async def test_collections_falls_back_to_embedded_roms_on_4x(calls):
    calls.responses.append([{"id": 1, "name": "RPGs", "roms": [{}, {}]}])
    out = await server.romm_collections()
    assert "RPGs (2 ROMs)" in out


async def test_collection_detail_fetches_members_on_50(calls):
    # 5.0: detail has rom_count/rom_ids but no embedded roms list.
    calls.responses.append({"id": 4, "name": "RPGs", "rom_count": 2, "rom_ids": [1, 2]})
    calls.responses.append({"items": [{"name": "Chrono Trigger", "platform_slug": "snes"},
                                      {"name": "Earthbound"}], "total": 2})
    out = await server.romm_collection_detail(4)
    assert calls[1]["path"] == "roms"
    assert calls[1]["params"]["collection_id"] == 4
    assert "ROMs: 2" in out
    assert "Chrono Trigger" in out


async def test_collection_detail_uses_embedded_roms_on_4x(calls):
    calls.responses.append({"id": 4, "name": "RPGs",
                            "roms": [{"name": "Chrono Trigger"}]})
    out = await server.romm_collection_detail(4)
    assert len(calls) == 1  # no second fetch needed
    assert "Chrono Trigger" in out


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


# ── Version parsing & capability gate ────────────────────────────────────


@pytest.fixture
def fresh_version(monkeypatch):
    """Reset the cached server version so each gate test starts clean."""
    monkeypatch.setattr(server, "_version", server._VersionState())
    monkeypatch.setattr(server, "_scopes", server._ScopeState())
    return server


def test_parse_version_handles_plain_prefixed_and_prerelease():
    assert server._parse_version("5.2.0") == (5, 2, 0)
    assert server._parse_version("v5.1.0") == (5, 1, 0)
    # A pre-release already carries that version's endpoints, so it sorts as the base.
    assert server._parse_version("5.1.0-beta.2") == (5, 1, 0)
    assert server._parse_version("5.1") == (5, 1, 0)
    assert server._parse_version("unknown") is None
    assert server._parse_version("") is None


async def test_version_gate_blocks_older_server(fresh_version, calls):
    calls.responses.append({"SYSTEM": {"VERSION": "5.0.0"}})
    msg = await server._version_gate(server._V_5_1, "Music playlists")
    assert msg is not None
    assert "5.1.0 or newer" in msg
    assert "5.0.0" in msg


async def test_version_gate_allows_newer_server(fresh_version, calls):
    calls.responses.append({"SYSTEM": {"VERSION": "5.2.0"}})
    assert await server._version_gate(server._V_5_1, "Music playlists") is None


async def test_version_gate_fails_open_when_version_unreadable(fresh_version, calls):
    """An unreadable version must not refuse the call on a guess."""
    calls.responses.append({"SYSTEM": {}})
    assert await server._version_gate(server._V_5_2, "Random ROM pick") is None


async def test_version_is_cached_after_first_read(fresh_version, calls):
    calls.responses.append({"SYSTEM": {"VERSION": "5.2.0"}})
    await server._server_version()
    await server._server_version()
    assert len(calls) == 1


async def test_music_playlists_blocked_on_50_server(fresh_version, calls):
    calls.responses.append({"SYSTEM": {"VERSION": "5.0.0"}})
    out = await server.romm_music_playlists()
    assert "5.1.0 or newer" in out
    # Only the heartbeat went out — no doomed request to the missing endpoint.
    assert [c["path"] for c in calls] == ["heartbeat"]


async def test_random_rom_blocked_on_51_server(fresh_version, calls):
    calls.responses.append({"SYSTEM": {"VERSION": "5.1.0"}})
    out = await server.romm_random_rom()
    assert "5.2.0 or newer" in out
    assert [c["path"] for c in calls] == ["heartbeat"]


def test_scope_gate_silent_before_negotiation(fresh_version):
    """Before negotiation runs, the gate must not refuse on stale state."""
    assert server._scope_gate("playlists", "Music playlists") is None


def test_scope_gate_speaks_once_tier_is_known_missing(fresh_version):
    server._scopes.negotiated = True
    server._scopes.dropped_tiers = ("playlists",)
    msg = server._scope_gate("playlists", "Music playlists")
    assert msg is not None and "playlists scope" in msg


# ── Scope negotiation ────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = "Insufficient scope" if status_code == 403 else "ok"

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("err", request=None, response=self)  # type: ignore


@pytest.fixture
def grants(monkeypatch):
    """Stub the password grant, recording every scope string requested.

    `grants.allow` is the set of scopes the fake server will accept; a request
    for anything outside it 403s, exactly as RomM's token endpoint does.
    """
    recorded = []

    class _Grants:
        allow: set = set()
        calls = recorded

    state = _Grants()

    async def fake_grant(scope):
        recorded.append(scope)
        if set(scope.split()) <= state.allow:
            return _FakeResponse(200, {"access_token": "tok", "expires": 1800})
        return _FakeResponse(403)

    monkeypatch.setattr(server, "_password_grant", fake_grant)
    monkeypatch.setattr(server, "_token", server._TokenState())
    monkeypatch.setattr(server, "_scopes", server._ScopeState())
    return state


async def test_negotiation_keeps_everything_when_server_grants_all(grants):
    grants.allow = set(server._DEFAULT_SCOPES.split())
    token = await server._acquire_token()
    assert token == "tok"
    assert len(grants.calls) == 1  # no probing needed
    assert server._scopes.dropped_tiers == ()
    assert "playlists.read" in server._scopes.granted


async def test_negotiation_drops_playlists_on_50_server(grants):
    """A 5.0 server has no playlists.* scope — dropping it must not break the rest."""
    grants.allow = set(server._CORE_SCOPES) | {"tasks.run"}
    token = await server._acquire_token()
    assert token == "tok"
    assert server._scopes.dropped_tiers == ("playlists",)
    assert "tasks.run" in server._scopes.granted
    assert "playlists.read" not in server._scopes.granted


async def test_negotiation_drops_tasks_for_least_privilege_account(grants):
    """tasks.run is admin-tier; a scoped service account keeps everything else."""
    grants.allow = set(server._CORE_SCOPES)
    token = await server._acquire_token()
    assert token == "tok"
    assert set(server._scopes.dropped_tiers) == {"tasks", "playlists"}
    assert set(server._scopes.granted) == set(server._CORE_SCOPES)


async def test_negotiation_surfaces_real_auth_failure(grants):
    """A bad password is not a scope problem — don't mask it by degrading."""
    grants.allow = set()
    with pytest.raises(RuntimeError, match="auth failed"):
        await server._acquire_token()


async def test_negotiation_runs_once_then_reuses_result(grants):
    grants.allow = set(server._CORE_SCOPES)
    await server._acquire_token()
    first = len(grants.calls)
    server._token.access_token = ""  # force re-auth, e.g. token expiry
    await server._acquire_token()
    # Re-auth is a single grant with the already-negotiated set, not a re-probe.
    assert len(grants.calls) == first + 1
    assert grants.calls[-1] == " ".join(server._CORE_SCOPES)


async def test_negotiation_never_requests_dangerous_write_scopes(grants):
    grants.allow = set(server._DEFAULT_SCOPES.split())
    await server._acquire_token()
    for scope_string in grants.calls:
        requested = set(scope_string.split())
        assert not requested & {"roms.write", "platforms.write", "firmware.write",
                               "assets.write", "users.write", "users.read",
                               "me.write", "devices.write"}


# ── New tools: request shaping ───────────────────────────────────────────


@pytest.fixture
def modern(monkeypatch):
    """Pretend the server is 5.2.0 so version gates pass without a heartbeat."""
    monkeypatch.setattr(
        server, "_version",
        server._VersionState(checked=True, raw="5.2.0", parsed=(5, 2, 0)))
    monkeypatch.setattr(server, "_scopes", server._ScopeState())


async def test_music_tracks_shapes_filters(modern, calls):
    calls.responses.append({"items": [], "total": 0})
    await server.romm_music_tracks(search="zelda", artist="Koji Kondo",
                                   platform_id=3, year=1998, limit=500)
    p = calls[0]["params"]
    assert calls[0]["path"] == "music/tracks"
    assert p["search"] == "zelda"
    assert p["artist"] == "Koji Kondo"
    assert p["platform_ids"] == 3
    assert p["year"] == 1998
    assert p["limit"] == 200  # clamped


async def test_music_tracks_omits_unset_filters(modern, calls):
    calls.responses.append({"items": [], "total": 0})
    await server.romm_music_tracks()
    assert set(calls[0]["params"]) == {"order_by", "order_dir", "limit", "offset"}


async def test_music_tracks_formats_a_track(modern, calls):
    calls.responses.append({"items": [{
        "rom_file_id": 77, "rom_id": 5, "title": "Overworld",
        "artist": "Koji Kondo", "album": "OST", "year": 1986,
        "duration_seconds": 95, "is_favorite": True,
        "game_name": "Zelda", "platform_name": "NES",
    }], "total": 1})
    out = await server.romm_music_tracks()
    assert "Overworld" in out
    assert "Koji Kondo" in out
    assert "1:35" in out            # duration formatted
    assert "track file id: 77" in out


async def test_music_favorite_add_and_remove(modern, calls):
    calls.responses.append({"added": 2})
    out = await server.romm_music_favorite([1, 2])
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == "music/favorites"
    assert calls[0]["json"] == {"rom_file_ids": [1, 2]}
    assert "Added 2" in out

    calls.responses.append({"removed": 1})
    out = await server.romm_music_favorite([1], action="remove")
    assert calls[1]["method"] == "DELETE"
    assert "Removed 1" in out


async def test_music_favorite_rejects_bad_action(modern, calls):
    out = await server.romm_music_favorite([1], action="toggle")
    assert "add" in out and "remove" in out
    assert not calls


async def test_music_favorite_rejects_empty_ids(modern, calls):
    out = await server.romm_music_favorite([])
    assert "at least one" in out
    assert not calls


async def test_create_music_playlist_uses_json_body(modern, calls):
    calls.responses.append({"id": 9, "name": "Chill"})
    out = await server.romm_create_music_playlist("Chill", description="calm")
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == "music/playlists"
    assert calls[0]["json"] == {"name": "Chill", "is_public": False,
                                "description": "calm"}
    assert "id: 9" in out


async def test_update_music_playlist_sends_only_changed_fields(modern, calls):
    calls.responses.append({})
    await server.romm_update_music_playlist(9, name="New")
    assert calls[0]["method"] == "PUT"
    assert calls[0]["json"] == {"name": "New"}


async def test_update_music_playlist_noop_makes_no_request(modern, calls):
    out = await server.romm_update_music_playlist(9)
    assert "Nothing to update" in out
    assert not calls


async def test_playlist_tracks_reorder_uses_order_endpoint(modern, calls):
    calls.responses.append({})
    out = await server.romm_music_playlist_tracks(9, [3, 1, 2], action="reorder")
    assert calls[0]["method"] == "PUT"
    assert calls[0]["path"] == "music/playlists/9/tracks/order"
    assert calls[0]["json"] == {"rom_file_ids": [3, 1, 2]}
    assert "Reordered 3" in out


async def test_playlist_tracks_remove_uses_delete(modern, calls):
    calls.responses.append({"removed": 2})
    await server.romm_music_playlist_tracks(9, [1, 2], action="remove")
    assert calls[0]["method"] == "DELETE"
    assert calls[0]["path"] == "music/playlists/9/tracks"


async def test_music_facets_rejects_unknown_field(modern, calls):
    out = await server.romm_music_facets(field="composers")
    assert "artists" in out and "albums" in out
    assert not calls


async def test_music_facets_queries_the_right_endpoint(modern, calls):
    calls.responses.append({"items": [{"value": "Nobuo Uematsu", "count": 42}],
                            "total": 1})
    out = await server.romm_music_facets(field="artists")
    assert calls[0]["path"] == "music/artists"
    assert "Nobuo Uematsu" in out and "42 track(s)" in out


async def test_streaming_config_reports_disabled(modern, calls):
    calls.responses.append({"enabled": False, "containers": []})
    out = await server.romm_streaming_config()
    assert "disabled" in out.lower()


async def test_streaming_config_lists_slot_capabilities(modern, calls):
    calls.responses.append({"enabled": True, "containers": [
        {"platform": "ps2", "host": "http://box:3000", "label": "PS2",
         "capabilities": {"max_slots": 9, "has_autosave": True, "autosave_slot": 10}},
    ]})
    out = await server.romm_streaming_config()
    assert "save slots 1-9" in out
    assert "autosave slot 10" in out


async def test_streaming_start_posts_rom_id(modern, calls):
    calls.responses.append({"platform": "ps2", "host": "http://box:3000",
                            "label": "PS2", "rom_name": "SotC",
                            "claimed_at": "2026-08-30T00:00:00Z"})
    out = await server.romm_streaming_start(12)
    assert calls[0]["method"] == "POST"
    assert calls[0]["path"] == "streaming/sessions"
    assert calls[0]["json"] == {"rom_id": 12}
    assert "SotC" in out and "http://box:3000" in out


async def test_streaming_start_explains_conflict(modern, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("API error 409: Session in use")
    monkeypatch.setattr(server, "_request", boom)
    out = await server.romm_streaming_start(12)
    assert "already in use" in out


async def test_streaming_state_load_uses_load_endpoint(modern, calls):
    calls.responses.append({"status": "ok", "loaded": True})
    out = await server.romm_streaming_state("ps2", action="load", slot=3)
    assert calls[0]["path"] == "streaming/sessions/ps2/load-state"
    assert calls[0]["json"] == {"slot": 3}
    assert "Loaded from slot 3" in out


async def test_streaming_state_explains_bad_slot(modern, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("API error 422: Slot 9 is not available")
    monkeypatch.setattr(server, "_request", boom)
    out = await server.romm_streaming_state("ngc", slot=9)
    assert "not available" in out
    assert "romm_streaming_config" in out


async def test_streaming_audio_toggles_mute_when_nothing_given(modern, calls):
    calls.responses.append({"status": "ok", "mute": True})
    out = await server.romm_streaming_audio("ps2")
    assert calls[0]["path"] == "streaming/sessions/ps2/mute"
    assert calls[0]["json"] == {}
    assert "mute is now True" in out


async def test_streaming_audio_rejects_out_of_range_volume(modern, calls):
    out = await server.romm_streaming_audio("ps2", volume=150)
    assert "between 0 and 100" in out
    assert not calls


async def test_streaming_stop_saves_by_default(modern, calls):
    calls.responses.append({"saved": True})
    out = await server.romm_streaming_stop("ps2")
    assert calls[0]["path"] == "streaming/sessions/ps2/save-and-exit"
    assert calls[0]["json"] == {"slot": 0, "wait": True}
    assert "state saved" in out


async def test_streaming_stop_flags_unconfirmed_save(modern, calls):
    """A save the emulator didn't confirm must not read as a clean stop."""
    calls.responses.append({"saved": False})
    out = await server.romm_streaming_stop("ps2")
    assert "did NOT confirm a save" in out


async def test_streaming_stop_without_saving_uses_delete(modern, calls):
    calls.responses.append({"status": "released"})
    out = await server.romm_streaming_stop("ps2", save_first=False)
    assert calls[0]["method"] == "DELETE"
    assert calls[0]["path"] == "streaming/sessions/ps2"
    assert "without saving" in out


async def test_streaming_sessions_explains_admin_only(modern, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("API error 403: Forbidden")
    monkeypatch.setattr(server, "_request", boom)
    out = await server.romm_streaming_sessions()
    assert "admin-only" in out


async def test_random_rom_passes_scope_filters(modern, calls):
    calls.responses.append({"id": 3, "name": "Ico", "platform_slug": "ps2"})
    out = await server.romm_random_rom(platform_id=7, collection_id=2)
    assert calls[0]["path"] == "roms/random"
    assert calls[0]["params"] == {"platform_ids": 7, "collection_id": 2}
    assert "Ico" in out


async def test_random_rom_handles_empty_scope(modern, calls):
    calls.responses.append({})
    out = await server.romm_random_rom(platform_id=99)
    assert "No ROM matched" in out


async def test_platform_folders_reports_none_clearly(modern, calls):
    calls.responses.append([])
    out = await server.romm_platform_folders()
    assert "No unscanned platform folders" in out


async def test_platform_folders_lists_unscanned(modern, calls):
    calls.responses.append([{"name": "Dreamcast", "fs_slug": "dc", "slug": "dc"}])
    out = await server.romm_platform_folders()
    assert "Dreamcast" in out
    assert "romm_scan_library" in out


# ── Tool annotations ─────────────────────────────────────────────────────


async def test_every_tool_carries_annotations():
    tools = await server.mcp._list_tools()
    bare = [t.name for t in tools
            if t.annotations is None or t.annotations.readOnlyHint is None]
    assert bare == []


async def test_deleting_tools_are_marked_destructive():
    tools = {t.name: t.annotations for t in await server.mcp._list_tools()}
    for name in ("romm_delete_note", "romm_delete_collection",
                 "romm_delete_play_session", "romm_delete_smart_collection",
                 "romm_delete_music_playlist", "romm_streaming_stop"):
        assert tools[name].destructiveHint is True, name


async def test_read_tools_are_not_marked_writable():
    tools = {t.name: t.annotations for t in await server.mcp._list_tools()}
    for name in ("romm_search", "romm_music_tracks", "romm_random_rom",
                 "romm_streaming_config", "romm_platform_folders"):
        assert tools[name].readOnlyHint is True, name


# ══ 1.4.0 — RomM 5.3/5.4 parity ═══════════════════════════════════════════


@pytest.fixture
def v54(monkeypatch):
    """Pretend the server is RomM 5.4.1 so version gates open."""
    monkeypatch.setattr(server, "_version", server._VersionState(checked=True, raw="5.4.1", parsed=(5, 4, 1)))


@pytest.fixture
def v52(monkeypatch):
    monkeypatch.setattr(server, "_version", server._VersionState(checked=True, raw="5.2.0", parsed=(5, 2, 0)))


@pytest.fixture
def tiers_on(monkeypatch):
    """Enable every opt-in write tier and mark its scope as granted."""
    monkeypatch.setattr(server, "cfg", server.Config(
        romm_url="http://x", romm_username="t", romm_password="t", request_timeout=1,
        request_timeout_long=1, tls_verify=True, read_only=False,
        write_tiers=("notifications", "devices", "assets")))
    monkeypatch.setattr(server, "_scopes", server._ScopeState(negotiated=True, granted=("me.read",), dropped_tiers=()))


@pytest.fixture
def read_only(monkeypatch):
    monkeypatch.setattr(server, "cfg", server.Config(
        romm_url="http://x", romm_username="t", romm_password="t", request_timeout=1,
        request_timeout_long=1, tls_verify=True, read_only=True, write_tiers=()))


# ── Config ──────────────────────────────────────────────────────────────────


def test_write_tiers_off_by_default():
    assert server.cfg.write_tiers == ()
    for forbidden in ("me.write", "devices.write", "assets.write"):
        assert forbidden not in server._DEFAULT_SCOPES.split()


def test_write_tiers_parse_and_ignore_unknown(monkeypatch):
    monkeypatch.setenv("ROMM_WRITE_TIERS", "assets, Devices ,bogus")
    c = server.Config.from_env()
    assert c.write_tiers == ("assets", "devices")


def test_password_file_wins_over_env(monkeypatch, tmp_path):
    f = tmp_path / "pw"
    f.write_text("from-file\nsecond line ignored\n")
    monkeypatch.setenv("ROMM_PASSWORD", "from-env")
    monkeypatch.setenv("ROMM_PASSWORD_FILE", str(f))
    assert server.Config.from_env().romm_password == "from-file"


def test_password_file_unreadable_is_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("ROMM_PASSWORD_FILE", str(tmp_path / "missing"))
    assert server.Config.from_env().romm_password == ""


# ── Read-only mode ──────────────────────────────────────────────────────────


async def test_read_only_blocks_at_request_boundary(read_only, monkeypatch):
    # No HTTP client should ever be touched for a write in read-only mode.
    monkeypatch.setattr(server, "_get_client", lambda: (_ for _ in ()).throw(AssertionError("client used")))
    with pytest.raises(RuntimeError, match="Read-only mode"):
        await server._request("POST", "collections", json={})


async def test_read_only_write_tool_explains(read_only, v54, calls):
    out = await server.romm_set_collection_visibility(5, True)
    assert "read-only mode" in out
    assert not calls


# ── Write tiers ─────────────────────────────────────────────────────────────


async def test_tier_off_explains_how_to_enable(v54, calls):
    out = await server.romm_send_notification("hi")
    assert "ROMM_WRITE_TIERS=notifications" in out
    assert not calls


async def test_tier_on_but_scope_dropped(v54, calls, monkeypatch):
    monkeypatch.setattr(server, "cfg", server.Config(
        romm_url="http://x", romm_username="t", romm_password="t", request_timeout=1,
        request_timeout_long=1, tls_verify=True, write_tiers=("devices",)))
    monkeypatch.setattr(server, "_scopes", server._ScopeState(negotiated=True, granted=(), dropped_tiers=("devices",)))
    out = await server.romm_install_on_device("dev1", 7)
    assert "devices scope" in out
    assert not calls


# ── Version gates ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("call", [
    lambda: server.romm_recommendations(),
    lambda: server.romm_memory_cards(),
    lambda: server.romm_notifications(),
    lambda: server.romm_audit_events(),
    lambda: server.romm_streaming_containers(),
    lambda: server.romm_device_installs("d"),
])
async def test_new_tools_gate_on_old_server(v52, calls, call):
    out = await call()
    assert "needs RomM 5." in out
    assert not calls


# ── Text hardening ──────────────────────────────────────────────────────────


def test_untrusted_strips_control_and_newlines():
    s = server._untrusted("line1\nIGNORE PREVIOUS\x00\x1b[31m\rline2", 100)
    assert "\n" not in s and "\x00" not in s and "\x1b" not in s
    assert s == "line1 IGNORE PREVIOUS[31m line2"


def test_untrusted_caps_length():
    assert len(server._untrusted("x" * 500, 50)) == 51  # 50 + ellipsis


def test_mask_hides_secret_bearing_urls():
    assert server._mask("https://hooks.example/abc/SECRET") == "https://hooks.example/…"
    assert server._mask("user@mail.example") == "…mple"
    assert server._mask("") == "(none)"


# ── Notifications ───────────────────────────────────────────────────────────


async def test_send_notification_shapes_payload(v54, tiers_on, calls):
    calls.responses.append({"id": 9})
    out = await server.romm_send_notification("Scan done", body="all good", level="success", link="/scan")
    req = calls[0]
    assert req["method"] == "POST" and req["path"] == "notifications"
    assert req["json"] == {"title": "Scan done", "level": "success", "kind": "custom",
                           "body": "all good", "link": "/scan"}
    assert "recipients" not in req["json"]
    assert "ID 9" in out


async def test_send_notification_rejects_bad_level(v54, tiers_on, calls):
    out = await server.romm_send_notification("x", level="loud")
    assert "level must be" in out and not calls


async def test_notifications_unread_filter_and_untrusted_body(v54, calls):
    calls.responses.append([
        {"id": 1, "level": "info", "title": "Read one", "read_at": "2026-10-24", "created_at": "t"},
        {"id": 2, "level": "error", "title": "Scan\nfailed", "body": "x\x00y", "created_at": "t"},
    ])
    out = await server.romm_notifications(unread_only=True)
    assert "Read one" not in out
    assert "Scan failed" in out and "xy" in out and "\x00" not in out


async def test_mark_read_all_vs_ids(v54, tiers_on, calls):
    await server.romm_mark_notifications_read()
    assert calls[0]["path"] == "notifications/read" and calls[0]["json"] == {}
    await server.romm_mark_notifications_read([3, 1, 3])
    assert calls[1]["json"] == {"ids": [1, 3]}


async def test_dismiss_specific_ids_deletes_each(v54, tiers_on, calls):
    out = await server.romm_dismiss_notifications([5, 6])
    assert [c["path"] for c in calls] == ["notifications/5", "notifications/6"]
    assert all(c["method"] == "DELETE" for c in calls)
    assert "2 notification" in out


async def test_create_webhook_channel_requires_http_url(v54, tiers_on, calls):
    out = await server.romm_create_notification_channel("webhook", "hook", url="ftp://x")
    assert "http://" in out and not calls


async def test_create_apprise_channel_parses_fields_json(v54, tiers_on, calls):
    calls.responses.append({"id": 4})
    out = await server.romm_create_notification_channel(
        "apprise", "disc", service="discord", fields_json='{"webhook_id": "a", "webhook_token": "b"}',
        topics=["scans", "tasks"])
    body = calls[0]["json"]
    assert body["type"] == "apprise" and body["fields"] == {"webhook_id": "a", "webhook_token": "b"}
    assert body["topics"] == ["scans", "tasks"]
    assert "ID 4" in out


async def test_create_channel_rejects_bad_topic(v54, tiers_on, calls):
    out = await server.romm_create_notification_channel("webhook", "h", url="https://x", topics=["spam"])
    assert "Unknown topic" in out and not calls


async def test_channel_listing_masks_target(v54, calls):
    calls.responses.append([{"id": 1, "name": "hook", "type": "webhook", "enabled": True,
                             "min_level": "info", "target": "https://hooks.example/abc/TOKEN",
                             "has_secret": True}])
    out = await server.romm_notification_channels()
    assert "TOKEN" not in out and "hooks.example/…" in out and "secret set" in out


async def test_update_channel_uses_patch_and_hides_secret(v54, tiers_on, calls):
    out = await server.romm_update_notification_channel(3, enabled=False, secret="s3cr3t")
    assert calls[0]["method"] == "PATCH" and calls[0]["json"] == {"enabled": False, "secret": "s3cr3t"}
    assert "s3cr3t" not in out and "secret" in out


# ── Device installs ─────────────────────────────────────────────────────────


async def test_install_on_device_shapes_request(v54, tiers_on, calls):
    calls.responses.append({"id": "req1", "status": "pending"})
    out = await server.romm_install_on_device(" dev-1 ", 42)
    assert calls[0]["method"] == "POST" and calls[0]["path"] == "devices/dev-1/installs"
    assert calls[0]["json"] == {"rom_id": 42}
    assert "req1" in out


async def test_cancel_install_deletes(v54, tiers_on, calls):
    await server.romm_cancel_install("d", "r")
    assert calls[0]["method"] == "DELETE" and calls[0]["path"] == "devices/d/installs/r"


async def test_devices_marks_online_on_54(v54, calls):
    calls.responses.append([{"id": "a", "name": "Thor", "client": "argosy", "sync_enabled": True,
                             "capabilities": {"installs": True}}])
    calls.responses.append(["a"])
    out = await server.romm_devices()
    assert calls[1]["path"] == "devices/online"
    assert "ONLINE" in out and "Accepts installs: yes" in out


async def test_devices_skips_online_on_52(v52, calls):
    calls.responses.append([{"id": "a", "name": "Thor"}])
    await server.romm_devices()
    assert len(calls) == 1


# ── Saves / states ──────────────────────────────────────────────────────────


async def test_update_save_requires_a_field(v54, tiers_on, calls):
    out = await server.romm_update_save(1)
    assert "Nothing to update" in out and not calls


async def test_update_save_issues_one_put_per_field(v54, tiers_on, calls):
    out = await server.romm_update_save(7, favorite=True, labels=["100%", " speedrun "], new_name="a.srm")
    assert [(c["method"], c["path"]) for c in calls] == [
        ("PUT", "saves/7/favorite"), ("PUT", "saves/7/labels"), ("PUT", "saves/7/file-name")]
    assert calls[1]["json"] == {"labels": ["100%", "speedrun"]}
    assert "favourited" in out and "renamed" in out


async def test_saves_shows_54_fields(calls):
    calls.responses.append([{"id": 3, "file_name": "x.srm", "rom_id": 1, "is_favorite": True,
                             "labels": ["l1"], "device_syncs": [{"device_name": "Thor", "is_current": True}]}])
    out = await server.romm_saves(rom_id=1)
    assert "x.srm" in out and " *" in out and "Labels: l1" in out and "current on: Thor" in out


# ── Memory cards ────────────────────────────────────────────────────────────


async def test_delete_memory_cards_dedupes_and_posts(v54, tiers_on, calls):
    out = await server.romm_delete_memory_cards([3, 3, 1])
    assert calls[0]["method"] == "POST" and calls[0]["path"] == "memory-cards/delete"
    assert calls[0]["json"] == {"cards": [1, 3]}
    assert "2 memory card" in out


async def test_memory_cards_include_shared(v54, calls):
    calls.responses.extend([[{"id": 1, "name": "mine", "emulator": "duckstation", "slot": 1}],
                            [{"id": 2, "name": "theirs", "emulator": "duckstation", "username": "bob"}]])
    out = await server.romm_memory_cards(emulator="duckstation", include_shared=True)
    assert calls[0]["params"] == {"emulator": "duckstation"}
    assert "mine" in out and "theirs" in out and "by bob" in out


# ── Recommendations / scan / audit / collections ────────────────────────────


async def test_recommendations_refresh_param_and_reasons(v54, calls):
    calls.responses.append([{"rom": {"id": 1, "name": "Chrono Trigger", "platform_display_name": "SNES"},
                             "score": 0.9, "reasons": [{"facet": "genre", "value": "RPG"}],
                             "seed_rom_name": "FF6"}])
    out = await server.romm_recommendations(limit=500, refresh=True)
    assert calls[0]["params"] == {"limit": 50, "refresh": "true"}
    assert "Chrono Trigger" in out and "genre: RPG" in out and "FF6" in out


async def test_scan_library_uses_scan_endpoint_on_54(v54, calls):
    calls.responses.append({"task_id": "abc", "status": "queued"})
    out = await server.romm_scan_library("update", platform_ids=[2, 5])
    assert calls[0]["path"] == "tasks/scan" and calls[0]["json"] == {"type": "update", "platforms": [2, 5]}
    assert "abc" in out


async def test_scan_library_falls_back_on_52(v52, calls):
    await server.romm_scan_library()
    assert calls[0]["path"] == "tasks/run/scan_library"


async def test_scan_library_refuses_typed_scan_on_52(v52, calls):
    out = await server.romm_scan_library("complete")
    assert "need RomM 5.4" in out and not calls


async def test_scan_library_rejects_bad_type(v54, calls):
    out = await server.romm_scan_library("deep")
    assert "scan_type must be" in out and not calls


async def test_audit_events_params_and_paging(v54, calls):
    calls.responses.append({"items": [{"occurred_at": "t", "action": "rom.download", "actor_name": "me",
                                       "target_type": "rom", "target_name": "Game\nX"}],
                            "total": 3, "limit": 1, "offset": 0})
    out = await server.romm_audit_events(action="rom.download,auth.login", category="consumption", limit=1)
    assert calls[0]["params"]["action"] == ["rom.download", "auth.login"]
    assert calls[0]["params"]["category"] == ["consumption"]
    assert "Game X" in out and "offset=1" in out


async def test_audit_events_rejects_bad_category(v54, calls):
    out = await server.romm_audit_events(category="fun")
    assert "category must be" in out and not calls


async def test_collection_visibility_smart_path(v54, calls):
    await server.romm_set_collection_visibility(8, False, smart=True)
    assert calls[0]["path"] == "collections/smart/8/visibility" and calls[0]["json"] == {"is_public": False}


async def test_music_facets_new_fields_gate(v52, calls):
    out = await server.romm_music_facets(field="games")
    assert "needs RomM 5.3" in out and not calls


async def test_music_facets_games_format(v54, calls):
    calls.responses.append({"items": [{"rom_id": 4, "name": "OoT", "platform_name": "N64", "count": 12}], "total": 1})
    out = await server.romm_music_facets(field="game_genres" if False else "games")
    assert calls[0]["path"] == "music/games" and "OoT [N64] — 12 track(s)" in out


# ── Streaming additions ─────────────────────────────────────────────────────


async def test_streaming_join_passes_container(v54, calls):
    calls.responses.append({"host": "https://stream.example", "rom_name": "Game"})
    out = await server.romm_streaming_join("ps2", container="ps2-b")
    assert calls[0]["method"] == "POST" and calls[0]["path"] == "streaming/sessions/ps2/join"
    assert calls[0]["params"] == {"container": "ps2-b"}
    assert "stream.example" in out


async def test_streaming_swap_disc_body(v54, calls):
    await server.romm_streaming_swap_disc("psx", 77)
    assert calls[0]["json"] == {"file_id": 77} and calls[0]["path"] == "streaming/sessions/psx/swap-disc"


async def test_streaming_containers_admin_only(v54, monkeypatch):
    async def forbidden(*a, **k):
        raise RuntimeError("API error 403: forbidden")
    monkeypatch.setattr(server, "_request", forbidden)
    out = await server.romm_streaming_containers()
    assert "admin-only" in out


# ── Annotation hygiene ──────────────────────────────────────────────────────


async def test_every_tool_is_annotated_and_destructive_ones_say_so():
    tools = {t.name: t.annotations for t in await server.mcp._list_tools()}
    assert len(tools) >= 90
    destructive = {"romm_delete_memory_cards", "romm_dismiss_notifications",
                   "romm_delete_notification_channel", "romm_cancel_install"}
    for name, ann in tools.items():
        assert ann is not None and ann.readOnlyHint is not None, f"{name} has no annotations"
        if name in destructive:
            assert ann.destructiveHint is True, f"{name} should be destructive"
    for name in ("romm_recommendations", "romm_notifications", "romm_audit_events",
                 "romm_memory_cards", "romm_states", "romm_device_installs"):
        assert tools[name].readOnlyHint is True, name


# ── Path-segment quoting ────────────────────────────────────────────────────


def test_seg_quotes_everything_unsafe():
    assert server._seg("../users") == "..%2Fusers"
    assert server._seg(" a b/c?d ") == "a%20b%2Fc%3Fd"
    assert server._seg("ps2") == "ps2"


async def test_device_and_request_ids_are_quoted_in_paths(v54, tiers_on, calls):
    await server.romm_cancel_install("dev/../x", "r?1")
    assert calls[0]["path"] == "devices/dev%2F..%2Fx/installs/r%3F1"
    await server.romm_device_installs("a b")
    assert calls[1]["path"] == "devices/a%20b/installs"


async def test_platform_slug_is_quoted_in_streaming_paths(v54, calls):
    calls.responses.append({"host": "h"})
    await server.romm_streaming_join("ps2/../admin")
    assert calls[0]["path"] == "streaming/sessions/ps2%2F..%2Fadmin/join"


# ── Live-run fixes (5.4.1 e2e, 2026-10-24) ──────────────────────────────────


async def test_memory_cards_shared_requires_emulator(v54, calls):
    out = await server.romm_memory_cards(include_shared=True)
    assert "needs an emulator" in out and not calls


async def test_state_picks_no_container_is_explained(v54, monkeypatch):
    async def nf(*a, **k):
        raise RuntimeError("API error 404: No streaming container configured for platform 'gba'")
    monkeypatch.setattr(server, "_request", nf)
    out = await server.romm_streaming_state_picks(5)
    assert "no streaming container" in out


async def test_apprise_services_admin_only(v54, monkeypatch):
    async def forbidden(*a, **k):
        raise RuntimeError("API error 403: Administrator privileges required")
    monkeypatch.setattr(server, "_request", forbidden)
    out = await server.romm_apprise_services()
    assert "admin-only" in out


async def test_scan_library_409_is_explained(v54, monkeypatch):
    async def busy(*a, **k):
        raise RuntimeError('API error 409: {"detail":"Quick Scan is already running"}')
    monkeypatch.setattr(server, "_request", busy)
    out = await server.romm_scan_library()
    assert "already running" in out


# ══ Security review follow-ups (2026-10-24) ═════════════════════════════════


async def test_streaming_state_platform_is_quoted(v54, calls):
    calls.responses.append({"status": "ok"})
    await server.romm_streaming_state("ps2/../x", action="save", slot=1)
    assert calls[0]["path"].startswith("streaming/sessions/ps2%2F..%2Fx/")


async def test_virtual_collection_id_is_quoted(calls):
    calls.responses.append({})
    await server.romm_virtual_collection_detail("../config")
    assert calls[0]["path"] == "collections/virtual/..%2Fconfig"


def test_read_only_narrows_requested_scopes(monkeypatch):
    monkeypatch.setenv("ROMM_READ_ONLY", "true")
    monkeypatch.setenv("ROMM_WRITE_TIERS", "notifications,devices,assets")
    import importlib
    ro = importlib.reload(server)
    try:
        requested = set(ro._DEFAULT_SCOPES.split())
        assert not any(s.endswith(".write") for s in requested), requested
        assert "tasks.run" not in requested
        assert "me.read" in requested and "roms.read" in requested
    finally:
        monkeypatch.delenv("ROMM_READ_ONLY")
        monkeypatch.delenv("ROMM_WRITE_TIERS")
        importlib.reload(server)


def test_untrusted_strips_unicode_invisibles():
    s = server._untrusted("A\u202eB\u200bC\U000e0041D\x85E", 50)
    assert s == "ABCDE"


@pytest.mark.parametrize("url,bad", [
    ("https://hooks.example/x", False),
    ("http://10.0.0.5/hook", True),
    ("https://127.0.0.1:8000/", True),
    ("https://localhost/x", True),
    ("https://nas.local/x", True),
    ("https://romm/x", True),
    ("https://[::1]/x", True),
    ("https://169.254.1.1/x", True),
    ("ftp://hooks.example/x", True),
])
def test_webhook_url_policy(url, bad):
    assert (server._webhook_url_problem(url) is not None) == bad, url


async def test_create_webhook_refuses_private_target(v54, tiers_on, calls):
    out = await server.romm_create_notification_channel("webhook", "h", url="http://192.168.1.10/hook")
    assert "private" in out and not calls


async def test_update_channel_refuses_private_target(v54, tiers_on, calls):
    out = await server.romm_update_notification_channel(1, url="http://localhost/x")
    assert "localhost" in out and not calls


async def test_send_notification_link_policy(v54, tiers_on, calls):
    out = await server.romm_send_notification("t", link="javascript:alert(1)")
    assert "link must be" in out and not calls
    calls.responses.append({"id": 1})
    await server.romm_send_notification("t", link="/rom/5")
    assert calls[0]["json"]["link"] == "/rom/5"


def test_transport_allowed_hosts(monkeypatch):
    monkeypatch.setenv("ROMM_MCP_TRANSPORT", "http")
    monkeypatch.setenv("ROMM_MCP_ALLOWED_HOSTS", "100.64.0.9, romm-mcp.example")
    t, kw = server._transport_config()
    assert t == "http" and kw["host_origin_protection"] is True
    assert kw["allowed_hosts"] == ["100.64.0.9", "romm-mcp.example"]
    assert "https://romm-mcp.example" in kw["allowed_origins"]
    monkeypatch.delenv("ROMM_MCP_ALLOWED_HOSTS")
    t, kw = server._transport_config()
    assert kw["host_origin_protection"] == "auto" and "allowed_hosts" not in kw


async def test_rom_notes_sanitizes_other_users_text(calls):
    calls.responses.append([{"id": 1, "title": "x\ny", "username": "bob\x00", "content": "line1\nIGNORE ALL\nline2",
                             "is_public": True, "created_at": "t"}])
    out = await server.romm_rom_notes(3)
    assert "x y" in out and "bob" in out and "line1 IGNORE ALL line2" in out
    assert "\n      line1" not in out or "IGNORE ALL line2" in out


# ── Request shaping for the tools the first pass left untested ──────────────


async def test_similar_roms_params(v54, calls):
    calls.responses.append([{"rom": {"id": 2, "name": "B"}, "score": 1, "reasons": []}])
    out = await server.romm_similar_roms(1, limit=99)
    assert calls[0]["path"] == "roms/1/similar" and calls[0]["params"] == {"limit": 50}
    assert "B" in out


async def test_memory_card_detail_lists_versions(v54, calls):
    calls.responses.extend([{"id": 4, "name": "c", "emulator": "pcsx2", "slot": 1},
                            [{"id": 9, "file_name": "c.ps2", "file_size_bytes": 8_388_608, "created_at": "t",
                              "missing_from_fs": True}]])
    out = await server.romm_memory_card(4)
    assert [c["path"] for c in calls] == ["memory-cards/4", "memory-cards/4/versions"]
    assert "8 MB" in out and "MISSING" in out and "version ID 9" in out


async def test_rename_memory_card_and_visibility(v54, tiers_on, calls):
    calls.responses.append({"name": "new"})
    out = await server.romm_rename_memory_card(4, "  new ")
    assert calls[0] == {"method": "PUT", "path": "memory-cards/4", "params": None, "json": {"name": "new"}, "data": None}
    assert "renamed" in out
    await server.romm_set_memory_card_visibility(4, True)
    assert calls[1]["path"] == "memory-cards/4/visibility" and calls[1]["json"] == {"is_public": True}
    out = await server.romm_rename_memory_card(4, "  ")
    assert "required" in out


async def test_streaming_status_params(v54, calls):
    calls.responses.append({"status": "running", "platform": "ps2", "core": "pcsx2", "core_tier": "gold"})
    out = await server.romm_streaming_status("ps2", claimed_at="2026-10-24T00:00:00Z")
    assert calls[0]["path"] == "streaming/sessions/ps2/status"
    assert calls[0]["params"] == {"claimed_at": "2026-10-24T00:00:00Z"}
    assert "running" in out and "pcsx2 (gold)" in out


async def test_channel_test_confirm_delete(v54, tiers_on, calls):
    calls.responses.append({"ok": False, "error": "timeout\nx"})
    out = await server.romm_test_notification_channel(2)
    assert calls[0]["path"] == "notification-channels/2/test" and "FAILED" in out and "timeout x" in out
    calls.responses.append({"confirmed": True})
    out = await server.romm_confirm_notification_channel(2, code=" 1234 ")
    assert calls[1]["json"] == {"code": "1234"} and "confirmed" in out
    await server.romm_confirm_notification_channel(2, resend=True)
    assert calls[2]["path"] == "notification-channels/2/resend-code"
    out = await server.romm_delete_notification_channel(2)
    assert calls[3]["method"] == "DELETE" and calls[3]["path"] == "notification-channels/2" and "deleted" in out


async def test_rom_installs_listing(v54, calls):
    calls.responses.append([{"id": "r1", "rom_id": 5, "device_id": "d", "status": "done", "created_at": "t"}])
    out = await server.romm_rom_installs(5)
    assert calls[0]["path"] == "roms/5/installs" and "done" in out


async def test_update_state_routes_to_states(v54, tiers_on, calls):
    await server.romm_update_state(8, labels=[])
    assert calls[0]["path"] == "states/8/labels" and calls[0]["json"] == {"labels": []}
