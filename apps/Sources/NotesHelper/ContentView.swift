//
//  ContentView.swift
//  NotesHelper
//
//  The single-screen UI: import or record audio, run the on-device pipeline with
//  live progress, then show the self-contained report. Deliberately minimal —
//  the point of NotesHelper is the guarantee, not chrome.
//
//  Author: Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
//

import SwiftUI
import UniformTypeIdentifiers

/// Root view driving capture → processing → report.
struct ContentView: View {
    @StateObject private var recorder = AudioRecorder()

    /// The audio queued for processing (imported or freshly recorded).
    @State private var selectedAudio: URL?
    @State private var isProcessing = false
    @State private var stage: EngineStage = .preparing
    @State private var reportURL: URL?
    @State private var errorText: String?
    @State private var showImporter = false

    /// Transcription/synthesis language, persisted across launches.
    @AppStorage("language") private var language = "fr"

    var body: some View {
        VStack(spacing: 16) {
            header

            if let reportURL {
                // Finished: show the report and a way to start over.
                ReportWebView(reportURL: reportURL)
                    .frame(minWidth: 360, minHeight: 360)
                Button("Nouveau compte-rendu") { reset() }
                    .accessibilityLabel("Commencer un nouveau compte-rendu")
            } else if isProcessing {
                ProgressView { Text(stage.rawValue) }
                    .padding()
            } else {
                controls
            }
        }
        .padding()
        .frame(minWidth: 420, minHeight: 480)
        .alert("Erreur", isPresented: .constant(errorText != nil)) {
            Button("OK") { errorText = nil }
        } message: {
            Text(errorText ?? "")
        }
        .fileImporter(isPresented: $showImporter, allowedContentTypes: [.audio]) { result in
            handleImport(result)
        }
    }

    // MARK: - Subviews

    /// Title + the sovereignty promise, always visible.
    private var header: some View {
        VStack(spacing: 4) {
            Text("NotesHelper").font(.largeTitle.bold())
            Text("Rien ne quitte votre appareil.")
                .font(.subheadline).foregroundStyle(.secondary)
        }
    }

    /// Import / record controls and the run button.
    private var controls: some View {
        VStack(spacing: 14) {
            Picker("Langue", selection: $language) {
                Text("Français").tag("fr")
                Text("English").tag("en")
            }
            .pickerStyle(.segmented)
            .frame(maxWidth: 240)

            Button {
                showImporter = true
            } label: {
                Label("Importer un fichier audio", systemImage: "square.and.arrow.down")
            }

            Button {
                Task { await toggleRecording() }
            } label: {
                Label(recorder.isRecording ? "Arrêter l'enregistrement" : "Enregistrer",
                      systemImage: recorder.isRecording ? "stop.circle.fill" : "mic.circle.fill")
            }
            .tint(recorder.isRecording ? .red : .accentColor)

            if let selectedAudio {
                Text(selectedAudio.lastPathComponent)
                    .font(.footnote).foregroundStyle(.secondary)
                Button {
                    Task { await process(selectedAudio) }
                } label: {
                    Label("Générer le compte-rendu", systemImage: "doc.text.magnifyingglass")
                        .frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
            }
        }
    }

    // MARK: - Actions

    /// Toggle microphone capture, requesting permission on first use.
    private func toggleRecording() async {
        if recorder.isRecording {
            selectedAudio = recorder.stop()
        } else {
            guard await recorder.requestPermission() else {
                errorText = "Accès au micro refusé."
                return
            }
            do { try recorder.start() } catch { errorText = error.localizedDescription }
        }
    }

    /// Copy a security-scoped imported file into a working location.
    private func handleImport(_ result: Result<URL, Error>) {
        switch result {
        case .success(let url):
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            let dest = FileManager.default.temporaryDirectory
                .appendingPathComponent(url.lastPathComponent)
            try? FileManager.default.removeItem(at: dest)
            do {
                try FileManager.default.copyItem(at: url, to: dest)
                selectedAudio = dest
            } catch {
                errorText = error.localizedDescription
            }
        case .failure(let error):
            errorText = error.localizedDescription
        }
    }

    /// Run the full engine over `audio`, updating progress and the report.
    private func process(_ audio: URL) async {
        isProcessing = true
        errorText = nil
        stage = .preparing
        let outputDir = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("NotesHelper/\(Int(Date().timeIntervalSince1970))")
        do {
            let result = try await makeEngine().process(
                audio: audio, outputDir: outputDir, language: language
            ) { newStage in stage = newStage }
            reportURL = result.reportHTMLURL
            if reportURL == nil { errorText = "Compte-rendu généré, mais report.html introuvable." }
        } catch {
            errorText = error.localizedDescription
        }
        isProcessing = false
    }

    /// Return to the capture screen.
    private func reset() {
        reportURL = nil
        selectedAudio = nil
        stage = .preparing
    }
}
