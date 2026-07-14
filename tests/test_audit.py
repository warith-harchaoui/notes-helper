"""
Tests for the egress audit (:func:`notes_helper.cli.audit_egress`).

Module summary
--------------
Confirms the sovereignty gate flags an external URL in a generated artifact and
passes cleanly when there is none.

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""

from __future__ import annotations

from notes_helper.cli import audit_egress


def test_audit_flags_external_url(tmp_path):
    """A report referencing an external URL makes the audit report a hit."""
    (tmp_path / "report.html").write_text(
        '<a href="https://evil.example.com/x">x</a>', encoding="utf-8"
    )
    assert audit_egress(str(tmp_path)) >= 1


def test_audit_passes_when_clean(tmp_path):
    """A URL-free artifact yields zero hits (the gate would pass)."""
    (tmp_path / "report.md").write_text("# clean\nno urls here\n", encoding="utf-8")
    assert audit_egress(str(tmp_path)) == 0
