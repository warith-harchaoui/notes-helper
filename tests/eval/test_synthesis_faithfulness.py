"""
AI-eval: summary faithfulness with DeepEval and a *local* judge.

Module summary
--------------
Rule-15 evaluation layer for Notes Helper's generative step. Deterministic unit tests
(``tests/test_verify.py``) already gate citation grounding cheaply on every
commit; this module adds the deeper, model-judged *faithfulness* check with
`DeepEval <https://github.com/confident-ai/deepeval>`_.

To honour the sovereignty thesis, the judge must be **local** — configure Ollama
as the DeepEval model before running::

    deepeval set-ollama qwen2.5:32b

Then run the opt-in, slow suite::

    NOTES_HELPER_RUN_EVAL=1 pytest -q -m slow tests/eval/

The suite is skipped by default (no ``NOTES_HELPER_RUN_EVAL``) and whenever DeepEval
is not installed or its judge is not configured, so the fast CI lane never
depends on a model and never burns tokens. Thresholds live in
``tests/eval/dataset.json`` (versioned golden dataset).

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# Opt-in gate: this suite runs only when explicitly requested, so per-commit CI
# stays fast, deterministic, and token-free (rule-15 cost control).
pytestmark = pytest.mark.slow

_DATASET = Path(__file__).with_name("dataset.json")


def _load_cases() -> list[dict]:
    """Load the versioned golden cases from ``dataset.json``.

    Returns
    -------
    list of dict
        Each case carries a ``transcript``, a candidate ``summary`` and either a
        ``min_faithfulness`` (must exceed) or ``max_faithfulness`` (must stay
        below, for hallucination negatives) threshold.
    """
    return json.loads(_DATASET.read_text(encoding="utf-8"))["cases"]


def _faithfulness(transcript: list[dict], summary: str) -> float:
    """Score a summary's faithfulness to a transcript with a local DeepEval judge.

    Parameters
    ----------
    transcript : list of dict
        Utterances used as the retrieval context.
    summary : str
        Candidate summary to score.

    Returns
    -------
    float
        DeepEval faithfulness score in ``[0, 1]``.

    Raises
    ------
    pytest.skip.Exception
        If DeepEval is unavailable or its (local) judge is not configured — the
        eval is optional infrastructure, never a hard failure of the fast lane.
    """
    deepeval_metrics = pytest.importorskip("deepeval.metrics")
    deepeval_cases = pytest.importorskip("deepeval.test_case")

    context = [f"{u['speaker']}: {u['text']}" for u in transcript]
    case = deepeval_cases.LLMTestCase(
        input="Résume fidèlement la réunion, sans rien inventer.",
        actual_output=summary,
        retrieval_context=context,
    )
    # Uses whatever judge DeepEval is configured with — configure a LOCAL one
    # (`deepeval set-ollama ...`) to keep everything on-device.
    metric = deepeval_metrics.FaithfulnessMetric(threshold=0.7)
    try:
        metric.measure(case)
    except Exception as exc:  # judge not configured / local model unreachable
        pytest.skip(f"DeepEval judge unavailable (configure a local judge): {exc}")
    return float(metric.score)


@pytest.mark.skipif(
    os.environ.get("NOTES_HELPER_RUN_EVAL") != "1",
    reason="set NOTES_HELPER_RUN_EVAL=1 (and a local DeepEval judge) to run AI evals",
)
@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["name"])
def test_summary_faithfulness(case: dict) -> None:
    """Each golden case must meet its faithfulness threshold with a local judge."""
    score = _faithfulness(case["transcript"], case["summary"])
    if "min_faithfulness" in case:
        assert score >= case["min_faithfulness"], (
            f"{case['name']}: faithfulness {score:.2f} < {case['min_faithfulness']}"
        )
    if "max_faithfulness" in case:
        # Hallucination negative: a fabricated summary must score LOW.
        assert score <= case["max_faithfulness"], (
            f"{case['name']}: hallucinated summary scored too high ({score:.2f})"
        )
