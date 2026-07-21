import io
import os
import zipfile

import openpyxl
import pytest

from bundle_loader import extract_media, parse_bundle

DEFAULT_COLUMNS = ["board", "category", "value", "question", "answer", "media"]


def make_bundle(rows, *, media_files=None, columns=None, include_xlsx=True, extra_sheets=None):
    """Builds an in-memory .zip bundle from row dicts keyed by column name.

    Missing keys in a row dict become a blank cell. `media_files` is a dict
    of filename -> bytes written under media/. `extra_sheets` is a dict of
    sheet name -> list of raw row lists, appended after the main sheet.
    """
    columns = columns or DEFAULT_COLUMNS
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(columns)
    for row in rows:
        ws.append([row.get(col) for col in columns])

    for name, sheet_rows in (extra_sheets or {}).items():
        extra_ws = wb.create_sheet(name)
        for r in sheet_rows:
            extra_ws.append(r)

    xlsx_buf = io.BytesIO()
    wb.save(xlsx_buf)

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        if include_xlsx:
            zf.writestr("quiz.xlsx", xlsx_buf.getvalue())
        for filename, content in (media_files or {}).items():
            zf.writestr(f"media/{filename}", content)
    zip_buf.seek(0)
    return zip_buf


# ------------------------------------------------------------------
# happy path
# ------------------------------------------------------------------

def test_valid_multi_board_bundle():
    rows = [
        {"board": "1", "category": "History", "value": 10, "question": "Q1", "answer": "A1"},
        {"board": "1", "category": "History", "value": 20, "question": "Q2", "answer": "A2"},
        {"board": "2", "category": "Movies", "value": 10, "question": "Q3", "answer": "A3"},
    ]
    result = parse_bundle(make_bundle(rows))

    assert result.errors == []
    assert result.warnings == []
    assert list(result.boards.keys()) == ["1", "2"]
    assert [q.id for q in result.boards["1"]] == ["1:History:10", "1:History:20"]
    assert [q.id for q in result.boards["2"]] == ["2:Movies:10"]
    q = result.boards["1"][0]
    assert q.value == 10 and isinstance(q.value, int)
    assert q.question == "Q1" and q.answer == "A1" and q.media == []


def test_works_without_media_column_at_all():
    rows = [{"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A"}]
    bundle = make_bundle(rows, columns=["board", "category", "value", "question", "answer"])
    result = parse_bundle(bundle)
    assert result.errors == []
    assert result.boards["1"][0].media == []


def test_skips_fully_blank_rows():
    rows = [
        {"board": "1", "category": "History", "value": 10, "question": "Q1", "answer": "A1"},
        {},
        {"board": "1", "category": "History", "value": 20, "question": "Q2", "answer": "A2"},
    ]
    result = parse_bundle(make_bundle(rows))
    assert result.errors == []
    assert len(result.boards["1"]) == 2


def test_numeric_board_normalizes_same_as_text():
    numeric = parse_bundle(make_bundle(
        [{"board": 1, "category": "History", "value": 10, "question": "Q", "answer": "A"}]
    ))
    text = parse_bundle(make_bundle(
        [{"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A"}]
    ))
    assert list(numeric.boards.keys()) == list(text.boards.keys()) == ["1"]


# ------------------------------------------------------------------
# value validation
# ------------------------------------------------------------------

@pytest.mark.parametrize("raw_value", [10, 10.0, "10", " 10 "])
def test_value_accepts_numeric_and_text_whole_numbers(raw_value):
    rows = [{"board": "1", "category": "History", "value": raw_value, "question": "Q", "answer": "A"}]
    result = parse_bundle(make_bundle(rows))
    assert result.errors == []
    assert result.boards["1"][0].value == 10
    assert isinstance(result.boards["1"][0].value, int)


@pytest.mark.parametrize("raw_value", [10.5, "abc", "", None, True])
def test_value_rejects_invalid(raw_value):
    row = {"board": "1", "category": "History", "question": "Q", "answer": "A"}
    if raw_value is not None:
        row["value"] = raw_value
    result = parse_bundle(make_bundle([row]))
    assert result.boards is None
    assert result.errors


@pytest.mark.parametrize("raw_value", [0, -5])
def test_value_rejects_non_positive(raw_value):
    rows = [{"board": "1", "category": "History", "value": raw_value, "question": "Q", "answer": "A"}]
    result = parse_bundle(make_bundle(rows))
    assert result.boards is None
    assert any("positive" in e.message for e in result.errors)


# ------------------------------------------------------------------
# required fields
# ------------------------------------------------------------------

@pytest.mark.parametrize("field", ["board", "category", "answer"])
def test_rejects_missing_required_text_field(field):
    row = {"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A"}
    row[field] = ""
    result = parse_bundle(make_bundle([row]))
    assert result.boards is None
    assert any(field in e.message and e.row == 2 for e in result.errors)


def test_rejects_empty_question_without_media():
    rows = [{"board": "1", "category": "History", "value": 10, "question": "", "answer": "A"}]
    result = parse_bundle(make_bundle(rows))
    assert result.boards is None
    assert any("question or media" in e.message for e in result.errors)


def test_allows_empty_question_with_media():
    rows = [
        {"board": "1", "category": "History", "value": 10, "question": "", "answer": "A", "media": "pic.jpg"}
    ]
    bundle = make_bundle(rows, media_files={"pic.jpg": b"fake-image-bytes"})
    result = parse_bundle(bundle)
    assert result.errors == []
    assert result.boards["1"][0].media == ["pic.jpg"]


# ------------------------------------------------------------------
# media validation
# ------------------------------------------------------------------

def test_rejects_unsupported_media_extension():
    rows = [
        {"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A", "media": "clip.mp4"}
    ]
    bundle = make_bundle(rows, media_files={"clip.mp4": b"fake"})
    result = parse_bundle(bundle)
    assert result.boards is None
    assert any("extension" in e.message for e in result.errors)


def test_rejects_missing_referenced_media_file():
    rows = [
        {"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A", "media": "missing.jpg"}
    ]
    result = parse_bundle(make_bundle(rows))
    assert result.boards is None
    assert any("not found" in e.message for e in result.errors)


def test_media_placeholder_na_is_treated_literally_not_as_blank():
    rows = [{"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A", "media": "NA"}]
    result = parse_bundle(make_bundle(rows))
    assert result.boards is None
    assert result.errors


def test_unreferenced_media_file_is_a_warning_not_error():
    rows = [{"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A"}]
    bundle = make_bundle(rows, media_files={"orphan.png": b"fake"})
    result = parse_bundle(bundle)
    assert result.errors == []
    assert result.boards is not None
    assert any("orphan.png" in w for w in result.warnings)


def test_media_names_reflects_all_files_in_media_folder():
    rows = [
        {"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A", "media": "pic.jpg"}
    ]
    bundle = make_bundle(rows, media_files={"pic.jpg": b"x", "orphan.png": b"y"})
    result = parse_bundle(bundle)
    assert result.media_names == {"pic.jpg", "orphan.png"}


# ------------------------------------------------------------------
# duplicates
# ------------------------------------------------------------------

def test_rejects_duplicate_question_id():
    rows = [
        {"board": "1", "category": "History", "value": 10, "question": "Q1", "answer": "A1"},
        {"board": "1", "category": "History", "value": 10, "question": "Q1 dup", "answer": "A1 dup"},
    ]
    result = parse_bundle(make_bundle(rows))
    assert result.boards is None
    assert any("duplicate" in e.message and e.row == 3 for e in result.errors)


# ------------------------------------------------------------------
# bundle-level (structural) errors
# ------------------------------------------------------------------

def test_rejects_non_zip_input():
    result = parse_bundle(io.BytesIO(b"this is not a zip file"))
    assert result.boards is None
    assert len(result.errors) == 1
    assert result.errors[0].row is None


def test_rejects_bundle_missing_quiz_xlsx():
    rows = [{"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A"}]
    bundle = make_bundle(rows, include_xlsx=False)
    result = parse_bundle(bundle)
    assert result.boards is None
    assert any("quiz.xlsx" in e.message and e.row is None for e in result.errors)


def test_rejects_missing_required_header_column():
    rows = [{"board": "1", "category": "History", "value": 10}]
    bundle = make_bundle(rows, columns=["board", "category", "value"])
    result = parse_bundle(bundle)
    assert result.boards is None
    assert any("answer" in e.message and e.row is None for e in result.errors)


def test_only_first_sheet_is_read():
    rows = [{"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A"}]
    bundle = make_bundle(
        rows,
        extra_sheets={"Extra": [["this", "sheet", "is", "garbage"], [1, 2, 3]]},
    )
    result = parse_bundle(bundle)
    assert result.errors == []
    assert list(result.boards.keys()) == ["1"]


# ------------------------------------------------------------------
# extract_media
# ------------------------------------------------------------------

def test_extract_media_writes_referenced_and_unreferenced_files(tmp_path):
    rows = [
        {"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A", "media": "pic.jpg"}
    ]
    bundle = make_bundle(rows, media_files={"pic.jpg": b"pic-bytes", "orphan.png": b"orphan-bytes"})

    extract_media(bundle, str(tmp_path))

    assert (tmp_path / "pic.jpg").read_bytes() == b"pic-bytes"
    assert (tmp_path / "orphan.png").read_bytes() == b"orphan-bytes"


def test_extract_media_noop_when_no_media_folder(tmp_path):
    rows = [{"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A"}]
    bundle = make_bundle(rows)

    extract_media(bundle, str(tmp_path))

    assert os.listdir(tmp_path) == []


def test_extract_media_works_after_parse_bundle_already_consumed_the_stream(tmp_path):
    rows = [
        {"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A", "media": "pic.jpg"}
    ]
    bundle = make_bundle(rows, media_files={"pic.jpg": b"pic-bytes"})

    result = parse_bundle(bundle)
    assert result.errors == []

    # bundle's read position is now wherever parse_bundle left it —
    # extract_media must seek(0) itself, not assume the caller does.
    extract_media(bundle, str(tmp_path))

    assert (tmp_path / "pic.jpg").read_bytes() == b"pic-bytes"
