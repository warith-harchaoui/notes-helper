#!/usr/bin/env python3
"""
Convert NVIDIA TitaNet (NeMo) to CoreML for on-device diarization.

Module summary
--------------
Exports the TitaNet speaker-embedding model to a CoreML package that the iOS app
loads via ``CoreMLEmbedder`` (``apps/Sources/NotesHelper/Engine/SpeakerEmbedder.swift``).
This is the *quality* diarization path; the app runs on a dependency-free DSP
embedder until this model is provided.

Honest caveat: NeMo models use custom modules, so a clean CoreML export is not
guaranteed and may need per-op massaging (or exporting to ONNX first, then
``coremltools`` from ONNX). Treat this as a starting scaffold, not a one-click
converter.

Usage example
-------------
>>> # python scripts/convert_titanet_coreml.py --out apps/Models/SpeakerEmbedder.mlpackage
>>> # (requires: pip install nemo_toolkit['asr'] coremltools torch)

Author
------
Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
"""
from __future__ import annotations

import argparse


def convert(out_path: str, model_name: str = "titanet_large", n_samples: int = 32000) -> None:
    """Export TitaNet to CoreML.

    Parameters
    ----------
    out_path : str
        Destination ``.mlpackage`` / ``.mlmodelc`` path.
    model_name : str, optional
        NeMo pretrained model name (default ``"titanet_large"``).
    n_samples : int, optional
        Example input length in samples (2 s at 16 kHz by default) used to trace
        the model.

    Raises
    ------
    SystemExit
        With guidance if ``nemo_toolkit`` / ``coremltools`` are missing, or if the
        trace/convert step fails (with the underlying error).
    """
    try:
        import coremltools as ct
        import torch
        from nemo.collections.asr.models import EncDecSpeakerLabelModel
    except ImportError as exc:  # dependency guidance rather than a bare crash
        raise SystemExit(
            "Missing dependency: install with\n"
            "  pip install 'nemo_toolkit[asr]' coremltools torch\n"
            f"(import error: {exc})") from exc

    print(f"→ loading NeMo {model_name} …")
    model = EncDecSpeakerLabelModel.from_pretrained(model_name).eval()

    # Trace on a single example waveform. TitaNet consumes (audio_signal, length).
    example = torch.randn(1, n_samples)

    class EmbeddingWrapper(torch.nn.Module):
        """Expose only the L2-normalised embedding for a fixed-length input."""

        def __init__(self, m: EncDecSpeakerLabelModel) -> None:
            super().__init__()
            self.m = m

        def forward(self, audio_signal: torch.Tensor) -> torch.Tensor:
            length = torch.tensor([audio_signal.shape[-1]])
            _, emb = self.m.forward(input_signal=audio_signal, input_signal_length=length)
            return torch.nn.functional.normalize(emb, dim=-1)

    wrapper = EmbeddingWrapper(model).eval()
    try:
        traced = torch.jit.trace(wrapper, example)
        mlmodel = ct.convert(
            traced,
            inputs=[ct.TensorType(name="audio", shape=(1, ct.RangeDim(8000, 480000)))],
            minimum_deployment_target=ct.target.iOS17,
        )
    except Exception as exc:  # noqa: BLE001 — surface the real conversion error
        raise SystemExit(
            f"Conversion failed ({exc}).\n"
            "TitaNet has custom ops; consider exporting to ONNX first "
            "(model.export('titanet.onnx')) then converting from ONNX, or a "
            "lighter embedder (ECAPA/WeSpeaker) with an existing CoreML export.") from exc

    mlmodel.save(out_path)
    print(f"✓ saved {out_path}")


def main() -> None:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description="Convert TitaNet (NeMo) to CoreML")
    ap.add_argument("--out", default="apps/Models/SpeakerEmbedder.mlpackage")
    ap.add_argument("--model", default="titanet_large")
    args = ap.parse_args()
    convert(args.out, model_name=args.model)


if __name__ == "__main__":
    main()
