# SPEC — V7 Delta (Post-Start Roster Removal)

Extends `SPEC.md` and `SPEC V6.md`. Everything in V1-V6 stands unless
explicitly changed here. V7's job: let the host remove a roster member
*after* Start, discarding whatever scores they have — something V6
explicitly declined to cover.

Prompted by a real scenario V6 didn't anticipate: a player typo'd the
room *code* into the name field before Start, that mistake froze into
the roster snapshot, and they only rejoined under their correct name
*after* Start (real players aren't gated from joining once live — see
`SPEC.md` §8's "Late joiners... land directly on the buzzer"). The
mistaken entry has no scores of its own most of the time, but there's no
requirement that it doesn't — a host might also want to clear a roster
row that's already been scored, e.g. a player who has to leave early.

## 1. Scope: live phase, real or virtual, scores discarded

**`[V7: supersedes SPEC V6.md §1's "There is no roster or score
removal... this delta does not apply"]`**

- Only valid once `phase == "live"` — there's no roster before Start
  (that's still `host:player_remove`'s job, `SPEC V6.md`, unchanged).
- Works on any roster member, real (`virtual=False`) or host-added
  (`virtual=True`) — the roster is just a list of player ids regardless
  of how they got there.
- Unlike V6's lobby removal, this is allowed even if the member already
  has scored entries — removal discards them outright, along with the
  roster row and (for a real, still-connected player) their live
  identity. This is a genuine, no-undo data-loss action, distinct from
  `SPEC.md` §10's "no undo/redo" decision — that rule is about
  *correcting a score value* (re-click a cell, re-submit is still the
  only way to fix a mis-entered award); this removes a *player*
  wholesale, which happens to also clear whatever was attached to them.
  Same distinction `CLAUDE.md` already draws for V3's `question_cancel`
  ("a pre-score reveal-undo, not a scoring undo").

## 2. Real-time protocol delta

Extends `SPEC.md` §7 and `SPEC V6.md` §2. One new event; reuses V6's
`player:removed` rather than introducing another:

- `host:roster_remove` (host → server) — payload `{ player_id }`. If the
  room is live and the id is a current roster member, removes them from
  the roster, deletes any of their score entries, and deletes their
  `Player` record outright. Otherwise `error { message, context:
  "roster_remove" }`.
- If the removed player still has a live connection, the server sends
  them `player:removed` (`SPEC V6.md` §2) and force-disconnects them —
  identical treatment to a lobby removal, so they land on a working join
  form instead of a buzzer for a game they're no longer part of.

## 3. What's unchanged

- `host:player_remove` (V6) is untouched — still lobby-only, still a
  plain delete with nothing to reconcile.
- Scoring (`host:question_submit`), the reveal state machine
  (`SPEC V3.md` §4), and roster snapshotting at Start (`SPEC.md` §4) are
  all unaffected — this only ever removes an *existing* roster row, never
  changes how one gets created or scored while it exists.
- `host:roster_add` (`SPEC.md` §7) is unaffected — still the only way to
  create a roster entry.

## 4. Out of scope for V7

- No confirmation/undo beyond the host client's own confirm dialog —
  once `host:roster_remove` is sent, it's final, same as every other
  host action in this protocol.
- No partial removal (e.g. clearing one board's scores but not another)
  — it's the whole roster row and all of their scores, or nothing.
