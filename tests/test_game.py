import pytest
from quiz_loader import Question
from game import Game


def make_game(boards=None) -> Game:
    if boards is None:
        boards = {
            "1": [
                Question("1:History:10", "1", "History", 10),
                Question("1:History:20", "1", "History", 20),
                Question("1:Science:10", "1", "Science", 10),
            ],
            "2": [
                Question("2:Movies:10", "2", "Movies", 10),
            ],
        }
    return Game(questions=boards)


# ------------------------------------------------------------------
# player_join
# ------------------------------------------------------------------

def test_player_join_returns_player_id_and_phase():
    g = make_game()
    pid, phase = g.player_join("Ankur")
    assert pid
    assert phase == "lobby"
    assert pid in g.players
    assert g.players[pid].name == "Ankur"


def test_player_join_strips_whitespace():
    g = make_game()
    pid, _ = g.player_join("  Dev  ")
    assert g.players[pid].name == "Dev"


def test_player_join_rejects_empty_name():
    g = make_game()
    with pytest.raises(ValueError):
        g.player_join("")
    with pytest.raises(ValueError):
        g.player_join("   ")


def test_player_join_returns_live_phase_after_start():
    g = make_game()
    g.player_join("Ankur")
    g.start_quiz()
    pid2, phase = g.player_join("Late")
    assert phase == "live"


# ------------------------------------------------------------------
# start_quiz
# ------------------------------------------------------------------

def test_start_quiz_transitions_phase():
    g = make_game()
    g.player_join("Ankur")
    g.start_quiz()
    assert g.phase == "live"


def test_start_quiz_snapshots_roster_in_join_order():
    g = make_game()
    pid1, _ = g.player_join("Ankur")
    pid2, _ = g.player_join("Dev")
    pid3, _ = g.player_join("Meera")
    g.start_quiz()
    assert g.roster == [pid1, pid2, pid3]


def test_start_quiz_is_idempotent():
    g = make_game()
    g.player_join("Ankur")
    g.start_quiz()
    roster_first = list(g.roster)
    g.player_join("Late")
    g.start_quiz()
    assert g.roster == roster_first  # late joiner not added


# ------------------------------------------------------------------
# player_buzz
# ------------------------------------------------------------------

def test_buzz_fifo_ordering():
    g = make_game()
    pid1, _ = g.player_join("Ankur")
    pid2, _ = g.player_join("Dev")
    g.start_quiz()
    g.player_buzz(pid1)
    g.player_buzz(pid2)
    queue = g.get_queue_payload()["queue"]
    assert queue[0]["player_id"] == pid1
    assert queue[1]["player_id"] == pid2


def test_buzz_rejected_in_lobby():
    g = make_game()
    pid, _ = g.player_join("Ankur")
    result = g.player_buzz(pid)
    assert result is None


def test_buzz_rejected_when_locked():
    g = make_game()
    pid, _ = g.player_join("Ankur")
    g.start_quiz()
    g.queue_freeze()
    result = g.player_buzz(pid)
    assert result is None


def test_buzz_rejected_if_already_queued():
    g = make_game()
    pid, _ = g.player_join("Ankur")
    g.start_quiz()
    g.player_buzz(pid)
    result = g.player_buzz(pid)
    assert result is None
    assert len(g.queue) == 1


def test_buzz_rejected_for_unknown_player():
    g = make_game()
    g.start_quiz()
    result = g.player_buzz("nonexistent")
    assert result is None


# ------------------------------------------------------------------
# queue_freeze / queue_reset
# ------------------------------------------------------------------

def test_queue_freeze_sets_locked():
    g = make_game()
    g.start_quiz()
    g.queue_freeze()
    assert g.queue_locked is True
    assert g.get_queue_payload()["locked"] is True


def test_queue_reset_clears_queue_and_unlocks():
    g = make_game()
    pid, _ = g.player_join("Ankur")
    g.start_quiz()
    g.player_buzz(pid)
    g.queue_freeze()
    g.queue_reset()
    assert g.queue == []
    assert g.queue_locked is False
    payload = g.get_queue_payload()
    assert payload["queue"] == []
    assert payload["locked"] is False


# ------------------------------------------------------------------
# roster_add
# ------------------------------------------------------------------

def test_roster_add_creates_entry():
    g = make_game()
    g.start_quiz()
    pid = g.roster_add("Priya")
    assert pid in g.roster
    assert g.players[pid].name == "Priya"


def test_roster_add_rejects_empty_name():
    g = make_game()
    g.start_quiz()
    with pytest.raises(ValueError):
        g.roster_add("")


def test_roster_add_creates_independent_entry():
    g = make_game()
    pid_buzz, _ = g.player_join("Ankur")
    g.start_quiz()
    # Priya joins late — not in the snapshotted roster
    pid_roster = g.roster_add("Priya")
    assert pid_roster in g.roster
    assert pid_roster != pid_buzz
    assert g.players[pid_roster].name == "Priya"


# ------------------------------------------------------------------
# question_submit
# ------------------------------------------------------------------

def _started_game():
    g = make_game()
    pid1, _ = g.player_join("Ankur")
    pid2, _ = g.player_join("Dev")
    g.start_quiz()
    return g, pid1, pid2


def test_question_submit_stores_values():
    g, pid1, pid2 = _started_game()
    g.question_submit("1:History:10", {pid1: 10.0, pid2: -10.0})
    assert g.scores[pid1]["1:History:10"] == 10.0
    assert g.scores[pid2]["1:History:10"] == -10.0


def test_question_submit_stores_decimal():
    g, pid1, _ = _started_game()
    g.question_submit("1:History:10", {pid1: 12.5})
    assert g.scores[pid1]["1:History:10"] == 12.5


def test_question_submit_blank_rows_skipped():
    g, pid1, pid2 = _started_game()
    g.question_submit("1:History:10", {pid1: 10.0})
    assert "1:History:10" not in g.scores.get(pid2, {})


def test_question_submit_all_blank_is_passed():
    g, _, _ = _started_game()
    g.question_submit("1:History:10", {})
    assert "1:History:10" in g.closed_questions
    cell = g._cell_state("1:History:10")
    assert cell["state"] == "passed"


def test_question_submit_overwrites_prior_entries():
    g, pid1, pid2 = _started_game()
    g.question_submit("1:History:10", {pid1: 10.0, pid2: -10.0})
    g.question_submit("1:History:10", {pid1: 5.0})
    assert g.scores[pid1]["1:History:10"] == 5.0
    assert "1:History:10" not in g.scores.get(pid2, {})


def test_question_submit_ignores_non_roster_players():
    g, pid1, _ = _started_game()
    g.question_submit("1:History:10", {"nonexistent": 99.0, pid1: 10.0})
    assert "nonexistent" not in g.scores
    assert g.scores[pid1]["1:History:10"] == 10.0


def test_question_submit_marks_closed():
    g, pid1, _ = _started_game()
    g.question_submit("1:History:10", {pid1: 10.0})
    assert "1:History:10" in g.closed_questions


# ------------------------------------------------------------------
# Cell state derivation
# ------------------------------------------------------------------

def test_cell_state_unplayed():
    g, _, _ = _started_game()
    cell = g._cell_state("1:History:10")
    assert cell["state"] == "unplayed"
    assert cell["value"] == 10


def test_cell_state_awarded_positive():
    g, pid1, _ = _started_game()
    g.question_submit("1:History:10", {pid1: 10.0})
    cell = g._cell_state("1:History:10")
    assert cell["state"] == "awarded"
    assert cell["entries"][0]["value"] == 10.0


def test_cell_state_awarded_negative_only():
    g, pid1, _ = _started_game()
    g.question_submit("1:History:10", {pid1: -10.0})
    cell = g._cell_state("1:History:10")
    assert cell["state"] == "awarded"


def test_cell_state_passed():
    g, _, _ = _started_game()
    g.question_submit("1:History:10", {})
    cell = g._cell_state("1:History:10")
    assert cell["state"] == "passed"


# ------------------------------------------------------------------
# Totals
# ------------------------------------------------------------------

def test_board_totals_correct():
    g, pid1, pid2 = _started_game()
    g.question_submit("1:History:10", {pid1: 10.0})
    g.question_submit("1:History:20", {pid1: 20.0, pid2: -20.0})
    payload = g.get_scores_payload()
    board1_totals = {r["player_id"]: r for r in payload["per_board_totals"]["1"]}
    assert board1_totals[pid1]["board_total"] == 30.0
    assert board1_totals[pid2]["board_total"] == -20.0


def test_cumulative_totals_span_all_boards():
    g = make_game()
    pid1, _ = g.player_join("Ankur")
    g.start_quiz()
    g.question_submit("1:History:10", {pid1: 10.0})
    g.question_submit("2:Movies:10", {pid1: 20.0})
    payload = g.get_scores_payload()
    board1 = {r["player_id"]: r for r in payload["per_board_totals"]["1"]}
    assert board1[pid1]["board_total"] == 10.0
    assert board1[pid1]["cumulative"] == 30.0


def test_board_totals_sorted_descending():
    g, pid1, pid2 = _started_game()
    g.question_submit("1:History:10", {pid1: 10.0, pid2: 30.0})
    payload = g.get_scores_payload()
    rows = payload["per_board_totals"]["1"]
    assert rows[0]["player_id"] == pid2
    assert rows[1]["player_id"] == pid1


def test_split_value_award():
    g, pid1, pid2 = _started_game()
    g.question_submit("1:History:20", {pid1: 10.0, pid2: 10.0})
    payload = g.get_scores_payload()
    board1 = {r["player_id"]: r for r in payload["per_board_totals"]["1"]}
    assert board1[pid1]["board_total"] == 10.0
    assert board1[pid2]["board_total"] == 10.0
