"""Parses and validates a quiz bundle: a .zip containing a single .xlsx file (+ optional media/).

See SPEC.md §6 for the file format contract this enforces. parse_bundle()
is pure/side-effect-free (no filesystem writes); extract_media() is the one
function here that writes to disk, used by the upload route to persist a
room's media into its own temp dir. Both tolerate the zip shape produced by
macOS Finder's "Compress" on a folder (contents nested one level under a
wrapper folder, plus __MACOSX/ and .DS_Store junk) — see
_resolve_bundle_entries().
"""

import difflib
import os
import zipfile
from dataclasses import dataclass
from io import BytesIO

import openpyxl

REQUIRED_COLUMNS = {"board", "category", "value", "question", "answer"}
OPTIONAL_COLUMNS = {"question_media", "answer_media"}
ALL_COLUMNS = REQUIRED_COLUMNS | OPTIONAL_COLUMNS

# Media MIME types by sniffed format — shared with app.py's /media route,
# which needs the same mapping to serve an extension-less stored file with
# the right Content-Type.
MEDIA_MIME_TYPES = {
    "png": "image/png",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}

# How close a header cell must be to a recognized column name (via
# difflib's similarity ratio) before it's silently treated as that column
# — high enough to only catch spacing/underscore/case/minor-typo variants
# (e.g. "question media" -> "question_media"), not semantic swaps like
# "points" vs "value", which get surfaced to the QM instead of guessed at.
HEADER_MATCH_CUTOFF = 0.75


@dataclass
class BundleQuestion:
    id: str
    board: str
    category: str
    value: int
    question: str
    answer: str
    question_media: list[str]
    answer_media: list[str]


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


def sniff_image_format(data: bytes) -> str | None:
    """Detects the real image format from file content (magic bytes), never
    from a claimed filename extension — see SPEC.md §6. Returns one of
    MEDIA_MIME_TYPES' keys, or None if the bytes don't match a supported
    image format.
    """
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _strip_ext(name: str) -> str:
    return name.rsplit(".", 1)[0] if "." in name else name


def _resolve_media_refs(filenames, resolved_media, collisions, referenced_bases):
    """Resolves each raw cell filename (e.g. 'poster' or 'poster.png' —
    extension irrelevant on either side) to media/'s actual filename by base
    name only, using resolved_media's already-sniffed format to validate it.
    Adds matched base names to referenced_bases (for the unreferenced-media
    warning) and returns (resolved_filenames, errors) — resolved_filenames
    is only meaningful when errors is empty. Shared between the
    `question_media` and `answer_media` columns, which follow identical rules.
    """
    errors = []
    resolved = []
    for filename in filenames:
        base = _strip_ext(filename)
        if base in collisions:
            errors.append(
                f"{filename!r} matches more than one file in media/ — rename "
                "one of them so the reference is unambiguous"
            )
            continue
        entry = resolved_media.get(base)
        if entry is None:
            errors.append(f"{filename!r} was not found in media/")
            continue
        basename, fmt = entry
        if fmt is None:
            errors.append(
                f"{basename!r} (referenced as {filename!r}) isn't a supported "
                "image format — supported: " + ", ".join(sorted(MEDIA_MIME_TYPES))
            )
            continue
        referenced_bases.add(base)
        resolved.append(basename)
    return resolved, errors


def _filter_mac_junk(namelist):
    """Drops macOS Finder zip artifacts: the __MACOSX/ AppleDouble sidecar
    tree, stray .DS_Store files, and ._-prefixed resource-fork files —
    wherever they appear, since they're an artifact of the zip tool, not
    part of the QM's bundle content.
    """
    return [
        n for n in namelist
        if not n.startswith("__MACOSX/")
        and os.path.basename(n.rstrip("/")) != ".DS_Store"
        and not os.path.basename(n.rstrip("/")).startswith("._")
    ]


def _resolve_bundle_entries(namelist):
    """Maps each real zip entry to the effective path used to match the
    .xlsx file / media/, unwrapping a single top-level wrapper folder if
    every entry lives under one.

    Finder's "Compress" on a folder (rather than on its contents) nests
    everything one level under a folder named after the original directory
    — the most natural non-technical zip workflow. Only a single level of
    unwrapping is attempted; deeper nesting isn't a shape that workflow
    produces.

    Returns a list of (effective_name, original_name) tuples. A bundle
    that's already flat (the .xlsx file and media/ as zip-root siblings) has
    more than one top-level path segment, so the unwrap condition below
    naturally doesn't fire and effective_name == original_name for every
    entry.
    """
    names = _filter_mac_junk(namelist)
    if not names:
        return []

    top_segments = {n.split("/", 1)[0] for n in names}
    if len(top_segments) == 1:
        prefix = next(iter(top_segments)) + "/"
        if all(n.startswith(prefix) for n in names):
            return [(n[len(prefix):], n) for n in names if n != prefix]

    return [(n, n) for n in names]


def parse_bundle(fileobj) -> BundleParseResult:
    try:
        zf = zipfile.ZipFile(fileobj)
    except zipfile.BadZipFile:
        return BundleParseResult(None, [ValidationError(None, "not a valid .zip file")], [], set())

    with zf:
        entries = _resolve_bundle_entries(zf.namelist())

        # The workbook's filename isn't dictated by the app — any single
        # .xlsx file in the bundle is accepted, whatever it's named (a QM
        # exporting from Google Sheets gets a file named after the sheet's
        # title, not "quiz.xlsx"). The "media/" folder name is still fixed,
        # matched case-insensitively below.
        xlsx_entries = [(eff, orig) for eff, orig in entries if eff.lower().endswith(".xlsx")]
        if not xlsx_entries:
            return BundleParseResult(None, [ValidationError(None, "bundle is missing an Excel file (.xlsx)")], [], set())
        if len(xlsx_entries) > 1:
            names = ", ".join(eff.rsplit("/", 1)[-1] for eff, orig in xlsx_entries)
            return BundleParseResult(
                None,
                [ValidationError(
                    None,
                    f"found multiple Excel files ({names}) — keep only one .xlsx file in the bundle",
                )],
                [],
                set(),
            )
        quiz_eff, quiz_entry = xlsx_entries[0]
        quiz_name = quiz_eff.rsplit("/", 1)[-1]

        try:
            workbook = openpyxl.load_workbook(BytesIO(zf.read(quiz_entry)), data_only=True, read_only=True)
        except Exception:
            # openpyxl can raise a range of exception types for malformed
            # xlsx content — all of them mean "reject this upload".
            return BundleParseResult(None, [ValidationError(None, f"{quiz_name} is not a valid Excel file")], [], set())

        media_entries = [
            (eff.rsplit("/", 1)[-1], orig)
            for eff, orig in entries
            if eff.lower().startswith("media/") and not eff.endswith("/")
        ]
        media_names = {basename for basename, orig in media_entries}

        # Media is matched by base filename only — the claimed extension (if
        # any) on either the sheet's cell value or the actual file is never
        # trusted; the real format is sniffed from content instead (see
        # sniff_image_format). Stripping extensions can make two on-disk
        # files collide (e.g. poster.png and poster.jpg both resolve to
        # "poster") — reported once here, added to errors without stopping
        # the rest of validation, same non-blocking pattern as the
        # missing-column check below.
        by_base: dict[str, list[str]] = {}
        for basename, orig in media_entries:
            by_base.setdefault(_strip_ext(basename), []).append(basename)

        errors: list[ValidationError] = []
        collisions = {base for base, names in by_base.items() if len(names) > 1}
        for base in sorted(collisions):
            names = ", ".join(sorted(by_base[base]))
            errors.append(ValidationError(
                None, f"found multiple media files matching {base!r} ({names}) — keep only one"
            ))

        orig_by_basename = {basename: orig for basename, orig in media_entries}
        resolved_media: dict[str, tuple[str, str | None]] = {}
        for base, names in by_base.items():
            if base in collisions:
                continue
            basename = names[0]
            with zf.open(orig_by_basename[basename]) as f:
                header_bytes = f.read(16)
            resolved_media[base] = (basename, sniff_image_format(header_bytes))

        sheet = workbook.worksheets[0]
        rows_iter = sheet.iter_rows(values_only=True)
        try:
            header_row = next(rows_iter)
        except StopIteration:
            errors.append(ValidationError(None, f"{quiz_name} has no header row"))
            return BundleParseResult(None, errors, [], media_names)

        header = {}
        found_labels = []
        for idx, cell in enumerate(header_row):
            if cell is None:
                continue
            raw = str(cell).strip()
            if not raw:
                continue
            found_labels.append(raw)
            key = raw.lower()
            if key not in ALL_COLUMNS:
                # Confidence maps to friction: a close spelling/spacing match
                # (e.g. "question media" -> "question_media") is silently
                # treated as that column — there's no real ambiguity about
                # intent. Anything looser (e.g. "points" vs "value", which
                # share no characters for any similarity match to catch) is
                # left alone and surfaced via missing_columns/found_labels
                # below instead of guessed at.
                match = difflib.get_close_matches(key, ALL_COLUMNS, n=1, cutoff=HEADER_MATCH_CUTOFF)
                if match:
                    key = match[0]
            header[key] = idx

        # A missing required column no longer stops validation here — it's
        # reported once as a bundle-level error below, but the row loop
        # still runs so any other issues (media filenames, duplicates) in
        # the same file surface in this same pass instead of requiring a
        # separate re-upload to discover them one category at a time. Each
        # globally-missing column's per-row "X is required" check is
        # skipped, since repeating that N times would be pure noise once
        # it's already been said once at the bundle level.
        missing_columns = REQUIRED_COLUMNS - set(header.keys())
        if missing_columns:
            message = f"{quiz_name} is missing required column(s): {', '.join(sorted(missing_columns))}."
            if found_labels:
                message += f" Columns found in your file: {', '.join(found_labels)}."
            errors.append(ValidationError(None, message))

        boards: dict[str, list[BundleQuestion]] = {}
        seen_ids: set[tuple[str, str, int]] = set()
        referenced_bases: set[str] = set()

        for row_idx, row in enumerate(rows_iter, start=2):
            if row is None or all(c is None for c in row):
                continue

            board = _cell_to_str(_row_cell(row, header, "board"))
            category = _cell_to_str(_row_cell(row, header, "category"))
            question = _cell_to_str(_row_cell(row, header, "question"))
            answer = _cell_to_str(_row_cell(row, header, "answer"))
            question_media_raw = _cell_to_str(_row_cell(row, header, "question_media"))
            answer_media_raw = _cell_to_str(_row_cell(row, header, "answer_media"))

            row_errors = []

            if "board" not in missing_columns and not board:
                row_errors.append("board is required")
            if "category" not in missing_columns and not category:
                row_errors.append("category is required")
            if "answer" not in missing_columns and not answer:
                row_errors.append("answer is required")

            if "value" in missing_columns:
                value = None
            else:
                value, value_error = _parse_value(_row_cell(row, header, "value"))
                if value_error:
                    row_errors.append(value_error)

            # A blank cell means no media. A non-blank placeholder (e.g. "NA",
            # "-") is treated literally and validated as a filename below, so
            # it fails with a "not found" error rather than silently ignored.
            question_media = (
                [m.strip() for m in question_media_raw.split(",") if m.strip()] if question_media_raw else []
            )
            answer_media = (
                [m.strip() for m in answer_media_raw.split(",") if m.strip()] if answer_media_raw else []
            )

            if not question and not question_media:
                row_errors.append("question or question_media is required")

            question_media_resolved, qm_errors = _resolve_media_refs(
                question_media, resolved_media, collisions, referenced_bases
            )
            answer_media_resolved, am_errors = _resolve_media_refs(
                answer_media, resolved_media, collisions, referenced_bases
            )
            row_errors.extend(qm_errors)
            row_errors.extend(am_errors)

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
                    question_media=question_media_resolved,
                    answer_media=answer_media_resolved,
                )
            )

        warnings = [
            f"media file {basename!r} is not referenced by any question in the quiz"
            for base, (basename, fmt) in sorted(resolved_media.items())
            if base not in referenced_bases
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
        for eff, orig in _resolve_bundle_entries(zf.namelist()):
            if not eff.lower().startswith("media/") or eff.endswith("/"):
                continue
            basename = eff.rsplit("/", 1)[-1]
            with zf.open(orig) as src:
                with open(os.path.join(dest_dir, basename), "wb") as dst:
                    dst.write(src.read())
