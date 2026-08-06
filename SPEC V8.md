# SPEC — V8 Delta (Awarded Cell Color: Green vs. Red)

Extends `SPEC.md` directly. Everything in V1-V7 stands unless explicitly
changed here. V8's job: give the existing **Awarded** cell state a color
sub-rule so the board reads faster at a glance — today every closed,
scored question renders as one flat color regardless of outcome.

This is a presentational refinement, not a new state. `SPEC.md` §5's
Unplayed/Awarded/Passed three-state model is unchanged; V8 only adds a
derived field on the Awarded cell payload that the client uses to pick
between two color variants of the same state.

## 1. Color rule

**`[V8: refines SPEC.md §5's Awarded row]`**

- **Green** (unchanged from before V8): at least one entry for the cell
  is positive — someone got it, regardless of the net sum across all
  entries. A cell with `[+10, -5]` is green, same as `[+10]` alone.
- **Red** (new): there's a genuine negative (penalty) entry and *no*
  positive entry. Nobody got this one.
- An explicit `0` entry counts as neither positive nor negative — pure
  NA. A cell with only zero entries (`[0]`, `[0, 0]`) stays green/neutral
  rather than reading as a miss; a cell with a real negative plus a zero
  (`[-5, 0]`) is still red, since the zero doesn't cancel out the
  penalty. `0` entries are rare in practice — a blank scoring row is
  skipped entirely and produces no entry at all (`SPEC.md` §8's host
  flow); a host has to type `0` explicitly.

## 2. Protocol delta

Extends `SPEC.md` §7's payload shapes. One additive field, no new
events: every Awarded cell in `state:scores`'s `grid` and
`state:presentation`'s `board` gains `negative_only: bool`, computed
server-side once (`Game._cell_state`) so both views derive the same
color from the same source rather than each re-deriving it from
`entries`.

## 3. Out of scope for V8

- No change to how cells become Awarded/Passed/Unplayed, to scoring
  (`host:question_submit`), or to the entries shown inside a cell — only
  the cell's background color.
- No third color or further sub-states — green/red covers the Awarded
  case completely; Unplayed and Passed are unaffected.
