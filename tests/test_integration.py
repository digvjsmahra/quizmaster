import secrets
import pytest

from app import app, socketio, rooms, questions
from game import Game


@pytest.fixture(autouse=True)
def room():
    rooms.clear()
    g = Game(questions=questions)
    host_token = secrets.token_urlsafe(16)
    rooms[g.join_code] = {"game": g, "host_token": host_token}
    yield g.join_code, g, host_token
    rooms.clear()


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
