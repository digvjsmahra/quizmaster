"""Unit tests for stats.py, driven through a real Game so the log under test
is the one the app actually produces (SPEC.md §5)."""

from bundle_loader import BundleQuestion
from game import Game
from stats import buzz_stats

Q1 = "1:History:10"
Q2 = "1:History:20"
Q3 = "1:Science:10"


def _q(qid, board, category, value):
    return BundleQuestion(
        id=qid, board=board, category=category, value=value,
        question=f"Q for {qid}", answer=f"A for {qid}", question_media=[], answer_media=[],
    )


def _game():
    g = Game(questions={"1": [
        _q(Q1, "1", "History", 10),
        _q(Q2, "1", "History", 20),
        _q(Q3, "1", "Science", 10),
    ]})
    ankur, _ = g.player_join("Ankur")
    dev, _ = g.player_join("Dev")
    g.start_quiz()
    return g, ankur, dev


def _stats(g):
    return {r["name"]: r for r in buzz_stats(g.event_log, g.roster, g.players)}


def _shift(g, pid, seconds):
    """Backdate a player's last logged buzz so latencies are deterministic."""
    for ev in reversed(g.event_log):
        if ev.type == "buzz" and ev.player_id == pid:
            ev.at = _t0_of(g, ev) + seconds
            return
    raise AssertionError("no buzz logged for that player")


def _t0_of(g, buzz_ev):
    """The reveal-or-reset that opened the sub-round this buzz landed in."""
    t0 = None
    for ev in g.event_log:
        if ev.seq >= buzz_ev.seq:
            break
        if ev.type in ("question_reveal", "queue_reset"):
            t0 = ev.at
    return t0


def _close(g, qid, scores=None):
    if g.live_question["status"] != "answer_shown":
        g.answer_reveal()
    g.question_submit(qid, scores or {})


# ------------------------------------------------------------------
# Episode rule — which questions count at all
# ------------------------------------------------------------------

def test_normal_question_counts():
    g, ankur, dev = _game()
    g.question_reveal(Q1)
    g.player_buzz(ankur)
    g.player_buzz(dev)
    _close(g, Q1)
    rows = _stats(g)
    assert rows["Ankur"]["buzz_count"] == 1
    assert rows["Dev"]["buzz_count"] == 1
    assert rows["Ankur"]["avg_position"] == 1.0
    assert rows["Dev"]["avg_position"] == 2.0


def test_cancelled_question_buzzes_excluded():
    g, ankur, _ = _game()
    g.question_reveal(Q1)
    g.player_buzz(ankur)
    g.question_cancel()
    assert _stats(g)["Ankur"]["buzz_count"] == 0
    assert _stats(g)["Ankur"]["closed_count"] == 0


def test_cancel_then_rereveal_counts_only_the_second_episode():
    g, ankur, dev = _game()
    g.question_reveal(Q1)
    g.player_buzz(ankur)
    g.question_cancel()
    g.question_reveal(Q1)
    g.player_buzz(dev)
    _close(g, Q1)
    rows = _stats(g)
    assert rows["Ankur"]["buzz_count"] == 0
    assert rows["Dev"]["buzz_count"] == 1


def test_reviewing_reopen_does_not_open_an_episode():
    g, ankur, dev = _game()
    g.question_reveal(Q1)
    g.player_buzz(ankur)
    _close(g, Q1, {ankur: 10.0})
    g.question_reveal(Q1)          # reopen — already closed, reviewing
    g.player_buzz(dev)             # stray buzz during a correction
    _close(g, Q1, {ankur: 5.0})
    rows = _stats(g)
    assert rows["Ankur"]["buzz_count"] == 1
    assert rows["Dev"]["buzz_count"] == 0
    assert rows["Ankur"]["closed_count"] == 1


def test_dead_period_buzz_excluded():
    g, ankur, _ = _game()
    g.player_buzz(ankur)           # nothing revealed
    g.question_reveal(Q1)
    _close(g, Q1)
    assert _stats(g)["Ankur"]["buzz_count"] == 0


def test_still_open_question_excluded():
    g, ankur, _ = _game()
    g.question_reveal(Q1)
    g.player_buzz(ankur)
    rows = _stats(g)               # summary opened mid-question
    assert rows["Ankur"]["buzz_count"] == 0
    assert rows["Ankur"]["closed_count"] == 0


# ------------------------------------------------------------------
# Sub-round rule — which buzzes within a counted question survive
# ------------------------------------------------------------------

def test_accidental_buzzes_discarded_by_an_immediate_reset():
    g, ankur, dev = _game()
    g.question_reveal(Q1)
    g.player_buzz(ankur)           # misfire
    g.player_buzz(dev)             # misfire
    g.queue_reset()                # QM wipes them, honour code
    g.player_buzz(dev)             # the real buzz
    _close(g, Q1, {dev: 10.0})
    rows = _stats(g)
    assert rows["Ankur"]["buzz_count"] == 0
    assert rows["Dev"]["buzz_count"] == 1
    assert rows["Dev"]["avg_position"] == 1.0


def test_rebuzz_is_timed_from_the_reset_not_the_reveal():
    g, ankur, _ = _game()
    g.question_reveal(Q1)
    g.player_buzz(ankur)
    g.queue_reset()
    g.player_buzz(ankur)
    _shift(g, ankur, 0.4)          # 400 ms after the reset
    _close(g, Q1, {ankur: 10.0})
    row = _stats(g)["Ankur"]
    assert row["buzz_count"] == 1
    assert row["avg_ms"] == 400


def test_no_double_count_when_a_player_buzzes_in_both_sub_rounds():
    g, ankur, _ = _game()
    g.question_reveal(Q1)
    g.player_buzz(ankur)
    g.queue_freeze()
    g.queue_reset()                # the only way back to an open queue
    g.player_buzz(ankur)
    _close(g, Q1, {ankur: 10.0})
    assert _stats(g)["Ankur"]["buzz_count"] == 1


def test_freeze_without_reset_is_still_timed_from_the_reveal():
    g, ankur, dev = _game()
    g.question_reveal(Q1)
    g.player_buzz(ankur)
    _shift(g, ankur, 0.25)
    g.queue_freeze()               # common case: freeze after the burst
    g.player_buzz(dev)             # locked out, never logged
    _close(g, Q1, {ankur: 10.0})
    rows = _stats(g)
    assert rows["Ankur"]["avg_ms"] == 250
    assert rows["Dev"]["buzz_count"] == 0


def test_trailing_reset_yields_no_buzz_data_for_that_question():
    g, ankur, _ = _game()
    g.question_reveal(Q1)
    g.player_buzz(ankur)
    g.queue_reset()                # nobody buzzes again
    _close(g, Q1)
    row = _stats(g)["Ankur"]
    assert row["buzz_count"] == 0
    assert row["closed_count"] == 1     # the question still closed


def test_reset_in_the_dead_period_does_not_affect_the_next_question():
    g, ankur, _ = _game()
    g.queue_reset()                # QM tidies up between questions
    g.question_reveal(Q1)
    g.player_buzz(ankur)
    _close(g, Q1, {ankur: 10.0})
    assert _stats(g)["Ankur"]["buzz_count"] == 1


# ------------------------------------------------------------------
# Aggregation
# ------------------------------------------------------------------

def test_averages_divide_by_own_buzzes_not_question_count():
    g, ankur, dev = _game()
    for qid in (Q1, Q2, Q3):
        g.question_reveal(qid)
        if qid == Q1:
            g.player_buzz(ankur)   # Ankur buzzes once across three questions
        g.player_buzz(dev)
        _close(g, qid, {dev: 10.0})
    rows = _stats(g)
    assert rows["Ankur"]["buzz_count"] == 1
    assert rows["Ankur"]["closed_count"] == 3
    assert rows["Ankur"]["avg_position"] == 1.0       # not diluted to 0.33
    assert rows["Dev"]["buzz_count"] == 3


def test_avg_and_median_latency():
    g, ankur, _ = _game()
    for qid, delay in ((Q1, 0.1), (Q2, 0.2), (Q3, 0.9)):
        g.question_reveal(qid)
        g.player_buzz(ankur)
        _shift(g, ankur, delay)
        _close(g, qid, {ankur: 10.0})
    row = _stats(g)["Ankur"]
    assert row["avg_ms"] == 400
    assert row["median_ms"] == 200


def test_player_who_never_buzzed_gets_a_row_with_nulls():
    g, ankur, dev = _game()
    g.question_reveal(Q1)
    g.player_buzz(ankur)
    _close(g, Q1, {ankur: 10.0})
    row = _stats(g)["Dev"]
    assert row["buzz_count"] == 0
    assert row["avg_ms"] is None and row["median_ms"] is None
    assert row["avg_position"] is None


def test_rows_sorted_fastest_first_with_non_buzzers_last():
    g = Game(questions={"1": [_q(Q1, "1", "History", 10), _q(Q2, "1", "History", 20)]})
    ankur, _ = g.player_join("Ankur")
    dev, _ = g.player_join("Dev")
    g.player_join("Quiet")            # joins, never buzzes
    g.start_quiz()

    g.question_reveal(Q1)
    g.player_buzz(dev)
    _shift(g, dev, 0.1)
    _close(g, Q1, {dev: 10.0})
    g.question_reveal(Q2)
    g.player_buzz(ankur)
    _shift(g, ankur, 0.5)
    _close(g, Q2, {ankur: 20.0})
    g.roster_add("Ghost")             # virtual, omitted entirely

    names = [r["name"] for r in buzz_stats(g.event_log, g.roster, g.players)]
    assert names == ["Dev", "Ankur", "Quiet"]


def test_virtual_roster_entries_are_omitted():
    g, ankur, _ = _game()
    g.roster_add("Phone-in team")
    g.question_reveal(Q1)
    g.player_buzz(ankur)
    _close(g, Q1, {ankur: 10.0})
    assert "Phone-in team" not in _stats(g)


def test_removed_roster_member_drops_out_of_the_table():
    g, ankur, dev = _game()
    g.question_reveal(Q1)
    g.player_buzz(ankur)
    g.player_buzz(dev)
    _close(g, Q1, {ankur: 10.0})
    g.remove_from_roster(dev)
    rows = _stats(g)
    assert "Dev" not in rows
    assert rows["Ankur"]["buzz_count"] == 1


def test_empty_log_gives_a_zeroed_row_per_player():
    g, _, _ = _game()
    rows = _stats(g)
    assert rows["Ankur"]["closed_count"] == 0
    assert rows["Ankur"]["buzz_count"] == 0


# ------------------------------------------------------------------
# score_timeline
# ------------------------------------------------------------------

def _timeline(g):
    from stats import score_timeline
    return score_timeline(g.event_log, g.roster, g.players)


def test_timeline_x_axis_is_play_order_not_board_order():
    g, ankur, _ = _game()
    for qid in (Q3, Q1, Q2):          # played out of board order
        g.question_reveal(qid)
        _close(g, qid, {ankur: 10.0})
    assert _timeline(g)["questions"] == [Q3, Q1, Q2]


def test_timeline_series_lead_with_zero_and_accumulate():
    g, ankur, dev = _game()
    g.question_reveal(Q1)
    _close(g, Q1, {ankur: 10.0})
    g.question_reveal(Q2)
    _close(g, Q2, {ankur: 20.0, dev: -20.0})
    rows = {s["name"]: s["points"] for s in _timeline(g)["series"]}
    assert rows["Ankur"] == [0.0, 10.0, 30.0]
    assert rows["Dev"] == [0.0, 0.0, -20.0]


def test_correction_amends_the_original_point_and_shifts_later_ones():
    g, ankur, _ = _game()
    g.question_reveal(Q1)
    _close(g, Q1, {ankur: 10.0})
    g.question_reveal(Q2)
    _close(g, Q2, {ankur: 20.0})
    g.question_reveal(Q1)             # reopen the first question
    _close(g, Q1, {ankur: 5.0})       # ...and correct it

    tl = _timeline(g)
    assert tl["questions"] == [Q1, Q2]        # no third point appended
    (row,) = [s["points"] for s in tl["series"] if s["name"] == "Ankur"]
    assert row == [0.0, 5.0, 25.0]            # point 1 amended, point 2 shifted


def test_timeline_last_point_equals_the_standings_total():
    g, ankur, dev = _game()
    g.question_reveal(Q1)
    _close(g, Q1, {ankur: 10.0, dev: -10.0})
    g.question_reveal(Q2)
    _close(g, Q2, {dev: 20.5})
    g.question_reveal(Q3)
    _close(g, Q3, {ankur: 10.0})
    g.question_reveal(Q1)
    _close(g, Q1, {ankur: 7.0, dev: -10.0})   # a late correction

    finals = {s["name"]: s["points"][-1] for s in _timeline(g)["series"]}
    totals = {r["name"]: r["total"] for r in g.get_standings()}
    assert finals == totals


def test_timeline_excludes_cancelled_questions():
    g, ankur, _ = _game()
    g.question_reveal(Q1)
    g.question_cancel()
    g.question_reveal(Q2)
    _close(g, Q2, {ankur: 20.0})
    assert _timeline(g)["questions"] == [Q2]


def test_timeline_series_sorted_by_final_score():
    g, ankur, dev = _game()
    g.question_reveal(Q1)
    _close(g, Q1, {dev: 30.0, ankur: 10.0})
    assert [s["name"] for s in _timeline(g)["series"]] == ["Dev", "Ankur"]


def test_timeline_includes_virtual_roster_entries():
    # Unlike the buzz table, the graph is about scores — a host-added team
    # scores like anyone else and belongs on it.
    g, ankur, _ = _game()
    ghost = g.roster_add("Phone-in team")
    g.question_reveal(Q1)
    _close(g, Q1, {ghost: 10.0, ankur: 5.0})
    assert [s["name"] for s in _timeline(g)["series"]] == ["Phone-in team", "Ankur", "Dev"]


def test_timeline_player_added_after_a_question_starts_flat():
    g, ankur, _ = _game()
    g.question_reveal(Q1)
    _close(g, Q1, {ankur: 10.0})
    late = g.roster_add("Latecomer")
    g.question_reveal(Q2)
    _close(g, Q2, {late: 20.0})
    rows = {s["name"]: s["points"] for s in _timeline(g)["series"]}
    assert rows["Latecomer"] == [0.0, 0.0, 20.0]


def test_timeline_empty_before_anything_closes():
    g, _, _ = _game()
    tl = _timeline(g)
    assert tl["questions"] == []
    assert all(s["points"] == [0.0] for s in tl["series"])
