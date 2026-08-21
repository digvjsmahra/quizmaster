import os

from bundle_loader import parse_bundle

SAMPLE_BUNDLE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "static", "sample", "starter-quiz-bundle.zip",
)


def test_sample_bundle_parses_cleanly():
    with open(SAMPLE_BUNDLE_PATH, "rb") as f:
        result = parse_bundle(f)

    assert result.errors == []
    assert result.warnings == []
    assert list(result.boards.keys()) == ["Board 1", "Board 2"]
    assert sum(len(qs) for qs in result.boards.values()) == 11
