# CLAUDE.md

Guidance for Claude Code in this repo. `SPEC.md` is *what* to build; this file is *how*.

## What this is

A real-time, single-game quiz buzzer. One host screen-shares questions over Zoom and keeps score from a control center at `/host/<secret>`; ~10 players buzz in from phones via a shared permalink. Flask + Flask-SocketIO backend, vanilla-JS frontend, no build step, quiz content from a CSV, all state in memory.

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
app.py            # Flask app + SocketIO init + HTTP routes (/play/<code>, /host/<secret>)
game.py           # all in-memory state and game logic (phase, roster, queue, scoring)
events.py         # SocketIO event handlers — thin wrappers that delegate to game.py
quiz_loader.py    # CSV parsing -> board grids; fails loudly on malformed input
data/quiz.csv     # board, category, value (thin 3-column; produced offline by host)
templates/
  player.html     # join → waiting → buzzer → queue-position (single page, JS-driven)
  host.html       # control center: board scorecard + queue + totals
static/
  js/player.js
  js/host.js
  css/styles.css
requirements.txt
```

## Configuration

`HOST_SECRET` — required env var. If missing, fail loudly at startup (same pattern as the CSV loader). Never auto-generate; never write to a file.

- Local dev: `export HOST_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(16))")` once, then reuse.
- Production: set in PaaS config vars. The value is stable across restarts/redeploys.
- Do **not** add `python-dotenv`.

The join code (`/play/<code>`) is auto-generated at startup and displayed on the host page for copying. It resets on restart, which is fine — a restart wipes all game state anyway.

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
- Player UI is one page swapping views via JS state driven by phase events, not separate routes.
- Responsive layout for phones; no design polish.
- Fail loudly on malformed CSV at startup.

## Testing

Unit-test `game.py`: join, FIFO buzz ordering, freeze/reset, host-entered awards (split/decimal/negative), `question_submit` overwrites, Start roster snapshot, `roster_add`, cell-state derivation (Unplayed/Awarded/Passed), per-board and cumulative totals.

One integration test with the Flask-SocketIO test client: join → buzz → queue broadcast. No browser/E2E tooling.

## Workflow

- **Plan mode for anything touching more than one file.**
- Read files only when making a specific change, not to "understand" them.
- Commit between features, not within them.

## When unsure

Pause and confirm if a request requires a database, auth, multiple games, pushing content to players, board authoring, undo/redo, or anything not in `SPEC.md §2`.
