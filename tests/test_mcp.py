"""
Smoke test for the MCP surface (:mod:`notes_helper.mcp`).

Module summary
--------------
Verifies that the MCP wrapper around the FastAPI app imports without error,
re-exposes the underlying FastAPI ``app`` object, and attaches the ``mcp``
handler. Full protocol round-trips belong to a separate integration suite once
the MCP client tooling is stable in CI.

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""

from __future__ import annotations

import pytest

# fastapi_mcp lives in the [mcp] optional extra — skip cleanly if absent.
pytest.importorskip("fastapi_mcp")


def test_mcp_module_imports_and_exposes_app() -> None:
    """The MCP module must import and re-expose the FastAPI app + mcp handler."""
    from notes_helper import mcp as mcp_module

    assert hasattr(mcp_module, "app"), "notes_helper.mcp must re-expose `app`."
    assert hasattr(mcp_module, "mcp"), "notes_helper.mcp must expose the `mcp` handler."


def test_main_entrypoint_is_callable() -> None:
    """The ``notes-helper-mcp`` console entry point should be a callable."""
    from notes_helper.mcp import main

    assert callable(main)
