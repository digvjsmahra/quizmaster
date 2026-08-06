import io
import secrets
import zipfile

import openpyxl
import pytest

from app import app, socketio, rooms
import events
from game import Game
from bundle_loader import BundleQuestion


def _q(id, board, category, value):
    return BundleQuestion(
        id=id, board=board, category=category, value=value,
        question=f"Q for {id}", answer=f"A for {id}", question_media=[], answer_media=[],
    )


DEFAULT_QUESTIONS = {
    "1": [
        _q("1:History:10", "1", "History", 10),
        _q("1:History:20", "1", "History", 20),
    ],
}


def _make_bundle_bytes(rows, media_files=None):
    columns = ["board", "category", "value", "question", "answer", "question_media"]
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(columns)
    for row in rows:
        ws.append([row.get(c) for c in columns])
    xlsx_buf = io.BytesIO()
    wb.save(xlsx_buf)
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("quiz.xlsx", xlsx_buf.getvalue())
        for fn, content in (media_files or {}).items():
            zf.writestr(f"media/{fn}", content)
    zip_buf.seek(0)
    return zip_buf.getvalue()


def _assert_no_content_leak(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            assert k not in ("question", "answer", "question_media", "answer_media"), \
                f"leaked key {k!r} in player payload: {obj}"
            _assert_no_content_leak(v)
    elif isinstance(obj, list):
        for item in obj:
            _assert_no_content_leak(item)


@pytest.fixture(autouse=True)
def room():
    rooms.clear()
    events._sid_player.clear()
    events._sid_room.clear()
    g = Game(questions=DEFAULT_QUESTIONS)
    host_token = secrets.token_urlsafe(16)
    rooms[g.join_code] = {"game": g, "host_token": host_token}
    yield g.join_code, g, host_token
    rooms.clear()
    events._sid_player.clear()
    events._sid_room.clear()


def test_join_buzz_queue_broadcast(room):
    join_code, game, _ = room

    host_client = socketio.test_client(app)
    p1_client = socketio.test_client(app)
    p2_client = socketio.test_client(app)

    # Host joins
    host_client.emit("host:join", {"room_id": join_code})
    host_received = host_client.get_received()
    assert any(e["name"] == "state:full" for e in host_received)

    # Two players join
    p1_client.emit("player:join", {"name": "Ankur", "room_id": join_code})
    p1_events = p1_client.get_received()
    accepted = next(e for e in p1_events if e["name"] == "player:accepted")
    p1_id = accepted["args"][0]["player_id"]
    assert accepted["args"][0]["phase"] == "lobby"

    p2_client.emit("player:join", {"name": "Dev", "room_id": join_code})
    p2_events = p2_client.get_received()
    accepted2 = next(e for e in p2_events if e["name"] == "player:accepted")
    p2_id = accepted2["args"][0]["player_id"]

    # Host starts quiz
    host_client.get_received()  # clear
    host_client.emit("host:start_quiz")
    host_events = host_client.get_received()
    phase_event = next(e for e in host_events if e["name"] == "state:phase")
    assert phase_event["args"][0]["phase"] == "live"

    # Players buzz — p1 first, then p2
    p1_client.get_received()
    p2_client.get_received()

    p1_client.emit("player:buzz")
    p2_client.emit("player:buzz")

    queue = game.get_queue_payload()["queue"]
    assert len(queue) == 2
    assert queue[0]["player_id"] == p1_id
    assert queue[1]["player_id"] == p2_id

    host_client.disconnect()
    p1_client.disconnect()
    p2_client.disconnect()


def test_player_rejected_on_empty_name(room):
    join_code, _, _ = room
    client = socketio.test_client(app)
    client.emit("player:join", {"name": "", "room_id": join_code})
    events = client.get_received()
    assert any(e["name"] == "player:rejected" for e in events)
    client.disconnect()


def test_host_sees_correct_join_url(room):
    join_code, game, _ = room
    host_client = socketio.test_client(app)
    host_client.emit("host:join", {"room_id": join_code})
    events = host_client.get_received()
    full = next(e for e in events if e["name"] == "state:full")
    assert full["args"][0]["join_code"] == game.join_code
    host_client.disconnect()


def test_validate_room_valid(room):
    join_code, _, _ = room
    res = app.test_client().get(f"/rooms/{join_code}/validate")
    assert res.status_code == 200


def test_validate_room_invalid(room):
    res = app.test_client().get("/rooms/XXXX/validate")
    assert res.status_code == 404


def test_validate_room_case_insensitive(room):
    join_code, _, _ = room
    res = app.test_client().get(f"/rooms/{join_code.lower()}/validate")
    assert res.status_code == 200


def test_late_joiner_receives_queue_state_when_frozen(room):
    join_code, game, _ = room

    host_client = socketio.test_client(app)
    host_client.emit("host:join", {"room_id": join_code})
    host_client.get_received()

    host_client.emit("host:start_quiz")
    host_client.get_received()

    host_client.emit("host:queue_freeze")
    host_client.get_received()

    late_client = socketio.test_client(app)
    late_client.emit("player:join", {"name": "Late", "room_id": join_code})
    events = late_client.get_received()

    queue_event = next((e for e in events if e["name"] == "state:queue"), None)
    assert queue_event is not None, "late joiner should receive state:queue"
    assert queue_event["args"][0]["locked"] is True, "queue should be locked"

    late_client.emit("player:buzz")
    assert len(game.queue) == 0, "frozen queue should reject the buzz"

    host_client.disconnect()
    late_client.disconnect()


def test_players_broadcast_to_room_on_join(room):
    join_code, _, _ = room

    p1 = socketio.test_client(app)
    p1.emit("player:join", {"name": "Ankur", "room_id": join_code})
    p1.get_received()  # clear — p1's own join event

    p2 = socketio.test_client(app)
    p2.emit("player:join", {"name": "Dev", "room_id": join_code})

    # p1 should receive state:players broadcast triggered by p2 joining
    p1_events = p1.get_received()
    players_event = next((e for e in p1_events if e["name"] == "state:players"), None)
    assert players_event is not None, "p1 should receive state:players when p2 joins"
    names = [p["name"] for p in players_event["args"][0]["players"]]
    assert "Ankur" in names
    assert "Dev" in names

    p1.disconnect()
    p2.disconnect()


def test_players_broadcast_on_disconnect(room):
    join_code, _, _ = room

    p1 = socketio.test_client(app)
    p1.emit("player:join", {"name": "Ankur", "room_id": join_code})
    p1.get_received()

    p2 = socketio.test_client(app)
    p2.emit("player:join", {"name": "Dev", "room_id": join_code})
    p1.get_received()  # clear the join broadcast
    p2.get_received()

    p2.disconnect()

    p1_events = p1.get_received()
    players_event = next((e for e in p1_events if e["name"] == "state:players"), None)
    assert players_event is not None, "p1 should receive state:players when p2 disconnects"
    names = [p["name"] for p in players_event["args"][0]["players"]]
    assert "Dev" not in names
    assert "Ankur" in names

    p1.disconnect()


def test_start_quiz_broadcasts_state_players(room):
    join_code, _, _ = room

    host = socketio.test_client(app)
    host.emit("host:join", {"room_id": join_code})
    host.get_received()

    p1 = socketio.test_client(app)
    p1.emit("player:join", {"name": "Ankur", "room_id": join_code})
    p1.get_received()

    host.emit("host:start_quiz")
    p1_events = p1.get_received()

    players_event = next((e for e in p1_events if e["name"] == "state:players"), None)
    assert players_event is not None, "start_quiz should broadcast state:players to players"
    names = [p["name"] for p in players_event["args"][0]["players"]]
    assert "Ankur" in names

    host.disconnect()
    p1.disconnect()


def test_roster_add_excludes_virtual_from_state_players(room):
    join_code, _, _ = room

    host = socketio.test_client(app)
    host.emit("host:join", {"room_id": join_code})
    host.get_received()

    p1 = socketio.test_client(app)
    p1.emit("player:join", {"name": "Ankur", "room_id": join_code})
    p1.get_received()

    host.emit("host:start_quiz")
    host.get_received()
    p1.get_received()

    host.emit("host:roster_add", {"name": "VirtualDev"})

    p1_events = p1.get_received()
    players_event = next((e for e in p1_events if e["name"] == "state:players"), None)
    assert players_event is not None, "roster_add should broadcast state:players to players"
    names = [p["name"] for p in players_event["args"][0]["players"]]
    assert "Ankur" in names
    assert "VirtualDev" not in names  # virtual entry must never appear on player phones

    host.disconnect()
    p1.disconnect()


def test_late_joiner_receives_current_queue(room):
    join_code, _, _ = room

    host = socketio.test_client(app)
    host.emit("host:join", {"room_id": join_code})
    host.get_received()

    p1 = socketio.test_client(app)
    p1.emit("player:join", {"name": "Ankur", "room_id": join_code})
    p1.get_received()

    host.emit("host:start_quiz")
    host.get_received()
    p1.get_received()

    p1.emit("player:buzz")
    p1.get_received()
    host.get_received()

    late = socketio.test_client(app)
    late.emit("player:join", {"name": "Late", "room_id": join_code})
    late_events = late.get_received()

    queue_event = next((e for e in late_events if e["name"] == "state:queue"), None)
    assert queue_event is not None, "late joiner should receive current state:queue"
    queue = queue_event["args"][0]["queue"]
    assert len(queue) == 1
    assert queue[0]["name"] == "Ankur"

    host.disconnect()
    p1.disconnect()
    late.disconnect()


# ------------------------------------------------------------------
# player:rejoin — durable identity across reconnects
# ------------------------------------------------------------------

def test_player_rejoin_restores_same_identity_and_queue_state(room):
    join_code, game, _ = room
    game.start_quiz()

    p1 = socketio.test_client(app)
    p1.emit("player:join", {"name": "Ankur", "room_id": join_code})
    accepted = next(e for e in p1.get_received() if e["name"] == "player:accepted")
    original_pid = accepted["args"][0]["player_id"]
    token = accepted["args"][0]["rejoin_token"]
    assert token

    p1.emit("player:buzz")
    p1.get_received()
    p1.disconnect()

    p1_new = socketio.test_client(app)
    p1_new.emit("player:rejoin", {"room_id": join_code, "token": token})
    events_received = p1_new.get_received()

    accepted2 = next(e for e in events_received if e["name"] == "player:accepted")
    assert accepted2["args"][0]["player_id"] == original_pid
    assert accepted2["args"][0]["phase"] == "live"

    queue_event = next(e for e in events_received if e["name"] == "state:queue")
    assert queue_event["args"][0]["queue"][0]["player_id"] == original_pid

    p1_new.disconnect()


def test_player_rejoin_rejects_unknown_token(room):
    join_code, _, _ = room
    client = socketio.test_client(app)
    client.emit("player:rejoin", {"room_id": join_code, "token": "bogus"})
    events_received = client.get_received()
    assert any(e["name"] == "player:rejected" for e in events_received)
    client.disconnect()


def test_player_rejoin_survives_stale_disconnect_from_old_connection(room):
    # Regression test for the race this feature introduces: the original
    # connection's disconnect can arrive *after* the player has already
    # rejoined on a new sid. It must not clobber connected=True.
    join_code, game, _ = room
    game.start_quiz()

    p1 = socketio.test_client(app)
    p1.emit("player:join", {"name": "Ankur", "room_id": join_code})
    accepted = next(e for e in p1.get_received() if e["name"] == "player:accepted")
    pid = accepted["args"][0]["player_id"]
    token = accepted["args"][0]["rejoin_token"]

    # Rejoin on a second connection *before* the first one's disconnect fires.
    p1_new = socketio.test_client(app)
    p1_new.emit("player:rejoin", {"room_id": join_code, "token": token})
    p1_new.get_received()
    assert game.players[pid].connected is True

    # The stale original connection's disconnect finally arrives.
    p1.disconnect()

    assert game.players[pid].connected is True
    p1_new.disconnect()


# ------------------------------------------------------------------
# host:player_remove — lobby-only cleanup of duplicate/mistaken joins
# ------------------------------------------------------------------

def test_host_player_remove_kicks_and_broadcasts(room):
    join_code, game, _ = room

    host = socketio.test_client(app)
    host.emit("host:join", {"room_id": join_code})
    host.get_received()

    p1 = socketio.test_client(app)
    p1.emit("player:join", {"name": "Ankur", "room_id": join_code})
    accepted = next(e for e in p1.get_received() if e["name"] == "player:accepted")
    pid = accepted["args"][0]["player_id"]
    host.get_received()  # clear the join broadcast

    host.emit("host:player_remove", {"player_id": pid})

    # p1 is disconnected by the handler as part of the kick, so the test
    # client's own is_connected() guard blocks get_received() afterward —
    # read the still-queued packets directly to confirm player:removed
    # was sent before the disconnect closed the channel.
    assert any(pkt["name"] == "player:removed" for pkt in p1.queue)
    assert not p1.is_connected()
    assert pid not in game.players

    host_events = host.get_received()
    players_event = next(e for e in host_events if e["name"] == "state:players")
    assert players_event["args"][0]["players"] == []

    host.disconnect()


def test_host_player_remove_rejected_after_start(room):
    join_code, game, _ = room

    host = socketio.test_client(app)
    host.emit("host:join", {"room_id": join_code})
    host.get_received()

    p1 = socketio.test_client(app)
    p1.emit("player:join", {"name": "Ankur", "room_id": join_code})
    accepted = next(e for e in p1.get_received() if e["name"] == "player:accepted")
    pid = accepted["args"][0]["player_id"]

    host.emit("host:start_quiz")
    host.get_received()

    host.emit("host:player_remove", {"player_id": pid})
    host_events = host.get_received()
    error_event = next((e for e in host_events if e["name"] == "error"), None)
    assert error_event is not None
    assert error_event["args"][0]["context"] == "player_remove"
    assert pid in game.players

    host.disconnect()
    p1.disconnect()


def test_host_player_remove_unknown_id_emits_error(room):
    join_code, _, _ = room

    host = socketio.test_client(app)
    host.emit("host:join", {"room_id": join_code})
    host.get_received()

    host.emit("host:player_remove", {"player_id": "bogus"})
    host_events = host.get_received()
    error_event = next((e for e in host_events if e["name"] == "error"), None)
    assert error_event is not None
    assert error_event["args"][0]["context"] == "player_remove"

    host.disconnect()


# ------------------------------------------------------------------
# host:roster_remove — post-Start roster cleanup, discards scores
# ------------------------------------------------------------------

def test_host_roster_remove_discards_scores_and_broadcasts_to_all_hosts(room):
    join_code, game, _ = room
    game.start_quiz()

    host1 = socketio.test_client(app)
    host1.emit("host:join", {"room_id": join_code})
    host1.get_received()

    host2 = socketio.test_client(app)
    host2.emit("host:join", {"room_id": join_code})
    host2.get_received()

    pid = game.roster_add("Ankur")
    host1.emit("host:question_reveal", {"question_id": "1:History:10"})
    host1.get_received()
    host2.get_received()
    host1.emit("host:answer_reveal")
    host1.get_received()
    host2.get_received()
    host1.emit("host:question_submit", {"question_id": "1:History:10", "scores": {pid: 10}})
    host1.get_received()
    host2.get_received()
    assert game.scores[pid]["1:History:10"] == 10

    host1.emit("host:roster_remove", {"player_id": pid})

    for client in (host1, host2):
        events_received = client.get_received()
        scores_event = next((e for e in events_received if e["name"] == "state:scores"), None)
        assert scores_event is not None, "both host tabs should see the removal"
        assert pid not in [r["player_id"] for r in scores_event["args"][0]["roster"]]

    assert pid not in game.roster
    assert pid not in game.scores

    host1.disconnect()
    host2.disconnect()


def test_host_roster_remove_kicks_still_connected_player(room):
    join_code, game, _ = room

    host = socketio.test_client(app)
    host.emit("host:join", {"room_id": join_code})
    host.get_received()

    p1 = socketio.test_client(app)
    p1.emit("player:join", {"name": "Ankur", "room_id": join_code})
    accepted = next(e for e in p1.get_received() if e["name"] == "player:accepted")
    pid = accepted["args"][0]["player_id"]
    host.get_received()

    game.start_quiz()
    host.emit("host:roster_remove", {"player_id": pid})

    # Same test-client limitation as V6's kick test — get_received() refuses
    # once the client is marked disconnected, so inspect the raw queue.
    assert any(pkt["name"] == "player:removed" for pkt in p1.queue)
    assert not p1.is_connected()
    assert pid not in game.players

    host.disconnect()


def test_host_roster_remove_rejected_before_start(room):
    join_code, game, _ = room

    host = socketio.test_client(app)
    host.emit("host:join", {"room_id": join_code})
    host.get_received()

    virtual_pid = game.roster_add("HostAdded")
    # roster_add itself doesn't gate on phase, so this is reachable pre-Start
    # even though the "+ add" UI control only appears live — the server-side
    # guard on roster_remove is what actually matters here.
    host.emit("host:roster_remove", {"player_id": virtual_pid})
    host_events = host.get_received()
    error_event = next((e for e in host_events if e["name"] == "error"), None)
    assert error_event is not None
    assert error_event["args"][0]["context"] == "roster_remove"
    assert virtual_pid in game.roster

    host.disconnect()


# ------------------------------------------------------------------
# A2: upload route, per-room storage, upload gate
# ------------------------------------------------------------------

def test_upload_valid_bundle_returns_200_and_populates_board(room):
    join_code, game, host_token = room
    bundle_bytes = _make_bundle_bytes([
        {"board": "1", "category": "History", "value": 10, "question": "Real question", "answer": "Real answer"},
    ])
    res = app.test_client().post(
        f"/host/{join_code}/{host_token}/upload",
        data={"bundle": (io.BytesIO(bundle_bytes), "bundle.zip")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200
    assert res.get_json()["errors"] == []
    assert game.questions["1"][0].question == "Real question"


def test_upload_success_broadcasts_state_scores_to_host(room):
    join_code, _, host_token = room
    host_client = socketio.test_client(app)
    host_client.emit("host:join", {"room_id": join_code})
    host_client.get_received()  # clear state:full

    bundle_bytes = _make_bundle_bytes([
        {"board": "1", "category": "History", "value": 10, "question": "Q1", "answer": "A1"},
    ])
    res = app.test_client().post(
        f"/host/{join_code}/{host_token}/upload",
        data={"bundle": (io.BytesIO(bundle_bytes), "bundle.zip")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 200

    events_received = host_client.get_received()
    scores_event = next((e for e in events_received if e["name"] == "state:scores"), None)
    assert scores_event is not None
    assert "1" in scores_event["args"][0]["boards"]
    host_client.disconnect()


def test_upload_malformed_bundle_returns_422_no_mutation(room):
    join_code, game, host_token = room
    original_questions = game.questions
    bundle_bytes = _make_bundle_bytes([
        {"board": "", "category": "History", "value": 10, "question": "Q1", "answer": "A1"},
    ])
    res = app.test_client().post(
        f"/host/{join_code}/{host_token}/upload",
        data={"bundle": (io.BytesIO(bundle_bytes), "bundle.zip")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 422
    body = res.get_json()
    assert len(body["errors"]) == 1
    assert body["errors"][0]["row"] == 2
    assert game.questions is original_questions


def test_upload_wrong_host_token_returns_404(room):
    join_code, _, _ = room
    bundle_bytes = _make_bundle_bytes([
        {"board": "1", "category": "History", "value": 10, "question": "Q1", "answer": "A1"},
    ])
    res = app.test_client().post(
        f"/host/{join_code}/wrong-token/upload",
        data={"bundle": (io.BytesIO(bundle_bytes), "bundle.zip")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 404


def test_start_quiz_before_upload_emits_error(room):
    join_code, game, _ = room
    game.load_questions({})  # simulate no upload yet

    host = socketio.test_client(app)
    host.emit("host:join", {"room_id": join_code})
    host.get_received()

    host.emit("host:start_quiz")
    events_received = host.get_received()
    error_event = next((e for e in events_received if e["name"] == "error"), None)
    assert error_event is not None
    assert game.phase == "lobby"
    host.disconnect()


def test_start_quiz_succeeds_after_upload(room):
    join_code, game, host_token = room
    game.load_questions({})
    bundle_bytes = _make_bundle_bytes([
        {"board": "1", "category": "History", "value": 10, "question": "Q1", "answer": "A1"},
    ])
    app.test_client().post(
        f"/host/{join_code}/{host_token}/upload",
        data={"bundle": (io.BytesIO(bundle_bytes), "bundle.zip")},
        content_type="multipart/form-data",
    )

    host = socketio.test_client(app)
    host.emit("host:join", {"room_id": join_code})
    host.get_received()
    host.emit("host:start_quiz")
    events_received = host.get_received()
    assert any(
        e["name"] == "state:phase" and e["args"][0]["phase"] == "live" for e in events_received
    )
    assert game.phase == "live"
    host.disconnect()


def test_reupload_after_live_returns_409(room):
    join_code, game, host_token = room
    game.start_quiz()
    bundle_bytes = _make_bundle_bytes([
        {"board": "1", "category": "History", "value": 10, "question": "Q1", "answer": "A1"},
    ])
    res = app.test_client().post(
        f"/host/{join_code}/{host_token}/upload",
        data={"bundle": (io.BytesIO(bundle_bytes), "bundle.zip")},
        content_type="multipart/form-data",
    )
    assert res.status_code == 409


def test_media_route_serves_uploaded_file(room):
    join_code, _, host_token = room
    bundle_bytes = _make_bundle_bytes(
        [{"board": "1", "category": "History", "value": 10, "question": "Q1", "answer": "A1", "question_media": "pic.jpg"}],
        media_files={"pic.jpg": b"pic-bytes"},
    )
    app.test_client().post(
        f"/host/{join_code}/{host_token}/upload",
        data={"bundle": (io.BytesIO(bundle_bytes), "bundle.zip")},
        content_type="multipart/form-data",
    )
    res = app.test_client().get(f"/media/{join_code}/{host_token}/pic.jpg")
    assert res.status_code == 200
    assert res.data == b"pic-bytes"


def test_media_route_wrong_token_returns_404(room):
    join_code, _, _ = room
    res = app.test_client().get(f"/media/{join_code}/wrong-token/pic.jpg")
    assert res.status_code == 404


def test_media_route_unknown_filename_returns_404(room):
    join_code, _, host_token = room
    bundle_bytes = _make_bundle_bytes(
        [{"board": "1", "category": "History", "value": 10, "question": "Q1", "answer": "A1", "question_media": "pic.jpg"}],
        media_files={"pic.jpg": b"pic-bytes"},
    )
    app.test_client().post(
        f"/host/{join_code}/{host_token}/upload",
        data={"bundle": (io.BytesIO(bundle_bytes), "bundle.zip")},
        content_type="multipart/form-data",
    )
    res = app.test_client().get(f"/media/{join_code}/{host_token}/nonexistent.jpg")
    assert res.status_code == 404


def test_state_full_includes_board_before_start_after_upload(room):
    # Regression test for the reconnect bug caught during A2 planning:
    # a host reconnecting (or opening a second tab) after a successful
    # upload but before Start must still see the board via state:full.
    join_code, game, host_token = room
    game.load_questions({})
    bundle_bytes = _make_bundle_bytes([
        {"board": "1", "category": "History", "value": 10, "question": "Q1", "answer": "A1"},
    ])
    app.test_client().post(
        f"/host/{join_code}/{host_token}/upload",
        data={"bundle": (io.BytesIO(bundle_bytes), "bundle.zip")},
        content_type="multipart/form-data",
    )
    assert game.phase == "lobby"

    host = socketio.test_client(app)
    host.emit("host:join", {"room_id": join_code})
    events_received = host.get_received()
    full_event = next(e for e in events_received if e["name"] == "state:full")
    assert full_event["args"][0]["phase"] == "lobby"
    assert full_event["args"][0]["scores"]["boards"] == ["1"]
    host.disconnect()


# ------------------------------------------------------------------
# B1: question_reveal / answer_reveal / question_cancel state machine
# ------------------------------------------------------------------

def test_question_reveal_broadcasts_state_live_question_to_host(room):
    join_code, game, _ = room
    game.start_quiz()

    host = socketio.test_client(app)
    host.emit("host:join", {"room_id": join_code})
    host.get_received()

    host.emit("host:question_reveal", {"question_id": "1:History:10"})
    events_received = host.get_received()
    live_event = next((e for e in events_received if e["name"] == "state:live_question"), None)
    assert live_event is not None
    assert live_event["args"][0]["live_question"]["question_id"] == "1:History:10"
    assert live_event["args"][0]["live_question"]["status"] == "revealed"

    host.disconnect()


def test_question_reveal_rejects_second_different_question(room):
    join_code, game, _ = room
    game.start_quiz()

    host = socketio.test_client(app)
    host.emit("host:join", {"room_id": join_code})
    host.get_received()

    host.emit("host:question_reveal", {"question_id": "1:History:10"})
    host.get_received()

    host.emit("host:question_reveal", {"question_id": "1:History:20"})
    events_received = host.get_received()
    error_event = next((e for e in events_received if e["name"] == "error"), None)
    assert error_event is not None
    assert game.live_question["question_id"] == "1:History:10"

    host.disconnect()


def test_answer_reveal_broadcasts_updated_status(room):
    join_code, game, _ = room
    game.start_quiz()

    host = socketio.test_client(app)
    host.emit("host:join", {"room_id": join_code})
    host.get_received()

    host.emit("host:question_reveal", {"question_id": "1:History:10"})
    host.get_received()

    host.emit("host:answer_reveal")
    events_received = host.get_received()
    live_event = next((e for e in events_received if e["name"] == "state:live_question"), None)
    assert live_event is not None
    assert live_event["args"][0]["live_question"]["status"] == "answer_shown"

    host.disconnect()


def test_question_cancel_broadcasts_cleared_live_question_and_queue(room):
    join_code, game, _ = room
    game.start_quiz()

    host = socketio.test_client(app)
    host.emit("host:join", {"room_id": join_code})
    host.get_received()

    p1 = socketio.test_client(app)
    p1.emit("player:join", {"name": "Ankur", "room_id": join_code})
    p1.get_received()
    host.get_received()

    p1.emit("player:buzz")
    host.get_received()
    p1.get_received()

    host.emit("host:question_reveal", {"question_id": "1:History:10"})
    host.get_received()

    host.emit("host:question_cancel")
    host_events = host.get_received()
    live_event = next((e for e in host_events if e["name"] == "state:live_question"), None)
    assert live_event is not None
    assert live_event["args"][0]["live_question"] is None
    queue_event = next((e for e in host_events if e["name"] == "state:queue"), None)
    assert queue_event is not None
    assert queue_event["args"][0]["queue"] == []

    p1_events = p1.get_received()
    p1_queue_event = next((e for e in p1_events if e["name"] == "state:queue"), None)
    assert p1_queue_event is not None
    assert p1_queue_event["args"][0]["queue"] == []

    host.disconnect()
    p1.disconnect()


def test_question_submit_broadcasts_to_whole_host_room(room):
    join_code, game, _ = room
    game.start_quiz()

    host1 = socketio.test_client(app)
    host1.emit("host:join", {"room_id": join_code})
    host1.get_received()

    host2 = socketio.test_client(app)
    host2.emit("host:join", {"room_id": join_code})
    host2.get_received()

    host1.emit("host:question_reveal", {"question_id": "1:History:10"})
    host1.get_received()
    host2.get_received()
    host1.emit("host:answer_reveal")
    host1.get_received()
    host2.get_received()

    host1.emit("host:question_submit", {"question_id": "1:History:10", "scores": {}})

    for client in (host1, host2):
        events_received = client.get_received()
        scores_event = next((e for e in events_received if e["name"] == "state:scores"), None)
        assert scores_event is not None, "both host tabs should receive state:scores on submit"

    host1.disconnect()
    host2.disconnect()


def test_question_submit_broadcasts_cleared_state_queue(room):
    join_code, game, _ = room
    game.start_quiz()

    host = socketio.test_client(app)
    host.emit("host:join", {"room_id": join_code})
    host.get_received()

    p1 = socketio.test_client(app)
    p1.emit("player:join", {"name": "Ankur", "room_id": join_code})
    p1.get_received()
    host.get_received()

    p1.emit("player:buzz")
    host.get_received()
    p1.get_received()

    host.emit("host:question_reveal", {"question_id": "1:History:10"})
    host.get_received()
    host.emit("host:answer_reveal")
    host.get_received()

    host.emit("host:question_submit", {"question_id": "1:History:10", "scores": {}})
    host_events = host.get_received()
    queue_event = next((e for e in host_events if e["name"] == "state:queue"), None)
    assert queue_event is not None
    assert queue_event["args"][0]["queue"] == []
    assert queue_event["args"][0]["locked"] is False

    host.disconnect()
    p1.disconnect()


def test_player_never_receives_state_live_question(room):
    join_code, game, _ = room
    game.start_quiz()

    host = socketio.test_client(app)
    host.emit("host:join", {"room_id": join_code})
    host.get_received()

    p1 = socketio.test_client(app)
    p1.emit("player:join", {"name": "Ankur", "room_id": join_code})
    p1.get_received()
    host.get_received()

    host.emit("host:question_reveal", {"question_id": "1:History:10"})
    host.get_received()
    host.emit("host:answer_reveal")
    host.get_received()
    host.emit("host:question_submit", {"question_id": "1:History:10", "scores": {}})
    host.get_received()

    p1_events = p1.get_received()
    assert not any(e["name"] == "state:live_question" for e in p1_events)
    for e in p1_events:
        for arg in e["args"]:
            _assert_no_content_leak(arg)

    host.disconnect()
    p1.disconnect()


def test_player_payloads_never_leak_question_or_answer(room):
    # Safety regression: golden rule 4 / SPEC V3.md §1 now carries real
    # teeth since state:scores' cell payloads include question/answer/media.
    join_code, _, _ = room

    host = socketio.test_client(app)
    host.emit("host:join", {"room_id": join_code})
    host.get_received()

    p1 = socketio.test_client(app)
    p1.emit("player:join", {"name": "Ankur", "room_id": join_code})
    all_events = p1.get_received()

    host.emit("host:start_quiz")
    host.get_received()
    all_events += p1.get_received()

    p1.emit("player:buzz")
    all_events += p1.get_received()

    for e in all_events:
        for arg in e["args"]:
            _assert_no_content_leak(arg)

    host.disconnect()
    p1.disconnect()


# ------------------------------------------------------------------
# B2: presentation view + board_select
# ------------------------------------------------------------------

def test_present_join_bootstraps_state_presentation_and_queue(room):
    join_code, game, _ = room
    game.start_quiz()

    present = socketio.test_client(app)
    present.emit("present:join", {"room_id": join_code})
    events_received = present.get_received()

    presentation_event = next((e for e in events_received if e["name"] == "state:presentation"), None)
    assert presentation_event is not None
    assert presentation_event["args"][0]["board_name"] == "1"

    queue_event = next((e for e in events_received if e["name"] == "state:queue"), None)
    assert queue_event is not None

    present.disconnect()


def test_negative_only_flag_propagates_to_scores_and_presentation(room):
    join_code, game, _ = room
    game.start_quiz()
    pid = game.roster_add("Ankur")

    present = socketio.test_client(app)
    present.emit("present:join", {"room_id": join_code})
    present.get_received()

    host = socketio.test_client(app)
    host.emit("host:join", {"room_id": join_code})
    host.get_received()

    host.emit("host:question_reveal", {"question_id": "1:History:10"})
    host.get_received()
    present.get_received()
    host.emit("host:answer_reveal")
    host.get_received()
    present.get_received()
    host.emit("host:question_submit", {"question_id": "1:History:10", "scores": {pid: -10}})
    host_events = host.get_received()
    present_events = present.get_received()

    scores_event = next(e for e in host_events if e["name"] == "state:scores")
    cell = scores_event["args"][0]["grid"]["1"]["History"]["10"]
    assert cell["negative_only"] is True

    presentation_event = next(e for e in present_events if e["name"] == "state:presentation")
    board_cell = presentation_event["args"][0]["board"]["History"]["10"]
    assert board_cell["negative_only"] is True

    host.disconnect()
    present.disconnect()


def test_presentation_board_never_leaks_question_or_answer(room):
    # Redaction regression, mirrors test_player_payloads_never_leak_question_or_answer
    # but scoped to state:presentation's "board" field — live_question
    # legitimately carries question/answer/media once revealed, so the
    # blanket _assert_no_content_leak helper can't apply to the whole payload.
    join_code, game, _ = room
    game.start_quiz()

    present = socketio.test_client(app)
    present.emit("present:join", {"room_id": join_code})
    all_events = present.get_received()

    host = socketio.test_client(app)
    host.emit("host:join", {"room_id": join_code})
    host.get_received()

    host.emit("host:question_reveal", {"question_id": "1:History:10"})
    host.get_received()
    all_events += present.get_received()

    host.emit("host:answer_reveal")
    host.get_received()
    all_events += present.get_received()

    host.emit("host:question_submit", {"question_id": "1:History:10", "scores": {}})
    host.get_received()
    all_events += present.get_received()

    presentation_events = [e for e in all_events if e["name"] == "state:presentation"]
    assert presentation_events, "expected at least one state:presentation broadcast"
    for e in presentation_events:
        board = e["args"][0].get("board", {})
        for category_cells in board.values():
            for cell in category_cells.values():
                assert "question" not in cell
                assert "answer" not in cell
                assert "question_media" not in cell
                assert "answer_media" not in cell

    host.disconnect()
    present.disconnect()


def test_presentation_never_receives_answer_until_answer_reveal(room):
    join_code, game, _ = room
    game.start_quiz()

    present = socketio.test_client(app)
    present.emit("present:join", {"room_id": join_code})
    present.get_received()

    host = socketio.test_client(app)
    host.emit("host:join", {"room_id": join_code})
    host.get_received()

    host.emit("host:question_reveal", {"question_id": "1:History:10"})
    host.get_received()
    presentation_event = next(e for e in present.get_received() if e["name"] == "state:presentation")
    assert "answer" not in presentation_event["args"][0]["live_question"]
    assert presentation_event["args"][0]["live_question"]["status"] == "revealed"

    host.emit("host:answer_reveal")
    host.get_received()
    presentation_event = next(e for e in present.get_received() if e["name"] == "state:presentation")
    assert presentation_event["args"][0]["live_question"]["answer"] == "A for 1:History:10"
    assert presentation_event["args"][0]["live_question"]["status"] == "answer_shown"

    host.disconnect()
    present.disconnect()


def test_question_submit_hard_gate_rejection_over_socket(room):
    join_code, game, _ = room
    game.start_quiz()

    host = socketio.test_client(app)
    host.emit("host:join", {"room_id": join_code})
    host.get_received()

    # No reveal at all — submit must be rejected by the hard gate.
    host.emit("host:question_submit", {"question_id": "1:History:10", "scores": {}})
    events_received = host.get_received()
    error_event = next((e for e in events_received if e["name"] == "error"), None)
    assert error_event is not None
    assert error_event["args"][0]["context"] == "question_submit"
    assert "1:History:10" not in game.closed_questions

    host.disconnect()


def test_board_select_broadcasts_to_presentation_room(room):
    join_code, game, _ = room
    boards = {
        "1": [BundleQuestion(id="1:History:10", board="1", category="History", value=10, question="Q1", answer="A1", question_media=[], answer_media=[])],
        "2": [BundleQuestion(id="2:Movies:10", board="2", category="Movies", value=10, question="Q2", answer="A2", question_media=[], answer_media=[])],
    }
    game.load_questions(boards)
    game.start_quiz()

    present = socketio.test_client(app)
    present.emit("present:join", {"room_id": join_code})
    present.get_received()

    host = socketio.test_client(app)
    host.emit("host:join", {"room_id": join_code})
    host.get_received()

    host.emit("host:board_select", {"board_index": 1})
    host.get_received()
    presentation_event = next(e for e in present.get_received() if e["name"] == "state:presentation")
    assert presentation_event["args"][0]["board_name"] == "2"
    assert game.current_board_index == 1

    host.disconnect()
    present.disconnect()


def test_board_select_rejected_while_question_live(room):
    join_code, game, _ = room
    game.start_quiz()

    host = socketio.test_client(app)
    host.emit("host:join", {"room_id": join_code})
    host.get_received()

    host.emit("host:question_reveal", {"question_id": "1:History:10"})
    host.get_received()

    host.emit("host:board_select", {"board_index": 0})
    events_received = host.get_received()
    error_event = next((e for e in events_received if e["name"] == "error"), None)
    assert error_event is not None
    assert error_event["args"][0]["context"] == "board_select"

    host.disconnect()
