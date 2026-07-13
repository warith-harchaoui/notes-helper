//
//  SpeakerEmbedder.swift
//  NotesHelper
//
//  Turns a voiced audio segment into a fixed-length speaker embedding used for
//  diarization and cross-meeting identity. Two implementations:
//
//    - `DSPEmbedder`   : dependency-free acoustic features (always available).
//    - `CoreMLEmbedder`: a bundled TitaNet-style CoreML model (quality path).
//
//  The engine prefers CoreML when a model is bundled and falls back to the DSP
//  features otherwise, so the pipeline always runs on-device.
//
//  Author: Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
//

import CoreML
import Foundation

/// Produces an L2-normalised speaker embedding for a segment of samples.
protocol SpeakerEmbedder {
    /// Embed a slice of 16 kHz mono samples.
    ///
    /// - Parameter samples: The segment's samples.
    /// - Returns: An L2-normalised embedding (empty if the segment is too short).
    func embed(_ samples: ArraySlice<Float>) -> [Float]
}

/// Dependency-free embedder from time-domain acoustic statistics.
///
/// For each frame it computes RMS, zero-crossing rate, and short-lag normalised
/// autocorrelation (a coarse pitch/formant proxy), then aggregates mean and
/// standard deviation across frames. Crude next to TitaNet, but real and
/// deterministic — enough to separate distinct voices and to keep the pipeline
/// runnable before a CoreML model is bundled.
struct DSPEmbedder: SpeakerEmbedder {
    /// Autocorrelation lags (in samples) sampled per frame.
    private let lags = [40, 80, 120, 160]  // ~400/200/133/100 Hz at 16 kHz

    func embed(_ samples: ArraySlice<Float>) -> [Float] {
        let frame = Int(0.025 * Double(DSP.sampleRate))
        let hop = Int(0.010 * Double(DSP.sampleRate))
        let s = Array(samples)
        let frames = DSP.frames(s, frame: frame, hop: hop)
        guard frames.count >= 2 else { return [] }

        // Per-frame feature vectors: [rms, zcr, ac@lag...].
        var feats: [[Float]] = []
        feats.reserveCapacity(frames.count)
        for f in frames {
            var v: [Float] = [DSP.rms(f), DSP.zcr(f)]
            v.append(contentsOf: lags.map { autocorr(f, lag: $0) })
            feats.append(v)
        }

        // Aggregate mean + std across frames into one segment vector.
        let dim = feats[0].count
        var mean = [Float](repeating: 0, count: dim)
        for v in feats { for i in 0..<dim { mean[i] += v[i] } }
        for i in 0..<dim { mean[i] /= Float(feats.count) }
        var std = [Float](repeating: 0, count: dim)
        for v in feats { for i in 0..<dim { let d = v[i] - mean[i]; std[i] += d * d } }
        for i in 0..<dim { std[i] = (std[i] / Float(feats.count)).squareRoot() }

        return DSP.l2(mean + std)
    }

    /// Normalised autocorrelation of a frame at a given lag.
    private func autocorr(_ f: ArraySlice<Float>, lag: Int) -> Float {
        let a = Array(f)
        guard a.count > lag else { return 0 }
        var num: Float = 0, den: Float = 0
        for i in 0..<(a.count - lag) { num += a[i] * a[i + lag] }
        for i in 0..<a.count { den += a[i] * a[i] }
        return den > 1e-9 ? num / den : 0
    }
}

/// CoreML-backed embedder (e.g. TitaNet exported to CoreML).
///
/// - Note: Conversion (NeMo → CoreML via coremltools) and the model's exact
///   input/output feature names are the integration point. This loader expects
///   a compiled `SpeakerEmbedder.mlmodelc` in the app bundle, a single Float
///   MultiArray input, and a single Float MultiArray embedding output. If any of
///   that is missing it throws, and the engine falls back to ``DSPEmbedder``.
struct CoreMLEmbedder: SpeakerEmbedder {
    private let model: MLModel
    private let inputName: String
    private let outputName: String

    /// Load the bundled model, or return `nil` if it is not present.
    init?(modelName: String = "SpeakerEmbedder") {
        guard let url = Bundle.main.url(forResource: modelName, withExtension: "mlmodelc"),
              let m = try? MLModel(contentsOf: url) else { return nil }
        model = m
        // Pick the first (and expected only) input/output feature.
        guard let inName = m.modelDescription.inputDescriptionsByName.keys.first,
              let outName = m.modelDescription.outputDescriptionsByName.keys.first
        else { return nil }
        inputName = inName
        outputName = outName
    }

    func embed(_ samples: ArraySlice<Float>) -> [Float] {
        let arr = Array(samples)
        guard arr.count >= Int(0.25 * Double(DSP.sampleRate)),
              let input = try? MLMultiArray(shape: [NSNumber(value: arr.count)], dataType: .float32)
        else { return [] }
        for (i, v) in arr.enumerated() { input[i] = NSNumber(value: v) }
        guard let provider = try? MLDictionaryFeatureProvider(dictionary: [inputName: input]),
              let out = try? model.prediction(from: provider),
              let vec = out.featureValue(for: outputName)?.multiArrayValue
        else { return [] }
        var result = [Float](repeating: 0, count: vec.count)
        for i in 0..<vec.count { result[i] = vec[i].floatValue }
        return DSP.l2(result)
    }
}
