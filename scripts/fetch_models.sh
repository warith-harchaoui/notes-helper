#!/usr/bin/env bash
#
# fetch_models.sh — provision on-device models for the iOS app.
#
# Downloads the whisper.cpp ggml model into apps/Models/ (git-ignored, bundled by
# project.yml). TitaNet→CoreML and MLX are documented below.
#
# Usage:  scripts/fetch_models.sh [base|small|tiny|medium]   (default: base)
#
# Author: Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
set -euo pipefail

SIZE="${1:-base}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/apps/Models"
mkdir -p "$DEST"

# --- 1. whisper.cpp ASR (required for transcription) ---------------------------
URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-${SIZE}.bin"
echo "→ whisper ggml-${SIZE}.bin"
curl -L --fail -o "$DEST/ggml-${SIZE}.bin" "$URL"
echo "  saved to apps/Models/ggml-${SIZE}.bin ($(du -h "$DEST/ggml-${SIZE}.bin" | cut -f1))"

cat <<'NOTE'

# --- 2. TitaNet → CoreML (optional, diarization quality) ---------------------
Run: python scripts/convert_titanet_coreml.py --out apps/Models/SpeakerEmbedder.mlmodelc
(requires nemo_toolkit + coremltools; see that script's header.)

# --- 3. MLX synthesis model (optional, summary quality) ----------------------
Resolved at runtime by model id, not bundled. Suggested:
  mlx-community/Qwen2.5-3B-Instruct-4bit
Wire it into MLXSynthesizer (apps/Sources/NotesHelper/Engine/Synthesizer.swift).

Large models? Point HF_HOME / downloads at an external volume (e.g. /Volumes/LaCie).
NOTE
