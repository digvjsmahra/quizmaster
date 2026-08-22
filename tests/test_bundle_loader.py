import io
import os
import zipfile

import openpyxl
import pytest

from bundle_loader import extract_media, parse_bundle

DEFAULT_COLUMNS = ["board", "category", "value", "question", "answer", "question_media", "answer_media"]

# Minimal magic-byte-prefixed fixtures for content-sniffing tests — just
# enough for sniff_image_format() to recognize them, not full valid images
# (parse_bundle never decodes media, only serves raw bytes to the browser).
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
JPEG_BYTES = b"\xff\xd8\xff" + b"\x00" * 8


def make_bundle(
    rows, *, media_files=None, columns=None, include_xlsx=True, extra_sheets=None,
    xlsx_name="quiz.xlsx", media_folder="media", wrapper_folder=None, include_mac_junk=False,
):
    """Builds an in-memory .zip bundle from row dicts keyed by column name.

    Missing keys in a row dict become a blank cell. `media_files` is a dict
    of filename -> bytes written under media/. `extra_sheets` is a dict of
    sheet name -> list of raw row lists, appended after the main sheet.
    `xlsx_name`/`media_folder` let tests exercise casing variations.
    `wrapper_folder` nests every entry one level under that folder name
    (the shape produced by macOS Finder's "Compress" on a folder);
    `include_mac_junk` additionally writes a .DS_Store and a __MACOSX/
    AppleDouble entry, matching what Finder actually produces.
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

    def _path(p):
        return f"{wrapper_folder}/{p}" if wrapper_folder else p

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        if include_xlsx:
            zf.writestr(_path(xlsx_name), xlsx_buf.getvalue())
        for filename, content in (media_files or {}).items():
            zf.writestr(_path(f"{media_folder}/{filename}"), content)
        if include_mac_junk:
            zf.writestr(_path(".DS_Store"), b"junk")
            zf.writestr("__MACOSX/._quiz.xlsx", b"junk")
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
    assert q.question == "Q1" and q.answer == "A1" and q.question_media == []


def test_works_without_media_column_at_all():
    rows = [{"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A"}]
    bundle = make_bundle(rows, columns=["board", "category", "value", "question", "answer"])
    result = parse_bundle(bundle)
    assert result.errors == []
    assert result.boards["1"][0].question_media == []


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
    assert any("question or question_media" in e.message for e in result.errors)


def test_allows_empty_question_with_media():
    rows = [
        {"board": "1", "category": "History", "value": 10, "question": "", "answer": "A", "question_media": "pic.jpg"}
    ]
    bundle = make_bundle(rows, media_files={"pic.jpg": PNG_BYTES})
    result = parse_bundle(bundle)
    assert result.errors == []
    assert result.boards["1"][0].question_media == ["pic.jpg"]


# ------------------------------------------------------------------
# media validation
# ------------------------------------------------------------------

def test_rejects_non_image_content_at_matched_filename():
    rows = [
        {"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A", "question_media": "clip.mp4"}
    ]
    bundle = make_bundle(rows, media_files={"clip.mp4": b"not an image"})
    result = parse_bundle(bundle)
    assert result.boards is None
    assert any("isn't a supported image format" in e.message for e in result.errors)


@pytest.mark.parametrize(
    "cell_ref, actual_filename",
    [
        ("poster.png", "poster"),      # cell has extension, file doesn't
        ("poster", "poster.png"),      # cell has no extension, file does
        ("poster", "poster"),          # neither has one
        ("poster.png", "poster.jpg"),  # claimed extension disagrees with the real (sniffed) format — still fine
    ],
)
def test_media_matches_by_base_name_ignoring_extension(cell_ref, actual_filename):
    rows = [
        {"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A", "question_media": cell_ref}
    ]
    bundle = make_bundle(rows, media_files={actual_filename: JPEG_BYTES if actual_filename.endswith(".jpg") else PNG_BYTES})
    result = parse_bundle(bundle)
    assert result.errors == []
    assert result.boards["1"][0].question_media == [actual_filename]


def test_rejects_media_base_name_collision():
    rows = [
        {"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A", "question_media": "poster"}
    ]
    bundle = make_bundle(rows, media_files={"poster.png": PNG_BYTES, "poster.jpg": JPEG_BYTES})
    result = parse_bundle(bundle)
    assert result.boards is None
    assert any(
        "multiple media files matching" in e.message and "poster.png" in e.message
        and "poster.jpg" in e.message and e.row is None
        for e in result.errors
    )
    assert any("matches more than one file" in e.message and e.row == 2 for e in result.errors)


def test_rejects_missing_referenced_media_file():
    rows = [
        {"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A", "question_media": "missing.jpg"}
    ]
    result = parse_bundle(make_bundle(rows))
    assert result.boards is None
    assert any("not found" in e.message for e in result.errors)


def test_media_placeholder_na_is_treated_literally_not_as_blank():
    rows = [{"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A", "question_media": "NA"}]
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
        {"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A", "question_media": "pic.jpg"}
    ]
    bundle = make_bundle(rows, media_files={"pic.jpg": b"x", "orphan.png": b"y"})
    result = parse_bundle(bundle)
    assert result.media_names == {"pic.jpg", "orphan.png"}


# ------------------------------------------------------------------
# answer_media validation (independent column, same rules as question_media)
# ------------------------------------------------------------------

def test_answer_media_is_independent_of_question_media():
    rows = [
        {
            "board": "1", "category": "History", "value": 10,
            "question": "Q", "answer": "A",
            "question_media": "q.jpg", "answer_media": "a.jpg",
        }
    ]
    bundle = make_bundle(rows, media_files={"q.jpg": JPEG_BYTES, "a.jpg": JPEG_BYTES})
    result = parse_bundle(bundle)
    assert result.errors == []
    q = result.boards["1"][0]
    assert q.question_media == ["q.jpg"]
    assert q.answer_media == ["a.jpg"]


def test_answer_media_defaults_to_empty_list():
    rows = [{"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A"}]
    result = parse_bundle(make_bundle(rows))
    assert result.boards["1"][0].answer_media == []


def test_answer_media_does_not_satisfy_question_requirement():
    rows = [
        {
            "board": "1", "category": "History", "value": 10,
            "question": "", "answer": "A", "answer_media": "a.jpg",
        }
    ]
    bundle = make_bundle(rows, media_files={"a.jpg": JPEG_BYTES})
    result = parse_bundle(bundle)
    assert result.boards is None
    assert any("question or question_media" in e.message for e in result.errors)


def test_answer_media_rejects_non_image_content():
    rows = [
        {"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A", "answer_media": "clip.mp4"}
    ]
    bundle = make_bundle(rows, media_files={"clip.mp4": b"not an image"})
    result = parse_bundle(bundle)
    assert result.boards is None
    assert any("isn't a supported image format" in e.message for e in result.errors)


def test_answer_media_rejects_missing_referenced_file():
    rows = [
        {
            "board": "1", "category": "History", "value": 10,
            "question": "Q", "answer": "A", "answer_media": "missing.jpg",
        }
    ]
    result = parse_bundle(make_bundle(rows))
    assert result.boards is None
    assert any("not found" in e.message for e in result.errors)


def test_answer_media_counts_toward_referenced_media_for_warnings():
    rows = [
        {"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A", "answer_media": "a.jpg"}
    ]
    bundle = make_bundle(rows, media_files={"a.jpg": JPEG_BYTES})
    result = parse_bundle(bundle)
    assert result.errors == []
    assert result.warnings == []


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


def test_rejects_bundle_missing_xlsx():
    rows = [{"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A"}]
    bundle = make_bundle(rows, include_xlsx=False)
    result = parse_bundle(bundle)
    assert result.boards is None
    assert any(".xlsx" in e.message and e.row is None for e in result.errors)


def test_rejects_missing_required_header_column():
    rows = [{"board": "1", "category": "History", "value": 10}]
    bundle = make_bundle(rows, columns=["board", "category", "value"])
    result = parse_bundle(bundle)
    assert result.boards is None
    assert any("answer" in e.message and e.row is None for e in result.errors)


def test_missing_column_still_surfaces_row_level_issues_in_same_pass():
    # A required column is missing AND a row has an unrelated media
    # problem — both should come back together, not just the first.
    columns = ["board", "category", "question", "answer", "question_media"]
    rows = [
        {"board": "1", "category": "Cap", "question": "Q", "answer": "A", "question_media": "poster.png"},
    ]
    bundle = make_bundle(rows, columns=columns)  # no media/ folder at all -> "not found"
    result = parse_bundle(bundle)
    assert result.boards is None
    messages = [e.message for e in result.errors]
    assert any("missing required column(s): value" in m for m in messages)
    assert any("not found" in m for m in messages)
    # No flood of a "board is required"/"value is required" per-row
    # message repeating what the bundle-level message already said.
    assert not any("value is required" in m for m in messages)


def test_near_miss_column_header_silently_corrected():
    # "question media" (space instead of underscore) is close enough that
    # it's silently treated as question_media — no error, no mention of it.
    columns = ["board", "category", "value", "question", "answer", "question media"]
    rows = [{
        "board": "1", "category": "History", "value": 10,
        "question": "Q", "answer": "A", "question media": "pic.jpg",
    }]
    bundle = make_bundle(rows, columns=columns, media_files={"pic.jpg": PNG_BYTES})
    result = parse_bundle(bundle)
    assert result.errors == []
    assert result.boards["1"][0].question_media == ["pic.jpg"]


def test_semantic_column_mismatch_lists_columns_found_without_guessing():
    # "points" has no character-level similarity to "value" — no silent
    # correction is attempted; the QM sees what was actually found instead.
    columns = ["board", "category", "points", "question", "answer"]
    rows = [{"board": "1", "category": "History", "points": 10, "question": "Q", "answer": "A"}]
    bundle = make_bundle(rows, columns=columns)
    result = parse_bundle(bundle)
    assert result.boards is None
    message = next(e.message for e in result.errors)
    assert "value" in message
    assert "did you mean" not in message.lower()
    assert "points" in message  # listed among "columns found in your file"


@pytest.mark.parametrize("xlsx_name", ["Quiz.xlsx", "QUIZ.XLSX", "quiz.XLSX"])
def test_quiz_xlsx_name_matched_case_insensitively(xlsx_name):
    rows = [{"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A"}]
    bundle = make_bundle(rows, xlsx_name=xlsx_name)
    result = parse_bundle(bundle)
    assert result.errors == []
    assert result.boards["1"][0].id == "1:History:10"


def test_accepts_any_single_xlsx_filename():
    rows = [{"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A"}]
    bundle = make_bundle(rows, xlsx_name="My Trivia Night.xlsx")
    result = parse_bundle(bundle)
    assert result.errors == []
    assert result.boards["1"][0].id == "1:History:10"


def test_rejects_multiple_xlsx_files():
    rows = [{"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A"}]
    bundle = make_bundle(rows, xlsx_name="quiz.xlsx")
    # Add a second .xlsx entry directly, since make_bundle only writes one.
    buf = io.BytesIO(bundle.getvalue())
    out = io.BytesIO()
    with zipfile.ZipFile(buf) as src, zipfile.ZipFile(out, "w") as dst:
        for name in src.namelist():
            dst.writestr(name, src.read(name))
        dst.writestr("quiz (copy).xlsx", src.read("quiz.xlsx"))
    out.seek(0)
    result = parse_bundle(out)
    assert result.boards is None
    assert any(
        "multiple Excel files" in e.message and "quiz.xlsx" in e.message and "quiz (copy).xlsx" in e.message
        for e in result.errors
    )


@pytest.mark.parametrize("media_folder", ["Media", "MEDIA"])
def test_media_folder_matched_case_insensitively(media_folder):
    rows = [
        {"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A", "question_media": "pic.jpg"}
    ]
    bundle = make_bundle(rows, media_files={"pic.jpg": PNG_BYTES}, media_folder=media_folder)
    result = parse_bundle(bundle)
    assert result.errors == []
    assert result.boards["1"][0].question_media == ["pic.jpg"]


def test_tolerates_finder_wrapper_folder_and_mac_junk():
    rows = [
        {"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A", "question_media": "pic.jpg"}
    ]
    bundle = make_bundle(
        rows, media_files={"pic.jpg": PNG_BYTES},
        wrapper_folder="QuizMaster 3000", include_mac_junk=True,
    )
    result = parse_bundle(bundle)
    assert result.errors == []
    assert result.boards["1"][0].question_media == ["pic.jpg"]


def test_wrapper_folder_without_media_still_works():
    rows = [{"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A"}]
    bundle = make_bundle(rows, wrapper_folder="MyQuiz")
    result = parse_bundle(bundle)
    assert result.errors == []


def test_mac_junk_does_not_produce_spurious_warning():
    rows = [
        {"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A", "question_media": "pic.jpg"}
    ]
    bundle = make_bundle(
        rows, media_files={"pic.jpg": PNG_BYTES},
        wrapper_folder="Quiz", include_mac_junk=True,
    )
    result = parse_bundle(bundle)
    assert result.warnings == []


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
        {"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A", "question_media": "pic.jpg"}
    ]
    bundle = make_bundle(rows, media_files={"pic.jpg": b"pic-bytes", "orphan.png": b"orphan-bytes"})

    extract_media(bundle, str(tmp_path))

    assert (tmp_path / "pic.jpg").read_bytes() == b"pic-bytes"
    assert (tmp_path / "orphan.png").read_bytes() == b"orphan-bytes"


def test_extract_media_matches_media_folder_case_insensitively(tmp_path):
    rows = [
        {"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A", "question_media": "pic.jpg"}
    ]
    bundle = make_bundle(rows, media_files={"pic.jpg": b"pic-bytes"}, media_folder="Media")

    extract_media(bundle, str(tmp_path))

    assert (tmp_path / "pic.jpg").read_bytes() == b"pic-bytes"


def test_extract_media_noop_when_no_media_folder(tmp_path):
    rows = [{"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A"}]
    bundle = make_bundle(rows)

    extract_media(bundle, str(tmp_path))

    assert os.listdir(tmp_path) == []


def test_extract_media_tolerates_finder_wrapper_folder(tmp_path):
    rows = [
        {"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A", "question_media": "pic.jpg"}
    ]
    bundle = make_bundle(
        rows, media_files={"pic.jpg": b"pic-bytes"},
        wrapper_folder="QuizMaster 3000", include_mac_junk=True,
    )

    extract_media(bundle, str(tmp_path))

    assert (tmp_path / "pic.jpg").read_bytes() == b"pic-bytes"
    assert not (tmp_path / ".DS_Store").exists()
    assert os.listdir(tmp_path) == ["pic.jpg"]


def test_extract_media_works_after_parse_bundle_already_consumed_the_stream(tmp_path):
    rows = [
        {"board": "1", "category": "History", "value": 10, "question": "Q", "answer": "A", "question_media": "pic.jpg"}
    ]
    bundle = make_bundle(rows, media_files={"pic.jpg": PNG_BYTES})

    result = parse_bundle(bundle)
    assert result.errors == []

    # bundle's read position is now wherever parse_bundle left it —
    # extract_media must seek(0) itself, not assume the caller does.
    extract_media(bundle, str(tmp_path))

    assert (tmp_path / "pic.jpg").read_bytes() == PNG_BYTES
