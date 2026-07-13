//
//  SpeechRecognizer.swift
//  NotesHelper
//
//  On-device ASR via whisper.cpp (through the SwiftWhisper package). Guarded by
//  `#if canImport(SwiftWhisper)` so the app still compiles without the package;
//  when it is linked and a ggml model is bundled, transcription is fully native
//  and offline.
//
//  Author: Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
//

import Foundation

/// Transcribes a segment of 16 kHz mono samples into text.
protocol SpeechRecognizer {
    /// Transcribe one turn's samples.
    ///
    /// - Parameters:
    ///   - samples: Mono 16 kHz Float samples for the turn.
    ///   - language: Language code (e.g. `"fr"`).
    /// - Returns: The transcribed text (may be empty).
    func transcribe(_ samples: [Float], language: String) async throws -> String
}

/// Factory + model resolution for the ASR backend.
enum ASR {
    /// Build the best available recognizer, or `nil` if none can be created.
    ///
    /// - Returns: A whisper.cpp-backed recognizer when SwiftWhisper is linked and
    ///   a ggml model is bundled; otherwise `nil` (the engine then reports that a
    ///   model is required).
    static func make() -> SpeechRecognizer? {
        #if canImport(SwiftWhisper)
        if let url = modelURL() { return WhisperRecognizer(modelURL: url) }
        #endif
        return nil
    }

    /// Locate a bundled ggml whisper model.
    ///
    /// Looks for common tags in the app bundle. Ship one (e.g.
    /// `ggml-base.bin`) or download it on first launch into Application Support.
    static func modelURL() -> URL? {
        for tag in ["ggml-base", "ggml-small", "ggml-tiny", "ggml-medium"] {
            if let url = Bundle.main.url(forResource: tag, withExtension: "bin") { return url }
        }
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("NotesHelper/ggml-base.bin")
        return FileManager.default.fileExists(atPath: support.path) ? support : nil
    }
}

#if canImport(SwiftWhisper)
import SwiftWhisper

/// whisper.cpp-backed recognizer (native, offline).
final class WhisperRecognizer: SpeechRecognizer {
    private let whisper: Whisper

    /// - Parameter modelURL: Path to a ggml whisper model file.
    init(modelURL: URL) {
        whisper = Whisper(fromFileURL: modelURL)
    }

    func transcribe(_ samples: [Float], language: String) async throws -> String {
        // Bias decoding to the requested language when recognised.
        if let lang = WhisperLanguage(rawValue: language) {
            whisper.params.language = lang
        }
        let segments = try await whisper.transcribe(audioFrames: samples)
        return segments.map(\.text).joined().trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
#endif
