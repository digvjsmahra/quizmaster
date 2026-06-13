# CLAUDE.md

Guidance for working in this repo with Claude Code. Read `SPEC.md` for *what* to
build; this file is *how* to build and run it, plus the rules that keep V1 from
sprawling.

## What this is

A real-time, single-game quiz buzzer. One host screen-shares questions over Zoom
and keeps score from a control center; ~10 players buzz in from phones over
WebSockets. Players join via a shared permalink and a name; the host works from
an unguessable `/host/<secret>` path. Flask + Flask-SocketIO backend, vanilla-JS
frontend with no build step, quiz content from a CSV, all state in memory.

## Golden rules (read before generating code)

1. **Single worker, always.** State is in-process and unshared. The app must run
   as exactly one worker (`gunicorn -k eventlet -w 1`). Never introduce
   multi-worker configs, and never add Redis / a message queue to "fix" it — for
   V1 the answer is one worker.
2. **In-memory only.** No database, no ORM, no on-disk persistence of game
   state. The CSV is read-only input. A restart wiping state is expected.
3. **No build step.** Frontend is server-rendered HTML + plain JS. Load the
   Socket.IO client from a CDN. Do not add npm, bundlers, frameworks, or
   transpilers.
4. **Never leak questions to players.** Question text and answers don't even live
   in this app — they're in the host's slide deck. Player-bound Socket.IO emits
   carry only join state and queue position. A question, answer, or **score** in
   a player payload is a bug.
5. **Scores are host-entered, and the host is the only scorer.** The host sends a
   number per cell (decimal or negative allowed); the server stores it verbatim.
   The server does **not** compute scores from the CSV — the CSV `value` is the
   face value, used only for tile labels and the `±value` quick-fill defaults.
   (This is deliberate: split-value scoring requires arbitrary host-entered
   awards.) Scoring happens on the scorecard against roster players, **never**
   from the buzz queue.
6. **Async mode is pinned to `eventlet`.** Do not substitute `gevent` or
   `threading`, even if a snippet you're adapting uses one of them. Mismatches
   between the worker invocation and the installed async library produce silent
   connection failures.
7. **Don't add features that aren't in SPEC.md.** If you find yourself wanting to
   add player avatars, sound effects, animations, a "spectator" role, an
   audience-facing board, or any other "nice touch" — stop and ask. The
   discipline for V1 is to ship the spec, not to embellish it.
8. **Stay in V1 scope.** No auth framework, no board authoring UI, no multi-game,
   no player- or audience-facing board/scoreboard, no presentation platform, no
   presence indicators. If a change needs one of those, stop and flag it rather
   than building it. See `SPEC.md §2` and §12.
9. **Keep dependencies minimal.** Flask, Flask-SocketIO, eventlet, gunicorn. CSV
   parsing uses the stdlib. Justify anything beyond that before adding it.

## Tech stack

- Python 3.11+, Flask, Flask-SocketIO.
- Async mode: `eventlet` (pinned).
- gunicorn for production serving.
- Vanilla JS + HTML; Socket.IO client via CDN.
- CSV for quiz content; in-memory Python objects for state.

## Proposed project layout

Adjust if there's a clearly better shape, but keep all game state in one module.

```
quiz-buzzer/
  app.py            # Flask app + SocketIO init + HTTP routes (/play, /host/<secret>)
  game.py           # in-memory state + all game logic (phase, roster, queue, scoring, undo/redo)
  events.py         # SocketIO event handlers (thin; delegate to game.py)
  quiz_loader.py    # CSV parsing (board, category, value) -> board grids
  data/quiz.csv     # quiz content (board, category, value); committed, swapped via redeploy
  templates/
    player.html     # join -> waiting -> buzzer -> queue-position (single page, JS-driven)
    host.html       # control center: per-board scorecard + queue
  static/
    js/player.js
    js/host.js
    css/styles.css
  requirements.txt
  README.md
  SPEC.md
  CLAUDE.md
```

The thin CSV is produced **offline** from the host's source (a one-time
transform). That utility is not part of the app's runtime and must not pull
answers or question text into the app.

Keep event handlers thin: validate input, call a `game.py` function, broadcast
the result. All state mutation lives in `game.py` so it can be unit-tested
without a socket.

## Commands

Setup:
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Run locally (dev):
```bash
python app.py        # uses socketio.run(...) with the eventlet async mode
```

Run in production (single worker is mandatory):
```bash
gunicorn -k eventlet -w 1 -b 0.0.0.0:${PORT:-8000} app:app
```

Tests:
```bash
pytest
```

## Conventions

- Put pure logic in `game.py` and test it directly; sockets should be a thin shell.
- Emit the **minimal** payload an audience needs; default to broadcasting derived
  state (e.g. the ordered queue) rather than raw internals. Never put scores,
  questions, or answers in a player-bound emit.
- Generate IDs server-side; never trust client-supplied IDs for authority.
- **No reconnection identity matching.** Buzz identities are disposable; the
  durable identity is the roster entry, snapshotted at "Start quiz." Don't add
  `name_key` matching or any other reconnection mechanism in V1.
- Player UI is one page that swaps views (join → waiting → buzzer → position) via
  JS state, driven by the game phase, not separate routes.
- Responsive layout for phones (sensible tap targets), but no design polish work.
- Fail loudly on a malformed CSV at startup rather than half-loading.

## Testing approach

- Unit-test `game.py`: join, buzz ordering (FIFO by arrival), freeze/reset,
  storing host-entered awards (including split, decimal, and negative values),
  undo/redo against the action log, the "Start quiz" roster snapshot, host
  `roster_add`, and cell-state derivation (Unplayed / Awarded / Passed).
- A light integration test with the Flask-SocketIO test client covering
  join → buzz → queue broadcast is enough for V1; skip browser/E2E tooling.

## Workflow expectations

- **Use plan mode for anything that touches more than one file.** Lay out the
  approach, show me the plan, let me push back, then implement. Don't vibe-code
  multi-file changes — especially around the socket protocol or the game state
  machine.
- **Don't read existing files just to "understand" them.** Read them when you
  need to make a specific change. Avoid generating long file summaries or
  "here's how this works" walkthroughs unless I ask for one.
- **Commit between features, not within them.** Each Claude Code session should
  end at a place where `git status` is clean and the app still runs.

## When unsure

If a request seems to require a database, auth, multiple games, pushing content
to players, an audience-facing board, board authoring, or a feature not listed in
`SPEC.md §2` (in scope), it's out of V1 scope — pause and confirm before
implementing. All previously-open questions have been resolved in `SPEC.md §10`;
don't re-litigate them.