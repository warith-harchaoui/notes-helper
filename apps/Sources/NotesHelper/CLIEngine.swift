//
//  CLIEngine.swift
//  NotesHelper (macOS)
//
//  macOS engine: drives the locally-installed Python `notes-helper` command-line tool
//  through `Process`. This reuses the exact same on-device pipeline as the CLI
//  (VAD → diarization → ASR → local LLM → report), so the desktop app inherits
//  every sovereignty guarantee for free — nothing here opens a network socket.
//
//  Author: Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
//

#if os(macOS)
import Foundation

/// Runs the local `notes-helper` CLI as a subprocess and locates its output.
struct CLIEngine: NotesHelperEngine {

    /// How to invoke notes-helper: either a resolved `notes-helper` binary, or a Python
    /// interpreter with `-m notes_helper.cli`.
    private struct Invocation {
        let launchPath: String
        let baseArgs: [String]
    }

    /// Resolve how to launch notes-helper on this machine.
    ///
    /// GUI apps inherit a minimal `PATH`, so we probe common install locations
    /// (a user override in `UserDefaults`, Homebrew, `~/.local/bin`) for a
    /// `notes-helper` binary, then fall back to a discovered `python3 -m notes_helper.cli`.
    ///
    /// - Throws: ``EngineError/executableNotFound(_:)`` when nothing works.
    private func resolveInvocation() throws -> Invocation {
        let fm = FileManager.default
        let home = fm.homeDirectoryForCurrentUser.path

        // 1) explicit user override, then common `notes-helper` binary locations.
        var candidates: [String] = []
        if let override = UserDefaults.standard.string(forKey: "notesHelperCLIPath") {
            candidates.append(override)
        }
        candidates += ["/opt/homebrew/bin/notes-helper", "/usr/local/bin/notes-helper",
                       "\(home)/.local/bin/notes-helper"]
        for path in candidates where fm.isExecutableFile(atPath: path) {
            return Invocation(launchPath: path, baseArgs: [])
        }

        // 2) fall back to a Python interpreter running the module.
        for py in ["/opt/homebrew/bin/python3", "/usr/local/bin/python3",
                   "\(home)/miniconda3/bin/python3", "/usr/bin/python3"]
        where fm.isExecutableFile(atPath: py) {
            return Invocation(launchPath: py, baseArgs: ["-m", "notes_helper.cli"])
        }

        throw EngineError.executableNotFound(
            "Installez-le puis, si besoin, indiquez son chemin dans les réglages "
            + "(`pip install notes-helper`).")
    }

    /// Run one notes-helper subcommand, throwing on a non-zero exit.
    ///
    /// - Parameters:
    ///   - inv: Resolved invocation.
    ///   - step: Human-readable step name (for error messages).
    ///   - args: Subcommand arguments appended to ``Invocation/baseArgs``.
    /// - Throws: ``EngineError/processFailed(step:code:stderr:)``.
    private func run(_ inv: Invocation, step: String, _ args: [String]) async throws {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: inv.launchPath)
        process.arguments = inv.baseArgs + args
        let errPipe = Pipe()
        process.standardError = errPipe
        process.standardOutput = Pipe()  // discard stdout; artifacts live on disk

        try process.run()
        // Read stderr off the run so a chatty process never deadlocks the pipe.
        let errData = errPipe.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()

        if process.terminationStatus != 0 {
            let stderr = String(data: errData, encoding: .utf8) ?? ""
            throw EngineError.processFailed(step: step,
                                            code: process.terminationStatus,
                                            stderr: stderr)
        }
    }

    func process(audio: URL,
                 outputDir: URL,
                 language: String,
                 progress: @escaping @MainActor @Sendable (EngineStage) -> Void) async throws -> NotesHelperResult {
        let inv = try resolveInvocation()
        try FileManager.default.createDirectory(at: outputDir, withIntermediateDirectories: true)
        let out = outputDir.path

        // The CLI already does diarize → identify → ASR inside `run`; we surface
        // coarse stages around the three subcommands.
        await MainActor.run { progress(.diarizing) }
        try await run(inv, step: "run", ["run", audio.path, "--out", out, "--lang", language])

        await MainActor.run { progress(.synthesizing) }
        // synth may fail if Ollama is down; notes-helper writes a heuristic synthese and
        // still exits 0, so a failure here is a real error worth surfacing.
        try await run(inv, step: "synth", ["synth", out, "--lang", language])

        await MainActor.run { progress(.rendering) }
        try await run(inv, step: "report", ["report", out, "--format", "html,md"])

        await MainActor.run { progress(.done) }
        let report = outputDir.appendingPathComponent("report.html")
        return NotesHelperResult(
            outputDir: outputDir,
            transcriptURL: outputDir.appendingPathComponent("transcript.json"),
            reportHTMLURL: FileManager.default.fileExists(atPath: report.path) ? report : nil)
    }
}
#endif
