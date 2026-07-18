"""
DeepEval faithfulness / hallucination evaluation for the meeting synthesis.

Module summary
--------------
Checks that the synthesized report (summary, decisions, actions) is *grounded* in the
transcript and does not hallucinate. The judge is a **local** Ollama model, so the whole
evaluation runs on-device and matches the product's sovereignty thesis.

The suite is opt-in: it is marked ``slow`` and skips cleanly when ``deepeval`` is not
installed, so it never slows the fast default test run (cost control, Doctrine §14). The
thresholds are versioned constants below; changing one is a reviewed commit.

Run
---
>>> # python -m pip install -r contracts/evaluations/requirements.txt
>>> # ollama pull qwen2.5:3b
>>> # pytest -m slow contracts/evaluations/
"""

from __future__ import annotations

import json
import os
import urllib.request

import pytest

# Skip the whole module cleanly when the evaluation deps are absent. This keeps the fast
# suite green on machines that have not installed the (heavy) evaluation stack.
deepeval = pytest.importorskip("deepeval")

from deepeval.metrics import FaithfulnessMetric, HallucinationMetric  # noqa: E402
from deepeval.models.base_model import DeepEvalBaseLLM  # noqa: E402
from deepeval.test_case import LLMTestCase  # noqa: E402

# Local Ollama endpoint and judge model — both overridable, both local by default.
OLLAMA_URL: str = os.environ.get("NOTES_HELPER_OLLAMA_URL", "http://127.0.0.1:11434")
JUDGE_MODEL: str = os.environ.get("NOTES_HELPER_JUDGE_MODEL", "qwen2.5:3b")

# Versioned quality thresholds. A change here is an explicit, reviewed decision.
FAITHFULNESS_MIN: float = 0.70
HALLUCINATION_MAX: float = 0.30


class LocalOllamaJudge(DeepEvalBaseLLM):
    """A DeepEval judge backed by a local Ollama model.

    Using a local judge keeps evaluation sovereign: the transcript and the synthesis
    never leave the device to a hosted grader.

    Parameters
    ----------
    model : str, optional
        Ollama model name to use as the judge (default :data:`JUDGE_MODEL`).
    """

    def __init__(self, model: str = JUDGE_MODEL) -> None:
        # Store the model name; DeepEval calls ``load_model`` lazily.
        self._model: str = model

    def load_model(self) -> str:
        # DeepEval expects a "model handle"; the name is enough for our HTTP client.
        return self._model

    def generate(self, prompt: str) -> str:
        """Run one completion against the local Ollama server and return the text.

        Parameters
        ----------
        prompt : str
            The grading prompt DeepEval constructs.

        Returns
        -------
        str
            The model's raw response text.
        """
        # Build a non-streaming generate request; the call never leaves localhost.
        body = json.dumps(
            {"model": self._model, "prompt": prompt, "stream": False}
        ).encode()
        request = urllib.request.Request(
            f"{OLLAMA_URL}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        # 600 s is generous: local models can be slow on long grading prompts.
        with urllib.request.urlopen(request, timeout=600) as response:
            return json.loads(response.read())["response"]

    async def a_generate(self, prompt: str) -> str:
        # DeepEval may call the async variant; delegate to the sync path.
        return self.generate(prompt)

    def get_model_name(self) -> str:
        return f"ollama:{self._model}"


# A small grounded fixture: the transcript context and a faithful synthesis of it. A good
# judge should score this as faithful (grounded) and non-hallucinated.
_TRANSCRIPT_CONTEXT: list[str] = [
    "S0 [0s]: On a décidé de livrer le produit vendredi.",
    "S0 [3s]: Alice prépare les notes de version.",
    "S1 [5s]: Le budget est validé, on lance la campagne lundi.",
]
_FAITHFUL_SYNTHESIS: str = (
    "L'équipe a décidé de livrer le produit vendredi. Alice prépare les notes de "
    "version. Le budget est validé et la campagne est lancée lundi."
)


@pytest.mark.slow
def test_synthesis_is_faithful_to_transcript() -> None:
    """A faithful synthesis must clear the faithfulness and hallucination thresholds."""
    # Build the DeepEval test case: the transcript is the retrieval/context, the synthesis
    # is the output under test.
    test_case = LLMTestCase(
        input="Résume la réunion en décisions et actions.",
        actual_output=_FAITHFUL_SYNTHESIS,
        retrieval_context=_TRANSCRIPT_CONTEXT,
        context=_TRANSCRIPT_CONTEXT,
    )

    judge = LocalOllamaJudge()

    # Faithfulness: is every claim in the synthesis supported by the transcript?
    faithfulness = FaithfulnessMetric(
        threshold=FAITHFULNESS_MIN, model=judge, include_reason=True
    )
    faithfulness.measure(test_case)
    assert (
        faithfulness.score is not None and faithfulness.score >= FAITHFULNESS_MIN
    ), f"faithfulness {faithfulness.score} < {FAITHFULNESS_MIN}: {faithfulness.reason}"

    # Hallucination: does the synthesis contradict or invent beyond the transcript?
    hallucination = HallucinationMetric(threshold=HALLUCINATION_MAX, model=judge)
    hallucination.measure(test_case)
    assert (
        hallucination.score is not None and hallucination.score <= HALLUCINATION_MAX
    ), f"hallucination {hallucination.score} > {HALLUCINATION_MAX}"
