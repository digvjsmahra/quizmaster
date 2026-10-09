"""The static landing page (site/) can't load the app's stylesheet without
waking the app's server, so it carries a copy of the app's two colour token
blocks. This keeps that copy honest: change a colour in the app, and this
fails until the blocks are re-copied into site/styles.css."""

import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BLOCKS = {
    "light": r'^:root \{.*?^\}\n',
    "dark": r'^:root\[data-theme="dark"\] \{.*?^\}\n',
}


def _first_block(path, pattern):
    with open(os.path.join(REPO_ROOT, path)) as f:
        match = re.search(pattern, f.read(), re.S | re.M)
    assert match, f"no {pattern!r} block in {path}"
    return match.group(0)


def test_site_token_blocks_match_the_app():
    for name, pattern in BLOCKS.items():
        app = _first_block("static/css/styles.css", pattern)
        site = _first_block("site/styles.css", pattern)
        assert site == app, (
            f"site/styles.css's {name} token block has drifted from "
            "static/css/styles.css — re-copy it verbatim"
        )
