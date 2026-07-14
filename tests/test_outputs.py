"""
Tests for :mod:`notes_helper.outputs` — the render dispatcher and sovereignty.

Module summary
--------------
Renders Markdown + HTML from the fixture output directory and asserts the files
exist. The key test enforces the product's core guarantee: the generated HTML
report contains **zero external URLs** — nothing phones home.

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""

from __future__ import annotations

import os

import pytest

from notes_helper.outputs import render


def test_render_writes_md_and_html(out_dir):
    """``render`` produces the requested formats and copies local assets."""
    written = render(out_dir, ["md", "html"])
    assert set(written) == {"md", "html"}
    assert os.path.exists(written["md"])
    assert os.path.exists(written["html"])
    # Assets are copied next to the report so the page is self-contained.
    assert os.path.isdir(os.path.join(out_dir, "assets"))


def test_html_report_has_zero_egress(out_dir):
    """SOVEREIGNTY: the rendered HTML references no external http(s) URL."""
    written = render(out_dir, ["html"])
    html_text = open(written["html"], encoding="utf-8").read()
    assert "http://" not in html_text
    assert "https://" not in html_text


def test_missing_synthesis_raises(tmp_path):
    """Rendering without ``synthese.json`` fails with a helpful message."""
    (tmp_path / "transcript.json").write_text("[]", encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        render(str(tmp_path), ["md"])


def test_vault_requires_dir(out_dir):
    """Requesting the ``vault`` format without a vault path raises ValueError."""
    with pytest.raises(ValueError):
        render(out_dir, ["vault"])
