# CLAUDE.md

Guidance for Claude Code in this repo. `SPEC.md` is *what* to build; this file is *how*.

## What this is

A real-time quiz buzzer. The host creates a room from the landing page and gets a unique control center URL; ~10 players join via a shared link or by entering a 4-char room code at the landing page. Flask + Flask-SocketIO backend, vanilla-JS frontend, no build step, quiz content from a CSV, all state in memory.

## Golden rules

1. **Single worker.** State is in-process and unshared. Run as exactly one worker (`gunicorn -k eventlet -w 1`). Never add Redis or a message queue.
2. **In-memory only.** No database, no ORM, no on-disk persistence. CSV is read-only input.
3. **No build step.** Vanilla JS + server-rendered HTML. Socket.IO client from CDN. No npm, bundlers, or transpilers.
4. **Never leak questions or scores to players.** Player-bound emits carry only join state and queue position. Any question, answer, or score in a player payload is a bug.
5. **Host enters scores; server stores verbatim.** Awards may be decimal or negative. The server never computes scores from the CSV — `value` is used only for tile labels and `±value` quick-fill defaults. Scoring is always against roster entries, never from the queue.
6. **No undo/redo.** The always-open scorecard grid is the correction mechanism — the host re-clicks a cell and re-submits.
7. **Async mode is pinned to `eventlet`.** Do not substitute `gevent` or `threading`.
8. **No features beyond `SPEC.md §2`.** Stop and ask before building anything not listed there.
9. **Minimal dependencies.** Flask, Flask-SocketIO, eventlet, gunicorn. stdlib csv for parsing. Justify anything else.

## Tech stack

Python 3.11+, Flask, Flask-SocketIO, eventlet, gunicorn. Vanilla JS + HTML; Socket.IO client via CDN. CSV for quiz content; in-memory Python objects for state.

## Project layout

```
app.py            # Flask app + SocketIO init + HTTP routes
game.py           # all in-memory state and game logic (phase, roster, queue, scoring)
events.py         # SocketIO event handlers — thin wrappers that delegate to game.py
quiz_loader.py    # CSV parsing -> board grids; fails loudly on malformed input
data/quiz.csv     # board, category, value (thin 3-column; produced offline by host)
templates/
  create.html     # landing page — join by code or create a new room
  player.html     # waiting → buzzer → queue-position (single page, JS-driven)
  host.html       # control center: board scorecard + queue + totals
static/
  js/create.js    # OTP input logic, code validation, redirect
  js/player.js
  js/host.js
  css/styles.css
requirements.txt
```

## Configuration

No required env vars. Rooms are created dynamically via the landing page UI; each room has a server-generated per-room host token. No HOST_SECRET needed.

The quiz content is loaded from `data/quiz.csv` at startup. Swap the file and redeploy to change questions. A restart wipes all in-memory room state — acceptable, as each quiz is a single session.

## Commands

```bash
# Setup
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Dev
python app.py

# Production (single worker is mandatory)
gunicorn -k eventlet -w 1 -b 0.0.0.0:${PORT:-8000} app:app

# Tests
pytest
```

## Conventions

- Pure logic in `game.py`; event handlers in `events.py` are thin shells (validate → call game.py → broadcast).
- Emit minimal derived state (e.g. the full ordered queue), never raw internals.
- Generate IDs server-side; never trust client-supplied IDs.
- No reconnection identity matching. Buzz identities are disposable; roster entries are durable.
- Player UI is one page — buzzer view contains the queue list and sections inline; no separate route or view for queue position.
- Responsive layout for phones; no design polish.
- Fail loudly on malformed CSV at startup.
- **Two player identity types**: `virtual=False` (real socket connection, joined via URL/code) and `virtual=True` (host-added scorecard entry, no socket). `get_active_players()` returns only non-virtual connected players for `state:players`. The roster contains both; the scorecard shows both; player phones see only non-virtual.
- **`state:players`** is the player-facing presence event (who's in the room). **`state:roster`** does not exist — the host's roster is derived from `state:scores`. Never conflate the two.

## Testing

Unit-test `game.py`: join, FIFO buzz ordering, freeze/reset, host-entered awards (split/decimal/negative), `question_submit` overwrites, Start roster snapshot (real players only, not virtual), `roster_add` virtual flag, `get_active_players` excludes virtual, cell-state derivation (Unplayed/Awarded/Passed), per-board and cumulative totals.

Integration tests with the Flask-SocketIO test client: join → buzz → queue broadcast; room validation (valid/invalid/case-insensitive); late joiner behind frozen queue. No browser/E2E tooling.

## Workflow

- **Plan mode for anything touching more than one file.**
- Read files only when making a specific change, not to "understand" them.
- Commit between features, not within them.

## When unsure

Pause and confirm if a request requires a database, auth, multiple games, pushing content to players, board authoring, undo/redo, or anything not in `SPEC.md §2`.
