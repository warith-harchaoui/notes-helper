"""
Unit tests for the diarization speaker-embedder selector.

Module summary
--------------
Covers :func:`notes_helper.diarize._make_embedder`, the factory that picks the
speaker-embedding backend from :data:`notes_helper.config.DIAR_EMBEDDER`. The
default ``"nemo"`` (torch/NeMo TitaNet-large) stays the desktop path; ``"sherpa"``
selects the torch-free ONNX TitaNet-large used by the cross-platform app (ADR
0002). These tests are model-free: the embedder classes are stubbed so no weights
load and no network is touched — only the selection logic is exercised.

Author
------
Warith Harchaoui — https://www.linkedin.com/in/warith-harchaoui/
"""

from __future__ import annotations

import sys
import types

import pytest


def _install_fake_vocal_helper(monkeypatch) -> dict[str, object]:
    """Register a stub ``vocal_helper.diar`` module and return its sentinels.

    Parameters
    ----------
    monkeypatch : pytest.MonkeyPatch
        Used to inject the fake module into ``sys.modules``.

    Returns
    -------
    dict[str, object]
        Maps ``"titanet"`` / ``"sherpa"`` to the sentinel instances the stubs
        return, so a test can assert which class ``_make_embedder`` built.
    """
    titanet_sentinel = object()
    sherpa_sentinel = object()

    mod = types.ModuleType("vocal_helper.diar")

    class _FakeTitaNet:
        def __new__(cls):
            return titanet_sentinel

    class _FakeSherpa:
        def __init__(self, model_path=None):
            self.model_path = model_path

    def _fake_resolve():
        return ("seg.onnx", "emb.onnx")

    mod._TitaNetEmbedder = _FakeTitaNet
    mod._SherpaEmbedder = lambda model_path=None: sherpa_sentinel
    mod._resolve_sherpa_models = _fake_resolve

    monkeypatch.setitem(sys.modules, "vocal_helper.diar", mod)
    return {"titanet": titanet_sentinel, "sherpa": sherpa_sentinel}


def test_make_embedder_defaults_to_nemo(monkeypatch) -> None:
    """With ``DIAR_EMBEDDER='nemo'`` the torch TitaNet embedder is built."""
    sentinels = _install_fake_vocal_helper(monkeypatch)
    import notes_helper.diarize as dz

    monkeypatch.setattr(dz, "DIAR_EMBEDDER", "nemo")
    assert dz._make_embedder() is sentinels["titanet"]


def test_make_embedder_selects_sherpa(monkeypatch) -> None:
    """With ``DIAR_EMBEDDER='sherpa'`` the torch-free ONNX embedder is built."""
    sentinels = _install_fake_vocal_helper(monkeypatch)
    import notes_helper.diarize as dz

    monkeypatch.setattr(dz, "DIAR_EMBEDDER", "sherpa")
    assert dz._make_embedder() is sentinels["sherpa"]


def test_make_embedder_rejects_unknown_backend(monkeypatch) -> None:
    """An unrecognised backend name raises a clear ``ValueError``."""
    _install_fake_vocal_helper(monkeypatch)
    import notes_helper.diarize as dz

    monkeypatch.setattr(dz, "DIAR_EMBEDDER", "bogus")
    with pytest.raises(ValueError, match="unknown DIAR_EMBEDDER"):
        dz._make_embedder()
