//
//  DSP.swift
//  NotesHelper
//
//  Small, dependency-free numeric helpers shared by the native engine
//  (normalisation, cosine similarity, framing, aggregation). Kept in one place
//  so the diarization, embedding, and clustering code reads clearly.
//
//  Author: Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
//

import Foundation

/// Numeric utilities operating on `[Float]` vectors.
enum DSP {
    /// The working sample rate of the whole pipeline (Hz).
    static let sampleRate: Int = 16_000

    /// L2-normalise a vector in place-safe fashion.
    ///
    /// - Parameter v: Input vector.
    /// - Returns: `v / ‖v‖` (or `v` unchanged if the norm is ~0).
    static func l2(_ v: [Float]) -> [Float] {
        let norm = (v.reduce(0) { $0 + $1 * $1 }).squareRoot()
        guard norm > 1e-9 else { return v }
        return v.map { $0 / norm }
    }

    /// Cosine similarity between two equal-length vectors.
    ///
    /// - Parameters:
    ///   - a: First vector.
    ///   - b: Second vector (same length as `a`).
    /// - Returns: Dot product of the L2-normalised inputs, in `[-1, 1]`.
    static func cosine(_ a: [Float], _ b: [Float]) -> Float {
        let na = l2(a), nb = l2(b)
        return zip(na, nb).reduce(0) { $0 + $1.0 * $1.1 }
    }

    /// Element-wise mean of a set of equal-length vectors.
    ///
    /// - Parameter vectors: Non-empty list of equal-length vectors.
    /// - Returns: Their centroid, or an empty array if `vectors` is empty.
    static func mean(_ vectors: [[Float]]) -> [Float] {
        guard let first = vectors.first else { return [] }
        var acc = [Float](repeating: 0, count: first.count)
        for v in vectors { for i in 0..<acc.count { acc[i] += v[i] } }
        let n = Float(vectors.count)
        return acc.map { $0 / n }
    }

    /// Split a signal into fixed frames.
    ///
    /// - Parameters:
    ///   - signal: The samples.
    ///   - frame: Frame length in samples.
    ///   - hop: Hop length in samples.
    /// - Returns: A list of frames (the trailing partial frame is dropped).
    static func frames(_ signal: [Float], frame: Int, hop: Int) -> [ArraySlice<Float>] {
        guard frame > 0, hop > 0, signal.count >= frame else { return [] }
        var out: [ArraySlice<Float>] = []
        var i = 0
        while i + frame <= signal.count {
            out.append(signal[i..<i + frame])
            i += hop
        }
        return out
    }

    /// Root-mean-square energy of a slice.
    static func rms(_ s: ArraySlice<Float>) -> Float {
        guard !s.isEmpty else { return 0 }
        let sum = s.reduce(0) { $0 + $1 * $1 }
        return (sum / Float(s.count)).squareRoot()
    }

    /// Zero-crossing rate of a slice (fraction of sign changes).
    static func zcr(_ s: ArraySlice<Float>) -> Float {
        guard s.count > 1 else { return 0 }
        var crossings = 0
        var prev = s[s.startIndex]
        for i in s.indices.dropFirst() {
            let cur = s[i]
            if (cur >= 0) != (prev >= 0) { crossings += 1 }
            prev = cur
        }
        return Float(crossings) / Float(s.count - 1)
    }
}
