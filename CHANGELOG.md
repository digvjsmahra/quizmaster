# Changelog

All notable changes to this project will be documented in this file.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased] — V2 planned

### Planned
- **Room creation flow** — host creates a room from a landing page (no deploy needed), gets an admin link, lands in lobby with the player URL ready to share
- **Responsive host UI** — Quiz Control Center optimised for mobile phones and iPad
- **In-app CSV upload** — host uploads quiz content at room creation; no redeploy needed to change questions

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
