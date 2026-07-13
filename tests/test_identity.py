"""Tests for the on-device identity store and matcher (synthetic vectors, no ML deps).

Module summary
--------------
Deterministic unit tests for :mod:`notes_helper.identity`. They exercise the raw-space
cluster-centroid helper, the enroll-then-identify round trip across two
synthetic "meetings", and the empty-store behaviour — all on seeded synthetic
192-d vectors so no real audio or ML model is required. Every random draw is
seeded (``numpy.random.default_rng``) so the suite is fully reproducible.

Usage example
-------------
>>> import subprocess, sys
>>> code = subprocess.run(
...     [sys.executable, "-m", "pytest", "-q", "tests/test_identity.py"],
...     capture_output=True, text=True).returncode
>>> print(code)
0
# expected output: 0

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""
from __future__ import annotations

import numpy as np

from notes_helper.identity import (
    PeopleStore,
    cluster_centroids_raw,
    enroll_cluster,
    identify_recording,
)


def _rng(seed: int) -> np.random.Generator:
    """Return a seeded numpy Generator so every test draw is reproducible."""
    return np.random.default_rng(seed)


def _speaker_blob(center: np.ndarray, n: int, jitter: float,
                  rng: np.random.Generator) -> np.ndarray:
    """Make ``n`` unit-norm 192-d embeddings jittered around ``center``."""
    v = center + jitter * rng.standard_normal((n, 192)).astype(np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


def test_cluster_centroids_raw() -> None:
    """Assert one unit-norm centroid per non-negative cluster (noise ignored)."""
    X = np.random.default_rng(0).standard_normal((10, 192)).astype(np.float32)
    labels = np.array([0, 0, 1, 1, 1, -1, 0, 2, 2, -1])
    cents = cluster_centroids_raw(X, labels)
    assert set(cents) == {0, 1, 2}
    assert all(abs(np.linalg.norm(v) - 1) < 1e-5 for v in cents.values())


def test_enroll_then_identify(tmp_path) -> None:
    """Assert enrolled voices re-identify as auto and a stranger stays unknown."""
    rng = _rng(42)
    ca = rng.standard_normal(192).astype(np.float32); ca /= np.linalg.norm(ca)
    cb = rng.standard_normal(192).astype(np.float32); cb /= np.linalg.norm(cb)

    # --- meeting 1: two speakers, enrolled by name ---
    Xa, Xb = _speaker_blob(ca, 8, 0.05, rng), _speaker_blob(cb, 8, 0.05, rng)
    X1 = np.vstack([Xa, Xb])
    labels1 = np.array([0] * 8 + [1] * 8)
    store = PeopleStore(str(tmp_path / "people.db"))
    enroll_cluster(store, X1, labels1, 0, "Alice")
    enroll_cluster(store, X1, labels1, 1, "Bob")
    assert {p["name"] for p in store.all_people()} == {"Alice", "Bob"}

    # --- meeting 2: same two voices (fresh jitter) + one stranger ---
    rng2 = _rng(7)
    cs = rng2.standard_normal(192).astype(np.float32); cs /= np.linalg.norm(cs)
    X2 = np.vstack([_speaker_blob(cb, 6, 0.05, rng2),   # cluster 0 = Bob
                    _speaker_blob(ca, 6, 0.05, rng2),   # cluster 1 = Alice
                    _speaker_blob(cs, 6, 0.05, rng2)])  # cluster 2 = unknown
    labels2 = np.array([0] * 6 + [1] * 6 + [2] * 6)
    mp = identify_recording(X2, labels2, store)

    assert mp["S0"]["name"] == "Bob" and mp["S0"]["mode"] == "auto"
    assert mp["S1"]["name"] == "Alice" and mp["S1"]["mode"] == "auto"
    assert mp["S2"]["mode"] == "unknown" and mp["S2"]["name"] == "S2"
    store.close()


def test_empty_store_all_unknown(tmp_path) -> None:
    """Assert an empty store labels every cluster as unknown."""
    store = PeopleStore(str(tmp_path / "p.db"))
    X = np.random.default_rng(1).standard_normal((6, 192)).astype(np.float32)
    labels = np.array([0, 0, 0, 1, 1, 1])
    mp = identify_recording(X, labels, store)
    assert all(v["mode"] == "unknown" for v in mp.values())
    store.close()
