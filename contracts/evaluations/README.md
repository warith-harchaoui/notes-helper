# Evaluation layer (`contracts/evaluations/`)

The AI-evaluation layer required by the Engineering Doctrine (§14) and `CODING.md`
(rule 15). Evaluation lives in **Python** (Python explores and evaluates); the judge is a
**local** Ollama model so evaluation stays sovereign — nothing leaves the device.

## What is evaluated

| Target | Tool | Metrics | Versioned threshold |
|---|---|---|---|
| Meeting **synthesis** (summary/decisions/actions) | **DeepEval** | faithfulness, hallucination | faithfulness ≥ 0.70, hallucination ≤ 0.30 |
| **Plutchik emotions** (later) | DeepEval / rubric | agreement vs reference | TBD |
| LLM/ML robustness (later) | **Giskard** | robustness, bias, edge cases | TBD |
| **ASR** (later) | WER | vs golden corpus | TBD |
| **Diarization** (later) | DER | vs golden corpus | TBD |

Golden corpora live under `contracts/golden/` and `contracts/fixtures/`.

## Run (opt-in — heavy deps + local judge)

```bash
python -m pip install -r contracts/evaluations/requirements.txt
# a local Ollama server must be running with the judge model:
ollama pull qwen2.5:3b
NOTES_HELPER_JUDGE_MODEL=qwen2.5:3b pytest -m slow contracts/evaluations/
```

The suite is marked `slow` and skips cleanly when `deepeval` is not installed, so the fast
default test run is unaffected (cost control per the doctrine). Thresholds are versioned in
`eval_synthesis.py`; a threshold change is a reviewed commit.
