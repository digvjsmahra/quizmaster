import builtins

import pytest

from quiz_loader import load_quiz


def write_csv(tmp_path, content, name="quiz.csv"):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return str(path)


# ------------------------------------------------------------------
# happy path
# ------------------------------------------------------------------

def test_load_quiz_valid_multi_board(tmp_path):
    path = write_csv(
        tmp_path,
        "Board,Category,Value\n"
        "1,History,10\n"
        "1,History,20\n"
        "2,Movies,10\n",
    )
    boards = load_quiz(path)

    assert list(boards.keys()) == ["1", "2"]
    assert [q.id for q in boards["1"]] == ["1:History:10", "1:History:20"]
    assert boards["2"][0].id == "2:Movies:10"
    q = boards["1"][0]
    assert q.board == "1" and q.category == "History" and q.value == 10
    assert isinstance(q.value, int)


def test_load_quiz_case_insensitive_columns(tmp_path):
    path = write_csv(tmp_path, "  BOARD , category ,VALUE\n1,History,10\n")
    boards = load_quiz(path)
    assert boards["1"][0].category == "History"


def test_load_quiz_board_stays_string_in_file_order(tmp_path):
    # Board is never coerced to int and never sorted numerically — display
    # order (and, eventually, Prev/Next navigation) follows file row order.
    path = write_csv(tmp_path, "Board,Category,Value\n10,History,10\n2,Movies,10\n")
    boards = load_quiz(path)
    assert list(boards.keys()) == ["10", "2"]
    assert all(isinstance(b, str) for b in boards.keys())


# ------------------------------------------------------------------
# structural errors
# ------------------------------------------------------------------

def test_load_quiz_missing_required_column(tmp_path):
    path = write_csv(tmp_path, "Board,Category\n1,History\n")
    with pytest.raises(SystemExit) as exc:
        load_quiz(path)
    assert "missing required columns" in str(exc.value)
    assert "value" in str(exc.value)


def test_load_quiz_empty_file(tmp_path):
    path = write_csv(tmp_path, "")
    with pytest.raises(SystemExit) as exc:
        load_quiz(path)
    assert "empty or unreadable" in str(exc.value)


def test_load_quiz_no_data_rows(tmp_path):
    path = write_csv(tmp_path, "Board,Category,Value\n")
    with pytest.raises(SystemExit) as exc:
        load_quiz(path)
    assert "contains no data rows" in str(exc.value)


def test_load_quiz_file_not_found(tmp_path):
    with pytest.raises(SystemExit) as exc:
        load_quiz(str(tmp_path / "does-not-exist.csv"))
    assert "Quiz file not found" in str(exc.value)


def test_load_quiz_permission_error(tmp_path, monkeypatch):
    path = write_csv(tmp_path, "Board,Category,Value\n1,History,10\n")
    real_open = builtins.open

    def fake_open(file, *args, **kwargs):
        if str(file) == path:
            raise PermissionError()
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)
    with pytest.raises(SystemExit) as exc:
        load_quiz(path)
    assert "Cannot read quiz file" in str(exc.value)


# ------------------------------------------------------------------
# per-row errors
# ------------------------------------------------------------------

@pytest.mark.parametrize("row", ["1,,10", ",History,10", "1,History,"])
def test_load_quiz_empty_field(tmp_path, row):
    path = write_csv(tmp_path, f"Board,Category,Value\n{row}\n")
    with pytest.raises(SystemExit) as exc:
        load_quiz(path)
    assert "empty field" in str(exc.value)
    assert "line 2" in str(exc.value)


def test_load_quiz_non_integer_value(tmp_path):
    path = write_csv(tmp_path, "Board,Category,Value\n1,History,abc\n")
    with pytest.raises(SystemExit) as exc:
        load_quiz(path)
    assert "value must be a positive integer" in str(exc.value)


@pytest.mark.parametrize("value", ["True", "False", "10.5"])
def test_load_quiz_value_rejects_boolean_and_decimal_looking_text(tmp_path, value):
    # CSV has no typed cells (unlike bundle_loader's xlsx path) — a cell is
    # always plain text, so "True"/"False" fail int() the same as any other
    # non-numeric string. No bool-vs-int leak-through is possible here.
    path = write_csv(tmp_path, f"Board,Category,Value\n1,History,{value}\n")
    with pytest.raises(SystemExit) as exc:
        load_quiz(path)
    assert "value must be a positive integer" in str(exc.value)


@pytest.mark.parametrize("value", ["0", "-5"])
def test_load_quiz_non_positive_value(tmp_path, value):
    path = write_csv(tmp_path, f"Board,Category,Value\n1,History,{value}\n")
    with pytest.raises(SystemExit) as exc:
        load_quiz(path)
    assert "value must be positive" in str(exc.value)


def test_load_quiz_duplicate_question_id(tmp_path):
    path = write_csv(
        tmp_path,
        "Board,Category,Value\n1,History,10\n1,History,10\n",
    )
    with pytest.raises(SystemExit) as exc:
        load_quiz(path)
    assert "duplicate question_id" in str(exc.value)
    assert "line 3" in str(exc.value)
