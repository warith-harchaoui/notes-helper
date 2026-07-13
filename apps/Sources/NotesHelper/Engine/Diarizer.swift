//
//  Diarizer.swift
//  NotesHelper
//
//  Speaker diarization in pure Swift: VAD → per-segment embeddings →
//  agglomerative clustering → merged turns. Mirrors the Python pipeline,
//  including the per-recording centering trick (subtract the mean embedding
//  before clustering) that removes the common channel/room component on
//  single-device far-field audio.
//
//  Author: Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
//

import Foundation

/// A merged same-speaker turn, in seconds.
struct Turn: Hashable {
    let t0: Double
    let t1: Double
    let speaker: Int
}

/// Result of diarizing one recording.
struct DiarizationResult {
    /// Voiced segments (aligned with `labels`/`embeddings`).
    let segments: [VoicedSegment]
    /// Cluster label per segment (`-1` = unusable/too-short).
    let labels: [Int]
    /// Raw L2-normalised embedding per segment (for cross-meeting identity).
    let embeddings: [[Float]]
    /// Same-speaker merged turns.
    let turns: [Turn]
}

/// Runs VAD + embedding + clustering + turn merging.
struct Diarizer {
    var mergeGap: Double = 0.8
    var maxTurn: Double = 28.0

    /// Diarize a full signal.
    ///
    /// - Parameters:
    ///   - signal: Mono 16 kHz samples.
    ///   - embedder: Speaker embedder (CoreML or DSP fallback).
    ///   - nSpk: Fixed speaker count, or `nil` to estimate.
    /// - Returns: The ``DiarizationResult``.
    func diarize(_ signal: [Float], embedder: SpeakerEmbedder, nSpk: Int? = nil) -> DiarizationResult {
        let sr = Double(DSP.sampleRate)
        let segments = VoiceActivityDetector().detect(signal)

        // Embed each segment; empty embedding => unusable.
        var embeddings: [[Float]] = []
        var usableIdx: [Int] = []
        for (i, seg) in segments.enumerated() {
            let lo = Int(seg.start * sr), hi = min(signal.count, Int(seg.end * sr))
            let emb = lo < hi ? embedder.embed(signal[lo..<hi]) : []
            embeddings.append(emb)
            if !emb.isEmpty { usableIdx.append(i) }
        }

        var labels = [Int](repeating: -1, count: segments.count)
        if usableIdx.count >= 2 {
            let usableEmb = usableIdx.map { embeddings[$0] }
            let sub = cluster(usableEmb, nSpk: nSpk)
            for (k, idx) in usableIdx.enumerated() { labels[idx] = sub[k] }
        } else if usableIdx.count == 1 {
            labels[usableIdx[0]] = 0
        }

        let turns = mergeTurns(segments, labels: labels)
        return DiarizationResult(segments: segments, labels: labels, embeddings: embeddings, turns: turns)
    }

    // MARK: - Clustering

    /// Cluster embeddings into speakers (centered cosine, average linkage).
    ///
    /// - Parameters:
    ///   - embeddings: Raw L2-normalised embeddings.
    ///   - nSpk: Target count, or `nil` to estimate by silhouette.
    /// - Returns: A label per input, relabelled `0..<k` by first appearance.
    func cluster(_ embeddings: [[Float]], nSpk: Int?) -> [Int] {
        let rows = embeddings.map { DSP.l2($0) }
        // Centering: remove the shared component, then renormalise.
        let mean = DSP.mean(rows)
        let centered = rows.map { row -> [Float] in
            DSP.l2(zip(row, mean).map { $0 - $1 })
        }
        let k = nSpk ?? estimateK(centered)
        let raw = agglomerative(centered, k: max(1, min(k, centered.count)))
        return relabelByFirstAppearance(raw)
    }

    /// Average-linkage agglomerative clustering to exactly `k` clusters.
    ///
    /// Uses the Lance-Williams update so merging is efficient. Distance is
    /// cosine distance (`1 - cosine`).
    private func agglomerative(_ points: [[Float]], k: Int) -> [Int] {
        let n = points.count
        if n == 0 { return [] }
        if k >= n { return Array(0..<n) }

        // Pairwise cosine distances.
        var d = [[Float]](repeating: [Float](repeating: 0, count: n), count: n)
        for i in 0..<n { for j in (i + 1)..<n {
            let dist = 1 - DSP.cosine(points[i], points[j])
            d[i][j] = dist; d[j][i] = dist
        } }

        var members: [[Int]] = (0..<n).map { [$0] }  // cluster -> member indices
        var alive = Array(repeating: true, count: n)
        var clusterDist = d                            // current cluster-cluster distances
        var count = n

        while count > k {
            // Find the closest live pair.
            var best = (a: -1, b: -1, dist: Float.greatestFiniteMagnitude)
            for i in 0..<n where alive[i] {
                for j in (i + 1)..<n where alive[j] {
                    if clusterDist[i][j] < best.dist { best = (i, j, clusterDist[i][j]) }
                }
            }
            guard best.a >= 0 else { break }
            let (a, b) = (best.a, best.b)
            let sizeA = Float(members[a].count), sizeB = Float(members[b].count)

            // Lance-Williams average-linkage update for the merged cluster `a`.
            for c in 0..<n where alive[c] && c != a && c != b {
                let merged = (sizeA * clusterDist[a][c] + sizeB * clusterDist[b][c]) / (sizeA + sizeB)
                clusterDist[a][c] = merged; clusterDist[c][a] = merged
            }
            members[a].append(contentsOf: members[b])
            alive[b] = false
            count -= 1
        }

        var labels = [Int](repeating: -1, count: n)
        var next = 0
        for c in 0..<n where alive[c] {
            for m in members[c] { labels[m] = next }
            next += 1
        }
        return labels
    }

    /// Estimate the speaker count by maximising the silhouette over 2…6.
    private func estimateK(_ points: [[Float]]) -> Int {
        let n = points.count
        if n < 3 { return max(1, n) }
        let hi = min(6, n - 1)
        var bestK = 2, bestScore = -Float.greatestFiniteMagnitude
        for k in 2...hi {
            let labels = agglomerative(points, k: k)
            let s = silhouette(points, labels: labels)
            if s > bestScore { bestScore = s; bestK = k }
        }
        return bestK
    }

    /// Mean silhouette score (cosine distance) for a labelling.
    private func silhouette(_ points: [[Float]], labels: [Int]) -> Float {
        let n = points.count
        let groups = Dictionary(grouping: 0..<n, by: { labels[$0] })
        if groups.count < 2 { return -1 }
        var total: Float = 0
        for i in 0..<n {
            let own = labels[i]
            let a = meanDist(i, to: groups[own] ?? [], points: points, excludeSelf: true)
            var b = Float.greatestFiniteMagnitude
            for (g, members) in groups where g != own {
                b = min(b, meanDist(i, to: members, points: points, excludeSelf: false))
            }
            let denom = max(a, b)
            total += denom > 0 ? (b - a) / denom : 0
        }
        return total / Float(n)
    }

    private func meanDist(_ i: Int, to members: [Int], points: [[Float]], excludeSelf: Bool) -> Float {
        var sum: Float = 0, cnt = 0
        for j in members where !(excludeSelf && j == i) {
            sum += 1 - DSP.cosine(points[i], points[j]); cnt += 1
        }
        return cnt > 0 ? sum / Float(cnt) : 0
    }

    private func relabelByFirstAppearance(_ labels: [Int]) -> [Int] {
        var remap: [Int: Int] = [:]
        var next = 0
        return labels.map { l in
            if l < 0 { return -1 }
            if let m = remap[l] { return m }
            remap[l] = next; defer { next += 1 }
            return next
        }
    }

    // MARK: - Turns

    /// Merge consecutive same-speaker segments into turns.
    private func mergeTurns(_ segments: [VoicedSegment], labels: [Int]) -> [Turn] {
        var turns: [Turn] = []
        for (seg, lab) in zip(segments, labels) where lab >= 0 {
            if let last = turns.last, last.speaker == lab,
               seg.start - last.t1 <= mergeGap, seg.end - last.t0 <= maxTurn {
                turns[turns.count - 1] = Turn(t0: last.t0, t1: seg.end, speaker: lab)
            } else {
                turns.append(Turn(t0: seg.start, t1: seg.end, speaker: lab))
            }
        }
        return turns
    }
}
