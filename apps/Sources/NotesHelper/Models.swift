//
//  Models.swift
//  NotesHelper
//
//  Codable models mirroring the on-disk artifacts produced by the local
//  `notes-helper` engine (`transcript.json` and `synthese.json`). Keeping these in one
//  place lets the SwiftUI layer read exactly what the Python/native engine
//  writes, with no lossy re-parsing.
//
//  Author: Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
//

import Foundation

/// A single diarized utterance from `transcript.json`.
struct Utterance: Codable, Identifiable, Hashable {
    /// Stable identity for SwiftUI lists (start time + speaker is unique enough).
    var id: String { "\(t0)-\(speaker)" }
    /// Start time in seconds.
    let t0: Double
    /// End time in seconds.
    let t1: Double
    /// Diarization speaker id (e.g. `"S0"`), or a resolved name after identity.
    let speaker: String
    /// Transcribed text.
    let text: String
}

/// One named speaker in `synthese.json`'s `speakers` map.
struct SpeakerInfo: Codable, Hashable {
    /// Display name (falls back to the speaker id when unknown).
    let name: String
    /// Optional role/title.
    let role: String?
}

/// Meeting metadata block (`synthese.json` → `meta`).
struct Meta: Codable, Hashable {
    let titre: String
    let date: String?
    let horaire: String?
    let lieu: String?
    let duree: String?
}

/// A decision with optional context.
struct Decision: Codable, Hashable {
    let decision: String
    let contexte: String?
}

/// An action item: what, who, by when.
struct ActionItem: Codable, Hashable {
    let action: String
    let responsable: String?
    let echeance: String?
}

/// A timeline chapter (seek target in the report).
struct Chapter: Codable, Hashable {
    let t: Double
    let titre: String
    let resume: String?
}

/// A thematic grouping of points.
struct Theme: Codable, Hashable {
    let theme: String
    let points: [String]
}

/// An attributed quote with an optional timestamp.
struct Citation: Codable, Hashable {
    let speaker: String?
    let texte: String
    let t: Double?
}

/// The full structured synthesis (`synthese.json`).
///
/// Optional arrays default to empty via the failable-friendly decoder so a
/// partial synthesis (e.g. the heuristic fallback) still decodes cleanly.
struct Synthesis: Codable {
    let meta: Meta
    let speakers: [String: SpeakerInfo]
    let resume: [String]
    let pointsCles: [String]
    let decisions: [Decision]
    let actions: [ActionItem]
    let chapitres: [Chapter]
    let themes: [Theme]
    let citations: [Citation]

    enum CodingKeys: String, CodingKey {
        case meta, speakers, resume, decisions, actions, chapitres, themes, citations
        case pointsCles = "points_cles"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        meta = try c.decode(Meta.self, forKey: .meta)
        speakers = try c.decodeIfPresent([String: SpeakerInfo].self, forKey: .speakers) ?? [:]
        resume = try c.decodeIfPresent([String].self, forKey: .resume) ?? []
        pointsCles = try c.decodeIfPresent([String].self, forKey: .pointsCles) ?? []
        decisions = try c.decodeIfPresent([Decision].self, forKey: .decisions) ?? []
        actions = try c.decodeIfPresent([ActionItem].self, forKey: .actions) ?? []
        chapitres = try c.decodeIfPresent([Chapter].self, forKey: .chapitres) ?? []
        themes = try c.decodeIfPresent([Theme].self, forKey: .themes) ?? []
        citations = try c.decodeIfPresent([Citation].self, forKey: .citations) ?? []
    }

    /// Memberwise initialiser (the custom `init(from:)` suppresses the synthesised
    /// one). Used by the native engine to build a synthesis in code. Encoding is
    /// still synthesised from ``CodingKeys`` (so `pointsCles` writes as
    /// `points_cles`, matching the Python `synthese.json`).
    init(meta: Meta, speakers: [String: SpeakerInfo], resume: [String], pointsCles: [String],
         decisions: [Decision], actions: [ActionItem], chapitres: [Chapter],
         themes: [Theme], citations: [Citation]) {
        self.meta = meta
        self.speakers = speakers
        self.resume = resume
        self.pointsCles = pointsCles
        self.decisions = decisions
        self.actions = actions
        self.chapitres = chapitres
        self.themes = themes
        self.citations = citations
    }
}
