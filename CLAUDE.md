# CLAUDE.md

Guidance for Claude Code in this repo. `SPEC.md` is *what* to build; this file is *how*.

## What this is

A real-time quiz buzzer. The host creates a room from the landing page and gets a unique control center URL; ~10 players join via a shared link or by entering a 4-char room code at the landing page. Flask + Flask-SocketIO backend, vanilla-JS frontend, no build step, quiz content uploaded per-room as an xlsx/zip bundle, all state in memory.

## Spec precedence

`SPEC.md` is the V1 baseline; each major version's changes live in its own `SPEC V<N>.md` delta file. Current chain: `SPEC.md` → `SPEC V3.md`.

- Where a `SPEC.md` (or older delta) section carries an explicit `[Superseded by V<N> — see ...]` / `[V3: ...]` pointer, the newer file is authoritative for that section — follow it without asking.
- Where a section *conflicts* with a newer file but carries **no** pointer, do not silently assume either file is right. Some apparent conflicts are actually deliberate, coexisting distinctions rather than contradictions to resolve — e.g. the buzz-identity vs. roster separation: a real player joining via URL/code reflects immediately on the player-facing buzzer/queue (`virtual=False`), but the control-center roster only reflects the Start-time snapshot or host-added (`virtual=True`) entries. Read superficially, "player joins" vs. "roster doesn't update" looks like a contradiction; it's actually two intentionally separate tracks (see "Two player identity types" below). Flag the apparent conflict to the user and ask which reading is intended before acting on it — never resolve it unilaterally by assuming the newer file just wins.

## Golden rules

1. **Single worker.** State is in-process and unshared. Run as exactly one worker (`gunicorn -k eventlet -w 1`). Never add Redis or a message queue.
2. **In-memory only.** No database, no ORM, no durable on-disk persistence. Quiz content comes from a per-room uploaded bundle (SPEC V3.md §3), held in memory plus an ephemeral temp-dir for media, wiped on restart.
3. **No build step.** Vanilla JS + server-rendered HTML. Socket.IO client from CDN. No npm, bundlers, or transpilers.
4. **Never leak questions or scores to players.** Player-bound emits carry only join state and queue position. Any question, answer, or score in a player payload is a bug. (V3 widens this to routes and media, not just payloads — see SPEC V3.md §1.)
5. **Host enters scores; server stores verbatim.** Awards may be decimal or negative. The server never computes scores from the uploaded quiz content — `value` is used only for tile labels and `±value` quick-fill defaults. Scoring is always against roster entries, never from the queue.
6. **No undo/redo.** The always-open scorecard grid is the correction mechanism — the host re-clicks a cell and re-submits. (V3's `question_cancel` is a pre-score reveal-undo, not a scoring undo — distinct from this rule. See SPEC V3.md §4. This rule still governs scoring corrections.)
7. **Async mode is pinned to `eventlet`.** Do not substitute `gevent` or `threading`.
8. **No features beyond `SPEC.md §2` or `SPEC V3.md`'s delta scope.** Stop and ask before building anything not listed in either.
9. **Minimal dependencies.** Flask, Flask-SocketIO, eventlet, gunicorn, `openpyxl`. Justify anything else. (`openpyxl` added for V3 Session A's xlsx bundle parser — justified in SPEC V3.md §3. The V1/V2 CSV path and its stdlib `csv` usage were retired in A2.)

## Tech stack

Python 3.11+, Flask, Flask-SocketIO, eventlet, gunicorn, openpyxl. Vanilla JS + HTML; Socket.IO client via CDN. Quiz content from a per-room uploaded xlsx/zip bundle; in-memory Python objects for state.

## Project layout

```
app.py            # Flask app + SocketIO init + HTTP routes (incl. per-room upload + media routes)
game.py           # all in-memory state and game logic (phase, roster, queue, scoring)
events.py         # SocketIO event handlers — thin wrappers that delegate to game.py
bundle_loader.py  # V3 zip/xlsx bundle parser + validation + media extraction (SPEC V3.md §3)
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

V3 will still add `present.html` and `static/js/present.js` — not yet present; see `SPEC V3.md` when B1/B2 land.

## Configuration

No required env vars. Rooms are created dynamically via the landing page UI; each room has a server-generated per-room host token. No HOST_SECRET needed.

The server boots with no quiz content. Each room's QM uploads a quiz bundle (`.zip` of `quiz.xlsx` + optional `media/`) from the control center before the quiz can start — content is per-room, not shared. A restart wipes all in-memory room state, including uploaded content — acceptable, as each quiz is a single session and the QM re-uploads.

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
- Fail loudly on a malformed upload: every row's errors surface together in one pass, not just the first.
- **Two player identity types**: `virtual=False` (real socket connection, joined via URL/code) and `virtual=True` (host-added scorecard entry, no socket). `get_active_players()` returns only non-virtual connected players for `state:players`. The roster contains both; the scorecard shows both; player phones see only non-virtual.
- **`state:players`** is the player-facing presence event (who's in the room). **`state:roster`** does not exist — the host's roster is derived from `state:scores`. Never conflate the two.

## Testing

Unit-test `game.py`: join, FIFO buzz ordering, freeze/reset, host-entered awards (split/decimal/negative), `question_submit` overwrites, Start roster snapshot (real players only, not virtual), `roster_add` virtual flag, `get_active_players` excludes virtual, cell-state derivation (Unplayed/Awarded/Passed), per-board and cumulative totals.

Unit-test `bundle_loader.py` independently of `game.py`: valid parse, every structured-error path (missing columns, empty fields, non-numeric or non-positive value, duplicate `question_id`, no data rows, unsupported/missing media), xlsx cell-type normalization, and `extract_media`.

Integration tests with the Flask-SocketIO test client: join → buzz → queue broadcast; room validation (valid/invalid/case-insensitive); late joiner behind frozen queue. No browser/E2E tooling.

## Workflow

- **Plan mode for anything touching more than one file.**
- Read files only when making a specific change, not to "understand" them.
- Commit between features, not within them.
- When drafting a new `SPEC V<N>.md`, its reconciliation pass (pointer annotations + precedence-chain update in this file) is part of the same deliverable, not a follow-up.
- This also applies when planning against an *already-existing* `SPEC V<N>.md`, not just when drafting a brand-new delta: if a plan locks in a decision, gate, or behavior the current spec chain doesn't yet state, fold the needed spec edit into the same work item before presenting the plan for approval. Before calling `ExitPlanMode`, explicitly check the plan's locked-in decisions against the current spec chain — don't wait for the user to notice a gap and ask (this nearly happened during A2 planning: re-upload gating and the pre-Start Q&A peek were both new decisions with no spec trace until caught in review).

### V3 build sequencing

SPEC V3.md's intro describes two phases (Session A: loader, Session B: reveal + presentation). Build each as two sub-sessions:

- **A1 (parser):** zip/xlsx parsing + validation logic per SPEC V3.md §3 (structured per-row errors). No route, no UI wiring — unit-testable in isolation.
- **A2 (loader wiring):** upload route, per-room content storage, "no upload → no board → no start" gate, board materializes in the control center. This is SPEC V3.md's "Session A checkpoint" — quiz can run V2-style (manual scoring panel, no reveal flow) if B1/B2 slip.
- **B1 (state machine):** `host:question_reveal` / `host:answer_reveal` / `host:question_cancel`, the queue lifecycle (SPEC V3.md §4), scoring panel relocated into the reveal flow. Server-side only — testable via the Flask-SocketIO test client, no presentation view yet.
- **B2 (presentation view):** `/present/<join_code>/<host_token>` route, `state:presentation` protocol, template/JS. The visual payoff; depends on B1 already emitting correct state.

Each session ends with a project-memory checkpoint: capture what shipped, key decisions, and what the next session builds on.

## When unsure

Pause and confirm if a request requires a database, auth, multiple games, pushing content to players, board authoring, undo/redo, or anything not in `SPEC.md §2`.

## Legacy reference material (do not use)

These files predate current spec/design decisions and must not be treated as the current data or design contract:

- `legacy-qm-control-center-mockup.jpg` — predates V2 (its cell states don't even distinguish awarded from negative; both render green).
- `data/legacy-quiz_sample.csv` — wide-format CSV (`Category, Q10, A10, Q20, A20, ...`); `SPEC V3.md` §3 commits to long-format `.xlsx` in a `.zip` bundle as the only import contract. Do not use this file's shape as a reference when building the V3 loader.
