"""
Smoke + round-trip tests for the FastAPI HTTP surface (:mod:`notes_helper.api`).

Module summary
--------------
Exercises the model-light endpoints without any network, Ollama, or heavy ML
backend: ``/health``, OpenAPI schema introspection (to catch route drift),
``/normalize`` (the render-schema boundary), and a real ``/render`` round-trip
that uploads a transcript + synthesis and asserts a self-contained HTML report
comes back. The ``/synth`` endpoint is not called here because it reaches for a
local LLM; its logic is covered by the synth unit tests.

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""
from __future__ import annotations

import io
import json
import zipfile

import pytest

# FastAPI + httpx live in the [api] / [dev] extras — skip cleanly otherwise.
pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="module")
def client() -> TestClient:
    """Yield a TestClient bound to the notes-helper FastAPI app."""
    from notes_helper.api import app

    with TestClient(app) as c:
        yield c


def test_health_returns_ok(client: TestClient) -> None:
    """``/health`` returns 200 + ``{"status": "ok"}``."""
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_openapi_exposes_expected_routes(client: TestClient) -> None:
    """The OpenAPI schema must list the documented endpoints (drift guard)."""
    paths = client.get("/openapi.json").json()["paths"]
    assert {"/health", "/normalize", "/synth", "/render"} <= set(paths)


def test_normalize_coerces_drifted_synthese(client: TestClient) -> None:
    """``/normalize`` coerces a drifted synthesis into the render schema."""
    r = client.post("/normalize", json={"resume": "one para", "themes": [{"theme": "T"}]})
    assert r.status_code == 200
    body = r.json()
    assert body["resume"] == ["one para"]
    assert body["themes"] == [{"theme": "T", "points": []}]


def test_render_single_format_returns_html(client: TestClient) -> None:
    """``/render`` with one format streams a self-contained HTML report."""
    transcript = [{"t0": 0.0, "t1": 2.0, "speaker": "S0", "text": "bonjour"}]
    synthese = {
        "meta": {"titre": "API test", "date": "2026-07-12", "duree": "0:00:02"},
        "speakers": {"S0": {"name": "S0", "role": ""}},
        "resume": ["Un résumé."],
        "chapitres": [{"t": "0:00:01", "titre": "Intro"}],
    }
    r = client.post(
        "/render?formats=html",
        files={
            "transcript": ("transcript.json", json.dumps(transcript), "application/json"),
            "synthese": ("synthese.json", json.dumps(synthese), "application/json"),
        },
    )
    assert r.status_code == 200
    text = r.content.decode("utf-8")
    assert "<html" in text.lower()
    # SOVEREIGNTY: the rendered report must not phone home.
    assert "http://" not in text and "https://" not in text


def test_render_multiple_formats_returns_zip(client: TestClient) -> None:
    """``/render`` with several formats streams a zip of the rendered files."""
    transcript = [{"t0": 0.0, "t1": 2.0, "speaker": "S0", "text": "bonjour"}]
    synthese = {"meta": {"titre": "Zip", "date": "2026-07-12"}, "speakers": {}, "resume": ["x"]}
    r = client.post(
        "/render?formats=md,html",
        files={
            "transcript": ("transcript.json", json.dumps(transcript), "application/json"),
            "synthese": ("synthese.json", json.dumps(synthese), "application/json"),
        },
    )
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert "report.md" in names
    assert "report.html" in names


def test_render_rejects_unknown_format(client: TestClient) -> None:
    """An unsupported format yields a 400, not a 500."""
    r = client.post(
        "/render?formats=docx",
        files={
            "transcript": ("t.json", "[]", "application/json"),
            "synthese": ("s.json", "{}", "application/json"),
        },
    )
    assert r.status_code == 400
