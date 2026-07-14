"""
notes-helper — Model Context Protocol (MCP) surface.

Module summary
--------------
Adapter that exposes the FastAPI app defined in :mod:`notes_helper.api` as MCP tools,
so any MCP-aware client can call ``normalize`` / ``synth`` / ``render`` as
first-class tools — proprietary ones (Claude Desktop, Cursor, Windsurf) or
open-source ones (Cline, Continue, Goose, Zed), plus custom agents and IDE
integrations. Uses
:mod:`fastapi_mcp` (https://github.com/tadata-org/fastapi_mcp) — one line wraps
the whole existing HTTP surface, so the route definitions are never duplicated.

Consistent with the rest of the ``*-helper`` suite (``os_helper.mcp`` /
``vocal_helper.mcp`` / …): the MCP endpoint is mounted on the same FastAPI app,
so a single process serves both the ``/…`` HTTP routes and the MCP tools.

Install the extra to pull in ``fastapi-mcp``::

    pip install 'notes-helper[api,mcp]'

Then run the MCP server::

    notes-helper-mcp                      # entry point (see pyproject)
    # or, equivalently:
    python -m notes_helper.mcp

Usage example
-------------
>>> # Register the MCP endpoint in your client. It publishes every route
>>> # defined in notes_helper.api (normalize / synth / render) with the same
>>> # argument names as the FastAPI endpoints.

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""

from __future__ import annotations

try:
    from fastapi_mcp import FastApiMCP
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "The MCP surface requires the [mcp] extra. "
        "Install with: pip install 'notes-helper[api,mcp]'"
    ) from exc

# Reuse the exact same FastAPI app — MCP is a thin wrapper on top.
from .api import app

# ``FastApiMCP`` mounts an MCP endpoint on the existing FastAPI app; we store the
# wrapped instance at module scope so downstream code (tests, ASGI runners) can
# access both the FastAPI app and the MCP handler.
mcp = FastApiMCP(
    app,
    name="notes-helper",
    description=(
        "notes-helper MCP tools: normalize a synthesis to the render schema, "
        "synthesize a structured report from a diarized transcript via a local "
        "LLM, and render a report to Markdown / HTML. Fully local."
    ),
)
# Attach the MCP endpoint to the FastAPI app. Newer fastapi-mcp releases split
# ``mount()`` into transport-specific ``mount_http()`` (recommended) and
# ``mount_sse()``. Fall back to the legacy ``mount()`` on older versions so a
# range of ``fastapi-mcp`` versions keeps working.
if hasattr(mcp, "mount_http"):
    mcp.mount_http()
else:  # pragma: no cover — legacy fastapi-mcp
    mcp.mount()


def main() -> None:
    """Entry point for the ``notes-helper-mcp`` console script.

    Boots the FastAPI app (which now serves both the ``/…`` HTTP routes and the
    MCP endpoint) with ``uvicorn`` in single-worker mode. Meant for local /
    container usage; behind a real load balancer, run ``uvicorn`` / ``gunicorn``
    directly against :data:`notes_helper.api.app`.
    """
    import os

    import uvicorn

    host = os.environ.get("NOTES_HELPER_HOST", "0.0.0.0")
    port = int(os.environ.get("NOTES_HELPER_PORT", "8000"))
    # Single worker keeps any in-process state consistent across requests
    # without cross-process coordination.
    uvicorn.run(app, host=host, port=port, workers=1)


if __name__ == "__main__":  # pragma: no cover
    main()
