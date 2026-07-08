from flask import request
from flask_socketio import emit, join_room

_sid_player: dict[str, str] = {}  # sid → player_id
_sid_room: dict[str, str] = {}    # sid → join_code


def _roster_names(game) -> list[str]:
    return [game.players[pid].name for pid in game.roster if pid in game.players]


def register(socketio, rooms):
    @socketio.on("disconnect")
    def on_disconnect():
        join_code = _sid_room.pop(request.sid, None)
        pid = _sid_player.pop(request.sid, None)
        if join_code and pid:
            room = rooms.get(join_code)
            if room and pid in room["game"].players:
                room["game"].players[pid].connected = False

    @socketio.on("player:join")
    def on_player_join(data):
        join_code = (data.get("room_id") or "").strip()
        room = rooms.get(join_code)
        if not room:
            emit("player:rejected", {"reason": "Room not found."})
            return
        name = (data.get("name") or "").strip()
        if not name:
            emit("player:rejected", {"reason": "Name cannot be empty."})
            return
        try:
            player_id, phase = room["game"].player_join(name)
        except ValueError as e:
            emit("player:rejected", {"reason": str(e)})
            return

        _sid_player[request.sid] = player_id
        _sid_room[request.sid] = join_code
        join_room(f"players_{join_code}")
        emit("player:accepted", {"player_id": player_id, "phase": phase})
        socketio.emit("state:players", {"players": room["game"].get_lobby_players()}, to=f"host_{join_code}")

    @socketio.on("player:buzz")
    def on_player_buzz():
        join_code = _sid_room.get(request.sid)
        player_id = _sid_player.get(request.sid)
        if not join_code or not player_id:
            return
        room = rooms.get(join_code)
        if not room:
            return
        result = room["game"].player_buzz(player_id)
        if result is not None:
            payload = room["game"].get_queue_payload()
            socketio.emit("state:queue", payload, to=f"players_{join_code}")
            socketio.emit("state:queue", payload, to=f"host_{join_code}")

    @socketio.on("host:join")
    def on_host_join(data):
        join_code = (data.get("room_id") or "").strip()
        room = rooms.get(join_code)
        if not room:
            emit("error", {"message": "Room not found."})
            return
        _sid_room[request.sid] = join_code
        join_room(f"host_{join_code}")
        emit("state:full", room["game"].get_full_state())

    @socketio.on("host:start_quiz")
    def on_start_quiz():
        join_code = _sid_room.get(request.sid)
        room = rooms.get(join_code) if join_code else None
        if not room:
            return
        room["game"].start_quiz()
        roster_payload = {"names": _roster_names(room["game"])}
        socketio.emit("state:phase", {"phase": "live"}, to=f"players_{join_code}")
        socketio.emit("state:roster", roster_payload, to=f"players_{join_code}")
        socketio.emit("state:phase", {"phase": "live"}, to=f"host_{join_code}")
        emit("state:scores", room["game"].get_scores_payload())

    @socketio.on("host:roster_add")
    def on_roster_add(data):
        join_code = _sid_room.get(request.sid)
        room = rooms.get(join_code) if join_code else None
        if not room:
            return
        name = (data.get("name") or "").strip()
        if not name:
            emit("error", {"message": "Name cannot be empty."})
            return
        try:
            room["game"].roster_add(name)
        except ValueError as e:
            emit("error", {"message": str(e)})
            return
        socketio.emit("state:roster", {"names": _roster_names(room["game"])}, to=f"players_{join_code}")
        emit("state:scores", room["game"].get_scores_payload())

    @socketio.on("host:queue_freeze")
    def on_queue_freeze():
        join_code = _sid_room.get(request.sid)
        room = rooms.get(join_code) if join_code else None
        if not room:
            return
        room["game"].queue_freeze()
        payload = room["game"].get_queue_payload()
        socketio.emit("state:queue", payload, to=f"players_{join_code}")
        socketio.emit("state:queue", payload, to=f"host_{join_code}")

    @socketio.on("host:queue_reset")
    def on_queue_reset():
        join_code = _sid_room.get(request.sid)
        room = rooms.get(join_code) if join_code else None
        if not room:
            return
        room["game"].queue_reset()
        payload = room["game"].get_queue_payload()
        socketio.emit("state:queue", payload, to=f"players_{join_code}")
        socketio.emit("state:queue", payload, to=f"host_{join_code}")

    @socketio.on("host:question_submit")
    def on_question_submit(data):
        join_code = _sid_room.get(request.sid)
        room = rooms.get(join_code) if join_code else None
        if not room:
            return
        question_id = data.get("question_id")
        raw_scores = data.get("scores") or {}

        if not question_id or not room["game"].question_exists(question_id):
            emit("error", {"message": "Unknown question."})
            return

        scores: dict[str, float] = {}
        for pid, val in raw_scores.items():
            try:
                scores[pid] = float(val)
            except (TypeError, ValueError):
                pass

        room["game"].question_submit(question_id, scores)
        emit("state:scores", room["game"].get_scores_payload())
