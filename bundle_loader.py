"""Parses and validates a V3 quiz bundle: a .zip containing quiz.xlsx (+ optional media/).

See SPEC V3.md §3 for the file format contract this enforces. parse_bundle()
is pure/side-effect-free (no filesystem writes); extract_media() is the one
function here that writes to disk, used by A2's upload route to persist a
room's media into its own temp dir.
"""

import os
import zipfile
from dataclasses import dataclass
from io import BytesIO

import openpyxl

REQUIRED_COLUMNS = {"board", "category", "value", "question", "answer"}
ALLOWED_MEDIA_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}


@dataclass
class BundleQuestion:
    id: str
    board: str
    category: str
    value: int
    question: str
    answer: str
    media: list[str]


@dataclass
class ValidationError:
    row: int | None  # spreadsheet row number (header = 1); None = bundle-level
    message: str


@dataclass
class BundleParseResult:
    boards: dict[str, list[BundleQuestion]] | None  # None whenever errors is non-empty
    errors: list[ValidationError]
    warnings: list[str]
    media_names: set[str]


def _cell_to_str(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _parse_value(value):
    """Returns (int, None) on success, or (None, error_message) on failure."""
    if isinstance(value, bool):
        return None, f"value must be a positive integer, got {value!r}"
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float):
        if not value.is_integer():
            return None, f"value must be a positive integer, got {value!r}"
        parsed = int(value)
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None, "value is required"
        try:
            parsed = int(stripped)
        except ValueError:
            try:
                as_float = float(stripped)
            except ValueError:
                return None, f"value must be a positive integer, got {stripped!r}"
            if not as_float.is_integer():
                return None, f"value must be a positive integer, got {stripped!r}"
            parsed = int(as_float)
    elif value is None:
        return None, "value is required"
    else:
        return None, f"value must be a positive integer, got {value!r}"

    if parsed <= 0:
        return None, f"value must be positive, got {parsed}"
    return parsed, None


def _row_cell(row, header, key):
    idx = header.get(key)
    if idx is None or idx >= len(row):
        return None
    return row[idx]


def parse_bundle(fileobj) -> BundleParseResult:
    try:
        zf = zipfile.ZipFile(fileobj)
    except zipfile.BadZipFile:
        return BundleParseResult(None, [ValidationError(None, "not a valid .zip file")], [], set())

    with zf:
        if "quiz.xlsx" not in zf.namelist():
            return BundleParseResult(None, [ValidationError(None, "bundle is missing quiz.xlsx")], [], set())

        try:
            workbook = openpyxl.load_workbook(BytesIO(zf.read("quiz.xlsx")), data_only=True, read_only=True)
        except Exception:
            # openpyxl can raise a range of exception types for malformed
            # xlsx content — all of them mean "reject this upload".
            return BundleParseResult(None, [ValidationError(None, "quiz.xlsx is not a valid Excel file")], [], set())

        media_names = {
            name.rsplit("/", 1)[-1]
            for name in zf.namelist()
            if name.startswith("media/") and not name.endswith("/")
        }

        sheet = workbook.worksheets[0]
        rows_iter = sheet.iter_rows(values_only=True)
        try:
            header_row = next(rows_iter)
        except StopIteration:
            return BundleParseResult(
                None, [ValidationError(None, "quiz.xlsx has no header row")], [], media_names
            )

        header = {}
        for idx, cell in enumerate(header_row):
            if cell is None:
                continue
            key = str(cell).strip().lower()
            if key:
                header[key] = idx

        missing_columns = REQUIRED_COLUMNS - set(header.keys())
        if missing_columns:
            return BundleParseResult(
                None,
                [
                    ValidationError(
                        None,
                        f"quiz.xlsx is missing required column(s): {', '.join(sorted(missing_columns))}",
                    )
                ],
                [],
                media_names,
            )

        errors: list[ValidationError] = []
        boards: dict[str, list[BundleQuestion]] = {}
        seen_ids: set[tuple[str, str, int]] = set()
        referenced_media: set[str] = set()

        for row_idx, row in enumerate(rows_iter, start=2):
            if row is None or all(c is None for c in row):
                continue

            board = _cell_to_str(_row_cell(row, header, "board"))
            category = _cell_to_str(_row_cell(row, header, "category"))
            question = _cell_to_str(_row_cell(row, header, "question"))
            answer = _cell_to_str(_row_cell(row, header, "answer"))
            media_raw = _cell_to_str(_row_cell(row, header, "media"))

            row_errors = []

            if not board:
                row_errors.append("board is required")
            if not category:
                row_errors.append("category is required")
            if not answer:
                row_errors.append("answer is required")

            value, value_error = _parse_value(_row_cell(row, header, "value"))
            if value_error:
                row_errors.append(value_error)

            # A blank cell means no media. A non-blank placeholder (e.g. "NA",
            # "-") is treated literally and validated as a filename below, so
            # it fails with a "not found" error rather than silently ignored.
            media = [m.strip() for m in media_raw.split(",") if m.strip()] if media_raw else []

            if not question and not media:
                row_errors.append("question or media is required")

            for filename in media:
                ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
                if ext not in ALLOWED_MEDIA_EXTENSIONS:
                    row_errors.append(f"unsupported media extension: {filename!r}")
                elif filename not in media_names:
                    row_errors.append(f"{filename!r} was not found among the uploaded media files")
                else:
                    referenced_media.add(filename)

            question_key = None
            if board and category and value is not None:
                question_key = (board, category, value)
                if question_key in seen_ids:
                    row_errors.append(
                        f"this board/category/value combination ('{board} / {category} / {value}') "
                        "is used by more than one row — that's a duplicate question"
                    )
                else:
                    seen_ids.add(question_key)

            if row_errors:
                errors.extend(ValidationError(row_idx, msg) for msg in row_errors)
                continue

            boards.setdefault(board, []).append(
                BundleQuestion(
                    id=f"{board}:{category}:{value}",
                    board=board,
                    category=category,
                    value=value,
                    question=question,
                    answer=answer,
                    media=media,
                )
            )

        warnings = [
            f"media file {filename!r} is not referenced by any row"
            for filename in sorted(media_names - referenced_media)
        ]

        return BundleParseResult(
            boards=boards if not errors else None,
            errors=errors,
            warnings=warnings,
            media_names=media_names,
        )


def extract_media(fileobj, dest_dir: str) -> None:
    """Writes every file under media/ in the bundle to dest_dir (flat, by basename).

    Re-opens fileobj as a zip from the start — callers that already ran
    parse_bundle() on the same fileobj don't need to seek() first.
    """
    fileobj.seek(0)
    with zipfile.ZipFile(fileobj) as zf:
        for name in zf.namelist():
            if not name.startswith("media/") or name.endswith("/"):
                continue
            basename = name.rsplit("/", 1)[-1]
            with zf.open(name) as src:
                with open(os.path.join(dest_dir, basename), "wb") as dst:
                    dst.write(src.read())
