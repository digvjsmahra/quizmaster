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
        question=f"Q for {id}", answer=f"A for {id}", media=[],
    )


DEFAULT_QUESTIONS = {
    "1": [
        _q("1:History:10", "1", "History", 10),
        _q("1:History:20", "1", "History", 20),
    ],
}


def _make_bundle_bytes(rows, media_files=None):
    columns = ["board", "category", "value", "question", "answer", "media"]
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
            assert k not in ("question", "answer", "media"), f"leaked key {k!r} in player payload: {obj}"
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
        [{"board": "1", "category": "History", "value": 10, "question": "Q1", "answer": "A1", "media": "pic.jpg"}],
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
        [{"board": "1", "category": "History", "value": 10, "question": "Q1", "answer": "A1", "media": "pic.jpg"}],
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
