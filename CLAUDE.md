# CLAUDE.md

Guidance for Claude Code in this repo. `SPEC.md` is *what* to build; this file is *how*.

## What this is

A real-time quiz buzzer. The host creates a room from the landing page and gets a unique control center URL; ~10 players join via a shared link or by entering a 4-char room code at the landing page. Flask + Flask-SocketIO backend, vanilla-JS frontend, no build step, quiz content uploaded per-room as an xlsx/zip bundle, all state in memory.

## Spec

`SPEC.md` is the single, living source of truth for current system behavior. Most changes are edited into it directly, in the same work item as the code change that causes them — not spun into a new file. A `size:L`/multi-session build may use a temporary working `SPEC V<N>.md` while in progress (see "Large builds use a temporary delta file" under Workflow below); once it ships and stabilizes, it's folded into `SPEC.md` and deleted. There is no permanent version chain to maintain.

(The project previously kept a permanent chain — `SPEC V3.md` through `SPEC V8.md` — that was consolidated into this file; the old per-version documents are recoverable from git history if the exact historical delta is ever needed.)

Where a `SPEC.md` section appears to conflict with another, do not silently assume it's a contradiction to resolve — some are deliberate, coexisting distinctions. E.g. the buzz-identity vs. roster separation: a real player joining via URL/code reflects immediately on the player-facing buzzer/queue (`virtual=False`), but the control-center roster only reflects the Start-time snapshot or host-added (`virtual=True`) entries. Read superficially, "player joins" vs. "roster doesn't update" looks like a contradiction; it's actually two intentionally separate tracks (see "Two player identity types" below). Flag the apparent conflict to the user and ask which reading is intended before acting on it.

## Golden rules

1. **Single worker.** State is in-process and unshared. Run as exactly one worker (`gunicorn -k eventlet -w 1`). Never add Redis or a message queue.
2. **In-memory only.** No database, no ORM, no durable on-disk persistence. Quiz content comes from a per-room uploaded bundle (SPEC.md §6), held in memory plus an ephemeral temp-dir for media, wiped on restart.
3. **No build step.** Vanilla JS + server-rendered HTML. Socket.IO client from CDN. No npm, bundlers, or transpilers.
4. **Never leak questions or scores to players.** Player-bound emits carry only join state and queue position. Any question, answer, or score in a player payload is a bug. (The boundary also covers routes and media, not just payloads — see SPEC.md §4.)
5. **Host enters scores; server stores verbatim.** Awards may be decimal or negative. The server never computes scores from the uploaded quiz content — `value` is used only for tile labels and `±value` quick-fill defaults. Scoring is always against roster entries, never from the queue.
6. **No undo/redo.** The always-open scorecard grid is the correction mechanism — the host re-clicks a cell and re-submits. (`question_cancel` is a pre-score reveal-undo, not a scoring undo — distinct from this rule. See SPEC.md §7. This rule still governs scoring corrections.)
7. **Async mode is pinned to `eventlet`.** Do not substitute `gevent` or `threading`.
8. **No features beyond `SPEC.md §2`'s scope.** Stop and ask before building anything not listed there.
9. **Minimal dependencies.** Flask, Flask-SocketIO, eventlet, gunicorn, `openpyxl`. Justify anything else. (`openpyxl` is required for the XLSX bundle parser — see SPEC.md §6. The earlier CSV path and its stdlib `csv` usage have been retired.)

## Tech stack

Python 3.11+, Flask, Flask-SocketIO, eventlet, gunicorn, openpyxl. Vanilla JS + HTML; Socket.IO client via CDN. Quiz content from a per-room uploaded xlsx/zip bundle; in-memory Python objects for state.

## Project layout

```
app.py            # Flask app + SocketIO init + HTTP routes (incl. per-room upload + media routes)
game.py           # all in-memory state and game logic (phase, roster, queue, scoring)
events.py         # SocketIO event handlers — thin wrappers that delegate to game.py
bundle_loader.py  # zip/xlsx bundle parser + validation + media extraction (SPEC.md §6)
templates/
  create.html     # landing page — join by code or create a new room
  player.html     # waiting → buzzer → queue-position (single page, JS-driven)
  host.html       # control center: board scorecard + shared board-covering modal (peek + reveal) + queue + totals
  present.html    # read-only presentation view (SPEC.md §8) — stage + sidebar, no interaction
static/
  js/create.js    # OTP input logic, code validation, redirect
  js/player.js    # rejoin_token persisted to localStorage; connect handler prefers silent rejoin over name entry (SPEC.md §10)
  js/media.js     # shared between host.js/present.js: mediaImagesHtml() — the one identical sliver of question rendering
  js/host.js      # includes the reveal-flow rewire (question_reveal/answer_reveal/question_cancel/board_select) and the shared modal (peek + reveal)
  js/present.js   # pure rendering — state:presentation + state:queue, no emits beyond present:join
  css/styles.css  # :root token block (colors/radii/shadows) + all page styles
requirements.txt
```

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
- No reconnection identity matching *between devices*. Roster entries are always durable. On the *same* device, a buzz identity now persists across reconnects via a rejoin token (SPEC.md §10) — only a token-less device (first join, a different device, cleared storage) gets a fresh, disposable buzz identity.
- Player UI is one page — buzzer view contains the queue list and sections inline; no separate route or view for queue position.
- Responsive layout for phones; no design polish.
- Fail loudly on a malformed upload: every row's errors surface together in one pass, not just the first.
- **Two player identity types**: `virtual=False` (real socket connection, joined via URL/code) and `virtual=True` (host-added scorecard entry, no socket). `get_active_players()` returns only non-virtual connected players for `state:players`. The roster contains both; the scorecard shows both; player phones see only non-virtual. Every `Player` also carries a `rejoin_token` (SPEC.md §5) — for `virtual=False` players it's how their browser silently resumes the same identity across reconnects; `virtual=True` entries have one too (simpler than conditionally generating it) but it's never handed to a client since they have no socket.
- **`state:players`** is the player-facing presence event (who's in the room). **`state:roster`** does not exist — the host's roster is derived from `state:scores`. Never conflate the two.

## Testing

Unit-test `game.py`: join, FIFO buzz ordering, freeze/reset, host-entered awards (split/decimal/negative), `question_submit` overwrites, Start roster snapshot (real players only, not virtual), `roster_add` virtual flag, `get_active_players` excludes virtual, cell-state derivation (Unplayed/Awarded/Passed), per-board and cumulative totals, `player_rejoin` (valid token resumes the same identity, unknown/virtual token rejected, roster/scores/queue untouched), `remove_player` (deletes a lobby entry, rejected once live, rejected for an unknown id), `remove_from_roster` (deletes a real or virtual roster member and discards their scores, rejected before Start, rejected for an unknown id), `_cell_state`'s `negative_only` flag (green/red truth table: all-positive, mixed, all-negative, negative+zero, all-zero).

Unit-test `bundle_loader.py` independently of `game.py`: valid parse, every structured-error path (missing columns, empty fields, non-numeric or non-positive value, duplicate `question_id`, no data rows, unsupported/missing media), xlsx cell-type normalization, and `extract_media`.

Integration tests with the Flask-SocketIO test client: join → buzz → queue broadcast; room validation (valid/invalid/case-insensitive); late joiner behind frozen queue; `player:rejoin` restores identity + queue state, and survives a stale disconnect arriving after the new connection (SPEC.md §9); `host:player_remove` kicks the target socket with `player:removed` before disconnecting it, and is rejected once live (SPEC.md §9); `host:roster_remove` discards scores and broadcasts `state:scores` to every host tab, and is rejected before Start (SPEC.md §9). No browser/E2E tooling.

## Workflow

- **Plan mode for anything touching more than one file.**
- Read files only when making a specific change, not to "understand" them.
- Commit between features, not within them.
- When a plan changes `SPEC.md`-documented behavior, updating `SPEC.md` — directly, or via a temporary delta file for a `size:L` build (see below) — is part of the same work item as the code change, not a follow-up.
- Before calling `ExitPlanMode`, explicitly check the plan's locked-in decisions against `SPEC.md` — don't wait for the user to notice a gap and ask (this nearly happened during A2 planning: re-upload gating and the pre-Start Q&A peek were both new decisions with no spec trace until caught in review).

### Large builds use a temporary delta file

A `size:L`/multi-session build (the repo's issue-sizing convention) may be drafted as a working `SPEC V<N>.md` while in progress, rather than edited directly into `SPEC.md` — a scoped, reviewable design doc that captures "why" reasoning while it's fresh, without disturbing the current-state spec mid-build. Once it ships and stabilizes, fold its content into `SPEC.md` and delete the delta file — it's a working document, not a permanent addition to a chain.

Worked example, from the presentation platform (the largest build so far, originally `SPEC V3.md`, since folded into `SPEC.md`): it shipped as two phases split into four sub-sessions —

- **A1 (parser):** zip/xlsx parsing + validation logic (structured per-row errors). No route, no UI wiring — unit-testable in isolation.
- **A2 (loader wiring):** upload route, per-room content storage, "no upload → no board → no start" gate, board materializes in the control center. A checkpoint the quiz could still run from (manual scoring panel, no reveal flow) if the next two sessions slipped.
- **B1 (state machine):** the reveal/answer/cancel events and the queue lifecycle, scoring panel relocated into the reveal flow. Server-side only — testable via the Flask-SocketIO test client, no presentation view yet. The scoring-submit hard gate intentionally stayed deferred to B2, built together with the only UI that would ever trigger a reveal in the first place — not an oversight; gating earlier would've risked breaking the currently-deployed scoring flow if B1 shipped before B2 was ready.
- **B2 (presentation view):** the presentation route, protocol, template/JS — the visual payoff, depending on B1 already emitting correct state — plus the control-center rewire and the hard gate deferred from B1.

Each session ended with a project-memory checkpoint: what shipped, key decisions, what the next session builds on. Apply the same shape to the next `size:L` build (e.g. the theme/preset system, #9).

## When unsure

Pause and confirm if a request requires a database, auth, multiple games, pushing content to players, board authoring, undo/redo, or anything not in `SPEC.md §2`.

## Legacy reference material (do not use)

These files predate current spec/design decisions and must not be treated as the current data or design contract:

- `legacy-qm-control-center-mockup.jpg` — predates V2 (its cell states don't even distinguish awarded from negative; both render green).
- `data/legacy-quiz_sample.csv` — wide-format CSV (`Category, Q10, A10, Q20, A20, ...`); `SPEC.md` §6 commits to long-format `.xlsx` in a `.zip` bundle as the only import contract. Do not use this file's shape as a reference when working on the bundle loader.
