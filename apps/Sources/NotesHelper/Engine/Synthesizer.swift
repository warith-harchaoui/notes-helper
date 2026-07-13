//
//  Synthesizer.swift
//  NotesHelper
//
//  Turns a transcript into a structured summary. A local MLX model is the
//  quality path (guarded by `#if canImport(MLXLLM)`); a deterministic heuristic
//  synthesizer is always available so the pipeline never blocks and never leaves
//  the device. Mirrors the Python `synth.py` fallback.
//
//  Author: Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
//

import Foundation

/// Produces a ``Synthesis`` from a transcript.
protocol Synthesizer {
    /// Summarise a transcript.
    ///
    /// - Parameters:
    ///   - transcript: Ordered utterances.
    ///   - speakers: Speaker id → info.
    ///   - meta: Meeting metadata (title/date/place/duration).
    ///   - language: Output language code.
    /// - Returns: The structured ``Synthesis``.
    func summarize(transcript: [Utterance],
                   speakers: [String: SpeakerInfo],
                   meta: Meta,
                   language: String) async throws -> Synthesis
}

/// Factory for the best available synthesizer.
enum Synth {
    /// Build a synthesizer — MLX when available, else the heuristic fallback.
    static func make() -> Synthesizer {
        #if canImport(MLXLLM)
        if let mlx = MLXSynthesizer() { return mlx }
        #endif
        return HeuristicSynthesizer()
    }
}

/// No-LLM fallback: never blocks the report; clearly minimal. Builds coarse
/// chapters from the transcript so the timeline is still useful.
struct HeuristicSynthesizer: Synthesizer {
    func summarize(transcript: [Utterance],
                   speakers: [String: SpeakerInfo],
                   meta: Meta,
                   language: String) async throws -> Synthesis {
        let note = language == "en"
            ? "(Local summary unavailable — no on-device model. Transcript and diarization are complete below.)"
            : "(Synthèse locale indisponible — aucun modèle sur l'appareil. Transcription et diarisation restent complètes ci-dessous.)"

        // One chapter roughly every eighth of the transcript.
        var chapters: [Chapter] = []
        if !transcript.isEmpty {
            let step = max(1, transcript.count / 8)
            var i = 0
            while i < transcript.count {
                let u = transcript[i]
                chapters.append(Chapter(t: u.t0, titre: String(u.text.prefix(60)), resume: ""))
                i += step
            }
        }
        return Synthesis(meta: meta, speakers: speakers, resume: [note], pointsCles: [],
                         decisions: [], actions: [], chapitres: chapters, themes: [], citations: [])
    }
}

#if canImport(MLXLLM)
import MLXLLM

/// MLX-backed local LLM synthesizer (quality path).
///
/// - Note: Integration point — load a small instruction model (e.g. a 3–8B
///   quantised model) via MLXLLM, run the same map-reduce prompt as the Python
///   `synth.py`, and parse the JSON. Until the model is bundled/downloaded this
///   initialiser returns `nil` and the engine uses ``HeuristicSynthesizer``.
struct MLXSynthesizer: Synthesizer {
    init?() {
        // TODO: locate/load the MLX model container; return nil if unavailable.
        return nil
    }

    func summarize(transcript: [Utterance],
                   speakers: [String: SpeakerInfo],
                   meta: Meta,
                   language: String) async throws -> Synthesis {
        // TODO: map-reduce prompt over the transcript, parse strict JSON.
        try await HeuristicSynthesizer().summarize(
            transcript: transcript, speakers: speakers, meta: meta, language: language)
    }
}
#endif
