//
//  VoiceActivityDetector.swift
//  NotesHelper
//
//  Energy-based voice-activity detection: splits a waveform into voiced
//  segments. This is the dependency-free baseline; a Silero-VAD CoreML model
//  can be dropped in later for parity with the Python pipeline (see Notes).
//
//  Author: Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
//

import Foundation

/// A voiced span, in seconds.
struct VoicedSegment: Hashable {
    let start: Double
    let end: Double
}

/// Detects voiced segments from a 16 kHz mono signal.
///
/// - Note: The threshold adapts to the recording (a fraction of the frame-energy
///   distribution), which is robust to overall gain. For far-field or noisy
///   audio, swap in a Silero-VAD CoreML model behind the same ``detect(_:)`` API.
struct VoiceActivityDetector {
    /// Frame length in seconds.
    var frameSeconds: Double = 0.030
    /// Hop length in seconds.
    var hopSeconds: Double = 0.010
    /// Minimum voiced-segment duration to keep (seconds).
    var minSegment: Double = 0.30
    /// Bridge gaps shorter than this between voiced frames (seconds).
    var maxGap: Double = 0.20
    /// Voiced threshold as a quantile of the frame-RMS distribution.
    var energyQuantile: Double = 0.5
    /// Multiplier applied to the quantile energy to set the voiced threshold.
    var thresholdScale: Float = 1.4

    /// Detect voiced segments in `signal`.
    ///
    /// - Parameter signal: Mono samples at ``DSP/sampleRate``.
    /// - Returns: Non-overlapping voiced segments, ordered by time.
    func detect(_ signal: [Float]) -> [VoicedSegment] {
        let sr = Double(DSP.sampleRate)
        let frame = Int(frameSeconds * sr)
        let hop = Int(hopSeconds * sr)
        let frames = DSP.frames(signal, frame: frame, hop: hop)
        guard !frames.isEmpty else { return [] }

        let energies = frames.map { DSP.rms($0) }
        let threshold = adaptiveThreshold(energies)

        // Mark voiced frames, then merge runs (bridging short gaps).
        let hopSec = Double(hop) / sr
        let frameSec = Double(frame) / sr
        var segments: [VoicedSegment] = []
        var runStart: Int? = nil
        var lastVoiced: Int = -1
        let gapFrames = Int(maxGap / hopSeconds)

        for (i, e) in energies.enumerated() {
            if e >= threshold {
                if runStart == nil { runStart = i }
                lastVoiced = i
            } else if let s = runStart, i - lastVoiced > gapFrames {
                segments.append(segment(fromFrame: s, toFrame: lastVoiced, hopSec: hopSec, frameSec: frameSec))
                runStart = nil
            }
        }
        if let s = runStart {
            segments.append(segment(fromFrame: s, toFrame: lastVoiced, hopSec: hopSec, frameSec: frameSec))
        }
        return segments.filter { $0.end - $0.start >= minSegment }
    }

    /// Threshold = `thresholdScale ×` the `energyQuantile` of frame energies.
    private func adaptiveThreshold(_ energies: [Float]) -> Float {
        let sorted = energies.sorted()
        let idx = min(sorted.count - 1, max(0, Int(Double(sorted.count) * energyQuantile)))
        return sorted[idx] * thresholdScale
    }

    private func segment(fromFrame s: Int, toFrame e: Int, hopSec: Double, frameSec: Double) -> VoicedSegment {
        VoicedSegment(start: Double(s) * hopSec, end: Double(e) * hopSec + frameSec)
    }
}
