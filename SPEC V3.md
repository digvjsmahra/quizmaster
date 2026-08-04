# SPEC — V3 Delta (Presentation Platform)

> V4 delta lives in `SPEC V4.md`. Sections below marked `[V4: ...]` describe
> pre-V4 behavior superseded by that delta.

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
   center**, so the QM can visually verify it before going live. Clicking a
   board cell at this stage shows that question's text/answer/media,
   read-only — a pre-flight content check, **not** the §4 reveal mechanism:
   no state transition, no phase change, and it's only available before
   Start (once live, this affordance is gone until B1 adds reveal-based
   Q&A visibility). "Start quiz" remains a separate, deliberate second
   click (same lobby→live gate as V1/V2) — the QM confirms the board, then
   starts; players never see the board ahead of the QM.
4. **Re-upload is lobby-only.** Once the quiz has gone live, a further
   upload to the same room is rejected — the only way to load a different
   bundle is a new room. Prevents a re-upload from silently orphaning
   scores tied to `question_id`s that don't exist in a newly-uploaded board.

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

The parser also accepts this same content nested one level under a single
wrapper folder — the shape produced by zipping a *folder* (rather than its
contents) via macOS Finder's "Compress" command, the most natural
non-technical workflow. Finder also drops a `__MACOSX/` sidecar tree of
AppleDouble files and stray `.DS_Store` entries alongside the real content;
both are ignored wherever they appear, as an artifact of the zip tool, not
QM-authored bundle content. Deeper nesting than one wrapper folder is not
supported.

**`quiz.xlsx` — long format, one row per question.** Header row required:

| column     | required | notes                                                    |
|------------|----------|----------------------------------------------------------|
| `board`    | yes      | Board/round name. Multiple boards per file supported.    |
| `category` | yes      | Category within the board.                               |
| `value`    | yes      | Positive integer. +value correct / −value incorrect.     |
| `question` | see note | Question text. May be empty **only if** `media` is set. `[V4: column renamed to question_media — see SPEC V4.md §1]` |
| `answer`   | yes      | Answer text (revealed on `answer_reveal`).               |
| `media`    | no       | Comma-separated filenames relative to `media/` (e.g. `biopics_30a.jpg,biopics_30b.jpg`). One flat `media/` folder for the whole bundle — no per-question subfolders, so QM authoring overhead doesn't grow with image count. A **blank** cell means no media — a non-blank placeholder (e.g. `NA`, `-`) is validated as a literal filename and errors if not found in `media/`. `[V4: this column is renamed question_media, and gains a sibling answer_media column, same rules — see SPEC V4.md §1]` |

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
- Error messages avoid internal vocabulary (no raw `question_id`, no
  internal folder-path references) so a non-technical QM can act on them
  directly.
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
- **Score + close**: scoring moves *inside* the reveal flow — the scoring
  panel (V2 split-value semantics unchanged) lives in the reveal modal on
  the control center, which covers only the board (not the sidebar — queue
  freeze/reset, totals, add-player stay usable throughout). Submitting
  scores closes the question atomically →
  Awarded or Passed, tile grays on both views, presentation returns to the
  board. Works identically whether this is the first close or a reopened
  correction's resubmit — `question_submit` already overwrites atomically.
  **Hard-gated** (B2): `question_submit` is rejected unless the question is
  currently live *and* `answer_shown` — matching the diagram above, where
  score+close only ever originates from AnswerShown or Reviewing, never
  directly from Revealed. (B1 shipped this ungated, deferred deliberately
  until B2 built the only UI that could ever drive a reveal in the first
  place — see `CLAUDE.md`'s B1 note.)
- **Board navigation is locked while a question is live** (B2): the control
  center's prev/next board controls are disabled for any `live_question`
  state (Revealed, AnswerShown, or Reviewing) — the QM must `question_cancel`
  or close the question before switching boards. This is what keeps the
  presentation view's board unambiguous (§5): it can always be derived from
  wherever `live_question` is, since the host is provably not looking
  anywhere else while one is live.
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
  player-reachable page. Opened manually by the QM (a plain link in the
  control center header) — not auto-opened on Start, so there's time to set
  up Zoom screen-share calmly beforehand; opening it at any point always
  bootstraps full current state, so there's no wrong moment to open it.
- Read-only, socket-driven; the QM never interacts with it. Intended use:
  second browser window on the QM's screen, and **that window** (not the full
  screen) is what's shared on Zoom.
- **Layout: a fixed-size stage plus a persistent sidebar.** The stage is one
  fixed-aspect-ratio box (16:9) that never resizes, showing exactly one of:
  - *Board slide* (resting state, nothing live): the board grid with live
    tile states (unplayed / awarded-with-names / passed).
  - *Question slide* (something's live): the question (text + image), with
    the answer fading in below it — via a CSS transition inside the same
    box, not a hard cut or a full slide swap — once `answer_reveal` fires.
    `[V4: superseded — this is now conditional on the answer's own media and
    whether it fits; see SPEC V4.md §2]` Reopening a closed question
    (`reviewing`) renders straight into this same layout with question+answer
    already together, no phasing, marked with a "reviewing" badge.

    The board shown is always `live_question`'s board when something's
    live; otherwise it's whatever the QM last navigated to via the control
    center's board-select controls (`host:board_select`, §6) — never both,
    and never ambiguous, since board navigation is locked while a question
    is live (§4).
  - The stage never shows scoring controls or upload UI, and never shows an
    unrevealed answer.
  - Board grid updating on question close replaces the PPT "gray out the
    slide" step entirely.
  - Sidebar (outside the stage, always visible, independent of whatever's
    on stage): running score totals (a live leaderboard) and the live buzz
    queue.

## 6. Real-time protocol delta

New client → server (host-authenticated socket only):
| event                  | payload            |
|------------------------|--------------------|
| `host:question_reveal` | `{ question_id }`  |
| `host:answer_reveal`   | `{}`               |
| `host:question_cancel` | `{}`               |
| `host:board_select`    | `{ board_index }`  |

`host:board_select` (B2) changes which board the control center — and, by
extension, the presentation view when nothing's live — is showing. Rejected
if a question is currently live (§4's navigation lock) or the index is out
of range.

Host-only (B1): `state:live_question`, `{ live_question: {question_id,
board, category, value, question, answer, media, status, reviewing} |
null }`. Broadcast to the host room only. `answer` is always included once
`live_question` is set — the QM's private judging aid, visible from the
moment of reveal regardless of presentation-facing phasing.
`[V4: media field renamed question_media, and adds answer_media, included
whenever answer is — see SPEC V4.md §3]`

Presentation-room-only (B2): `state:presentation`, `{ board_name,
board_index, board_count, board, totals, live_question }`. `board` is a
redacted grid (`value`/`state`/`entries` per cell — no `question`/`answer`/
`media`, unlike the host's `state:scores`) for whichever board is current
(§5). `live_question` mirrors `state:live_question`'s shape minus `board`/
`category`/`value`, with `answer` included **only** when
`status == "answer_shown"` — the presentation room must never receive an
unrevealed answer. `[V4: live_question.answer_media follows the same
answer_shown gate — see SPEC V4.md §3]` **No `queue` field** — the
presentation room also
receives the existing `state:queue` broadcast directly (same event, same
payload, no redaction needed — queue entries never carried question/answer
content), rather than duplicating queue data inside `state:presentation`.
This deliberately diverges from embedding `queue` in one bundled payload:
avoids a second source of truth for board/totals-adjacent data that every
future scoring/queue-touching event would otherwise need to remember to
keep in sync inside `state:presentation` too.

Player-facing events are unchanged from V2.

## 7. QM authoring workflow (documented trade-offs)

- Answers move from a separate tab into the `answer` column (single-sheet
  contract).
- Images move from in-cell embeds to files: export each image from the sheet
  into `media/`, reference by filename in the `media` column. `[V4: column
  renamed question_media — see SPEC V4.md §1]`
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