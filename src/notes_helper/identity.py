#!/usr/bin/env python3
"""Cross-meeting speaker identity, 100% on-device — "name once, known forever".

Module summary
--------------
This module replaces the per-recording direct-address heuristic
(``map_speakers.py``) with a *persistent voiceprint store*: name a voice once,
and every later recording that matches it is auto-labelled — the voiceprint
refines itself over time.

Key design point (do not skip)
------------------------------
* ``diar_pipeline.cluster()`` works in a *per-recording centered* space
  (``Xc = Xn - Xn.mean(0)``) to SEPARATE speakers within one file. Those
  vectors are recording-relative and NOT comparable across meetings.
* IDENTITY here works in the *raw* L2-normalised TitaNet space (``Xn``, the
  embeddings stored in the checkpoint's ``X``), compared by cosine against the
  enrolled store. An absolute space is comparable across recordings.

That distinction is the whole point of this file: clustering wants a
recording-relative space so that within-file separation is maximal, whereas
identity wants an absolute space so that "the same voice" lands at the same
coordinates across different meetings. Mixing the two would silently break
cross-meeting matching, so every centroid built here comes from the RAW space.

Nothing here ever touches the network. The store is a local SQLite file
(default ``~/.notes-helper/people.db``) holding numeric voiceprints (192-d vectors),
not audio. Treat the store as biometric data: ``forget`` deletes a person, and
sync is opt-in only.

Usage example
-------------
>>> import numpy as np
>>> from notes_helper.identity import cluster_centroids_raw
>>> X = np.eye(4, 192, dtype=np.float32)
>>> labels = np.array([0, 0, 1, 1])
>>> cents = cluster_centroids_raw(X, labels)
>>> print(sorted(cents))
[0, 1]
# expected output: [0, 1]

CLI
---
* ``python identity.py identify <checkpoint.npz>``   -> write speaker_mapping.json
* ``python identity.py enroll <checkpoint.npz> --cluster S0 --name "Warith Harchaoui" [--role Produit]``
* ``python identity.py list``
* ``python identity.py forget <person_id>``
* ``python identity.py calibrate``

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import time

import numpy as np
from scipy.optimize import linear_sum_assignment

# --------------------------------------------------------------------------- #
# module constants
# --------------------------------------------------------------------------- #
EMB_DIM: int = 192  # TitaNet embedding size (matches diar_pipeline)
EXEMPLAR_CAP: int = 64  # bounded per-person exemplar ring
TAU_HIGH: float = 0.62  # cosine >= this => auto-assign
TAU_LOW: float = 0.45  # [TAU_LOW, TAU_HIGH) => suggest ("is this X?"); < => unknown

# Default store location: an env override, else a per-user dotfile. Numeric
# voiceprints only — never audio — so it is small and portable.
DEFAULT_DB: str = os.environ.get("NOTES_HELPER_DB", os.path.expanduser("~/.notes-helper/people.db"))


# --------------------------------------------------------------------------- #
# small numeric helpers
# --------------------------------------------------------------------------- #
def l2(v: np.ndarray) -> np.ndarray:
    """L2-normalise a single vector.

    Parameters
    ----------
    v : numpy.ndarray
        A 1-D array (or anything array-like) to normalise. It is cast to
        ``float32`` before normalisation.

    Returns
    -------
    numpy.ndarray
        The unit-norm ``float32`` vector ``v / (||v|| + eps)``.

    Notes
    -----
    A small ``1e-9`` epsilon guards against division by zero for the (rare)
    all-zero input, so the function never raises on degenerate vectors.

    Examples
    --------
    >>> import numpy as np
    >>> round(float(np.linalg.norm(l2(np.array([3.0, 4.0])))), 6)
    1.0
    """
    v = np.asarray(v, dtype=np.float32)
    return v / (np.linalg.norm(v) + 1e-9)


def l2_rows(X: np.ndarray) -> np.ndarray:
    """L2-normalise every row of a 2-D matrix independently.

    Parameters
    ----------
    X : numpy.ndarray
        A 2-D array of shape ``(n, d)``. It is cast to ``float32`` first.

    Returns
    -------
    numpy.ndarray
        A ``float32`` matrix with the same shape as ``X`` where each row has
        unit L2 norm.

    Notes
    -----
    Row-wise (``axis=1``) normalisation with a ``1e-9`` epsilon per row keeps
    zero rows finite instead of producing ``NaN``.

    Examples
    --------
    >>> import numpy as np
    >>> M = l2_rows(np.array([[3.0, 4.0], [0.0, 5.0]]))
    >>> [round(float(np.linalg.norm(r)), 6) for r in M]
    [1.0, 1.0]
    """
    X = np.asarray(X, dtype=np.float32)
    return X / (np.linalg.norm(X, axis=1, keepdims=True) + 1e-9)


def slug(name: str) -> str:
    """Turn a display name into a filesystem/id-safe slug.

    Parameters
    ----------
    name : str
        The human-readable person name, e.g. ``"Warith Harchaoui"``.

    Returns
    -------
    str
        A lowercase, hyphen-separated slug (non-alphanumeric runs collapse to a
        single ``-`` and leading/trailing hyphens are stripped). If nothing
        survives normalisation, ``"person"`` is returned as a safe fallback.

    Examples
    --------
    >>> slug("Warith Harchaoui")
    'warith-harchaoui'
    >>> slug("!!!")
    'person'
    """
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "person"


# --------------------------------------------------------------------------- #
# the on-device store
# --------------------------------------------------------------------------- #
class PeopleStore:
    """SQLite-backed voiceprint store — a local file that never leaves the device.

    Each enrolled person owns a raw-space centroid plus a bounded ring of raw
    exemplar embeddings; the centroid is recomputed from the surviving
    exemplars on every write so it stays consistent with the ring.

    Parameters
    ----------
    path : str, optional
        Filesystem path to the SQLite database. ``~`` is expanded and any
        missing parent directory is created. Defaults to :data:`DEFAULT_DB`.

    Attributes
    ----------
    path : str
        The expanded absolute path to the SQLite file backing this store.
    db : sqlite3.Connection
        The open connection (WAL journalling) to that file.

    Notes
    -----
    All stored vectors are numeric voiceprints (192-d), never audio. Treat the
    file as biometric data. The schema (``person``, ``exemplar``, ``meta``) is
    created lazily on construction, so opening a fresh path is safe.
    """

    def __init__(self, path: str = DEFAULT_DB) -> None:
        """Open (creating if needed) the SQLite store at ``path``.

        Parameters
        ----------
        path : str, optional
            Path to the SQLite database. Defaults to :data:`DEFAULT_DB`.

        Notes
        -----
        Enables WAL journalling for concurrent-reader friendliness and runs the
        idempotent schema migration so the store is immediately usable.
        """
        self.path: str = os.path.expanduser(path)
        # Ensure the parent directory exists (".," fallback handles bare names).
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self.db: sqlite3.Connection = sqlite3.connect(self.path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    def _migrate(self) -> None:
        """Create the store schema if it does not already exist.

        Returns
        -------
        None

        Notes
        -----
        Idempotent: uses ``CREATE TABLE IF NOT EXISTS`` for the ``person``,
        ``exemplar`` and ``meta`` tables, so it is safe to call on every open.
        """
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS person(
                id TEXT PRIMARY KEY, name TEXT, role TEXT,
                centroid BLOB, n_exemplars INTEGER,
                created_at REAL, updated_at REAL);
            CREATE TABLE IF NOT EXISTS exemplar(
                person_id TEXT, emb BLOB, source TEXT, ts REAL);
            CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v BLOB);
            """
        )
        self.db.commit()

    # -- blob (de)serialisation -------------------------------------------- #
    @staticmethod
    def _blob(vec: np.ndarray) -> bytes:
        """Serialise a vector to raw ``float32`` bytes for BLOB storage.

        Parameters
        ----------
        vec : numpy.ndarray
            The array to serialise. It is cast to ``float32`` first so the byte
            layout is stable regardless of the input dtype.

        Returns
        -------
        bytes
            The contiguous little/host-endian ``float32`` bytes of ``vec``.
        """
        return np.asarray(vec, dtype=np.float32).tobytes()

    @staticmethod
    def _unblob(b: bytes) -> np.ndarray:
        """Deserialise ``float32`` BLOB bytes back into a numpy vector.

        Parameters
        ----------
        b : bytes
            The raw ``float32`` bytes previously produced by :meth:`_blob`.

        Returns
        -------
        numpy.ndarray
            A writable ``float32`` copy of the decoded vector.

        Notes
        -----
        ``np.frombuffer`` returns a read-only view over ``b``; the explicit
        ``.copy()`` makes the result independently mutable.
        """
        return np.frombuffer(b, dtype=np.float32).copy()

    # -- reads -------------------------------------------------------------- #
    def get(self, pid: str) -> dict | None:
        """Fetch a single person record (with decoded centroid) by id.

        Parameters
        ----------
        pid : str
            The person id to look up.

        Returns
        -------
        dict or None
            ``{"id", "name", "role", "centroid", "n_exemplars"}`` where
            ``centroid`` is a decoded ``float32`` vector, or ``None`` if no
            person with that id exists.
        """
        r = self.db.execute(
            "SELECT id,name,role,centroid,n_exemplars FROM person WHERE id=?", (pid,)
        ).fetchone()
        if not r:
            return None
        return {
            "id": r[0],
            "name": r[1],
            "role": r[2],
            "centroid": self._unblob(r[3]),
            "n_exemplars": r[4],
        }

    def all_people(self) -> list[dict]:
        """List every enrolled person, ordered by name (no centroids loaded).

        Returns
        -------
        list of dict
            One ``{"id", "name", "role", "n_exemplars"}`` dict per person,
            sorted alphabetically by name. Centroids are intentionally omitted
            to keep the listing cheap.
        """
        rows = self.db.execute(
            "SELECT id,name,role,n_exemplars FROM person ORDER BY name"
        ).fetchall()
        return [{"id": a, "name": b, "role": c, "n_exemplars": d} for a, b, c, d in rows]

    def centroids(self) -> dict[str, np.ndarray]:
        """Return the raw L2-normalised centroids — the match targets.

        Returns
        -------
        dict of {str : numpy.ndarray}
            Mapping ``{person_id: unit-norm raw-space centroid}``. These live
            in the absolute TitaNet space and are directly comparable by cosine
            against cluster centroids from any recording.

        Notes
        -----
        Centroids are re-L2-normalised on read so callers always get unit
        vectors regardless of tiny drift in the stored blob.
        """
        rows = self.db.execute("SELECT id,centroid FROM person").fetchall()
        return {pid: l2(self._unblob(b)) for pid, b in rows}

    def exemplars(self, pid: str) -> np.ndarray:
        """Return all stored exemplar embeddings for one person.

        Parameters
        ----------
        pid : str
            The person id whose exemplars to load.

        Returns
        -------
        numpy.ndarray
            An ``(n, EMB_DIM)`` ``float32`` matrix of raw exemplar embeddings,
            or an empty ``(0, EMB_DIM)`` array when the person has none.
        """
        rows = self.db.execute("SELECT emb FROM exemplar WHERE person_id=?", (pid,)).fetchall()
        if not rows:
            return np.zeros((0, EMB_DIM), dtype=np.float32)
        return np.vstack([self._unblob(b) for (b,) in rows])

    # -- global mean (absolute-space upgrade; optional) -------------------- #
    def global_mean(self) -> np.ndarray | None:
        """Return the stored global mean of all exemplars, if computed.

        Returns
        -------
        numpy.ndarray or None
            The decoded ``float32`` global-mean vector, or ``None`` when
            :meth:`refresh_global_mean` has never been run on a non-empty store.

        Notes
        -----
        Subtracting this mean is an optional absolute-space upgrade that can
        sharpen cosine separation; it is opt-in via ``use_global_mean``.
        """
        r = self.db.execute("SELECT v FROM meta WHERE k='global_mean'").fetchone()
        return self._unblob(r[0]) if r else None

    def refresh_global_mean(self) -> None:
        """Recompute and persist the global mean across every stored exemplar.

        Returns
        -------
        None

        Notes
        -----
        A no-op when there are no exemplars yet. Otherwise the mean over all
        exemplar rows is stored under the ``global_mean`` key in ``meta`` and
        overwrites any previous value.
        """
        rows = self.db.execute("SELECT emb FROM exemplar").fetchall()
        if not rows:
            return
        M = np.vstack([self._unblob(b) for (b,) in rows]).mean(0)
        self.db.execute(
            "INSERT OR REPLACE INTO meta(k,v) VALUES('global_mean',?)", (self._blob(M),)
        )
        self.db.commit()

    # -- writes ------------------------------------------------------------- #
    def _unique_id(self, name: str) -> str:
        """Derive a store-unique person id from a display name.

        Parameters
        ----------
        name : str
            The person's display name to slugify.

        Returns
        -------
        str
            ``slug(name)`` if free, otherwise the slug with a numeric suffix
            (``-2``, ``-3``, ...) chosen so the id is not already taken.
        """
        base = slug(name)
        pid, i = base, 2
        while self.db.execute("SELECT 1 FROM person WHERE id=?", (pid,)).fetchone():
            pid, i = f"{base}-{i}", i + 1
        return pid

    def add_person(
        self, name: str, centroid: np.ndarray, exemplars: np.ndarray, role: str = ""
    ) -> str:
        """Create a new person and seed it with exemplars.

        Parameters
        ----------
        name : str
            Display name for the new person.
        centroid : numpy.ndarray
            Initial raw-space centroid; it is L2-normalised before storage.
        exemplars : numpy.ndarray
            An ``(n, EMB_DIM)`` matrix of raw exemplar embeddings used to seed
            the person's exemplar ring (which also recomputes the centroid).
        role : str, optional
            Free-text role/label, by default ``""``.

        Returns
        -------
        str
            The unique id assigned to the new person.

        Notes
        -----
        The row is inserted with ``n_exemplars = 0`` and then
        :meth:`push_exemplars` fills the ring and recomputes ``n_exemplars`` and
        the centroid, keeping the stored centroid consistent with the ring.
        """
        pid = self._unique_id(name)
        now = time.time()
        self.db.execute(
            "INSERT INTO person(id,name,role,centroid,n_exemplars,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (pid, name, role, self._blob(l2(centroid)), 0, now, now),
        )
        self.db.commit()
        self.push_exemplars(pid, exemplars)
        return pid

    def push_exemplars(self, pid: str, embs: np.ndarray, cap: int = EXEMPLAR_CAP) -> None:
        """Append exemplars to a person's ring and recompute its centroid.

        Parameters
        ----------
        pid : str
            The person id to reinforce.
        embs : numpy.ndarray
            An ``(n, EMB_DIM)`` matrix of new embeddings; each row is
            L2-normalised before insertion.
        cap : int, optional
            Maximum number of exemplars to keep per person. Defaults to
            :data:`EXEMPLAR_CAP`.

        Returns
        -------
        None

        Notes
        -----
        The ring is bounded: after inserting, exemplars beyond the ``cap`` most
        recent (by timestamp) are deleted. The centroid is then recomputed as
        the L2-normalised mean of the *surviving* exemplars, so it always
        reflects the current ring rather than the raw input.
        """
        embs = l2_rows(embs)
        now = time.time()
        self.db.executemany(
            "INSERT INTO exemplar(person_id,emb,source,ts) VALUES(?,?,?,?)",
            [(pid, self._blob(e), "run", now) for e in embs],
        )
        # bounded ring: drop the oldest exemplars beyond `cap`
        ids = [
            r[0]
            for r in self.db.execute(
                "SELECT rowid FROM exemplar WHERE person_id=? ORDER BY ts DESC", (pid,)
            ).fetchall()
        ]
        for rid in ids[cap:]:
            self.db.execute("DELETE FROM exemplar WHERE rowid=?", (rid,))
        # recompute the centroid from the surviving exemplars only
        ex = self.exemplars(pid)
        if len(ex):
            self.db.execute(
                "UPDATE person SET centroid=?, n_exemplars=?, updated_at=? WHERE id=?",
                (self._blob(l2(ex.mean(0))), len(ex), now, pid),
            )
        self.db.commit()

    def delete(self, pid: str) -> None:
        """Forget a person: delete their exemplars and their record.

        Parameters
        ----------
        pid : str
            The person id to delete.

        Returns
        -------
        None

        Notes
        -----
        Because voiceprints are biometric, this is the primary erasure path and
        removes both the exemplar rows and the ``person`` row for ``pid``.
        """
        self.db.execute("DELETE FROM exemplar WHERE person_id=?", (pid,))
        self.db.execute("DELETE FROM person WHERE id=?", (pid,))
        self.db.commit()

    def close(self) -> None:
        """Close the underlying SQLite connection.

        Returns
        -------
        None
        """
        self.db.close()


# --------------------------------------------------------------------------- #
# matching
# --------------------------------------------------------------------------- #
def cluster_centroids_raw(X: np.ndarray, labels: np.ndarray) -> dict[int, np.ndarray]:
    """Compute one RAW-space centroid per cluster from checkpoint embeddings.

    Parameters
    ----------
    X : numpy.ndarray
        The checkpoint's ``X`` — raw TitaNet embeddings of shape
        ``(n, EMB_DIM)``. Rows are L2-normalised here before averaging.
    labels : numpy.ndarray
        Per-row integer cluster labels of length ``n``. Negative labels (e.g.
        ``-1`` for noise/unassigned) are ignored.

    Returns
    -------
    dict of {int : numpy.ndarray}
        Mapping ``{cluster_k: unit-norm centroid}`` for each non-negative
        cluster that has at least one row.

    Notes
    -----
    This deliberately uses the RAW L2-normalised space, NOT the per-recording
    *centered* space that ``diar_pipeline.cluster()`` uses to separate speakers
    within one file. Identity must live in the absolute space so that the same
    voice maps to comparable coordinates across different meetings; building
    centroids from the centered space would make them recording-relative and
    silently break cross-meeting matching.

    Examples
    --------
    >>> import numpy as np
    >>> X = np.eye(4, 192, dtype=np.float32)
    >>> cents = cluster_centroids_raw(X, np.array([0, 0, 1, 1]))
    >>> sorted(cents)
    [0, 1]
    """
    X = l2_rows(X)
    labels = np.asarray(labels)
    cents: dict[int, np.ndarray] = {}
    for k in sorted(int(l) for l in set(labels.tolist()) if l >= 0):
        rows = X[labels == k]
        if len(rows):
            cents[k] = l2(rows.mean(0))
    return cents


def _apply_global_mean(vecs: dict, gmean: np.ndarray | None) -> dict:
    """Optionally subtract the global mean and re-normalise a set of vectors.

    Parameters
    ----------
    vecs : dict
        Mapping of ``{key: vector}`` to transform (keys may be cluster ids or
        person ids).
    gmean : numpy.ndarray or None
        The global-mean vector. When ``None`` the input is returned unchanged.

    Returns
    -------
    dict
        Either ``vecs`` unchanged (``gmean is None``) or a new mapping where
        each vector has had the unit global mean subtracted and been
        re-L2-normalised.

    Notes
    -----
    This is the optional absolute-space upgrade referenced by
    ``use_global_mean``; centering both sides identically preserves cosine
    comparability while removing a shared bias direction.
    """
    if gmean is None:
        return vecs
    g = l2(gmean)
    return {k: l2(v - g) for k, v in vecs.items()}


def identify(
    cluster_cents: dict[int, np.ndarray],
    people: dict[str, np.ndarray],
    tau_high: float = TAU_HIGH,
    tau_low: float = TAU_LOW,
    gmean: np.ndarray | None = None,
) -> dict[int, tuple]:
    """Assign clusters to enrolled people 1-to-1, gated by cosine thresholds.

    Parameters
    ----------
    cluster_cents : dict of {int : numpy.ndarray}
        Per-cluster raw-space centroids (typically from
        :func:`cluster_centroids_raw`).
    people : dict of {str : numpy.ndarray}
        Enrolled ``{person_id: centroid}`` targets (typically from
        :meth:`PeopleStore.centroids`).
    tau_high : float, optional
        Cosine at/above which a match is accepted as ``"auto"``. Defaults to
        :data:`TAU_HIGH`.
    tau_low : float, optional
        Cosine at/above which a match becomes a ``"suggest"`` (below it,
        ``"unknown"``). Defaults to :data:`TAU_LOW`.
    gmean : numpy.ndarray or None, optional
        Optional global mean to subtract from both sides before scoring.

    Returns
    -------
    dict of {int : tuple}
        Mapping ``{cluster_k: (person_id_or_None, score, mode)}`` where
        ``mode`` is one of ``"auto"``, ``"suggest"`` or ``"unknown"``. For
        ``"unknown"`` the person id is ``None``.

    Notes
    -----
    The optimal 1-to-1 assignment is found with the Hungarian algorithm
    (``scipy.optimize.linear_sum_assignment``) on the negated cosine
    similarity matrix, so each person is matched to at most one cluster. Any
    cluster left without a viable match (including when either side is empty)
    defaults to ``(None, 0.0, "unknown")``.
    """
    # Center both sides identically (or not at all) so cosine stays comparable.
    cc = _apply_global_mean(cluster_cents, gmean)
    pc = _apply_global_mean(people, gmean)
    ks, ps = list(cc), list(pc)
    out: dict[int, tuple] = {}
    if ks and ps:
        # Full cosine similarity matrix (clusters x people); vectors are unit-norm.
        S = np.array([[float(cc[k] @ pc[p]) for p in ps] for k in ks], dtype=np.float64)
        # Hungarian maximises total similarity by minimising the negation.
        ri, ci = linear_sum_assignment(-S)
        for i, j in zip(ri, ci, strict=False):
            s = float(S[i, j])
            mode = "auto" if s >= tau_high else ("suggest" if s >= tau_low else "unknown")
            out[ks[i]] = (ps[j] if mode != "unknown" else None, s, mode)
    for k in ks:  # clusters with no viable match
        out.setdefault(k, (None, 0.0, "unknown"))
    return out


def identify_recording(
    X: np.ndarray,
    labels: np.ndarray,
    store: PeopleStore,
    tau_high: float = TAU_HIGH,
    tau_low: float = TAU_LOW,
    use_global_mean: bool = False,
) -> dict[str, dict]:
    """Map a recording's clusters to enrolled people, end to end.

    Parameters
    ----------
    X : numpy.ndarray
        The checkpoint's raw embeddings, shape ``(n, EMB_DIM)``.
    labels : numpy.ndarray
        Per-row cluster labels of length ``n``.
    store : PeopleStore
        The on-device voiceprint store to match against.
    tau_high : float, optional
        Auto-assign threshold, defaults to :data:`TAU_HIGH`.
    tau_low : float, optional
        Suggest threshold, defaults to :data:`TAU_LOW`.
    use_global_mean : bool, optional
        Whether to apply the store's global mean before scoring, by default
        ``False``.

    Returns
    -------
    dict of {str : dict}
        Mapping ``{"S<k>": {"name", "person_id", "confidence", "mode"}}`` for
        every cluster. Unmatched clusters keep their positional label
        (``"S<k>"``) as the name and a ``None`` ``person_id``. ``confidence``
        is the cosine score rounded to 3 decimals.

    Notes
    -----
    Thin high-level wrapper that builds RAW cluster centroids
    (:func:`cluster_centroids_raw`), pulls the store centroids, runs
    :func:`identify`, and resolves matched ids back to display names.
    """
    cents = cluster_centroids_raw(X, labels)
    people = store.centroids()
    gmean = store.global_mean() if use_global_mean else None
    res = identify(cents, people, tau_high, tau_low, gmean)
    mapping: dict[str, dict] = {}
    for k, (pid, score, mode) in sorted(res.items()):
        name = store.get(pid)["name"] if pid else f"S{k}"
        mapping[f"S{k}"] = {
            "name": name,
            "person_id": pid,
            "confidence": round(score, 3),
            "mode": mode,
        }
    return mapping


# --------------------------------------------------------------------------- #
# enrollment
# --------------------------------------------------------------------------- #
def enroll_cluster(
    store: PeopleStore, X: np.ndarray, labels: np.ndarray, cluster_k: int, name: str, role: str = ""
) -> str:
    """Name a cluster once, creating a new person from its RAW embeddings.

    Parameters
    ----------
    store : PeopleStore
        The store to add the person to.
    X : numpy.ndarray
        The checkpoint's raw embeddings, shape ``(n, EMB_DIM)``; rows are
        L2-normalised here.
    labels : numpy.ndarray
        Per-row cluster labels of length ``n``.
    cluster_k : int
        The cluster index to enroll.
    name : str
        Display name to assign to the new person.
    role : str, optional
        Free-text role/label, by default ``""``.

    Returns
    -------
    str
        The unique id of the newly created person.

    Raises
    ------
    ValueError
        If the requested cluster has no usable embeddings.

    Notes
    -----
    The seed centroid and exemplars come from the RAW L2-normalised space (see
    :func:`cluster_centroids_raw`), and the store's global mean is refreshed
    afterwards so later ``use_global_mean`` matches stay consistent.
    """
    X = l2_rows(X)
    rows = X[np.asarray(labels) == cluster_k]
    if not len(rows):
        raise ValueError(f"cluster S{cluster_k} has no usable embeddings")
    pid = store.add_person(name=name, centroid=l2(rows.mean(0)), exemplars=rows, role=role)
    store.refresh_global_mean()
    return pid


def reinforce(
    store: PeopleStore, pid: str, X: np.ndarray, labels: np.ndarray, cluster_k: int
) -> None:
    """Refine an existing voiceprint from a confident later re-match.

    Parameters
    ----------
    store : PeopleStore
        The store holding the person.
    pid : str
        The id of the person to reinforce.
    X : numpy.ndarray
        The later recording's raw embeddings, shape ``(n, EMB_DIM)``; rows are
        L2-normalised here.
    labels : numpy.ndarray
        Per-row cluster labels of length ``n``.
    cluster_k : int
        The cluster (in the later recording) confidently matched to ``pid``.

    Returns
    -------
    None

    Notes
    -----
    A no-op when the cluster is empty. Otherwise the cluster's RAW exemplars
    are pushed onto the person's bounded ring (which recomputes the centroid)
    and the store's global mean is refreshed. This is how a voiceprint refines
    itself over time.
    """
    X = l2_rows(X)
    rows = X[np.asarray(labels) == cluster_k]
    if len(rows):
        store.push_exemplars(pid, rows)
        store.refresh_global_mean()


# --------------------------------------------------------------------------- #
# threshold calibration (optional, data-driven)
# --------------------------------------------------------------------------- #
def calibrate_thresholds(store: PeopleStore) -> tuple[float, float]:
    """Suggest ``(tau_low, tau_high)`` from the store's own exemplar statistics.

    Parameters
    ----------
    store : PeopleStore
        The store whose exemplars/centroids drive the estimate.

    Returns
    -------
    tuple of (float, float)
        ``(tau_low, tau_high)`` rounded to 3 decimals. When there are no
        exemplars, the module defaults ``(TAU_LOW, TAU_HIGH)`` are returned.

    Notes
    -----
    The heuristic is intentionally conservative:

    * ``tau_high`` is the 10th percentile of the *intra*-person cosine
      similarities (exemplar-to-own-centroid), clipped to ``[0.4, 0.9]`` — high
      enough that most genuine re-matches clear it.
    * ``tau_low`` is the midpoint of the mean intra and mean *inter*-person
      similarities, clipped to ``[0.3, tau_high - 0.05]`` — a soft floor for the
      "suggest" band that stays strictly below ``tau_high``.

    Calling this on an empty store returns the module defaults unchanged, so it
    is always safe to invoke even before any enrolment.
    """
    people = store.all_people()
    intra: list[float] = []
    inter: list[float] = []
    cents = store.centroids()
    for p in people:
        ex = l2_rows(store.exemplars(p["id"]))
        c = cents[p["id"]]
        intra += [float(e @ c) for e in ex]
    ids = list(cents)
    for a in range(len(ids)):
        for b in range(a + 1, len(ids)):
            inter.append(float(cents[ids[a]] @ cents[ids[b]]))
    if not intra:
        return TAU_LOW, TAU_HIGH
    intra_arr = np.array(intra)
    inter_arr = np.array(inter) if inter else np.array([0.0])
    # conservative: high = 10th percentile of intra ; low = midpoint intra/inter
    tau_high = float(np.clip(np.percentile(intra_arr, 10), 0.4, 0.9))
    tau_low = float(np.clip((intra_arr.mean() + inter_arr.mean()) / 2, 0.3, tau_high - 0.05))
    return round(tau_low, 3), round(tau_high, 3)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _load_ckpt(path: str) -> tuple[np.ndarray, np.ndarray]:
    """Load ``X`` and ``labels`` arrays from a diar checkpoint ``.npz``.

    Parameters
    ----------
    path : str
        Path to the ``.npz`` checkpoint produced by ``diar_pipeline``.

    Returns
    -------
    tuple of (numpy.ndarray, numpy.ndarray)
        The ``X`` embedding matrix and the ``labels`` array.

    Raises
    ------
    SystemExit
        If the checkpoint has no ``labels`` array (i.e. diarisation has not run
        yet).
    """
    d = np.load(path, allow_pickle=True)
    if "labels" not in d:
        raise SystemExit(f"{path} has no `labels` — run diar_pipeline first")
    return d["X"], d["labels"]


def main() -> None:
    """Run the on-device identity CLI (identify / enroll / list / forget / calibrate).

    Returns
    -------
    None

    Notes
    -----
    This is a user-facing command-line entry point. The ``print(...)`` calls
    below are program *output* for the operator (the enrolled confirmation, the
    ``list`` rows, the mapping table, the calibration suggestion) — not
    diagnostic logging — so they are intentionally kept as ``print``.
    """
    ap = argparse.ArgumentParser(description="notes-helper on-device speaker identity")
    ap.add_argument("--db", default=DEFAULT_DB)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("identify", help="match clusters against the store")
    p.add_argument("checkpoint")
    p.add_argument("--out", default="speaker_mapping.json")
    p.add_argument("--global-mean", action="store_true")

    p = sub.add_parser("enroll", help="name a cluster once")
    p.add_argument("checkpoint")
    p.add_argument("--cluster", required=True, help="e.g. S0")
    p.add_argument("--name", required=True)
    p.add_argument("--role", default="")

    sub.add_parser("list", help="list enrolled people")
    p = sub.add_parser("forget", help="delete a person")
    p.add_argument("person_id")
    sub.add_parser("calibrate", help="suggest thresholds from the store")

    a = ap.parse_args()
    store = PeopleStore(a.db)

    if a.cmd == "identify":
        X, labels = _load_ckpt(a.checkpoint)
        mp = identify_recording(X, labels, store, use_global_mean=a.global_mean)
        with open(a.out, "w") as f:
            json.dump(
                {"mapping": {k: v["name"] for k, v in mp.items()}, "detail": mp},
                f,
                ensure_ascii=False,
                indent=2,
            )
        # Program output: the resolved mapping table for the operator.
        for k, v in mp.items():
            print(f"  {k} -> {v['name']:24s} conf={v['confidence']:.3f} ({v['mode']})")
        print(f"wrote {a.out}")

    elif a.cmd == "enroll":
        X, labels = _load_ckpt(a.checkpoint)
        k = int(a.cluster.lstrip("Ss"))
        pid = enroll_cluster(store, X, labels, k, a.name, a.role)
        # Program output: confirmation of the enrolment.
        print(f"enrolled {a.name} as '{pid}' from cluster {a.cluster}")

    elif a.cmd == "list":
        # Program output: one row per enrolled person.
        for p in store.all_people():
            print(f"  {p['id']:24s} {p['name']:24s} {p['role']:16s} ({p['n_exemplars']} exemplars)")

    elif a.cmd == "forget":
        store.delete(a.person_id)
        # Program output: confirmation of the deletion.
        print(f"forgot {a.person_id}")

    elif a.cmd == "calibrate":
        lo, hi = calibrate_thresholds(store)
        # Program output: the suggested thresholds vs the current defaults.
        print(f"suggested TAU_LOW={lo}  TAU_HIGH={hi}  (defaults {TAU_LOW}/{TAU_HIGH})")

    store.close()


if __name__ == "__main__":
    main()
