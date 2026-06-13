# SPEC — Quiz Buzzer App (V1)

A real-time, Jeopardy-style buzzer for a quiz hosted over Zoom. The host screen-shares questions; players buzz in from phones; the host keeps score from a control center.

This is the source of truth for V1. `CLAUDE.md` covers how to build it.

---

## 1. Goal & shape

One host runs a single live quiz for ~10 players. Questions live only in the host's slide deck — never in this app. A player's phone does three things in sequence: join, buzz, show queue position. The host has the only rich interface.

Players join via a **shared permalink** (`/play/<code>`) and enter a name. The host works from `/host/<secret>`.

The buzzer is **open-queue, FIFO**: buzzes accumulate in server-arrival order until the host freezes or resets. The queue is **advisory** — the host uses it to decide who to call on, but all scoring happens on the scorecard grid, never from the queue.

Scoring is **host-driven, split-value**: for each question the host enters per-player award amounts. Awards may be decimal or negative and need not sum to the face value.

## 2. Scope

### In scope for V1
- Single active game; players join via `/play/<code>` + a name.
- Host control center at `/host/<secret>` (obscurity, not auth).
- ~10 concurrent players, WebSocket-based buzzing, server-arrival FIFO.
- **Lobby → live phase gate**: host clicks "Start quiz" to freeze the scorecard roster and open buzzing.
- Host scorecard: per-board Jeopardy-style grids with split-value scoring, board and cumulative totals, board navigation.
- Host queue controls: view live ordered queue, freeze, reset.
- Host can add a player to the roster after Start.
- Quiz content from a CSV (`board, category, value`).
- In-memory state only; mobile-responsive player UI.
- Public deployment as a single always-on host.

### Out of scope for V1
- Auth of any kind. `/host/<secret>` is obscurity only.
- Persistence or database.
- Multiple concurrent games.
- Reconnection identity matching.
- In-app quiz authoring.
- Pushing questions, answers, or scores to players; any audience-facing board.
- Undo/redo (re-clicking a cell and re-submitting is the correction mechanism).
- Player presence indicators.

## 3. Tech stack

- **Backend:** Python 3.11+, Flask, Flask-SocketIO, eventlet (pinned), gunicorn.
- **Frontend:** server-rendered HTML + vanilla JS, no build step. Socket.IO client from CDN.
- **Content:** single CSV on disk (`board, category, value`).
- **State:** in-memory Python objects. No DB, no cache.

## 4. Architecture

Single Flask process: player page + host control center + Socket.IO endpoint. Must run as **one worker** — state is in-process and unshared.

```
Player phones ──WebSocket──┐
                           ├──► Flask + Flask-SocketIO ──► In-memory Game state
Host browser  ──WebSocket──┘          │                    (phase, roster, queue,
                                      └── reads ─► CSV          scores)
```

Socket.IO rooms: players in a shared player room; host in a `host` room. **No question text, answers, or scores ever reach a player socket.**

## 5. Data model

Conceptual shape; implementer may use dataclasses.

```
Game
  phase: "lobby" | "live"
  join_code: str                          # auto-generated at startup; in /play/<code>
  players: dict[player_id → Player]       # all buzz identities
  roster: list[player_id]                 # scorecard rows; snapshotted at Start, extendable
  queue: list[BuzzEntry]                  # ordered by server arrival
  queue_locked: bool
  scores: dict[player_id → dict[question_id → float]]   # host-entered; stored verbatim
  closed_questions: set[question_id]

Player:    id, name, connected, joined_at
BuzzEntry: player_id, received_at         # monotonic server timestamp
Question:  id, board, category, value     # id = f"{board}:{category}:{value}"
```

### Two-tier identity

**Buzz identity** — any joined connection; can buzz and appear in the queue.
**Roster entry** — durable scorecard row, snapshotted at Start; scores attach only here.

A player who reconnects under a different name becomes a new buzz identity; the host bridges them to their roster row when scoring.

### Cell states

| State | Condition | Display |
|-------|-----------|---------|
| **Unplayed** | Not in `closed_questions` | Face value (clickable) |
| **Awarded** | In `closed_questions`, at least one score entry | Player name(s) + amount(s) |
| **Passed** | In `closed_questions`, zero score entries | "~passed~" (grey) |

Awarded applies to any closed question with entries, including negative-only (e.g. "Dev −50"). Passed is strictly zero attempts — a wrong answer is still an entry.

## 6. CSV format

| column | required | purpose |
|--------|----------|---------|
| `board` | yes | Board/round grouping (e.g. `"1"`, `"2"`). |
| `category` | yes | Column label within the board (e.g. `"History"`). |
| `value` | yes | Positive integer; tile label and quick-fill default. |

`(board, category, value)` is unique and forms the `question_id`. Rows define display order; keep grouped by board. Fail loudly on duplicates or malformed rows. Question text and answers live in the host's slides and are never loaded into this app.

## 7. Socket.IO protocol

### Client → server

| event | sender | payload | effect |
|-------|--------|---------|--------|
| `player:join` | player | `{ name }` | Register buzz identity; ack with `player_id` + `phase`, or reject (empty name). |
| `player:buzz` | player | `{}` | If live, queue open, not already queued: append by arrival time. |
| `host:join` | host | `{}` | Register host socket; receive full game state. |
| `host:start_quiz` | host | `{}` | `lobby → live`; snapshot roster from current players; open buzzing. |
| `host:roster_add` | host | `{ name }` | Add a player to the roster after Start. |
| `host:queue_freeze` | host | `{}` | Set `queue_locked = true`. |
| `host:queue_reset` | host | `{}` | Clear queue; set `queue_locked = false`. |
| `host:question_submit` | host | `{ question_id, scores: { player_id: value, … } }` | Save all award values and mark question closed. Empty `scores` → Passed. Re-submitting overwrites the previous entry. This is the only scoring event; there is no per-cell save. |

### Server → client

| event | audience | payload |
|-------|----------|---------|
| `state:phase` | all | `{ phase }` |
| `state:queue` | all | `{ queue: [{player_id, name}], locked }` |
| `state:scores` | host | `{ grid, board_totals, cumulative_totals, closed }` |
| `player:accepted` | one player | `{ player_id, phase }` |
| `player:rejected` | one player | `{ reason }` |
| `error` | any | `{ message }` |

`state:scores` is host-only. `grid` is grouped by board. `board_totals` and `cumulative_totals` are both included so the host UI can render two columns without a second request.

## 8. User flows

### Host flow

1. Start the server; open `/host/<secret>`. The page shows the player join URL (`/play/<code>`) and a list of joined players.
2. Share the join URL with players over Zoom chat.
3. Click **"Start quiz"** — roster freezes, buzzing opens.
4. Screen-share slides; read questions aloud.
5. As players buzz, the live ordered queue appears. Call on the first.
6. Click any board cell to open the **scoring panel** (inline, below the board):
   - Header: `Category · Value` + `default +Value` reminder.
   - Per-roster-player row: value input field + `[+value]` `[-value]` quick-fill buttons.
   - Click **"Close question"** → all entered values saved atomically, question marked Awarded or Passed, panel closes. Nothing saves before this click.
   - Dismiss without clicking = changes discarded, question state unchanged.
7. Re-click any closed cell to reopen the scoring panel with existing values. "Close question" again overwrites.
8. Navigate boards with **`[← Prev]` `[Next →]`** above the board. Always starts on board 1.
9. **Totals panel** (right side, always visible): two columns — **Board** and **Total** — sorted by board score descending. Board total covers only the current board; Total is cumulative across all boards.
10. Use **"Add player"** to add a late joiner or reconnected player to the roster.

### Player flow

1. Open the shared link → **join screen**: name field only (code is in the URL; no separate code entry).
2. Lobby phase → **waiting screen**: "You're in — waiting for the host."
3. Live phase → **buzzer screen**: one large buzz button.
4. After buzzing → **queue position**: "You are #N in line." Live-updating; resets to the buzzer screen when the host clears the queue.

Late joiners (after Start) skip waiting and land directly on the buzzer. They can buzz immediately but aren't on the scorecard until the host adds them.

### Reconnection

No reconnection logic. A dropped player reopens the link, enters any name, and is a fresh buzz identity. Their roster entry and scores are unchanged on the server. The host bridges the queue name to the correct roster row when scoring. Players are asked to reuse a consistent name to make this easy, but the system neither enforces nor relies on it.

## 9. Non-functional requirements

- **Single worker mandatory.** `gunicorn -k eventlet -w 1`. Two workers without shared state silently breaks the queue and scorecard.
- **Async mode pinned to `eventlet`.** Not substitutable — a mismatch produces silent connection failures.
- **`HOST_SECRET` required.** Read from `os.environ` at startup; fail loudly if missing. Never auto-generate.
- **Latency.** Buzz ordering is server-arrival FIFO. Network RTT differences are accepted, not equalized.
- **Scale.** ~11 sockets. Load is trivial.
- **Volatility.** A restart wipes all state. Acceptable — a quiz is one session. The join code resets on restart; only `HOST_SECRET` (set via env var) is stable.
- **Deployment.** Single small always-on host (VM or PaaS dyno), HTTPS, WebSocket upgrades permitted. Quiz CSV committed at `data/quiz.csv`; swap content by replacing the file and redeploying.

## 10. Decisions (resolved — do not revisit in V1)

- **Shared permalink, no code entry.** `/play/<code>` embeds the join code; players only type a name.
- **`HOST_SECRET` env var.** Required, stable, set once. Not auto-generated.
- **FIFO buzz ordering** by server arrival time.
- **Open queue with freeze + reset.** Advisory; one entry per player per round.
- **Split-value scoring.** Host enters per-player amounts; server stores verbatim. `±value` buttons are editable quick-fills, not fixed events.
- **No undo/redo.** Re-clicking a cell and re-submitting is sufficient.
- **`host:question_submit` is atomic.** Saves all scores + closes the question in one event. Re-submitting overwrites.
- **Cell states: Unplayed / Awarded / Passed.** Passed = zero entries only. Negative-only entries = Awarded.
- **Active cell: blue border** while the scoring panel is open.
- **Totals: Board + Total columns, always visible, sorted by board score descending.**
- **Board navigation: `[← Prev]` `[Next →]` above the board.** Always available; starts on board 1.
- **Lobby → live gate.** Roster snapshotted at Start; host can add players afterward.
- **No reconnection matching.** Buzz identities disposable; roster entries durable; host bridges.
- **CSV: `board, category, value` only.** Produced offline. App never sees question text or answers.

## 11. Acceptance criteria (V1 "done")

- Host opens `/host/<secret>`; sees join URL and lobby player list.
- Players open shared link, enter a name, wait in lobby.
- Host clicks "Start quiz" — roster freezes, buzzing opens.
- Players buzz; host sees correct FIFO queue; each player sees their own position.
- Host can freeze and reset the queue.
- Host clicks a board cell; scoring panel opens inline below the board. Host enters awards (split/decimal/negative); "Close question" saves atomically. Cell renders as Awarded or Passed.
- Re-clicking a closed cell reopens with existing values; re-submitting overwrites.
- Totals panel shows Board and Total columns, sorted by board score descending.
- Board Prev/Next navigation works; starts on board 1.
- Late joiner can buzz; host can add them to the roster.
- No question text, answer, or score ever reaches a player socket.
- Single eventlet worker on a public host, survives a full quiz without restart.

## 12. V2 / later

- QM presentation platform (owns board + questions, drives tile navigation, auto-marks closed tiles).
- In-app board authoring.
- Read-only audience board projection.
- Player/audience live scorecard.
- Host authentication beyond `/host/<secret>` obscurity.
- Persistence and multiple concurrent games.
- Reconnection robustness (name-matched rebind).
- Player presence indicators.
