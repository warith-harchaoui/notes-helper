//
//  Engine.swift
//  NotesHelper
//
//  The engine abstraction shared by both platforms. macOS drives the local
//  Python `notes-helper` CLI (`CLIEngine`); iOS uses a native on-device engine
//  (`NativeEngine`, WIP). Both honour the same contract, so the SwiftUI layer
//  never branches on platform.
//
//  Author: Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
//

import Foundation

/// Coarse pipeline stages, surfaced to the UI as human-readable progress.
enum EngineStage: String, CaseIterable {
    case preparing = "Préparation…"
    case diarizing = "Séparation des voix…"
    case transcribing = "Transcription…"
    case identifying = "Reconnaissance des interlocuteurs…"
    case synthesizing = "Synthèse locale…"
    case rendering = "Rendu du compte-rendu…"
    case done = "Terminé"
}

/// What the engine produces for one recording.
struct NotesHelperResult {
    /// The output directory holding every artifact.
    let outputDir: URL
    /// Path to `transcript.json`.
    let transcriptURL: URL
    /// Path to the self-contained `report.html`, if it was rendered.
    let reportHTMLURL: URL?
}

/// Errors an engine can raise, with user-facing messages.
enum EngineError: LocalizedError {
    case executableNotFound(String)
    case processFailed(step: String, code: Int32, stderr: String)
    case notImplemented(String)

    var errorDescription: String? {
        switch self {
        case .executableNotFound(let hint):
            return "Moteur `notes-helper` introuvable. \(hint)"
        case .processFailed(let step, let code, let stderr):
            return "Échec à l'étape « \(step) » (code \(code)).\n\(stderr)"
        case .notImplemented(let what):
            return what
        }
    }
}

/// The contract every platform engine fulfils: audio in, structured result out,
/// entirely on-device.
protocol NotesHelperEngine {
    /// Process one audio file into a diarized, summarised report.
    ///
    /// - Parameters:
    ///   - audio: Local audio file (m4a/mp3/wav/…).
    ///   - outputDir: Destination directory for all artifacts.
    ///   - language: Transcription/synthesis language code (e.g. `"fr"`).
    ///   - progress: Called on the main actor as stages advance.
    /// - Returns: The produced ``NotesHelperResult``.
    /// - Throws: ``EngineError`` on failure.
    func process(audio: URL,
                 outputDir: URL,
                 language: String,
                 progress: @escaping @MainActor @Sendable (EngineStage) -> Void) async throws -> NotesHelperResult
}

/// Build the engine appropriate for the current platform.
///
/// - Returns: ``CLIEngine`` on macOS (drives the local Python CLI), otherwise
///   ``NativeEngine`` (native on-device, WIP).
func makeEngine() -> NotesHelperEngine {
    #if os(macOS)
    return CLIEngine()
    #else
    return NativeEngine()
    #endif
}
