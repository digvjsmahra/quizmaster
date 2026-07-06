from flask import request
from flask_socketio import emit, join_room

from app import socketio, game

# Maps socket session id → player_id (players only)
_sid_player: dict[str, str] = {}


@socketio.on("disconnect")
def on_disconnect():
    pid = _sid_player.pop(request.sid, None)
    if pid and pid in game.players:
        game.players[pid].connected = False


@socketio.on("player:join")
def on_player_join(data):
    name = (data.get("name") or "").strip()
    if not name:
        emit("player:rejected", {"reason": "Name cannot be empty."})
        return
    try:
        player_id, phase = game.player_join(name)
    except ValueError as e:
        emit("player:rejected", {"reason": str(e)})
        return

    _sid_player[request.sid] = player_id
    join_room("players")
    emit("player:accepted", {"player_id": player_id, "phase": phase})
    socketio.emit("state:players", {"players": game.get_lobby_players()}, to="host")


@socketio.on("player:buzz")
def on_player_buzz():
    player_id = _sid_player.get(request.sid)
    if not player_id:
        return
    result = game.player_buzz(player_id)
    if result is not None:
        payload = game.get_queue_payload()
        socketio.emit("state:queue", payload, to="players")
        socketio.emit("state:queue", payload, to="host")


@socketio.on("host:join")
def on_host_join():
    join_room("host")
    emit("state:full", game.get_full_state())


@socketio.on("host:start_quiz")
def on_start_quiz():
    game.start_quiz()
    socketio.emit("state:phase", {"phase": "live"}, to="players")
    socketio.emit("state:phase", {"phase": "live"}, to="host")
    emit("state:scores", game.get_scores_payload())


@socketio.on("host:roster_add")
def on_roster_add(data):
    name = (data.get("name") or "").strip()
    if not name:
        emit("error", {"message": "Name cannot be empty."})
        return
    try:
        game.roster_add(name)
    except ValueError as e:
        emit("error", {"message": str(e)})
        return
    emit("state:scores", game.get_scores_payload())


@socketio.on("host:queue_freeze")
def on_queue_freeze():
    game.queue_freeze()
    payload = game.get_queue_payload()
    socketio.emit("state:queue", payload, to="players")
    socketio.emit("state:queue", payload, to="host")


@socketio.on("host:queue_reset")
def on_queue_reset():
    game.queue_reset()
    payload = game.get_queue_payload()
    socketio.emit("state:queue", payload, to="players")
    socketio.emit("state:queue", payload, to="host")


@socketio.on("host:question_submit")
def on_question_submit(data):
    question_id = data.get("question_id")
    raw_scores = data.get("scores") or {}

    if not question_id or not game.question_exists(question_id):
        emit("error", {"message": "Unknown question."})
        return

    scores: dict[str, float] = {}
    for pid, val in raw_scores.items():
        try:
            scores[pid] = float(val)
        except (TypeError, ValueError):
            pass

    game.question_submit(question_id, scores)
    emit("state:scores", game.get_scores_payload())
