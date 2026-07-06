import os
import pytest

os.environ.setdefault("HOST_SECRET", "test-secret-token")

from app import app, socketio, game  # noqa: E402
from quiz_loader import Question  # noqa: E402


@pytest.fixture(autouse=True)
def reset_game():
    """Reset game state before each test."""
    game.players.clear()
    game.roster.clear()
    game.queue.clear()
    game.queue_locked = False
    game.scores.clear()
    game.closed_questions.clear()
    game.phase = "lobby"
    yield


def test_join_buzz_queue_broadcast():
    host_client = socketio.test_client(app)
    p1_client = socketio.test_client(app)
    p2_client = socketio.test_client(app)

    # Host joins
    host_client.emit("host:join")
    host_received = host_client.get_received()
    assert any(e["name"] == "state:full" for e in host_received)

    # Two players join
    p1_client.emit("player:join", {"name": "Ankur"})
    p1_events = p1_client.get_received()
    accepted = next(e for e in p1_events if e["name"] == "player:accepted")
    p1_id = accepted["args"][0]["player_id"]
    assert accepted["args"][0]["phase"] == "lobby"

    p2_client.emit("player:join", {"name": "Dev"})
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
    p1_client.get_received()  # clear
    p2_client.get_received()

    p1_client.emit("player:buzz")
    p2_client.emit("player:buzz")

    # Host should see queue with p1 before p2 (FIFO)
    host_client.get_received()  # flush any phase/score events
    # Give eventlet a moment then check game state directly
    queue = game.get_queue_payload()["queue"]
    assert len(queue) == 2
    assert queue[0]["player_id"] == p1_id
    assert queue[1]["player_id"] == p2_id

    host_client.disconnect()
    p1_client.disconnect()
    p2_client.disconnect()


def test_player_rejected_on_empty_name():
    client = socketio.test_client(app)
    client.emit("player:join", {"name": ""})
    events = client.get_received()
    assert any(e["name"] == "player:rejected" for e in events)
    client.disconnect()


def test_host_sees_correct_join_url():
    host_client = socketio.test_client(app)
    host_client.emit("host:join")
    events = host_client.get_received()
    full = next(e for e in events if e["name"] == "state:full")
    assert full["args"][0]["join_code"] == game.join_code
    host_client.disconnect()
