# SPEC — V3 Delta (Presentation Platform)

Extends `SPEC.md`. Everything in V1/V2 stands unless explicitly changed here.
V3's job: the QM runs an entire quiz from one screen — no PPT, no tab
switching. Questions are presented from the app via a screen-shared
presentation window; the buzzer, scoring, and board close-out all happen in the
control center.

Ships as one release (V3.0), built in two phases:
- **Session A (loader):** runtime quiz upload + validation + board from upload.
  Working checkpoint; runs a quiz V2-style if Session B slips.
- **Session B (reveal):** question/answer reveal state machine + presentation
  view. The payoff.

See `CLAUDE.md`'s Workflow section for the sub-session split used to build
each phase.

---

## 1. The new invariant (replaces "questions never live in this app")

Question and answer content now lives in the app. The leak boundary moves from
"content doesn't exist here" to:

> **Question/answer content and media may only be served on host-secret
> routes and emitted to host-secret sockets. A question, answer, or media URL
> in a player payload or on a player-reachable route is a bug.**

Concretely:
- Player sockets receive exactly what they received in V2: join state and
  queue. Nothing new.
- Answers are additionally gated *within* host surfaces: the presentation view
  never shows an answer before the QM triggers `answer_reveal` (players are
  watching it on Zoom).

## 2. Start journey (changed)

1. QM opens `/host/<join_code>/<host_token>` (the same per-room host token
   from V2 room creation — no `HOST_SECRET` env var) → **lobby**: player join
   link visible, players can join and appear in the lobby.
2. **Mandatory upload step**: QM uploads the quiz bundle (§3) before the quiz
   can start. No upload → no board → no start. Upload is **per-room**: each
   room's uploaded bundle becomes that room's own quiz content, stored on
   that room's `Game` instance — not shared globally. Simultaneous rooms can
   run different quizzes.
3. On successful upload, the board materializes **immediately in the control
   center**, so the QM can visually verify it before going live. "Start quiz"
   remains a separate, deliberate second click (same lobby→live gate as
   V1/V2) — the QM confirms the board, then starts; players never see the
   board ahead of the QM.

The server boots content-less. `data/quiz.csv` and the startup CSV loader are
retired. A restart wipes quiz content along with all other state (consistent
with V1 volatility rules) — the QM re-uploads.

## 3. Import contract

**Format: a single `.zip`** containing:

```
bundle.zip
  quiz.xlsx        # exactly one sheet is read (the first)
  media/           # optional; image files referenced by the sheet
    biopics_30.jpg
    iconic_10.png
    ...
```

**`quiz.xlsx` — long format, one row per question.** Header row required:

| column     | required | notes                                                    |
|------------|----------|----------------------------------------------------------|
| `board`    | yes      | Board/round name. Multiple boards per file supported.    |
| `category` | yes      | Category within the board.                               |
| `value`    | yes      | Positive integer. +value correct / −value incorrect.     |
| `question` | see note | Question text. May be empty **only if** `media` is set.  |
| `answer`   | yes      | Answer text (revealed on `answer_reveal`).               |
| `media`    | no       | Comma-separated filenames relative to `media/` (e.g. `biopics_30a.jpg,biopics_30b.jpg`). One flat `media/` folder for the whole bundle — no per-question subfolders, so QM authoring overhead doesn't grow with image count. A **blank** cell means no media — a non-blank placeholder (e.g. `NA`, `-`) is validated as a literal filename and errors if not found in `media/`. |

- `(board, category, value)` must be unique → forms `question_id`.
- Row order in the file defines display order of boards and categories.
- V3.0 media = **images only** (png / jpg / jpeg / gif / webp). Any other
  extension in `media` is a validation error.

**Validation (fail loudly, at upload time, in the browser):**
- The whole file is validated in a single pass — every row's errors are
  collected and reported together on one upload, not just the first bad
  row, so the QM doesn't have to fix-and-reupload repeatedly.
- Structural errors reported per row with row number and reason: missing
  required field, non-numeric value, duplicate `question_id`, empty
  question+media pair, unknown media extension.
- Every filename in `media` (split on comma, trimmed) must exist in `media/`
  → error if missing.
- Files in `media/` referenced by no row → warning (not an error).
- Nothing is half-loaded: any error rejects the whole upload; the QM fixes and
  re-uploads. Warnings alone don't block.

**Dependency note:** `openpyxl` is added (first dependency beyond the core
four). Justification: XLSX is the QM's native authoring output (Google Sheets
→ Download as .xlsx) and avoids CSV's Unicode/quoting fragility with
Hindi-heavy text. XLSX only — no CSV parser, no Google Sheets integration.

**Media storage/serving:** extracted media is held in a temp dir scoped to
the room, wiped on restart, and served only via a host-secret route
(`/media/<join_code>/<host_token>/<filename>`). Media URLs are emitted only
to host-secret sockets.

## 4. Question lifecycle (new state machine)

Cell states extend V1's Unplayed / Awarded / Passed:

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

- **Reveal** (`host:question_reveal { question_id }`): question (text and/or
  image) appears on the presentation view. Rejected if a *different* question
  is currently Revealed/AnswerShown/under review (one live question at a
  time). Reveal does **not** touch the queue (see queue lifecycle below).
  - On an **Unplayed** question: normal phased reveal — question appears
    first; the answer only appears after a separate `answer_reveal`.
  - On an already-**closed** (Awarded/Passed) question: `question_reveal`
    doubles as **reopen** — this is the only mechanism for post-close score
    correction (V1/V2's "re-click a cell and resubmit," carried forward).
    Question *and* answer appear together immediately (no phasing — neither
    is new information, since both were already shown once). The
    presentation view marks this as **reviewing**, distinct from a fresh
    reveal, so players don't mistake it for a new question (the board tile
    stays visibly closed throughout).
- **Answer reveal** (`host:answer_reveal`): answer appears on the presentation
  view. Allowed only from Revealed (not needed when reopening a closed
  question — the answer is already showing).
- **Cancel** (`host:question_cancel`): allowed from Revealed, AnswerShown, or
  a reopened/under-review question. Clears the presentation view. Writes no
  score entries and changes no scores either way. Destination depends on
  where it came from:
  - A fresh reveal being cancelled → back to **Unplayed**.
  - A reopened correction being dismissed without resubmitting → back to its
    existing **Awarded/Passed**, unchanged. (A closed question can never
    fall back to Unplayed — consistent with V1/V2, where this never existed
    either.)
- **Score + close**: scoring moves *inside* the reveal flow — the inline
  scoring panel (V2 split-value semantics unchanged) lives in the reveal panel
  on the control center. Submitting scores closes the question atomically →
  Awarded or Passed, tile grays on both views, presentation returns to the
  board. Works identically whether this is the first close or a reopened
  correction's resubmit — `question_submit` already overwrites atomically.
- Manual queue **freeze/reset controls remain** as overrides (e.g. freeze
  after the first buzz burst mid-question).

**Queue lifecycle:** `question_submit` (score+close) and `question_cancel`
both clear the queue and unlock it — the same clear-and-unlock operation the
existing manual `host:queue_reset` already performs, reused as-is. No new
lock/unlock protocol. `question_reveal` does not touch the queue at all,
since it's already open and empty by the time a reveal happens. Accepted
trade-off, by design: a player buzzing in the dead period between one
question's close and the next reveal will appear (incorrectly) queued once
the next question is revealed. Not engineered around — mitigated the same
way any stray buzz is today, via the QM's existing manual freeze/reset
controls. The queue remains advisory and host-corrected, not automatically
enforced; "fastest finger" stays the point of the game rather than something
gated behind extra protocol.

**Answer visibility:** the QM sees the answer in the control center from the
moment of Reveal (private judging aid). The presentation view shows it only
after `answer_reveal`. Player sockets never see it.

## 5. Presentation view (new surface)

- Route: `/present/<join_code>/<host_token>` (same per-room host token as the
  control center — no `HOST_SECRET` env var). Not linked from any
  player-reachable page.
- Read-only, socket-driven; the QM never interacts with it. Intended use:
  second browser window on the QM's screen, and **that window** (not the full
  screen) is what's shared on Zoom.
- Shows: board grid with live tile states (unplayed / grayed), the revealed
  question (text + image), the answer once revealed, running score totals, and
  the live buzz queue. Never shows: unrevealed answers, scoring controls,
  upload UI.
- Board grid updating on question close replaces the PPT "gray out the slide"
  step entirely.

## 6. Real-time protocol delta

New client → server (host-authenticated socket only):
| event                  | payload            |
|------------------------|--------------------|
| `host:question_reveal` | `{ question_id }`  |
| `host:answer_reveal`   | `{}`               |
| `host:question_cancel` | `{}`               |

New server → client (host + presentation rooms only, never players):
| event                | payload                                                        |
|----------------------|----------------------------------------------------------------|
| `state:presentation` | `{ live_question: {id, text, media_urls, answer?, phase, reviewing} \| null, board, totals, queue }` |

`answer` is included only when `phase == answer_shown` for the presentation
room; the host room receives it from `phase == revealed`. (Alternatively two
tailored emits — implementer's choice, but the presentation room must never
receive an unrevealed answer.) `reviewing` is `true` when `live_question` is
a reopened closed question (§4) rather than a fresh reveal — the
presentation view uses it to show a "reviewing" badge instead of implying a
new question is being asked.

Player-facing events are unchanged from V2.

## 7. QM authoring workflow (documented trade-offs)

- Answers move from a separate tab into the `answer` column (single-sheet
  contract).
- Images move from in-cell embeds to files: export each image from the sheet
  into `media/`, reference by filename in the `media` column.
- Both are one-time-per-quiz prep costs, paid in exchange for zero context
  switches during the live quiz.

## 8. Out of scope for V3.0

- **Audio/video media** (the "BGMs" rounds). V3.1 or alongside in-app
  authoring. Requires playback controls on the presentation view and the Zoom
  "share computer sound" caveat handled in UI. BGM rounds stay in PPT until
  then.
- In-app quiz authoring / grid editor (later; the upload-populated in-memory
  quiz object is the seam it will plug into).
- Multi-quiz storage, quiz editing after upload (fix the file, re-upload),
  CSV import, Google Sheets API integration.
- Player-facing anything new: players still see join → buzz → position only.

## 9. Acceptance criteria (V3.0 "done")

- Server boots with no quiz content; lobby works pre-upload; quiz cannot
  start without a valid upload.
- A malformed bundle is rejected with per-row, human-readable errors; a valid
  re-upload succeeds without a restart.
- QM reveals a question from the board; it appears on
  `/present/<join_code>/<host_token>` (text and image cases both).
- QM sees the answer privately on reveal; players (via the shared
  presentation window) see it only after answer-reveal.
- Cancel from Revealed or AnswerShown returns the tile to Unplayed, clears
  the presentation view and the queue, and writes no scores.
- Scoring + close from the reveal panel marks the tile Awarded/Passed on both
  the control center and the presentation view.
- Reopening an Awarded/Passed question shows question and answer together
  immediately on the presentation view, marked `reviewing` (not a fresh
  reveal); resubmitting scores closes it again to Awarded/Passed; cancelling
  a reopened correction without resubmitting returns it to its prior
  Awarded/Passed state, unchanged — never to Unplayed.
- A full quiz (multiple boards, image questions included) runs end-to-end
  with the QM touching only the control center — no PPT, no tab switches.
- No question text, answer, or media URL is ever emitted to a player socket
  or served on a player-reachable route.