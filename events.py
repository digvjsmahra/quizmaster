from flask import request
from flask_socketio import emit, join_room

_sid_player: dict[str, str] = {}  # sid → player_id
_sid_room: dict[str, str] = {}    # sid → join_code


def register(socketio, rooms):
    @socketio.on("disconnect")
    def on_disconnect():
        join_code = _sid_room.pop(request.sid, None)
        pid = _sid_player.pop(request.sid, None)
        if join_code and pid:
            room = rooms.get(join_code)
            if room and pid in room["game"].players:
                room["game"].players[pid].connected = False
                socketio.emit("state:players", {"players": room["game"].get_active_players()}, to=f"players_{join_code}")

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
        if phase == "live":
            emit("state:queue", room["game"].get_queue_payload())
        socketio.emit("state:players", {"players": room["game"].get_active_players()}, to=f"players_{join_code}")
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
            socketio.emit("state:queue", payload, to=f"present_{join_code}")

    @socketio.on("host:join")
    def on_host_join(data):
        join_code = (data.get("room_id") or "").strip()
        room = rooms.get(join_code)
        if not room:
            emit("error", {"message": "Room not found.", "context": "host_join"})
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
        try:
            room["game"].start_quiz()
        except ValueError as e:
            emit("error", {"message": str(e), "context": "start_quiz"})
            return
        socketio.emit("state:phase", {"phase": "live"}, to=f"players_{join_code}")
        socketio.emit("state:players", {"players": room["game"].get_active_players()}, to=f"players_{join_code}")
        socketio.emit("state:phase", {"phase": "live"}, to=f"host_{join_code}")
        emit("state:scores", room["game"].get_scores_payload())
        socketio.emit("state:presentation", room["game"].get_presentation_payload(), to=f"present_{join_code}")

    @socketio.on("host:roster_add")
    def on_roster_add(data):
        join_code = _sid_room.get(request.sid)
        room = rooms.get(join_code) if join_code else None
        if not room:
            return
        name = (data.get("name") or "").strip()
        if not name:
            emit("error", {"message": "Name cannot be empty.", "context": "roster_add"})
            return
        try:
            room["game"].roster_add(name)
        except ValueError as e:
            emit("error", {"message": str(e), "context": "roster_add"})
            return
        socketio.emit("state:players", {"players": room["game"].get_active_players()}, to=f"players_{join_code}")
        emit("state:scores", room["game"].get_scores_payload())
        socketio.emit("state:presentation", room["game"].get_presentation_payload(), to=f"present_{join_code}")

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
        socketio.emit("state:queue", payload, to=f"present_{join_code}")

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
        socketio.emit("state:queue", payload, to=f"present_{join_code}")

    @socketio.on("host:question_reveal")
    def on_question_reveal(data):
        join_code = _sid_room.get(request.sid)
        room = rooms.get(join_code) if join_code else None
        if not room:
            return
        question_id = (data or {}).get("question_id")
        try:
            room["game"].question_reveal(question_id)
        except ValueError as e:
            emit("error", {"message": str(e), "context": "question_reveal"})
            return
        socketio.emit("state:live_question", room["game"].get_live_question_payload(), to=f"host_{join_code}")
        socketio.emit("state:presentation", room["game"].get_presentation_payload(), to=f"present_{join_code}")

    @socketio.on("host:answer_reveal")
    def on_answer_reveal():
        join_code = _sid_room.get(request.sid)
        room = rooms.get(join_code) if join_code else None
        if not room:
            return
        try:
            room["game"].answer_reveal()
        except ValueError as e:
            emit("error", {"message": str(e), "context": "answer_reveal"})
            return
        socketio.emit("state:live_question", room["game"].get_live_question_payload(), to=f"host_{join_code}")
        socketio.emit("state:presentation", room["game"].get_presentation_payload(), to=f"present_{join_code}")

    @socketio.on("host:question_cancel")
    def on_question_cancel():
        join_code = _sid_room.get(request.sid)
        room = rooms.get(join_code) if join_code else None
        if not room:
            return
        try:
            room["game"].question_cancel()
        except ValueError as e:
            emit("error", {"message": str(e), "context": "question_cancel"})
            return
        socketio.emit("state:live_question", room["game"].get_live_question_payload(), to=f"host_{join_code}")
        socketio.emit("state:presentation", room["game"].get_presentation_payload(), to=f"present_{join_code}")
        queue_payload = room["game"].get_queue_payload()
        socketio.emit("state:queue", queue_payload, to=f"players_{join_code}")
        socketio.emit("state:queue", queue_payload, to=f"host_{join_code}")
        socketio.emit("state:queue", queue_payload, to=f"present_{join_code}")

    @socketio.on("host:question_submit")
    def on_question_submit(data):
        join_code = _sid_room.get(request.sid)
        room = rooms.get(join_code) if join_code else None
        if not room:
            return
        question_id = data.get("question_id")
        raw_scores = data.get("scores") or {}

        if not question_id or not room["game"].question_exists(question_id):
            emit("error", {"message": "Unknown question.", "context": "question_submit"})
            return

        scores: dict[str, float] = {}
        for pid, val in raw_scores.items():
            try:
                scores[pid] = float(val)
            except (TypeError, ValueError):
                pass

        try:
            room["game"].question_submit(question_id, scores)
        except ValueError as e:
            emit("error", {"message": str(e), "context": "question_submit"})
            return

        socketio.emit("state:scores", room["game"].get_scores_payload(), to=f"host_{join_code}")
        socketio.emit("state:live_question", room["game"].get_live_question_payload(), to=f"host_{join_code}")
        socketio.emit("state:presentation", room["game"].get_presentation_payload(), to=f"present_{join_code}")
        queue_payload = room["game"].get_queue_payload()
        socketio.emit("state:queue", queue_payload, to=f"players_{join_code}")
        socketio.emit("state:queue", queue_payload, to=f"host_{join_code}")
        socketio.emit("state:queue", queue_payload, to=f"present_{join_code}")

    @socketio.on("host:board_select")
    def on_board_select(data):
        join_code = _sid_room.get(request.sid)
        room = rooms.get(join_code) if join_code else None
        if not room:
            return
        try:
            room["game"].select_board((data or {}).get("board_index"))
        except (ValueError, TypeError) as e:
            emit("error", {"message": str(e), "context": "board_select"})
            return
        socketio.emit("state:presentation", room["game"].get_presentation_payload(), to=f"present_{join_code}")

    @socketio.on("present:join")
    def on_present_join(data):
        join_code = (data.get("room_id") or "").strip()
        room = rooms.get(join_code)
        if not room:
            emit("error", {"message": "Room not found.", "context": "present_join"})
            return
        join_room(f"present_{join_code}")
        emit("state:presentation", room["game"].get_presentation_payload())
        emit("state:queue", room["game"].get_queue_payload())
