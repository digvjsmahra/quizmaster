# SPEC — V6 Delta (Lobby Player Removal)

Extends `SPEC.md` directly (and, incidentally, sits alongside `SPEC V5.md`'s
player-identity work without changing it). Everything in V1-V5 stands
unless explicitly changed here. V6's job: let the host clean up a
duplicate or mistyped lobby join before it gets frozen into the roster.

Prompted by real hosting friction: players occasionally join twice before
Start (a typo, a retry, a double-tap on the link), and today there's no
way to fix that — every lobby entry becomes a permanent scorecard row at
Start, with no undo afterward (`SPEC.md` §10's "no undo/redo" rule). The
fix has to happen pre-Start, since that's the only point where removing
someone has no scoring data to reconcile.

## 1. Scope: lobby phase only

**`[V7: narrowed — see SPEC V7.md]`** Removal here is only possible while
`phase == "lobby"`; `host:player_remove` stays a plain delete with
nothing to reconcile. Post-Start roster/score removal — a real scenario
V6 didn't anticipate — is now covered separately by
`host:roster_remove` (`SPEC V7.md`), not by extending this event. Before
V7, there was no roster or score removal at all once Start snapshotted
the roster; the existing "no undo/redo, re-click and re-submit"
correction mechanism (`SPEC.md` §10) remains the only way to fix a
*score value* either way.
Host-added (`virtual=True`) entries aren't reachable by this either, since
they only exist post-Start in practice (the "+ add" control is live-phase
only per `SPEC.md`'s host flow).

## 2. Real-time protocol delta

Extends `SPEC.md` §7. One new event, no changes to existing ones:

- `host:player_remove` (host → server) — payload `{ player_id }`. If the
  room is still in `lobby` phase and the id is a known player, deletes
  them outright (`del self.players[player_id]` — nothing else to clean up
  pre-Start: no roster entry, no scores, no queue entry can exist yet).
  Otherwise emits `error { message, context: "player_remove" }`, same
  shape as every other host action's rejection.
- `player:removed` (server → the removed player only) — empty payload.
  Sent immediately before the server force-disconnects that player's
  socket, so the message reliably arrives first.

## 3. Client behavior

- Host control center: each lobby row gets a small remove control,
  confirmed via the same weight as other irreversible host actions (e.g.
  cancelling a live reveal) — a plain confirm dialog, not a silent action.
- Removed player: on receiving `player:removed`, the browser clears its
  saved rejoin identity (`SPEC V5.md`) and returns to a normal, working
  join form with a plain-language explanation — not a dead end. Nothing
  stops them from immediately rejoining with a corrected name; that's the
  intended recovery path.

## 4. Out of scope for V6

- No roster/score-level removal after Start — see §1.
- No bulk removal — one player at a time, mirroring the existing
  single-target host actions elsewhere in the protocol.
- No change to `host:roster_add`, scoring, or any other post-Start flow.
