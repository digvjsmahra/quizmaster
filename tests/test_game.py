import pytest
from bundle_loader import BundleQuestion
from game import Game


def _q(id, board, category, value):
    return BundleQuestion(
        id=id, board=board, category=category, value=value,
        question=f"Q for {id}", answer=f"A for {id}", media=[],
    )


def make_game(boards=None) -> Game:
    if boards is None:
        boards = {
            "1": [
                _q("1:History:10", "1", "History", 10),
                _q("1:History:20", "1", "History", 20),
                _q("1:Science:10", "1", "Science", 10),
            ],
            "2": [
                _q("2:Movies:10", "2", "Movies", 10),
            ],
        }
    return Game(questions=boards)


# ------------------------------------------------------------------
# construction / load_questions
# ------------------------------------------------------------------

def test_game_constructs_empty_with_no_args():
    g = Game()
    assert g.questions == {}
    assert g._boards == []
    assert g._all_questions == {}
    assert g.media_dir is None


def test_load_questions_populates_after_construction():
    g = Game()
    boards = {"1": [_q("1:History:10", "1", "History", 10)]}
    g.load_questions(boards)
    assert g.questions == boards
    assert g._boards == ["1"]
    assert "1:History:10" in g._all_questions


def test_start_quiz_raises_when_no_questions_loaded():
    g = Game()
    with pytest.raises(ValueError):
        g.start_quiz()
    assert g.phase == "lobby"


def test_start_quiz_succeeds_after_load_questions():
    g = Game()
    g.load_questions({"1": [_q("1:History:10", "1", "History", 10)]})
    g.start_quiz()
    assert g.phase == "live"


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


def test_start_quiz_excludes_virtual_from_snapshot():
    g = make_game()
    pid1, _ = g.player_join("Ankur")
    g.start_quiz()
    pid_virtual = g.roster_add("HostAdded")
    roster_real = [pid for pid in g.roster if not g.players[pid].virtual]
    assert pid1 in roster_real
    assert pid_virtual not in roster_real


def test_start_quiz_is_idempotent():
    g = make_game()
    pid1, _ = g.player_join("Ankur")
    g.start_quiz()
    roster_first = list(g.roster)
    g.player_join("Late")
    g.start_quiz()  # second call must not change roster
    assert g.roster == roster_first


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
    assert queue[0]["delta_ms"] == 0
    assert queue[1]["delta_ms"] >= 0


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


def test_roster_add_creates_virtual_entry():
    g = make_game()
    g.start_quiz()
    pid = g.roster_add("Priya")
    assert g.players[pid].virtual is True


def test_roster_add_is_independent_of_buzz_identity():
    g = make_game()
    pid_buzz, _ = g.player_join("Ankur")
    g.start_quiz()  # pid_buzz auto-snapshotted into roster
    pid_virtual = g.roster_add("Ankur")  # separate virtual entry, same name allowed
    assert pid_buzz in g.roster
    assert pid_virtual in g.roster
    assert pid_virtual != pid_buzz
    assert g.players[pid_virtual].virtual is True
    assert g.players[pid_buzz].virtual is False


def test_active_players_excludes_virtual():
    g = make_game()
    pid_real, _ = g.player_join("Ankur")
    g.start_quiz()
    pid_virtual = g.roster_add("Ankur")  # virtual scorecard entry
    active_ids = {p["player_id"] for p in g.get_active_players()}
    assert pid_real in active_ids
    assert pid_virtual not in active_ids


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
# question_reveal / answer_reveal / question_cancel
# ------------------------------------------------------------------

def test_question_reveal_sets_revealed_on_unplayed_question():
    g, _, _ = _started_game()
    g.question_reveal("1:History:10")
    assert g.live_question == {
        "question_id": "1:History:10",
        "status": "revealed",
        "reviewing": False,
    }


def test_question_reveal_on_closed_question_reopens_as_reviewing():
    g, pid1, _ = _started_game()
    g.question_submit("1:History:10", {pid1: 10.0})
    g.question_reveal("1:History:10")
    assert g.live_question == {
        "question_id": "1:History:10",
        "status": "answer_shown",
        "reviewing": True,
    }


def test_question_reveal_rejects_unknown_question():
    g, _, _ = _started_game()
    with pytest.raises(ValueError):
        g.question_reveal("nonexistent")


def test_question_reveal_rejects_different_question_while_one_is_live():
    g, _, _ = _started_game()
    g.question_reveal("1:History:10")
    with pytest.raises(ValueError):
        g.question_reveal("1:History:20")
    assert g.live_question["question_id"] == "1:History:10"


def test_question_reveal_same_question_again_is_idempotent():
    g, _, _ = _started_game()
    g.question_reveal("1:History:10")
    g.question_reveal("1:History:10")  # must not raise
    assert g.live_question["question_id"] == "1:History:10"


def test_answer_reveal_moves_revealed_to_answer_shown():
    g, _, _ = _started_game()
    g.question_reveal("1:History:10")
    g.answer_reveal()
    assert g.live_question["status"] == "answer_shown"


def test_answer_reveal_rejects_when_nothing_live():
    g, _, _ = _started_game()
    with pytest.raises(ValueError):
        g.answer_reveal()


def test_answer_reveal_rejects_when_already_answer_shown():
    g, _, _ = _started_game()
    g.question_reveal("1:History:10")
    g.answer_reveal()
    with pytest.raises(ValueError):
        g.answer_reveal()


def test_question_cancel_clears_live_question_and_unlocks_queue():
    g, pid1, _ = _started_game()
    g.player_buzz(pid1)
    g.queue_freeze()
    g.question_reveal("1:History:10")
    g.question_cancel()
    assert g.live_question is None
    assert g.queue == []
    assert g.queue_locked is False


def test_question_cancel_rejects_when_nothing_live():
    g, _, _ = _started_game()
    with pytest.raises(ValueError):
        g.question_cancel()


def test_question_cancel_on_reopened_question_leaves_score_unchanged():
    g, pid1, _ = _started_game()
    g.question_submit("1:History:10", {pid1: 10.0})
    g.question_reveal("1:History:10")  # reopen
    g.question_cancel()
    assert g.live_question is None
    assert "1:History:10" in g.closed_questions
    assert g.scores[pid1]["1:History:10"] == 10.0


def test_question_submit_clears_matching_live_question():
    g, pid1, _ = _started_game()
    g.question_reveal("1:History:10")
    g.question_submit("1:History:10", {pid1: 10.0})
    assert g.live_question is None


def test_question_submit_leaves_unrelated_live_question_untouched():
    g, pid1, _ = _started_game()
    g.question_reveal("1:History:10")
    g.question_submit("1:Science:10", {pid1: 10.0})
    assert g.live_question is not None
    assert g.live_question["question_id"] == "1:History:10"


def test_question_submit_on_reopened_question_clears_live_question():
    g, pid1, _ = _started_game()
    g.question_submit("1:History:10", {pid1: 10.0})
    g.question_reveal("1:History:10")  # reopen for correction
    g.question_submit("1:History:10", {pid1: 5.0})
    assert g.live_question is None
    assert g.scores[pid1]["1:History:10"] == 5.0


def test_question_submit_always_clears_and_unlocks_queue():
    g, pid1, pid2 = _started_game()
    g.player_buzz(pid1)
    g.queue_freeze()
    g.question_submit("1:History:10", {pid2: 10.0})  # no reveal involved at all
    assert g.queue == []
    assert g.queue_locked is False


def test_get_live_question_payload_none_when_nothing_live():
    g, _, _ = _started_game()
    assert g.get_live_question_payload() == {"live_question": None}


def test_get_live_question_payload_shape_when_live():
    g, _, _ = _started_game()
    g.question_reveal("1:History:10")
    payload = g.get_live_question_payload()["live_question"]
    assert payload["question_id"] == "1:History:10"
    assert payload["board"] == "1"
    assert payload["category"] == "History"
    assert payload["value"] == 10
    assert payload["question"] == "Q for 1:History:10"
    assert payload["answer"] == "A for 1:History:10"
    assert payload["media"] == []
    assert payload["status"] == "revealed"
    assert payload["reviewing"] is False


# ------------------------------------------------------------------
# Cell state derivation
# ------------------------------------------------------------------

def test_cell_state_unplayed():
    g, _, _ = _started_game()
    cell = g._cell_state("1:History:10")
    assert cell["state"] == "unplayed"
    assert cell["value"] == 10


def test_cell_state_includes_question_answer_media():
    # Host-only payload (state:scores never reaches a player socket) —
    # this is what powers the pre-Start Q&A peek in the control center.
    g, _, _ = _started_game()
    cell = g._cell_state("1:History:10")
    assert cell["question"] == "Q for 1:History:10"
    assert cell["answer"] == "A for 1:History:10"
    assert cell["media"] == []


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
