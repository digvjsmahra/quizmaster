# SPEC — V5 Delta (Persistent Player Identity)

Extends `SPEC.md` directly. Everything in V1/V2/V3/V4 stands unless
explicitly changed here. V5's job: let a player's browser silently resume
its exact same buzz identity after a reconnect (reload, screen-lock,
app-switch, network blip) instead of forcing a fresh name-entry each time.

Prompted by real 2-4 hour quiz nights on mobile: screen locks, switching to
other apps, and habitual pull-to-refresh are constant over that span. Under
V1's "no reconnection logic" model (`SPEC.md` §8 "Reconnection"), every one
of those was a fresh buzz identity — observed in practice as players
needing to re-enter their name 5-10 times a game, and the host's queue
going stale for anyone who'd silently dropped out of the broadcast room
without noticing.

---

## 1. Rejoin token

**`[V5: supersedes SPEC.md §2's "Reconnection identity matching" (out of
scope for V1)]`**

Every real (`virtual=False`) player is issued a `rejoin_token` — a random,
non-rotating, non-expiring string, generated once at `player_join` and
valid for the lifetime of the room (same lifetime as everything else in
this in-memory app: until server restart). It is not a security boundary
(same trust model as the existing per-room host token — obscurity, not
auth) — its only job is letting a device reclaim the identity it already
has.

The token resolves back to the *same* `player_id` on every use, no matter
how many times it's used. It does **not** create, remove, or reorder
anything in the roster, scores, or board grid — see §3.

## 2. Real-time protocol delta

Extends `SPEC.md` §7. One additive field on an existing event, one new
event:

- `player:accepted` (server → one player) gains `rejoin_token` alongside
  the existing `player_id`/`phase`.
- `player:rejoin` (player → server, new) — payload `{ room_id, token }`.
  Effect: if `token` matches a real player's `rejoin_token` in that room,
  marks them connected again and acks with `player:accepted` (same
  `player_id`, current `phase`, same `rejoin_token` echoed back) exactly
  as `player:join` would for a fresh identity — including the same
  live-phase `state:queue` catch-up emit and the same `state:players`
  broadcasts to both the players' room and the host. An unknown/expired/
  foreign token gets `player:rejected { reason }`, same shape as a
  rejected `player:join`.

No other event changes. The reveal/scoring/queue protocol from `SPEC.md`
§7 and `SPEC V3.md`/`V4.md`'s deltas are untouched.

## 3. What's explicitly unchanged

**`[V5: SPEC.md §5's "Two-tier identity" and §10's "No reconnection
matching" decision are narrowed, not reversed — see §4 below. Everything
else about roster/scoring is unaffected.]`**

- The roster (`SPEC.md` §4's `roster: list[player_id]`) is still a
  one-time snapshot taken at Start; rejoining never adds to it, removes
  from it, or reorders it.
- Scoring (`host:question_submit`) still operates purely on `roster`/
  `scores`; a rejoined player's award history is exactly what it was.
- Host-added (`virtual=True`) entries are untouched — they have no socket
  and no rejoin token is ever handed to a client for them.
- A player who was mid-queue (already buzzed) when their connection
  dropped keeps that exact queue position on rejoin — the queue is keyed
  by `player_id`, which a rejoin never changes.
- The host control center requires zero changes: it already derives its
  player lists purely from `Player.connected`/`virtual`
  (`get_active_players()`/`get_lobby_players()`), which a rejoin updates
  the same way a fresh join always has.

## 4. Client behavior

- On successful `player:accepted` (join or rejoin), the player's browser
  stores `{ rejoin_token, name }` in `localStorage`, keyed per room
  (`qb_rejoin_<join_code>`) so a device reused across different quiz
  nights doesn't cross-contaminate.
- On every socket `connect` — which fires identically on first load and on
  every reconnect — the client checks that storage first. Found → emits
  `player:rejoin` directly, skipping the name-entry view entirely. Not
  found → falls back to the existing `?name=` URL auto-join, then manual
  name entry.
- **Anti-flash:** since a full page reload re-parses the raw HTML (where
  the join form is the only view not hidden by default), a tiny inline
  script runs synchronously in `<head>`, before `<body>` paints, and hides
  the join form immediately if a token is present for this room — so a
  reload never visibly flashes a login screen before resuming.
- **Failure fallback:** a rejected rejoin (invalid/foreign/expired token)
  clears the stale `localStorage` entry, reveals the join form again, and
  surfaces a plain-language reason via the existing join-error element —
  never a stuck or blank screen.
- Repeated/rapid reconnects (e.g. an anxious player refreshing more than
  once) are safe: each rejoin independently resolves to the same token →
  same `player_id`, so no duplicate identities accumulate, and the queue/
  lock state returned is always whatever is *currently* true on the
  server, never stale or cached.

## 5. Out of scope for V5

- No change to the host or presentation protocols, the reveal state
  machine (`SPEC V3.md` §4), or scoring (`SPEC.md` §7/§10).
- No token expiry/rotation, and no server-side identity beyond the
  in-memory `Player` record — a server restart still wipes everything,
  same as every other piece of state in this app (`SPEC.md` §9
  "Volatility").
- No cross-device identity — a token lives in one browser's
  `localStorage`; joining from a second device is still a new buzz
  identity, same as V1.
- No Socket.IO-level Connection State Recovery — considered and rejected
  as the primary mechanism, since its recovery window doesn't cover the
  multi-minute-or-longer gaps (app-switching, extended screen lock) this
  delta targets; the durable token is the load-bearing fix. Could be
  layered on top later as a minor optimization for short blips, but isn't
  needed for correctness.
