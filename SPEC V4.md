# SPEC — V4 Delta (Answer Media + Adaptive Answer Slide)

Extends `SPEC V3.md` (and, through it, `SPEC.md`). Everything in V1/V2/V3
stands unless explicitly changed here. V4's job: give the answer half of a
reveal the same visual weight as the question half — its own optional image,
and a presentation layout that adapts instead of squeezing long answers into
the question's box.

Prompted by feedback from the first hosted quiz: answers with real
information (long text, or wanting their own image) had no good way to be
shown next to a small, fixed-size question box.

---

## 1. Import contract: `question_media` rename + `answer_media` column

This delta also renames V3's `media` column to `question_media`, for
symmetry with the new `answer_media` column below — naming only, no
behavior change. A bare `media` no longer reads unambiguously once an
answer can carry its own image too.

Extends `SPEC V3.md` §3's `quiz.xlsx` column table:

| column          | required | notes |
|-----------------|----------|-------|
| `question_media`| no       | Renamed from V3's `media` (see above). Comma-separated filenames, rules unchanged: extension allow-list, must exist in `media/`, same flat folder. |
| `answer_media`  | no       | Comma-separated filenames, same rules as `question_media`. Independent of `question_media` — a question and its answer may each carry their own image, neither, or both. |

- Does **not** satisfy the `question`-or-`question_media` requirement
  (`SPEC V3.md` §3) — that rule is unchanged and is about the *question*
  side only.
- Validation, warnings (unreferenced file), and storage/serving are identical
  to `question_media` — both columns draw from the same flat `media/` pool
  (folder name unchanged) and the same host-secret
  `/media/<join_code>/<host_token>/<filename>` route.

## 2. Presentation view: adaptive answer slide

**`[V4: supersedes SPEC V3.md §5's "the answer fading in below it ... not a
hard cut or a full slide swap"]`**

The presentation stage's question slide still shows the question the same
way (§5's phased reveal is unchanged: question first, answer on
`answer_reveal`). What changes is *how* the answer appears once revealed:

- **Inline (unchanged from V3):** if the answer has no `answer_media` and its
  content fits in the stage alongside the question, it fades in below the
  question in the same box — exactly as before.
- **Dedicated answer slide (new):** if the answer has its own `answer_media`,
  or its content would not fit in the stage, the presentation view instead
  advances to a full-stage answer slide — the question and its own media do
  not persist onto it, since neither is new information once the answer has
  taken over the moment. This applies identically whether the reveal is
  fresh or a reopened `reviewing` question (`SPEC V3.md` §4) — both are
  "the answer is now showing" moments, judged by the same rule.
- **Fully automatic, no host control.** The presentation client decides
  which layout to use at the moment `answer_shown` is reached; the QM does
  not choose or trigger it. This keeps the reveal flow (`SPEC V3.md` §4) and
  its protocol (`host:answer_reveal` etc.) unchanged — V4 only changes how
  the presentation view *renders* an already-revealed answer, not when or
  how it's revealed.
- **Sidebar (totals, buzz queue) is unaffected** — it's independent of the
  stage in both V3 and V4.
- **Host's control-center modal is unaffected in kind, extended in content:**
  the shared pre-Start-peek/live-reveal modal (`SPEC V3.md` §4's "QM sees the
  answer... from the moment of Reveal") now also shows `answer_media` inline,
  same as it already shows `question_media` — no slide-swap behavior there,
  since the QM's modal was never a fixed-size stage to overflow.

## 3. Real-time protocol delta

Extends `SPEC V3.md` §6's payload shapes. Following §1's column rename, the
`media` field in both `state:live_question` and `state:presentation`'s
`live_question` is renamed `question_media` (naming only, same gating as
before). Beyond that, one additive field, no new events:

- `state:live_question`: `answer_media` is included whenever `answer` is
  (i.e. from the moment `live_question` is set — host-only, private judging
  aid, same as today's `answer` field).
- `state:presentation`: `live_question.answer_media` is included **only**
  when `status == "answer_shown"` — same gate as `answer` itself, so the
  presentation room never receives answer media before the answer is
  actually revealed.
- Host's `state:scores` cell payload (`SPEC V3.md` §4/§7's Q&A peek) gains
  `answer_media` alongside its existing `question`/`answer`/`question_media`.

## 4. Out of scope for V4

- No new socket events, no change to the reveal state machine's states or
  transitions (`SPEC V3.md` §4's diagram is unchanged).
- No manual/host-triggered slide control — the adaptive choice in §2 is
  entirely automatic.
- No change to presentation view sizing/layout mechanics beyond what's
  needed to host the new answer slide — the fixed-stage, persistent-sidebar
  structure from `SPEC V3.md` §5 is otherwise unchanged.
