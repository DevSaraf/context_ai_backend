"""
clustering.py
=============
Groups raw `knowledge_chunks` into candidate procedures by clustering their
768-dim embeddings. A cluster = "a set of chunks that are all about the same
how-the-company-works topic" and becomes the input to one LLM extraction call.

Design choices for production:
  * Primary algorithm: HDBSCAN (density-based, finds cluster COUNT on its own
    and labels unrelated chunks as noise = -1, so we don't force junk together).
  * Fallback: a dependency-free greedy cosine-threshold clusterer, used when
    hdbscan isn't installed. This keeps deploys from breaking if the native
    HDBSCAN wheel fails to build on a given host.
  * All vectors are L2-normalized first so cosine == dot product and HDBSCAN's
    euclidean metric behaves like cosine distance.

This module is pure CPU/numpy and does NO database or network I/O — callers
pass in (chunk_id, embedding) pairs and get back {cluster_label: [chunk_ids]}.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Sequence, Tuple

import numpy as np

logger = logging.getLogger("krab.brain.clustering")

try:
    import hdbscan  # type: ignore

    _HAS_HDBSCAN = True
except Exception:  # pragma: no cover
    _HAS_HDBSCAN = False


def _normalize(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


def cluster_embeddings(
    items: Sequence[Tuple[int, Sequence[float]]],
    *,
    min_cluster_size: int = 3,
    min_samples: int | None = None,
    cosine_threshold: float = 0.62,
) -> Dict[int, List[int]]:
    """Cluster (chunk_id, embedding) pairs into topic groups.

    Args:
        items: sequence of (chunk_id, embedding_vector).
        min_cluster_size: smallest group we treat as a real procedure.
        min_samples: HDBSCAN conservativeness; defaults to min_cluster_size.
        cosine_threshold: similarity cut for the numpy fallback only.

    Returns:
        {cluster_label: [chunk_id, ...]} with noise (label -1) excluded.
        Labels are arbitrary positive ints.
    """
    if not items:
        return {}

    ids = [int(cid) for cid, _ in items]
    mat = np.asarray([list(vec) for _, vec in items], dtype=np.float32)
    if mat.ndim != 2 or mat.shape[0] != len(ids):
        raise ValueError("embeddings must be a 2-D matrix aligned with ids")

    mat = _normalize(mat)

    # Tiny corpora can't be meaningfully clustered: treat all as one group if
    # they're cohesive, else leave them out. This avoids HDBSCAN edge cases.
    if len(ids) < max(min_cluster_size, 3):
        return {0: ids} if _mean_cohesion(mat) >= cosine_threshold else {}

    if _HAS_HDBSCAN:
        labels = _hdbscan_labels(
            mat, min_cluster_size=min_cluster_size, min_samples=min_samples
        )
    else:
        logger.warning("hdbscan unavailable; using greedy cosine fallback")
        labels = _greedy_cosine_labels(
            mat, threshold=cosine_threshold, min_cluster_size=min_cluster_size
        )

    clusters: Dict[int, List[int]] = {}
    for cid, label in zip(ids, labels):
        if label < 0:  # noise
            continue
        clusters.setdefault(int(label), []).append(cid)

    # Drop clusters that ended up below the floor.
    return {k: v for k, v in clusters.items() if len(v) >= min_cluster_size}


def _hdbscan_labels(
    mat: np.ndarray, *, min_cluster_size: int, min_samples: int | None
) -> np.ndarray:
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=min_samples or min_cluster_size,
        metric="euclidean",  # on normalized vectors this tracks cosine
        cluster_selection_method="eom",
    )
    return clusterer.fit_predict(mat)


def _greedy_cosine_labels(
    mat: np.ndarray, *, threshold: float, min_cluster_size: int
) -> np.ndarray:
    """O(n^2) agglomerative-ish fallback. Fine for a few thousand chunks.

    Seeds a cluster from the first unassigned point, pulls in everything within
    `threshold` cosine similarity, repeats. Clusters below min_cluster_size are
    relabeled as noise (-1).
    """
    n = mat.shape[0]
    sim = mat @ mat.T  # cosine sim matrix (already normalized)
    labels = np.full(n, -1, dtype=int)
    current = 0
    for i in range(n):
        if labels[i] != -1:
            continue
        members = np.where(sim[i] >= threshold)[0]
        members = [m for m in members if labels[m] == -1]
        if len(members) >= min_cluster_size:
            for m in members:
                labels[m] = current
            current += 1
        else:
            labels[i] = -1
    return labels


def _mean_cohesion(mat: np.ndarray) -> float:
    if mat.shape[0] < 2:
        return 1.0
    sim = mat @ mat.T
    iu = np.triu_indices(mat.shape[0], k=1)
    return float(np.mean(sim[iu]))


def cluster_cohesion(embeddings: Sequence[Sequence[float]]) -> float:
    """Mean pairwise cosine similarity within a cluster, in [0, 1] (clamped).

    Used as the `cohesion_score` quality signal: a tight cluster yields a more
    trustworthy procedure than a loose one.
    """
    if not embeddings:
        return 0.0
    mat = _normalize(np.asarray([list(e) for e in embeddings], dtype=np.float32))
    return max(0.0, min(1.0, _mean_cohesion(mat)))
