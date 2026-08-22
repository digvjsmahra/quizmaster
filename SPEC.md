# SPEC — Quiz Buzzer App

A real-time, Jeopardy-style buzzer and quiz-presentation platform. One host (the "QM") runs a live quiz for ~10 players over Zoom: uploads quiz content, presents questions from a shared window, takes buzzes, and scores from a control center.

This is the source of truth for current system behavior. `CLAUDE.md` covers how to build it.

---

## 1. Goal & shape

One QM runs a single live quiz for ~10 players, entirely from the app — no PowerPoint, no tab-switching. Quiz content (questions, answers, media) is uploaded per room before the quiz starts and lives only in the app, presented via a read-only presentation view the QM screen-shares. Player phones do three things in sequence: join, buzz, show queue position. The QM has the only rich interface.

Players join via a **shared permalink** (`/play/<code>`) or by entering a 4-character room code on the landing page, then enter a name. The QM works from `/host/<join_code>/<host_token>` and, for the audience-facing screen-share, `/present/<join_code>/<host_token>`.

The buzzer is **open-queue, FIFO**: buzzes accumulate in server-arrival order until the QM freezes or resets. The queue is **advisory** — the QM uses it to decide who to call on, but all scoring happens on the scorecard grid, never from the queue.

Scoring is **host-driven, split-value**: for each question the QM enters per-player award amounts. Awards may be decimal or negative and need not sum to the question's face value.

## 2. Scope

### In scope
- Landing page (`/`): players enter a 4-character room code + name to join, or a host creates a new room. Multiple rooms run simultaneously, each with its own in-memory state and per-room host token.
- Join by code or shared permalink (`/play/<code>`, code pre-filled). Code input is OTP-style (auto-advance, auto-uppercase, paste support). Invalid codes show a human-legible inline error, never a 404.
- Host control center (`/host/<join_code>/<host_token>`) — obscurity, not auth. Shows the join link/code, joined players, and (once uploaded) the scorecard board.
- Presentation view (`/present/<join_code>/<host_token>`) — read-only, socket-driven, meant for screen-share; not linked from any player-reachable page.
- ~10 concurrent players, WebSocket-based buzzing, server-arrival FIFO.
- **Lobby → live phase gate**: QM clicks "Start quiz" to snapshot the roster and open buzzing.
- **Mandatory per-room quiz upload**: a `.zip` bundle of `quiz.xlsx` + optional `media/`, validated at upload time. No upload → no board → no start.
- **Question lifecycle**: reveal → answer-reveal → score/close, with reopen-to-correct and cancel. Drives both the control center's scoring panel and the presentation view.
- Host scorecard: per-board Jeopardy-style grids with split-value scoring, board and cumulative totals, board navigation (locked while a question is live).
- Host queue controls: view live ordered queue, freeze, reset.
- Host can add a player to the roster after Start, and remove a lobby entry (pre-Start) or a roster member (post-Start, discarding their scores).
- **Persistent player identity**: a rejoin token lets a device silently resume its buzz identity across reconnects.
- In-memory state only; mobile-responsive player UI.
- Public deployment as a single always-on host.

### Out of scope
- Auth of any kind. `/host/<join_code>/<host_token>` and `/present/<join_code>/<host_token>` are obscurity only.
- Persistence or a database.
- In-app quiz authoring — content is authored offline and uploaded as a bundle.
- Pushing questions, answers, or scores to players; any player-facing board.
- Undo/redo (re-clicking a cell and re-submitting is the correction mechanism for scores; `question_cancel` is a separate pre-score reveal-undo, not a scoring undo).
- Player presence indicators beyond the "Players buzzed" / "Others" sections.
- Cross-device reconnection matching — a rejoin token lives in one browser; a different device is always a new buzz identity.
- Audio/video media in questions/answers (images only).
- Bulk player removal — one at a time.

## 3. Tech stack

- **Backend:** Python 3.11+, Flask, Flask-SocketIO, eventlet (pinned), gunicorn, `openpyxl`.
- **Frontend:** server-rendered HTML + vanilla JS, no build step. Socket.IO client from CDN.
- **Content:** per-room uploaded `.zip` bundle (`quiz.xlsx` + optional `media/`), held in memory plus an ephemeral temp-dir for media.
- **State:** in-memory Python objects. No DB, no cache.

## 4. Architecture

Single Flask process: landing page + player page + host control center + presentation view + Socket.IO endpoint. Must run as **one worker** — state is in-process and unshared.

```
Player phones ──WebSocket──┐
                           ├──► Flask + Flask-SocketIO ──► In-memory Game state
Host browser  ──WebSocket──┤          │                    (phase, roster, queue,
Presentation  ──WebSocket──┘          │                     scores, quiz content)
                                      └── reads ─► per-room uploaded bundle
                                                   (quiz.xlsx + media/, in a
                                                   room-scoped temp dir)
```

Socket.IO rooms: players in a shared player room; host in a `host` room; presentation in a `presentation` room, per join_code.

**Content boundary:** question, answer, and media content may only be served on host-secret or presentation-secret routes and emitted to host/presentation sockets. A question, answer, or media URL reaching a player payload or a player-reachable route is a bug. Player sockets receive only join state and queue — nothing about quiz content, ever.

## 5. Data model

Conceptual shape; implementer may use dataclasses.

```
rooms: dict[join_code → RoomEntry]        # one entry per active room; join_code
                                           # is also embedded in /play/<code>

RoomEntry
  game: Game
  host_token: str                         # server-generated per room, forms
                                           # /host/<join_code>/<host_token> and
                                           # /present/<join_code>/<host_token>

Game
  phase: "lobby" | "live"
  players: dict[player_id → Player]       # all identities: real + virtual
  roster: list[player_id]                 # scorecard rows; snapshotted at Start, extendable
  queue: list[BuzzEntry]                  # ordered by server arrival
  queue_locked: bool
  scores: dict[player_id → dict[question_id → float]]   # host-entered; stored verbatim
  closed_questions: set[question_id]
  live_question: LiveQuestion | None      # current reveal-state-machine position
  boards: dict[board_name → list[Question]]              # from the uploaded bundle
  current_board_index: int

Player:    id, name, connected, joined_at, virtual, rejoin_token
BuzzEntry: player_id, received_at         # monotonic server timestamp
Question:  id, board, category, value, question, answer, question_media, answer_media
           # id = f"{board}:{category}:{value}"
```

### Two-tier identity

**Buzz identity** (`virtual=False`) — any player who joined via URL or room code. Has a live socket, can buzz, appears in the player-facing "Players buzzed" / "Others" sections. Auto-snapshotted into the roster at Start.

**Host-added entry** (`virtual=True`) — created by the host via "Add player" after Start. Scorecard row only; no socket, never shown on player phones, never handed a rejoin token.

On the *same* device, a reconnect silently resumes the same buzz identity via a rejoin token — no new identity, no host bridging needed (§10 "Reconnection"). A player joining from a device with no valid token (a different device, or cleared storage) becomes a new buzz identity; the host bridges them to their roster row when scoring. Duplicate names are allowed — the host is trusted to manage this consciously.

### Cell states

| State | Condition | Display |
|-------|-----------|---------|
| **Unplayed** | Not in `closed_questions` | Face value (clickable) |
| **Awarded** | In `closed_questions`, at least one score entry | Player name(s) + amount(s); **green** if any entry is positive, **red** if a negative entry exists and none is positive |
| **Passed** | In `closed_questions`, zero score entries | "~passed~" (grey) |

Awarded applies to any closed question with entries, including negative-only. Passed is strictly zero attempts — a wrong answer is still an entry. An explicit `0` entry counts as neither positive nor negative — a cell with only zero entries stays green/neutral; a real negative plus a zero is still red, since the zero doesn't cancel the penalty. `0` entries are rare in practice, since a blank scoring row is skipped and produces no entry at all — a host has to type `0` explicitly. The color is computed once server-side (`Game._cell_state`) as a `negative_only: bool`, so both the host board and the presentation board derive the same color from the same source.

## 6. Quiz content: bundle format

**Format: a single `.zip`** containing:

```
bundle.zip
  quiz.xlsx        # any single .xlsx file — name is not fixed (see below); exactly one sheet is read (the first)
  media/           # optional; image files referenced by the sheet
    biopics_30a.jpg
    iconic_10.png
    ...
```

The parser also accepts this same content nested one level under a single wrapper folder — the shape produced by zipping a *folder* (rather than its contents) via macOS Finder's "Compress" command, the most natural non-technical workflow. Finder also drops a `__MACOSX/` sidecar tree of AppleDouble files and stray `.DS_Store` entries alongside the real content; both are ignored wherever they appear. Deeper nesting than one wrapper folder is not supported.

**The workbook's filename is not fixed.** Any single `.xlsx` file in the bundle is accepted, whatever it's named — a QM exporting from Google Sheets gets a file named after the sheet's title (e.g. `My Trivia Night.xlsx`), not `quiz.xlsx`, and requiring an exact name added friction for no real benefit. Zero `.xlsx` files in the bundle is an error; two or more is also always an error (listing the filenames found) — no attempt is made to guess intent by preferring one literally named `quiz.xlsx` among several, since that's more surprising than just asking the QM to keep one. The `media/` folder name *is* still fixed (matched case-insensitively).

**`quiz.xlsx` — long format, one row per question.** Header row required (column names below; a header cell that's a close spelling/spacing variant of a required name — e.g. `question media` instead of `question_media` — is silently treated as that column, no error; anything looser, like `points` typed instead of `value`, is not guessed at and is instead surfaced as a missing-column error alongside the column names actually found, so the QM can spot the mismatch themselves):

| column | required | notes |
|--------|----------|-------|
| `board` | yes | Board/round name. Multiple boards per file supported. |
| `category` | yes | Category within the board. |
| `value` | yes | Positive integer. `+value` correct / `−value` incorrect (tile label and quick-fill default). |
| `question` | see note | Question text. May be empty **only if** `question_media` is set. |
| `answer` | yes | Answer text (revealed on `answer_reveal`). |
| `question_media` | no | Comma-separated filenames relative to `media/` (e.g. `biopics_30a.jpg,biopics_30b.jpg`). One flat `media/` folder for the whole bundle — no per-question subfolders, so QM authoring overhead doesn't grow with image count. A **blank** cell means no media — a non-blank placeholder (e.g. `NA`, `-`) is validated as a literal filename and errors if not found in `media/`. |
| `answer_media` | no | Comma-separated filenames, same rules as `question_media`. Independent of it — a question and its answer may each carry their own image, neither, or both. |

- `(board, category, value)` must be unique → forms `question_id`.
- Row order in the file defines display order of boards and categories.
- Media is **images only** (png / jpg / jpeg / gif / webp). Any other extension is a validation error.

**Validation (fail loudly, at upload time, in the browser):**
- The whole file is validated in a single pass — every row's errors are collected and reported together, not just the first bad row. This holds even when a required column is entirely missing: that's reported once as a bundle-level error, but row-level checks that don't depend on it (media filenames, duplicate detection) still run, so a QM sees everything wrong in one upload rather than fixing issues one category at a time across repeated attempts.
- Structural errors reported per row with row number and reason: missing required field, non-numeric value, duplicate `question_id`, empty question+media pair, unsupported/missing media extension.
- Every filename in `question_media`/`answer_media` (split on comma, trimmed) must exist in `media/` → error if missing.
- Files in `media/` referenced by no row → warning (not an error).
- Error messages avoid internal vocabulary (no raw `question_id`, no internal folder-path references) and are actionable, not just descriptive — e.g. a media filename with no extension names a concrete fix (`'poster' has no file extension — save it as e.g. poster.png`), and a missing required column lists the columns actually found in the file so a semantic mismatch (`points` typed instead of `value`) is visible even when it can't be silently auto-corrected.
- Nothing is half-loaded: any error rejects the whole upload; the QM fixes and re-uploads. Warnings alone don't block — the control center says so explicitly, since it's not obvious from a plain warning message that it's safe to proceed.
- The control center visually separates errors (blocking) from warnings (non-blocking) as two distinct boxes rather than one undifferentiated list, so "must fix" vs. "can proceed anyway" doesn't require reading closely. A long list scrolls within its own bounded box rather than growing the page indefinitely.

**Dependency note:** `openpyxl` — XLSX is the QM's native authoring output (Google Sheets → Download as .xlsx) and avoids CSV's Unicode/quoting fragility with non-Latin text. XLSX only — no CSV parser, no Google Sheets integration.

**Media storage/serving:** extracted media is held in a temp dir scoped to the room, wiped on restart, and served only via a host-secret route (`/media/<join_code>/<host_token>/<filename>`). Media URLs are emitted only to host-secret and presentation-secret sockets.

**Upload gating:** upload is per-room — each room's bundle is that room's own quiz content, stored on that room's `Game` instance. Simultaneous rooms can run different quizzes. Re-upload is lobby-only; once live, a further upload to the same room is rejected — the only way to load a different bundle is a new room. This prevents a re-upload from silently orphaning scores tied to `question_id`s that no longer exist in a newly-uploaded board.

## 7. Question lifecycle

Cell states extend the three-state model above with a live, in-flight machine:

```
Unplayed ──(host:question_reveal)──► Revealed ──(host:answer_reveal)──► AnswerShown
   ▲                                    │                                   │
   └────────(host:question_cancel)──────┴───────────────────────────────────┘
                                                                            │
                                                        (score + close, atomic)
                                                                            ▼
                                                                  Awarded / Passed ──┐
                                                                            ▲        │ host:question_reveal
                                                                            │        │ (reopen — question +
                                                    score + close, atomic   │        │  answer shown together)
                                                    (resubmit) ─────────────┤        ▼
                                                                            └── Reviewing
                                                                                 │
                                                              host:question_cancel
                                                              (back to Awarded/Passed, unchanged)
```

- **Reveal** (`host:question_reveal { question_id }`): question (text and/or image) appears on the presentation view. Rejected if a *different* question is currently Revealed/AnswerShown/under review — one live question at a time. Reveal does not touch the queue.
  - On an **Unplayed** question: question appears first; the answer only appears after a separate `answer_reveal`.
  - On an already-**closed** (Awarded/Passed) question: `host:question_reveal` doubles as **reopen** — the only mechanism for post-close score correction. Question *and* answer appear together immediately (neither is new information). The presentation view marks this **reviewing**, distinct from a fresh reveal, so players don't mistake it for a new question; the board tile stays visibly closed throughout.
- **Answer reveal** (`host:answer_reveal`): answer appears on the presentation view. Allowed only from Revealed — not needed when reopening a closed question, since the answer is already showing.
- **Cancel** (`host:question_cancel`): allowed from Revealed, AnswerShown, or a reopened/under-review question. Clears the presentation view. Writes no score entries and changes no scores. A fresh reveal being cancelled returns to Unplayed; a reopened correction being dismissed without resubmitting returns to its existing Awarded/Passed, unchanged — a closed question can never fall back to Unplayed.
- **Score + close**: scoring lives inside the reveal flow — the scoring panel (same split-value semantics as always) sits in the reveal modal on the control center, covering only the board (the sidebar — queue freeze/reset, totals, add-player — stays usable throughout). Submitting scores closes the question atomically → Awarded or Passed, tile grays on both views, presentation returns to the board. Works identically whether this is the first close or a reopened correction's resubmit — `question_submit` already overwrites atomically. `question_submit` is rejected unless the question is currently live *and* answer-shown — score+close only ever originates from AnswerShown or Reviewing, never directly from Revealed.
- **Board navigation is locked while a question is live** (Revealed, AnswerShown, or Reviewing) — the QM must cancel or close the question before switching boards. This keeps the presentation view's board unambiguous: it's always derivable from wherever `live_question` is.
- Manual queue freeze/reset controls remain as overrides (e.g. freeze after a first-buzz burst mid-question).

**Queue lifecycle:** `question_submit` (score+close) and `question_cancel` both clear the queue and unlock it — the same clear-and-unlock operation manual `host:queue_reset` performs. `question_reveal` doesn't touch the queue at all, since it's already open and empty by the time a reveal happens. A player buzzing in the dead period between one question's close and the next reveal will appear (incorrectly) queued once the next question is revealed — an accepted trade-off, mitigated the same way any stray buzz is, via the QM's manual freeze/reset controls. The queue stays advisory and host-corrected, not automatically enforced.

**Answer visibility:** the QM sees the answer in the control center from the moment of Reveal (private judging aid, including any `answer_media` once `answer` is present). The presentation view shows it only after `answer_reveal`. Player sockets never see it.

## 8. Presentation view

- Route: `/present/<join_code>/<host_token>` (same per-room host token as the control center). Not linked from any player-reachable page. Opened manually by the QM (a plain link in the control center header) — not auto-opened on Start, so there's time to set up Zoom screen-share calmly beforehand; opening it at any point bootstraps full current state, so there's no wrong moment to open it.
- Read-only, socket-driven; the QM never interacts with it. Intended use: a second browser window on the QM's screen, and *that window* (not the full screen) is what's shared on Zoom.
- **Layout: a fixed-size stage plus a persistent sidebar.** The stage is one fixed-aspect-ratio box (16:9) that never resizes, showing exactly one of:
  - *Board slide* (resting state, nothing live): the board grid with live tile states (unplayed / awarded-with-names, green or red / passed).
  - *Question slide* (something's live): the question (text + image). Once `answer_reveal` fires, the answer appears one of two ways:
    - **Inline:** if the answer has no `answer_media` and its content fits in the stage alongside the question, it fades in below the question in the same box, via a CSS transition — not a hard cut or a full slide swap.
    - **Dedicated answer slide:** if the answer has its own `answer_media`, or its content wouldn't fit in the stage, the presentation view instead advances to a full-stage answer slide — the question and its own media don't persist onto it, since neither is new information once the answer has taken over. This applies identically to a fresh reveal or a reopened `reviewing` question. The choice is fully automatic — the presentation client decides at the moment `answer_shown` is reached; the QM never chooses or triggers it. The dedicated slide auto-shrinks its text to fit the stage (down to a minimum readable size); if it still doesn't fit, it becomes a top-anchored scrollable view rather than centering the overflow — centering would make content above the natural center unreachable by scrolling (`scrollTop` can't go negative).
    - Reopening a closed question (`reviewing`) renders straight into this same layout with question+answer already together, no phasing, marked with a "reviewing" badge.

    The board shown is always `live_question`'s board when something's live; otherwise it's whatever the QM last navigated to via the control center's board-select controls (`host:board_select`) — never both, never ambiguous, since board navigation is locked while a question is live (§7).
  - The stage never shows scoring controls or upload UI, and never shows an unrevealed answer.
  - Board grid updating on question close is how a closed question visually "grays out."
  - Sidebar (outside the stage, always visible, independent of whatever's on stage): running score totals (a live leaderboard) and the live buzz queue.
- The host's control-center reveal modal shows the same content (question/answer text + media) as a private judging aid — it's never a fixed-size stage, so it has no slide-swap behavior; it just shows everything inline.

## 9. Socket.IO protocol

### Client → server

| event | sender | payload | effect |
|-------|--------|---------|--------|
| `player:join` | player | `{ name }` | Register buzz identity; ack with `player_id` + `phase` + `rejoin_token`, or reject (empty name). |
| `player:rejoin` | player | `{ room_id, token }` | Resume an existing buzz identity by rejoin token; same ack/reject shape as `player:join`, including the same live-phase `state:queue` catch-up emit and `state:players` broadcasts. An unknown/expired/foreign token gets `player:rejected`, same shape as a rejected `player:join`. |
| `player:buzz` | player | `{}` | If live, queue open, not already queued: append by arrival time. |
| `host:join` | host | `{}` | Register host socket; receive full game state. |
| `host:start_quiz` | host | `{}` | `lobby → live`; snapshot roster from current players; open buzzing. Rejected without a valid uploaded bundle. |
| `host:roster_add` | host | `{ name }` | Add a player to the roster after Start. Creates a standalone roster entry — the server assigns a new `player_id`, with no link to any buzz identity; the host bridges the mapping mentally. |
| `host:player_remove` | host | `{ player_id }` | Remove a lobby entry before Start; rejected once live. A plain delete — nothing to reconcile pre-Start (no roster entry, no scores, no queue entry can exist yet). Not reachable for host-added (`virtual=True`) entries, since those only exist post-Start. |
| `host:roster_remove` | host | `{ player_id }` | Remove a roster member (real or virtual) after Start, deleting their roster row, their score entries, and their `Player` record outright; rejected before Start. A genuine, no-undo data-loss action — distinct from the "no undo/redo" rule below, which is about correcting a score value, not removing a player wholesale. |
| `host:queue_freeze` | host | `{}` | Set `queue_locked = true`. |
| `host:queue_reset` | host | `{}` | Clear queue; set `queue_locked = false`. |
| `host:question_submit` | host | `{ question_id, scores: { player_id: value, … } }` | Save all award values and mark question closed. Empty `scores` → Passed. Re-submitting overwrites. Rejected unless the question is currently live and answer-shown (§7). |
| `host:question_reveal` | host | `{ question_id }` | Reveal a question, or reopen a closed one for correction (§7). |
| `host:answer_reveal` | host | `{}` | Reveal the answer of the currently-revealed question (§7). |
| `host:question_cancel` | host | `{}` | Clear the live question without scoring (§7). |
| `host:board_select` | host | `{ board_index }` | Change which board the control center/presentation view shows when nothing's live. Rejected if a question is currently live or the index is out of range. |

### Server → client

| event | audience | payload |
|-------|----------|---------|
| `state:phase` | all | `{ phase }` |
| `state:queue` | all | `{ queue: [{player_id, name, delta_ms}], locked }` — `delta_ms` is ms since first buzz (first entry = 0) |
| `state:scores` | host | `{ grid, board_totals, cumulative_totals, closed }` — `grid` cells include `question`/`answer`/`question_media`/`answer_media` (host-only) and, for Awarded cells, `negative_only` |
| `state:players` | players | `{ players: [{player_id, name}] }` — all connected non-virtual players; broadcast on join and disconnect |
| `state:live_question` | host | `{ live_question: {question_id, board, category, value, question, answer, question_media, answer_media, status, reviewing} \| null }` — the QM's private judging aid; `answer`/`answer_media` are always included once `live_question` is set, regardless of presentation-facing phasing |
| `state:presentation` | presentation | `{ board_name, board_index, board_count, board, totals, live_question }` — `board` is a redacted grid (`value`/`state`/`entries` per cell, `entries` including `negative_only` — no `question`/`answer`/media). `live_question` mirrors `state:live_question`'s shape minus `board`/`category`/`value`, with `answer`/`answer_media` included **only** when `status == "answer_shown"`. No `queue` field — the presentation room receives `state:queue` directly instead, since queue entries never carry question/answer content. |
| `player:accepted` | one player | `{ player_id, phase, rejoin_token }` |
| `player:rejected` | one player | `{ reason }` |
| `player:removed` | one player | `{}` — sent just before the server force-disconnects a removed player (lobby or post-Start roster removal) |
| `error` | any | `{ message, context }` |

`state:scores` and `state:live_question` are host-only. `state:presentation` is presentation-room-only. `state:players` is player-only, driving the "Others" section on the player phone — never sent to the host.

## 10. User flows

### Host flow

1. QM opens `/host/<join_code>/<host_token>` → **lobby**: player join link/code visible, players can join and appear in the lobby.
2. **Mandatory upload step**: QM uploads the quiz bundle (§6) before the quiz can start. No upload → no board → no start.
3. On successful upload, the board materializes immediately in the control center, so the QM can visually verify it before going live. Clicking a board cell at this stage shows that question's text/answer/media, read-only — a pre-flight content check, not the §7 reveal mechanism: no state transition, no phase change, only available before Start.
4. **"Start quiz"** — a separate, deliberate second click (same lobby→live gate as always): roster freezes, buzzing opens. Players never see the board ahead of the QM.
5. QM opens `/present/<join_code>/<host_token>` in a second window and shares that window on Zoom.
6. As players buzz, the live ordered queue appears in the sidebar. QM calls on the first.
7. QM clicks a board cell to reveal it (§7); the question appears on the presentation view. QM triggers answer-reveal when ready, then scores and closes from the reveal modal (split-value scoring, `[+value]`/`[-value]` quick-fills, blank rows skipped). Closing marks the tile Awarded or Passed on both views.
8. Re-clicking (reopening) any closed cell shows question+answer together immediately, marked "reviewing," with existing score values pre-filled; resubmitting overwrites.
9. Board navigation (`[← Prev]`/`[Next →]`) is available whenever no question is live; locked while one is.
10. **Totals** (sidebar, always visible): Board and Total columns, sorted by board score descending.
11. **Adding a late joiner:** the host types a name and clicks "Add" → the player appears immediately as a new roster row (Board 0, Total 0) and in every reveal modal opened from that point on. The host can re-open any closed cell to score them retroactively. Live-phase only.
12. **Removing a player:** pre-Start, a lobby entry can be removed outright (§9 `host:player_remove`). Post-Start, a roster member (real or host-added) can be removed, discarding their scores (§9 `host:roster_remove`).

### Player flow

1. Open the shared link or enter the 4-char code on the landing page → **join screen**: name field + pre-filled code.
2. Lobby phase → **waiting screen**: "You're in — waiting for the host."
3. Live phase → **buzzer screen**: one large 3D circular red buzz button.
4. After buzzing → buzzer greys out (disabled) in place; **"Players buzzed"** section appears below showing the full ordered queue with timing badges (⚡ first, +X ms, +X.X s); own row bolded.
5. **"Others"** section shows all connected players not yet in the queue as chips; names move to "Players buzzed" in real time when they buzz.
6. If the host freezes the queue before the player buzzes → buzzer greys out with "Host has frozen the queue" label.
7. Host resets queue → buzzer re-activates, sections clear.

Late joiners (after Start) skip waiting and land directly on the buzzer, receiving current queue state immediately. They can buzz immediately but aren't on the scorecard until the host adds them.

### Reconnection

A dropped player's browser silently resumes their exact same buzz identity — via a rejoin token stored in `localStorage`, keyed per room (`qb_rejoin_<join_code>`) — as long as it's the same device and the room still exists. On every socket `connect` (first load and every reconnect alike), the client checks that storage first: found → emits `player:rejoin` directly, skipping name-entry entirely; not found → falls back to `?name=` URL auto-join, then manual name entry. A tiny inline script in `<head>` hides the join form synchronously if a token is present, so a reload never flashes a login screen before resuming. A rejected rejoin (invalid/foreign/expired token) clears the stale `localStorage` entry, reveals the join form, and surfaces a plain-language reason. Repeated/rapid reconnects are safe: each rejoin resolves to the same token → same `player_id`, no duplicate identities.

A device with no valid token — first-time join, a different device, or cleared storage — falls back to the original behavior: reopen the link, enter any name, get a fresh buzz identity. Their roster entry and scores are unchanged on the server either way. The host bridges the queue name to the correct roster row when scoring a fresh identity. Players are asked to reuse a consistent name to make this easy, but the system neither enforces nor relies on it. A player mid-queue when their connection drops keeps that exact queue position on reconnect — the queue is keyed by `player_id`, which a rejoin never changes.

No cross-device identity — a token lives in one browser's `localStorage`; joining from a second device is always a new buzz identity. No token expiry/rotation, and no server-side identity beyond the in-memory `Player` record — a server restart wipes everything, same as all other state.

## 11. Non-functional requirements

- **Single worker mandatory.** `gunicorn -k eventlet -w 1`. Two workers without shared state silently breaks the queue and scorecard.
- **Async mode pinned to `eventlet`.** Not substitutable — a mismatch produces silent connection failures.
- **Per-room host token.** Server-generated (`secrets.token_urlsafe`) at room creation; forms `/host/<join_code>/<host_token>` and `/present/<join_code>/<host_token>`. Not read from `os.environ`; no global secret to configure.
- **Latency.** Buzz ordering is server-arrival FIFO. Network RTT differences are accepted, not equalized.
- **Scale.** ~11 sockets per room. Load is trivial.
- **Volatility.** A restart wipes all state, including uploaded quiz content. Acceptable — a quiz is one session; the QM re-uploads. Join codes and host tokens reset on restart; nothing is configured to survive it.
- **Deployment.** Single small always-on host (VM or PaaS dyno), HTTPS, WebSocket upgrades permitted. No redeploy needed to change quiz content — the QM uploads per room at runtime.

## 12. Locked decisions (do not revisit without a spec change)

- **Shared permalink or room code, no separate auth.** `/play/<code>` embeds the join code; players only type a name.
- **Per-room host token, not a `HOST_SECRET` env var.**
- **FIFO buzz ordering** by server arrival time.
- **Open queue with freeze + reset.** Advisory; one entry per player per round.
- **Split-value scoring.** Host enters per-player amounts; server stores verbatim. `±value` buttons are editable quick-fills, not fixed events.
- **No undo/redo for scores.** Re-clicking a cell and re-submitting is sufficient. (`question_cancel` is a separate pre-score reveal-undo; player/roster removal is a separate, genuine data-loss action — neither is a scoring undo.)
- **`host:question_submit` is atomic.** Saves all scores + closes the question in one event. Blank rows are skipped (no entry created). All rows blank = Passed; at least one row with a value = Awarded. Re-submitting overwrites.
- **Cell states: Unplayed / Awarded / Passed.** Passed = zero entries only. Awarded gets a green/red color split (green if any entry is positive, red if a negative entry exists and none is positive); Unplayed and Passed each render a single color.
- **Active cell: blue border** while the reveal/scoring panel is open.
- **Totals: Board + Total columns, always visible, sorted by board score descending.**
- **Board navigation: `[← Prev]` `[Next →]`, locked while a question is live.**
- **Lobby → live gate.** Roster snapshotted at Start; host can add players afterward.
- **Reconnection resumes identity via rejoin token, same device only.** A different device or cleared storage is always a fresh buzz identity; the host bridges it to the correct roster row.
- **Scoring panel always reflects the live roster.** Players added post-Start appear in every reveal/scoring panel opened after that point, including re-opened closed cells, with `—` on questions closed before they were added.
- **`host:roster_add { name }` creates a standalone roster entry**, unlinked to any buzz identity.
- **Quiz content is uploaded per room as an XLSX/zip bundle**, never authored in-app, never CSV.

## 13. Acceptance criteria

- Landing page: a player can join an existing room by code, or a host can create a new room.
- Host opens the control center; sees the join link/code and the lobby player list.
- Server boots with no quiz content; lobby works pre-upload; the quiz cannot start without a valid upload. A malformed bundle is rejected with per-row, human-readable errors; a valid re-upload succeeds without a restart.
- Host clicks "Start quiz" — roster freezes, buzzing opens.
- Players buzz; host sees the correct FIFO queue; each player sees their own position. Host can freeze and reset the queue.
- QM reveals a question from the board; it appears on the presentation view (text and image cases both). QM sees the answer privately on reveal; players (via the shared presentation window) see it only after answer-reveal.
- Cancel from Revealed or AnswerShown returns the tile to Unplayed, clears the presentation view and the queue, and writes no scores.
- Scoring + close from the reveal panel marks the tile Awarded/Passed (green/red as appropriate) on both the control center and the presentation view.
- Reopening an Awarded/Passed question shows question and answer together immediately, marked "reviewing"; resubmitting scores closes it again; cancelling a reopened correction without resubmitting returns it to its prior state, unchanged.
- A dropped player reconnecting on the same device silently resumes their identity and queue position, no re-entry.
- Host can remove a lobby entry pre-Start, and a roster member (discarding their scores) post-Start; the removed player, if connected, lands back on a working join form.
- Totals panel shows Board and Total columns, sorted by board score descending. Board Prev/Next navigation works and is locked while a question is live.
- No question text, answer, or media URL ever reaches a player socket or a player-reachable route.
- A full quiz (multiple boards, image questions included) runs end-to-end with the QM touching only the control center and presentation view — no external slides, no tab-switching.
- Single eventlet worker on a public host, survives a full quiz without restart.

## 14. What's next

Forward-looking feature ideas are tracked as GitHub Issues, not in this file. This file describes the system as it exists today.
