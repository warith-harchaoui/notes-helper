//
//  NativeEngine.swift
//  NotesHelper (iOS)
//
//  The fully-native, on-device engine — no Python, no subprocess, no network.
//  It chains the Swift components:
//
//    AudioDecoder → VAD → SpeakerEmbedder → Diarizer(cluster) → SpeechRecognizer
//                 → IdentityStore(match) → Synthesizer → ReportRenderer
//
//  ASR uses whisper.cpp (SwiftWhisper) and requires a bundled ggml model;
//  diarization uses a TitaNet CoreML model when bundled, else a DSP fallback;
//  synthesis uses MLX when available, else a heuristic. Everything stays on the
//  device — shipping with no network entitlement makes that OS-enforced.
//
//  Author: Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
//

#if os(iOS)
import Foundation

/// Native on-device engine for iOS.
struct NativeEngine: NotesHelperEngine {
    func process(audio: URL,
                 outputDir: URL,
                 language: String,
                 progress: @escaping @MainActor @Sendable (EngineStage) -> Void) async throws -> NotesHelperResult {
        let sr = Double(DSP.sampleRate)

        await MainActor.run { progress(.preparing) }
        let signal = try AudioDecoder.decode(audio)

        // --- diarization (CoreML embedder if a model is bundled, else DSP) ---
        await MainActor.run { progress(.diarizing) }
        let embedder: SpeakerEmbedder = CoreMLEmbedder() ?? DSPEmbedder()
        let diar = Diarizer().diarize(signal, embedder: embedder, nSpk: nil)

        // --- ASR per turn (whisper.cpp) ---
        await MainActor.run { progress(.transcribing) }
        guard let asr = ASR.make() else {
            throw EngineError.notImplemented(
                "Modèle de transcription requis : ajoutez la dépendance SwiftWhisper "
                + "et embarquez un modèle ggml (ex. ggml-base.bin).")
        }
        var utterances: [Utterance] = []
        for turn in diar.turns {
            let lo = max(0, Int(turn.t0 * sr)), hi = min(signal.count, Int(turn.t1 * sr))
            guard lo < hi else { continue }
            let text = try await asr.transcribe(Array(signal[lo..<hi]), language: language)
            if !text.isEmpty {
                utterances.append(Utterance(t0: round2(turn.t0), t1: round2(turn.t1),
                                            speaker: "S\(turn.speaker)", text: text))
            }
        }

        // --- cross-meeting identity (raw-space centroids) ---
        await MainActor.run { progress(.identifying) }
        let matches = IdentityStore().identify(rawCentroids(diar))
        var speakers: [String: SpeakerInfo] = [:]
        for (label, m) in matches { speakers["S\(label)"] = SpeakerInfo(name: m.name, role: "") }
        for u in utterances where speakers[u.speaker] == nil {
            speakers[u.speaker] = SpeakerInfo(name: u.speaker, role: "")
        }

        // --- synthesis (MLX if available, else heuristic) ---
        await MainActor.run { progress(.synthesizing) }
        let meta = Meta(titre: audio.deletingPathExtension().lastPathComponent,
                        date: isoDate(), horaire: "", lieu: "",
                        duree: hhmmss(utterances.last?.t1 ?? 0))
        let synthesis = try await Synth.make().summarize(
            transcript: utterances, speakers: speakers, meta: meta, language: language)

        // --- render report on-device ---
        await MainActor.run { progress(.rendering) }
        let reportURL = try ReportRenderer().write(dir: outputDir, transcript: utterances, synthesis: synthesis)

        await MainActor.run { progress(.done) }
        return NotesHelperResult(outputDir: outputDir,
                            transcriptURL: outputDir.appendingPathComponent("transcript.json"),
                            reportHTMLURL: reportURL)
    }

    // MARK: - Helpers

    /// Per-cluster centroid in the RAW embedding space (for cross-meeting identity).
    private func rawCentroids(_ diar: DiarizationResult) -> [Int: [Float]] {
        var byLabel: [Int: [[Float]]] = [:]
        for (i, label) in diar.labels.enumerated() where label >= 0 && !diar.embeddings[i].isEmpty {
            byLabel[label, default: []].append(diar.embeddings[i])
        }
        return byLabel.mapValues { DSP.l2(DSP.mean($0)) }
    }

    private func round2(_ x: Double) -> Double { (x * 100).rounded() / 100 }

    private func hhmmss(_ seconds: Double) -> String {
        let s = Int(seconds)
        return String(format: "%d:%02d:%02d", s / 3600, (s % 3600) / 60, s % 60)
    }

    private func isoDate() -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        return f.string(from: Date())
    }
}
#endif
