import os
import secrets
import tempfile

import eventlet

eventlet.monkey_patch()

from flask import Flask, render_template, abort, redirect, request, send_from_directory  # noqa: E402
from flask_socketio import SocketIO  # noqa: E402
from bundle_loader import extract_media, parse_bundle  # noqa: E402
from game import Game  # noqa: E402

# ------------------------------------------------------------------
# Server boots content-less — quiz content is uploaded per-room at
# runtime (see the /upload route below), not loaded at startup.
# ------------------------------------------------------------------

rooms: dict[str, dict] = {}  # join_code → {"game": Game, "host_token": str}

# ------------------------------------------------------------------
# Flask + SocketIO
# ------------------------------------------------------------------

app = Flask(__name__)
app.config["SECRET_KEY"] = secrets.token_hex(16)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB — bundles carry images
socketio = SocketIO(app, async_mode="eventlet", cors_allowed_origins="*")


@app.route("/")
def index():
    return render_template("create.html")


@app.route("/rooms", methods=["POST"])
def create_room():
    host_token = secrets.token_urlsafe(16)
    game = Game()
    rooms[game.join_code] = {"game": game, "host_token": host_token}
    return redirect(f"/host/{game.join_code}/{host_token}")


@app.route("/host/<join_code>/<host_token>")
def host_page(join_code, host_token):
    room = rooms.get(join_code)
    if not room or room["host_token"] != host_token:
        abort(404)
    return render_template("host.html", join_code=join_code, host_token=host_token)


@app.route("/host/<join_code>/<host_token>/upload", methods=["POST"])
def upload_bundle(join_code, host_token):
    room = rooms.get(join_code)
    if not room or room["host_token"] != host_token:
        abort(404)
    if room["game"].phase == "live":
        return {"errors": [{"row": None, "message": "Quiz is already live — create a new room to load a different bundle."}], "warnings": []}, 409

    uploaded = request.files.get("bundle")
    if not uploaded:
        return {"errors": [{"row": None, "message": "No file was uploaded."}], "warnings": []}, 400

    result = parse_bundle(uploaded.stream)
    if result.errors:
        return {
            "errors": [{"row": e.row, "message": e.message} for e in result.errors],
            "warnings": result.warnings,
        }, 422

    room["game"].load_questions(result.boards)
    room["game"].media_dir = None
    if result.media_names:
        media_dir = tempfile.mkdtemp(prefix=f"room_{join_code}_")
        extract_media(uploaded.stream, media_dir)
        room["game"].media_dir = media_dir

    socketio.emit("state:scores", room["game"].get_scores_payload(), to=f"host_{join_code}")
    return {"errors": [], "warnings": result.warnings}, 200


@app.route("/media/<join_code>/<host_token>/<filename>")
def media_file(join_code, host_token, filename):
    room = rooms.get(join_code)
    if not room or room["host_token"] != host_token:
        abort(404)
    if not room["game"].media_dir:
        abort(404)
    return send_from_directory(room["game"].media_dir, filename)


@app.route("/rooms/<join_code>/validate")
def validate_room(join_code):
    if join_code.upper() in rooms:
        return {"valid": True}
    return {"valid": False}, 404


@app.route("/play/<join_code>")
def player_page(join_code):
    if join_code.upper() not in rooms:
        abort(404)
    return render_template("player.html", code=join_code.upper())


import events  # noqa: E402
events.register(socketio, rooms)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    socketio.run(app, debug=True, use_reloader=False, port=port)
