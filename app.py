import os
import secrets

import eventlet

eventlet.monkey_patch()

from flask import Flask, render_template, abort  # noqa: E402
from flask_socketio import SocketIO  # noqa: E402
from quiz_loader import load_quiz  # noqa: E402
from game import Game  # noqa: E402

# ------------------------------------------------------------------
# Startup checks — fail loudly
# ------------------------------------------------------------------

HOST_SECRET = os.environ.get("HOST_SECRET")
if not HOST_SECRET:
    raise SystemExit(
        "ERROR: HOST_SECRET environment variable is not set.\n"
        "Set it with: export HOST_SECRET=$(python -c \"import secrets; print(secrets.token_urlsafe(16))\")"
    )

questions = load_quiz("data/quiz.csv")
game = Game(questions=questions)

# ------------------------------------------------------------------
# Flask + SocketIO
# ------------------------------------------------------------------

app = Flask(__name__)
app.config["SECRET_KEY"] = secrets.token_hex(16)
socketio = SocketIO(app, async_mode="eventlet", cors_allowed_origins="*")


@app.route("/play/<code>")
def player_page(code):
    if code != game.join_code:
        abort(404)
    return render_template("player.html", code=code)


@app.route("/host/<secret>")
def host_page(secret):
    if secret != HOST_SECRET:
        abort(404)
    return render_template("host.html", join_url=f"/play/{game.join_code}")


import events  # noqa: F401, E402 — registers socket handlers

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    print(f"  Host URL : http://localhost:{port}/host/{HOST_SECRET}")
    print(f"  Player URL: http://localhost:{port}/play/{game.join_code}")
    socketio.run(app, debug=True, port=port)
