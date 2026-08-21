"""Regenerates static/sample/starter-quiz-bundle.zip.

Rerunnable: `python scripts/generate_sample_bundle.py`. Builds a small
two-board quiz.xlsx (SPEC.md §6 column contract) plus a handful of
placeholder PNGs, and zips them into the sample bundle downloadable from
the control center's "Quiz content" card. No new dependency: media
placeholders are hand-encoded PNGs (solid-color rectangles) rather than
pulling in an image library for flat rectangles.
"""

import os
import struct
import zipfile
import zlib
from io import BytesIO

import openpyxl

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_PATH = os.path.join(REPO_ROOT, "static", "sample", "starter-quiz-bundle.zip")

COLUMNS = ["board", "category", "value", "question", "answer", "question_media", "answer_media"]

ROWS = [
    {"board": "Board 1", "category": "Capitals", "value": 10,
     "question": "What is the capital of France?", "answer": "Paris"},
    {"board": "Board 1", "category": "Capitals", "value": 20,
     "question": "What is the capital of Japan?", "answer": "Tokyo"},

    {"board": "Board 1", "category": "Movies", "value": 10,
     "question": "Name this movie from its poster", "answer": "Inception",
     "question_media": "movie_poster.png"},
    {"board": "Board 1", "category": "Movies", "value": 20,
     "question": "Which two posters are from the same director's films?",
     "answer": "Christopher Nolan",
     "question_media": "poster_a.png,poster_b.png"},
    {"board": "Board 1", "category": "Movies", "value": 30,
     "question": "", "answer": "Leonardo DiCaprio",
     "question_media": "mystery_silhouette.png", "answer_media": "answer_photo.png"},

    {"board": "Board 2", "category": "Science", "value": 10,
     "question": "What planet is known as the Red Planet?", "answer": "Mars"},
    {"board": "Board 2", "category": "Science", "value": 20,
     "question": "What gas do plants absorb from the atmosphere?", "answer": "Carbon dioxide"},
    {"board": "Board 2", "category": "Science", "value": 30,
     "question": "What is the chemical symbol for gold?", "answer": "Au"},

    {"board": "Board 2", "category": "History", "value": 10,
     "question": "In what year did the Berlin Wall fall?", "answer": "1989"},
    {"board": "Board 2", "category": "History", "value": 20,
     "question": "Who was the first President of the United States?", "answer": "George Washington"},
    {"board": "Board 2", "category": "History", "value": 30,
     "question": "Which ancient wonder stood in Alexandria?", "answer": "The Lighthouse of Alexandria"},
]

# filename -> (width, height, RGB)
MEDIA = {
    "movie_poster.png": (300, 200, (99, 102, 241)),
    "poster_a.png": (300, 200, (16, 185, 129)),
    "poster_b.png": (300, 200, (245, 158, 11)),
    "mystery_silhouette.png": (300, 200, (55, 65, 81)),
    "answer_photo.png": (300, 200, (220, 38, 38)),
}


def _png_chunk(tag: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))


def make_solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """Hand-encodes a minimal valid 8-bit RGB PNG filled with one color."""
    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    row = b"\x00" + bytes(rgb) * width  # filter-type byte + one scanline
    raw = row * height
    idat = zlib.compress(raw, 9)
    return signature + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")


def build_workbook() -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "quiz"
    ws.append(COLUMNS)
    for row in ROWS:
        ws.append([row.get(col, "") for col in COLUMNS])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def build_bundle() -> bytes:
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("quiz.xlsx", build_workbook())
        for filename, (width, height, rgb) in MEDIA.items():
            zf.writestr(f"media/{filename}", make_solid_png(width, height, rgb))
    return buf.getvalue()


def main():
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)
    with open(OUTPUT_PATH, "wb") as f:
        f.write(build_bundle())
    print(f"wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
