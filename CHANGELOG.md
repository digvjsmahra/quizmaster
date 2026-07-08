# Changelog

All notable changes to this project will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased] — V3 scope

## [2.1.1] — 2026-07-09

### Fixed
- `badge` variable in `host.js` `renderQueue()` shadowed by inner `badge` in `.map()` — renamed outer to `lockedBadge`
- `_sid_player` / `_sid_room` dicts in `events.py` not cleared between tests — fixture now resets them before and after each test, preventing stale sid leakage on test failure
- Stale blank lines in `events.py`

### Added
- 5 new multi-client integration tests covering `state:players` broadcast on join, disconnect, start quiz, `roster_add`; virtual player exclusion; and late joiner receiving current queue state (47 tests total)

### Planned
- **In-app CSV upload** — host uploads quiz content at room creation; no redeploy needed to change questions

## [2.1.0] — 2026-07-08

### Added
- **3D circular red buzzer** — CSS-only radial-gradient dome with layered box-shadow depth and translateY press animation
- **Buzzer stays visible after buzzing** — button greys out (disabled) instead of switching to a separate view; queue list renders below it
- **"Players buzzed" section** — shows the full ordered queue with timing badges (⚡ first, +X ms, +X.X s) directly below the buzzer
- **"Others" section** — chips for all connected players not yet in the queue; names move to "Players buzzed" in real time when they buzz
- **Frozen queue feedback** — if the host freezes the queue, unbuzzed players see a greyed buzzer and "Host has frozen the queue" label
- **Player screen title** renamed to "QuizMaster"; host control center renamed to "QM Control Center"

### Changed
- **Roster split** — real players (joined via URL/code) are auto-snapshotted into the scorecard at Start Quiz; host-added entries ("Add player") are scorecard-only and never shown on player phones. A `virtual` flag on `Player` enforces the split.
- **`state:players` replaces `state:roster`** for the player-facing view — built from all connected non-virtual players, broadcast whenever someone joins or disconnects
- **Late joiners receive `state:queue`** on join so a frozen buzzer renders correctly immediately
- **Player disconnect** broadcasts updated `state:players` so the "Others" list updates in real time for everyone

### Fixed
- Late joiner behind a frozen queue could see an enabled buzzer (server already rejected the buzz; now the client also shows it correctly)
- Roster chips showed host-added players on the player phone screen

## [2.0.0] — 2026-07-08

### Added
- **Unified landing page** — OTP-style 4-box room code input with auto-advance, auto-uppercase, and paste support; players join by code + name or via direct link
- **Room creation flow** — host clicks "Host a new game" from the landing page; server generates a unique per-room host token; no env vars or deploy needed to start a new game
- **Join validation** — `GET /rooms/<code>/validate` returns a human-legible inline error before any redirect; no more 404s for players
- **Player direct link** — `/play/<code>` pre-fills the code boxes (readonly); auto-joins when `?name` param is in the URL (arriving from the landing page)
- **Host lobby room code display** — large OTP-style display boxes show the 4-char code alongside the copyable URL for verbal sharing over Zoom
- **Responsive host control center** — stacks vertically on mobile and tablet (≤1024px); capped at 720px centered on tablet; sidebar (buzz queue) appears above the board on small screens

## [1.0.0] — 2026-07-08

### Added

**Host control center** (`/host/<secret>`)
- Lobby screen with live player list and copyable join URL
- "Start quiz" button — freezes roster, opens buzzing
- Jeopardy-style board grid with per-board navigation (Prev / Next)
- Scoring panel: per-player inputs, `+value` / `-value` quick-fill buttons, atomic save on "Close question"
- Re-open any closed cell to correct scores (overwrite)
- Split-value scoring — awards can be decimal or negative, need not sum to face value
- Totals panel (always visible): Board score + Cumulative score, sorted by board score descending
- Add a late joiner to the roster mid-quiz
- Queue freeze and reset controls

**Player experience** (`/play/<code>`)
- Join by name only — join code embedded in the URL
- Lobby waiting screen, transitions automatically when host starts
- One large buzz button
- Post-buzz queue list with timing badges: "⚡ first" for the leader, `+X ms` under 1s, `+X.X s` at 1s and above
- Own row highlighted in the queue
- Resets to buzzer when host clears the queue

**Infrastructure**
- Real-time via Socket.IO (Flask-SocketIO + eventlet, single worker)
- Quiz content loaded from `data/quiz.csv` — swap file and redeploy to change questions
- In-memory state only — restart wipes all state (acceptable for single-session use)
- Deployed on Render: `https://quizmaster-73xq.onrender.com`
- `HOST_SECRET` env var guards the host page; join code auto-generated at startup
