import csv
import sys
from dataclasses import dataclass


@dataclass
class Question:
    id: str
    board: str
    category: str
    value: int


def load_quiz(path: str = "data/quiz.csv") -> dict[str, list[Question]]:
    try:
        with open(path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)

            if reader.fieldnames is None:
                sys.exit(f"ERROR: {path} is empty or unreadable")

            # Case-insensitive column check
            normalised = {c.strip().lower(): c for c in reader.fieldnames}
            required = {"board", "category", "value"}
            missing = required - set(normalised.keys())
            if missing:
                sys.exit(f"ERROR: {path} is missing required columns: {', '.join(sorted(missing))}")

            board_col = normalised["board"]
            cat_col = normalised["category"]
            val_col = normalised["value"]

            boards: dict[str, list[Question]] = {}
            seen: set[str] = set()
            rows_read = 0

            for lineno, row in enumerate(reader, start=2):
                rows_read += 1
                board = row[board_col].strip()
                category = row[cat_col].strip()
                value_str = row[val_col].strip()

                if not board or not category or not value_str:
                    sys.exit(f"ERROR: {path} line {lineno}: empty field — {dict(row)!r}")

                try:
                    value = int(value_str)
                except ValueError:
                    sys.exit(
                        f"ERROR: {path} line {lineno}: value must be a positive integer, got {value_str!r}"
                    )

                if value <= 0:
                    sys.exit(f"ERROR: {path} line {lineno}: value must be positive, got {value}")

                qid = f"{board}:{category}:{value}"
                if qid in seen:
                    sys.exit(f"ERROR: {path} line {lineno}: duplicate question_id {qid!r}")
                seen.add(qid)

                if board not in boards:
                    boards[board] = []
                boards[board].append(Question(id=qid, board=board, category=category, value=value))

            if rows_read == 0:
                sys.exit(f"ERROR: {path} contains no data rows")

            return boards

    except FileNotFoundError:
        sys.exit(f"ERROR: Quiz file not found: {path}")
    except PermissionError:
        sys.exit(f"ERROR: Cannot read quiz file: {path}")
