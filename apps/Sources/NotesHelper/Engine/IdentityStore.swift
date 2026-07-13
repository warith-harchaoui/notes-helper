//
//  IdentityStore.swift
//  NotesHelper
//
//  On-device speaker identity — "name once, known forever on your device".
//  Stores a voiceprint (a numeric embedding, never audio) per named person in a
//  local JSON file and matches new recordings' clusters against it by cosine
//  similarity. Mirrors the Python `identity.py`; identity lives in the RAW
//  embedding space (not the per-recording centered space used for clustering).
//
//  Author: Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
//

import Foundation

/// A named person and their accumulated voiceprint.
struct Person: Codable, Identifiable {
    let id: String
    var name: String
    var role: String
    /// L2-normalised mean embedding (raw space).
    var centroid: [Float]
    /// Number of exemplars folded into the centroid.
    var count: Int
}

/// How a cluster was matched to a person.
enum MatchMode: String { case auto, suggest, unknown }

/// A cluster→person match decision.
struct Match {
    let personID: String?
    let name: String
    let score: Float
    let mode: MatchMode
}

/// Local JSON-backed voiceprint store.
///
/// - Note: The file lives under Application Support and is never synced unless
///   the user opts in. Thresholds mirror the Python defaults; tune them for the
///   embedder in use (TitaNet vs the DSP fallback have different score scales).
final class IdentityStore {
    var tauHigh: Float = 0.62
    var tauLow: Float = 0.45

    private let url: URL
    private(set) var people: [Person]

    /// Open (or create) the store at a given location.
    ///
    /// - Parameter url: JSON file URL; defaults to
    ///   `…/Application Support/NotesHelper/people.json`.
    init(url: URL? = nil) {
        let resolved = url ?? Self.defaultURL()
        self.url = resolved
        if let data = try? Data(contentsOf: resolved),
           let loaded = try? JSONDecoder().decode([Person].self, from: data) {
            people = loaded
        } else {
            people = []
        }
    }

    private static func defaultURL() -> URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("NotesHelper", isDirectory: true)
        try? FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
        return base.appendingPathComponent("people.json")
    }

    private func save() {
        if let data = try? JSONEncoder().encode(people) { try? data.write(to: url) }
    }

    // MARK: - Matching

    /// Match a recording's clusters (raw centroids) to stored people.
    ///
    /// - Parameter clusterCentroids: `[label: raw L2-normalised centroid]`.
    /// - Returns: `[label: Match]`. Uses a greedy 1-to-1 assignment gated by the
    ///   thresholds; a person is never matched to two clusters in one meeting.
    func identify(_ clusterCentroids: [Int: [Float]]) -> [Int: Match] {
        var result: [Int: Match] = [:]
        var usedPeople = Set<String>()

        // Sort candidate (cluster, person, score) triples by descending score.
        var triples: [(label: Int, person: Person, score: Float)] = []
        for (label, centroid) in clusterCentroids {
            for p in people {
                triples.append((label, p, DSP.cosine(centroid, p.centroid)))
            }
        }
        triples.sort { $0.score > $1.score }

        for t in triples where result[t.label] == nil && !usedPeople.contains(t.person.id) {
            if t.score >= tauLow {
                usedPeople.insert(t.person.id)
                result[t.label] = Match(personID: t.person.id, name: t.person.name,
                                        score: t.score,
                                        mode: t.score >= tauHigh ? .auto : .suggest)
            }
        }
        // Any cluster still unmatched is unknown.
        for label in clusterCentroids.keys where result[label] == nil {
            result[label] = Match(personID: nil, name: "S\(label)", score: 0, mode: .unknown)
        }
        return result
    }

    // MARK: - Enrollment

    /// Create a person from a cluster centroid ("name once").
    ///
    /// - Parameters:
    ///   - name: Display name.
    ///   - centroid: The cluster's raw L2-normalised centroid.
    ///   - role: Optional role.
    /// - Returns: The new person's id.
    @discardableResult
    func enroll(name: String, centroid: [Float], role: String = "") -> String {
        let id = Self.slug(name, existing: Set(people.map(\.id)))
        people.append(Person(id: id, name: name, role: role,
                             centroid: DSP.l2(centroid), count: 1))
        save()
        return id
    }

    /// Fold a confirmed re-match into a person's running centroid (refinement).
    func reinforce(personID: String, with centroid: [Float]) {
        guard let idx = people.firstIndex(where: { $0.id == personID }) else { return }
        let n = Float(people[idx].count)
        let updated = zip(people[idx].centroid, DSP.l2(centroid)).map { ($0 * n + $1) / (n + 1) }
        people[idx].centroid = DSP.l2(updated)
        people[idx].count += 1
        save()
    }

    /// Delete a person (biometric hygiene).
    func forget(personID: String) {
        people.removeAll { $0.id == personID }
        save()
    }

    private static func slug(_ name: String, existing: Set<String>) -> String {
        let base = name.lowercased()
            .replacingOccurrences(of: "[^a-z0-9]+", with: "-", options: .regularExpression)
            .trimmingCharacters(in: CharacterSet(charactersIn: "-"))
        var candidate = base.isEmpty ? "person" : base
        var i = 2
        while existing.contains(candidate) { candidate = "\(base)-\(i)"; i += 1 }
        return candidate
    }
}
