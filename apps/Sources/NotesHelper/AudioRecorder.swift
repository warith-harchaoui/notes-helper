//
//  AudioRecorder.swift
//  NotesHelper
//
//  Thin AVFoundation recorder that captures a local m4a file. Recording is the
//  only way audio enters NotesHelper besides importing a file; either way the bytes
//  stay on disk and are handed straight to the on-device engine.
//
//  Author: Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
//

import AVFoundation
import Foundation

/// Observable microphone recorder producing a 16 kHz mono m4a file.
@MainActor
final class AudioRecorder: ObservableObject {
    /// Whether a capture is currently in progress (drives the UI toggle).
    @Published private(set) var isRecording = false

    private var recorder: AVAudioRecorder?

    /// Request microphone permission on the current platform.
    ///
    /// - Returns: `true` if the user granted access.
    func requestPermission() async -> Bool {
        await withCheckedContinuation { continuation in
            #if os(iOS)
            AVAudioApplication.requestRecordPermission { continuation.resume(returning: $0) }
            #else
            AVCaptureDevice.requestAccess(for: .audio) { continuation.resume(returning: $0) }
            #endif
        }
    }

    /// Start recording into a fresh temporary file.
    ///
    /// - Returns: The URL being written to.
    /// - Throws: Any AVFoundation error setting up the session or recorder.
    @discardableResult
    func start() throws -> URL {
        #if os(iOS)
        // Configure the audio session for recording (iOS only; macOS has none).
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.record, mode: .default)
        try session.setActive(true)
        #endif

        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("notes-helper-\(UUID().uuidString).m4a")
        // 16 kHz mono AAC: matches the pipeline's working rate and stays small.
        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
            AVSampleRateKey: 16_000,
            AVNumberOfChannelsKey: 1,
            AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue,
        ]
        let rec = try AVAudioRecorder(url: url, settings: settings)
        rec.record()
        recorder = rec
        isRecording = true
        return url
    }

    /// Stop recording and return the finished file.
    ///
    /// - Returns: The recorded file URL, or `nil` if nothing was recording.
    @discardableResult
    func stop() -> URL? {
        guard let rec = recorder else { return nil }
        rec.stop()
        isRecording = false
        recorder = nil
        return rec.url
    }
}
