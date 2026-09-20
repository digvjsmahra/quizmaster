"""Derivations over a room's event log (SPEC.md §5).

Pure functions: they read a `list[LogEvent]` and the roster it should be
reported against, and hold no state of their own. Kept out of `game.py` for
the same reason `bundle_loader.py` is — `game.py` records what happened,
this interprets it, and the two are worth testing apart.
"""

from statistics import mean, median

# Deliberately no `from game import LogEvent`: game.py imports this module for
# get_summary_payload, and the pair would import circularly. Nothing here needs
# the class itself — every `log` argument is a list of game.LogEvent, read by
# attribute.


def _episodes(log: list) -> list[dict]:
    """Split the log into scoreable question episodes.

    An episode opens on a fresh `question_reveal` (never a `reviewing` reopen,
    which shows an already-answered question) and closes on that question's
    `question_submit` or `question_cancel`. Only submit-closed episodes are
    returned — a cancelled question was never played, so its buzzes aren't
    reaction times to anything that counted.

    Within an episode, a QM-initiated `queue_reset` starts a new sub-round and
    discards the one before it. Only the final sub-round survives, timed from
    whatever opened it. That is what makes an immediate reset after accidental
    buzzes wipe them and restart the clock, and it's why a player can never be
    double-counted for buzzing both before and after a reset.

    A frozen interval can never sit inside a counted sub-round: once frozen,
    no buzz is accepted (`Game.player_buzz`), and the only way back to unlocked
    is a reset, which opens a new sub-round.
    """
    episodes: list[dict] = []
    open_ep: dict | None = None

    for ev in log:
        if ev.type == "question_reveal":
            if ev.data.get("reviewing"):
                continue
            open_ep = {"question_id": ev.question_id, "t0": ev.at, "buzzes": []}
        elif open_ep is None or ev.question_id != open_ep["question_id"]:
            continue
        elif ev.type == "buzz":
            open_ep["buzzes"].append(ev)
        elif ev.type == "queue_reset":
            # The QM threw this queue away — new sub-round, new t0.
            open_ep["t0"] = ev.at
            open_ep["buzzes"] = []
        elif ev.type == "question_submit":
            episodes.append(open_ep)
            open_ep = None
        elif ev.type == "question_cancel":
            open_ep = None

    return episodes


def buzz_stats(log: list, roster: list[str], players: dict) -> list[dict]:
    """Per-player buzzer figures, one row per non-virtual roster member.

    Every average is over that player's own buzzes, never over the question
    count: someone who buzzed four times and was first each time averages
    position 1.0, undiluted by the questions they sat out. `closed_count` is
    the shared denominator that makes `buzz_count` readable ("buzzed 4 of 30").
    """
    episodes = _episodes(log)

    per_player: dict[str, list[LogEvent]] = {}
    for ep in episodes:
        for ev in ep["buzzes"]:
            per_player.setdefault(ev.player_id, []).append((ev, ep["t0"]))

    rows = []
    for pid in roster:
        player = players.get(pid)
        if player is None or player.virtual:
            continue
        buzzes = per_player.get(pid, [])
        latencies = [round((ev.at - t0) * 1000) for ev, t0 in buzzes]
        positions = [ev.data["position"] for ev, _ in buzzes]
        rows.append(
            {
                "player_id": pid,
                "name": player.name,
                "buzz_count": len(buzzes),
                "closed_count": len(episodes),
                "avg_ms": round(mean(latencies)) if latencies else None,
                "median_ms": round(median(latencies)) if latencies else None,
                "avg_position": round(mean(positions), 2) if positions else None,
            }
        )

    # Fastest first; players who never buzzed sort last regardless.
    rows.sort(key=lambda r: (r["avg_ms"] is None, r["avg_ms"] or 0))
    return rows
