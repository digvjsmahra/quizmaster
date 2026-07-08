import os
import secrets

import eventlet

eventlet.monkey_patch()

from flask import Flask, render_template, abort, redirect, request  # noqa: E402
from flask_socketio import SocketIO  # noqa: E402
from quiz_loader import load_quiz  # noqa: E402
from game import Game  # noqa: E402

# ------------------------------------------------------------------
# Startup — load quiz content once; fail loudly on bad CSV
# ------------------------------------------------------------------

questions = load_quiz("data/quiz.csv")
rooms: dict[str, dict] = {}  # join_code → {"game": Game, "host_token": str}

# ------------------------------------------------------------------
# Flask + SocketIO
# ------------------------------------------------------------------

app = Flask(__name__)
app.config["SECRET_KEY"] = secrets.token_hex(16)
socketio = SocketIO(app, async_mode="eventlet", cors_allowed_origins="*")


@app.route("/")
def index():
    return render_template("create.html")


@app.route("/rooms", methods=["POST"])
def create_room():
    host_token = secrets.token_urlsafe(16)
    game = Game(questions=questions)
    rooms[game.join_code] = {"game": game, "host_token": host_token}
    return redirect(f"/host/{game.join_code}/{host_token}")


@app.route("/host/<join_code>/<host_token>")
def host_page(join_code, host_token):
    room = rooms.get(join_code)
    if not room or room["host_token"] != host_token:
        abort(404)
    return render_template("host.html", join_code=join_code)


@app.route("/play/<join_code>")
def player_page(join_code):
    if join_code not in rooms:
        abort(404)
    return render_template("player.html", code=join_code)


import events  # noqa: E402
events.register(socketio, rooms)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    socketio.run(app, debug=True, use_reloader=False, port=port)
