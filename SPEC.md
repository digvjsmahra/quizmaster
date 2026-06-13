# SPEC — Quiz Buzzer App (V1)

A real-time, Jeopardy-style buzzer system for a quiz hosted over Zoom. The host
screen-shares the questions; players buzz in from their phones; the host keeps
score from a control center.

This document is the source of truth for what V1 must do. `CLAUDE.md` covers how
to build and run it.

---

## 1. Goal & shape of the product

One host runs a single live quiz for ~10 players. Questions are shown only on the
host's shared Zoom screen — they are **never** pushed to player devices. A
player's phone is a dumb terminal that does three things in sequence: join, buzz,
and show its place in line. The host has the only rich interface: a scorecard and
buzz-queue controls.

Players join through a **single shared permalink** (no join code) and enter a
name. The host works from a separate, unguessable `/host/<secret>` URL.

The buzzer is **open-queue, FIFO**: every player who buzzes is appended in the
order the server received their buzz. The queue stays open and accumulates until
the host explicitly freezes or resets it. The queue is **advisory** — the host
reads it to decide who to call on, but all scoring happens on the scorecard,
never from the queue.

Scoring is host-driven and supports **split values**: for each question the host
selects one or more roster players and enters the points each receives. Awards
may be decimals, may be negative, and need not sum to the question's face value.

## 2. Scope

### In scope for V1
- Single active game; players join via one shared permalink and a name.
- Host control center at an unguessable `/host/<secret>` path.
- ~10 concurrent players joining from phones.
- Real-time buzzing over WebSockets with server-arrival FIFO ordering.
- A **lobby → live phase gate**: the host clicks "Start quiz" to freeze the
  scorecard roster and open buzzing.
- Host scorecard: per-board grids (players × questions) with host-entered
  split-value scoring, running totals, undo, and redo.
- Host queue controls: view the live ordered queue, freeze it, reset it.
- Host can add a player to the roster after Start.
- Quiz content (board, category, value) loaded from a CSV file on disk.
- In-memory state only; mobile-responsive player UI (responsive, not "polished").
- Public deployment as a single always-on host.

### Out of scope for V1 (do not build)
- Authentication / accounts of any kind. `/host/<secret>` is obscurity, not auth.
- Any persistence or database (state is lost on restart, by design).
- Multiple concurrent games.
- Reconnection identity matching. Buzz identities are disposable (see §8).
- In-app quiz authoring / board editing (V2).
- Pushing questions to players, or any player- or audience-facing board or
  scorecard (V2; in V1 the host screen-shares slides).
- A shared / read-only board projection view (V2 — see §12).
- The "QM presentation platform" that owns the board and questions (V2).
- Player presence indicators on the host dashboard.
- Native-app or heavy mobile-UI polish.

## 3. Tech stack

- **Backend:** Python 3.11+, Flask.
- **Real-time:** Flask-SocketIO over WebSockets (with long-polling fallback).
- **Async mode:** `eventlet`. Pinned, not optional — see §9.
- **Frontend:** server-rendered HTML + vanilla JS, **no build step**. The
  Socket.IO browser client is loaded from a CDN.
- **Production:** gunicorn (single eventlet worker).
- **Content:** a single CSV file on disk.
- **State:** in-memory Python objects in one module. No DB, no cache, no queue.

## 4. Architecture overview

A single Flask process serves three things: the player page, the host control
center, and a Socket.IO endpoint. All game state lives in plain Python objects
inside one process. Because state is in-memory and unshared, the app **must run
as a single worker** (see §9) — this is the central deployment constraint, not an
optimization choice.

```
Player phones ──WebSocket──┐
                           ├──►  Flask + Flask-SocketIO  ──►  In-memory Game state
Host browser  ──WebSocket──┘            │                         (phase, roster,
                                        └── reads ─► quiz.csv       queue, scores)
```

Socket.IO rooms separate audiences:
- Players are placed in a player room.
- The host is placed in a dedicated `host` room.
- Player-facing broadcasts carry only join state and queue data. **No question
  text, answers, or scores are ever emitted to player sockets.**

## 5. Data model (in-memory)

Conceptual shape; the implementer may use dataclasses.

- **Game**
  - `phase: "lobby" | "live"`
  - `players: dict[player_id -> Player]` — every connection that has joined
    (buzz identities).
  - `roster: list[player_id]` — the scorecard members; snapshotted from the
    lobby at Start, extendable by the host. **Scores attach only to roster
    members.**
  - `queue: list[BuzzEntry]` — ordered by server arrival (FIFO).
  - `queue_locked: bool` — when true, new buzzes are rejected.
  - `scores: dict[player_id -> dict[question_id -> float]]` — host-entered award
    values (may be decimal or negative).
  - `closed_questions: set[question_id]`
  - `action_log: list[ScoreAction]` and `redo_stack: list[ScoreAction]`
- **Player**: `id`, `name`, `connected`, `joined_at`. (No `name_key` — there is
  no reconnection matching.)
- **BuzzEntry**: `player_id`, `received_at` (server monotonic timestamp).
- **Question** (from CSV): `id`, `board`, `category`, `value`. Question text and
  answers live in the host's slide deck and are never loaded into this app.
- **ScoreAction**: `player_id`, `question_id`, `previous_value`, `new_value` —
  enough to undo or redo a single cell change.

### Two-tier identity

Every joined connection is a **buzz identity** (can buzz, appears in the queue).
The **roster** is the durable set of scorecard rows, snapshotted at Start. The
two usually coincide, but a player who reconnects under a different name becomes
a new buzz identity while their roster entry is unchanged; the host bridges the
two when scoring (§8).

### Question cell states (for the host scorecard)

Each `(board, category, value)` cell renders in one of three states, derived from
`scores` and `closed_questions`:

- **Unplayed** — not in `closed_questions`, no score entries. Cell shows the face
  value as a clickable target.
- **Awarded** — in `closed_questions`, at least one player has a positive award.
  Cell shows the awarded player(s) and amounts (e.g. "Ankur +30" or
  "Dev 25 · Meera 25").
- **Passed** — in `closed_questions`, no positive awards. Cell renders blank or
  struck-through. Any negative entries from wrong attempts still count toward
  totals.

## 6. Quiz CSV format

A flat CSV defines the board grids. The questions themselves live in the host's
slides; the CSV exists only to build the grids and define face values. The full
question text and answers stay in the host's source and are **never** loaded into
this app.

The app does **not** transform or flatten anything. It reads the thin
three-column CSV as-is and fails loudly on malformed input. Producing the thin
CSV from the host's source (e.g. a wide deck with question/answer columns) is a
one-time, offline step.

| column     | required | purpose                                                         |
|------------|----------|-----------------------------------------------------------------|
| `board`    | yes      | Board/round grouping (e.g. "1", "2"). Categories are grouped and displayed by board. |
| `category` | yes      | Column label within a board (e.g. "History").                   |
| `value`    | yes      | Positive integer; serves as both the tile label and the face value. |

Rows are read in file order, which defines display order; keep rows grouped by
board. `(board, category, value)` must be unique and forms the `question_id`. A
single-board quiz uses one `board` value throughout.

Face value is an integer. Awards entered at scoring time may be decimal or
negative (§7).

## 7. Real-time protocol (Socket.IO events)

### Client → server
| event                | sender | payload                              | effect |
|----------------------|--------|--------------------------------------|--------|
| `player:join`        | player | `{ name }`                           | Register a buzz identity; ack with `player_id` and current `phase`, or reject (e.g. empty name). |
| `player:buzz`        | player | `{}`                                 | If live, queue open, and player not already queued, append by arrival time. |
| `host:join`          | host   | `{}`                                 | Register host socket; receive full state. |
| `host:start_quiz`    | host   | `{}`                                 | `lobby → live`. Snapshot the roster from current players; open buzzing. |
| `host:roster_add`    | host   | `{ name }`                           | Add a player to the roster after Start. |
| `host:queue_freeze`  | host   | `{}`                                 | Set `queue_locked = true`. |
| `host:queue_reset`   | host   | `{}`                                 | Clear the queue and set `queue_locked = false`. |
| `host:score_set`     | host   | `{ player_id, question_id, value }`  | Store `value` (a number; may be decimal or negative). `null` clears the cell. The host-entered value is authoritative. |
| `host:score_undo`    | host   | `{}`                                 | Revert the last score action. |
| `host:score_redo`    | host   | `{}`                                 | Reapply the last undone action. |
| `host:question_close`| host   | `{ question_id }`                    | Add to `closed_questions` (cell becomes Awarded or Passed). |

### Server → client
| event            | audience      | payload |
|------------------|---------------|---------|
| `state:phase`    | players + host| `{ phase }`. Flips waiting players to the buzzer at Start. |
| `state:queue`    | players + host| `{ queue: [{player_id, name}], locked }`. Each player derives its own position from the ordered list. |
| `state:scores`   | host          | `{ grid, totals, closed }`. `grid` is grouped by board. |
| `player:accepted`| one player    | `{ player_id, phase }`. |
| `player:rejected`| one player    | `{ reason }` (e.g. empty name). |
| `error`          | any           | `{ message }`. |

Scores are **host-entered and stored verbatim**; the server does not compute them
from the CSV. The CSV `value` is the face value, used only for tile labels and
the host's ±value quick-fill defaults.

## 8. User flows

### Host flow
1. Host starts the server and opens `/host/<secret>`.
2. Players join the lobby via the shared link; the host watches the roster form.
3. Host clicks **"Start quiz"** — the roster freezes and buzzing opens.
4. Host shares their Zoom screen with the slides and reads questions.
5. As players buzz, the host sees the live ordered queue and calls on the first.
6. Host scores on the scorecard: select the roster player(s) for that question
   and enter each award. `+value` / `−value` quick-fill the face value into an
   editable field; the host can type any number, including splits and decimals.
   The scorecard and totals update instantly; entries can be undone and redone.
7. Host closes the question (Awarded or Passed) and resets the queue for the next.
8. If a needed player isn't on the roster (joined late, or rejoined under a new
   name), the host adds them with "add player."

### Player flow
1. Open the shared link → **join screen**: enter a name.
2. If the quiz hasn't started → **waiting screen** ("you're in — waiting for the
   host").
3. Once live → **buzzer screen**: one large buzz button.
4. After buzzing → **queue position**: "You are #N in line." Updates live; resets
   when the host clears the queue.

A player who joins after Start skips the waiting screen and lands on the buzzer;
they can buzz immediately but are not on the scorecard until the host adds them.

Players never see questions, answers, scores, or other players in V1.

### Reconnection (disposable identities)
There is no reconnection matching. A player who drops (closed tab, phone sleep,
wifi) simply reopens the link and rejoins under any name; this creates a fresh
buzz identity and a fresh queue entry. Scores live on the scorecard against the
durable roster entry, never on the player's device, so nothing is lost. The host
bridges: the queue shows whatever name the player rejoined as, and the host scores
the correct roster row. Reconciling the two is the host's job in V1. Players are
asked to reuse a consistent first name to make this easy, but the system neither
enforces nor relies on it.

## 9. Non-functional requirements & the single-worker rule

- **Latency:** buzzes may land ~30 ms apart. Ordering is by **server arrival
  time**, processed serially by Socket.IO. Per-player network RTT differences
  mean "fairness" is defined as server-receive order — this is the contract;
  equalizing for network distance is explicitly out of scope.
- **Transport:** prefer the WebSocket transport; any reverse proxy in front must
  permit WebSocket upgrades and (if applicable) sticky sessions.
- **Concurrency / scale:** ~11 sockets total; load is trivial.
- **Single worker is mandatory.** State is in-process and unshared, so the app
  runs as exactly one worker (`gunicorn -k eventlet -w 1 …`). Running 2+ workers
  without a Redis message queue silently breaks the queue and scorecard. Adding
  a message queue is out of scope, so the answer is: one worker.
- **Async mode is pinned to `eventlet`.** Do not substitute `gevent` or `threading`.
  Pinning avoids the silent-mismatch trap where `requirements.txt` says one thing
  and the worker invocation expects another.
- **Volatility:** a restart wipes all state. Acceptable — a quiz is one session.
- **Deployment:** public is the V1 target. A single small always-on host (a VM or
  a PaaS dyno), reachable over HTTPS, with WebSocket upgrades permitted. The quiz
  CSV is committed to the repo at `data/quiz.csv`; swapping content means
  replacing it and redeploying / restarting.

## 10. Decisions (resolved, do not revisit in V1)

- **Single active game.** No join code; players join via a shared permalink and a
  name. (Multiple games are explicitly V2.)
- **Host on an unguessable `/host/<secret>` path** — obscurity, not auth.
- **Server-arrival FIFO** is the buzz-ordering contract (§9).
- **Open queue, with freeze + reset.** Advisory only: the host scores on the
  scorecard, never from the queue. One queue entry per player per round; extra
  buzzes from an already-queued player are ignored until reset.
- **Split-value scoring.** The host enters per-player award numbers (decimal or
  negative allowed; need not sum to the face value). Host-entered values are
  authoritative; the server does not compute scores. `±value` are editable
  quick-fill buttons, not separate events.
- **Undo and redo**, both supported, built on a single action log.
- **The scorecard grid is always open.** The host can score or re-score any cell
  at any time; there is no "active question" lock. Closing a question sets its
  display state (Awarded/Passed) but does not lock the cell — corrections use
  undo/redo or a direct re-entry.
- **No reconnection logic.** Buzz identities are disposable; the durable identity
  is the roster entry; the host bridges (§8).
- **Lobby → live "Start quiz" gate.** The roster is snapshotted from lobby joiners
  at Start; the host can add players afterward.
- **Two-tier identity:** buzz identity (any connection) vs. roster entry
  (scorecard row). Scores attach only to roster entries.
- **CSV is three columns:** `board`, `category`, `value`. The app does not
  flatten; it expects the thin CSV and fails loudly on malformed input.
- **Player cap.** Soft target of ~10. Not enforced.

## 11. Acceptance criteria (V1 "done")

- A host can start the app and open the control center at `/host/<secret>`.
- Players join via one shared link with just a name and wait in a lobby.
- The host clicks "Start quiz"; the roster freezes and buzzing opens.
- When players buzz, the host sees them in correct server-arrival order, and each
  player sees their own live position.
- The host can freeze and reset the queue; resets clear every player's position.
- The host can enter split-value awards (including decimals and negatives) per
  player per question, see running totals, undo an entry, and redo it.
- The host can close a question; the cell renders as Awarded (player(s) + points)
  or Passed.
- A late joiner can buzz and appear in the queue; the host can add them to the
  roster to score them.
- A dropped player can rejoin via the link under any name and keep playing; no
  score is lost (scores live on the roster).
- No question text, answer, or score is ever transmitted to a player socket.
- The app runs as a single eventlet worker behind a WebSocket-capable proxy on a
  public host, and survives a full quiz without a restart.

## 12. V2 / later (not now)

- **QM presentation platform:** the app owns the board and questions, drives
  navigation (click a tile to reveal the question), and auto-marks closed tiles —
  collapsing the host's Slides + buzzer + scorecard + slide-editing into one
  shared surface. This is the main external-release vision.
- In-app board authoring (build / edit boards, categories, values, and questions
  in the app).
- A read-only, screen-shareable board projection (the withdrawn V1 "middle slice").
- Player- or audience-facing live scorecard / pushed board.
- Player presence indicators on the host dashboard.
- Host authentication (real auth beyond the `/host/<secret>` obscurity).
- Persistence and multiple concurrent games.
- Roster rename / remove and deeper host-UI polish.
- Reconnection robustness (name-matched rebind) if ever wanted.