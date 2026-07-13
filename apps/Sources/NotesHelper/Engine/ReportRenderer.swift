//
//  ReportRenderer.swift
//  NotesHelper
//
//  Renders the report natively on-device: writes transcript.json + synthese.json
//  (same schema as the Python engine) plus report.md and a self-contained
//  report.html with inline CSS — zero external requests, so the sovereignty
//  guarantee extends to the output file.
//
//  Author: Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
//

import Foundation

/// Writes the report artifacts for one processed recording.
struct ReportRenderer {
    /// Speaker colours (mirrors the Python palette).
    private let palette = ["#2f6f5e", "#b45309", "#1d4ed8", "#9333ea", "#be123c", "#0f766e"]

    /// Write transcript.json, synthese.json, report.md and report.html.
    ///
    /// - Parameters:
    ///   - dir: Output directory (created if needed).
    ///   - transcript: Ordered utterances.
    ///   - synthesis: The structured summary.
    /// - Returns: The URL of `report.html`.
    /// - Throws: Any file-writing or JSON-encoding error.
    @discardableResult
    func write(dir: URL, transcript: [Utterance], synthesis: Synthesis) throws -> URL {
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .withoutEscapingSlashes]

        try encoder.encode(transcript).write(to: dir.appendingPathComponent("transcript.json"))
        try encoder.encode(synthesis).write(to: dir.appendingPathComponent("synthese.json"))
        try Data(markdown(transcript, synthesis).utf8)
            .write(to: dir.appendingPathComponent("report.md"))
        let htmlURL = dir.appendingPathComponent("report.html")
        try Data(html(transcript, synthesis).utf8).write(to: htmlURL)
        return htmlURL
    }

    // MARK: - Markdown

    /// Render the neutral Markdown report.
    func markdown(_ transcript: [Utterance], _ syn: Synthesis) -> String {
        let names = displayNames(syn)
        var l: [String] = ["# \(syn.meta.titre)\n"]
        let bits = [("Date", syn.meta.date), ("Lieu", syn.meta.lieu), ("Durée", syn.meta.duree)]
            .compactMap { (k, v) -> String? in (v?.isEmpty == false) ? "**\(k)** : \(v!)" : nil }
        if !bits.isEmpty { l.append(bits.joined(separator: "  ·  ")) }
        if !syn.speakers.isEmpty {
            let parts = syn.speakers.values.map { $0.name + ($0.role?.isEmpty == false ? " (\($0.role!))" : "") }
            l.append("\n**Participants** : \(parts.joined(separator: ", "))\n")
        }
        if !syn.resume.isEmpty { l.append("\n## Résumé\n"); l += syn.resume }
        if !syn.pointsCles.isEmpty { l.append("\n## Points clés\n"); l += syn.pointsCles.map { "- \($0)" } }
        if !syn.decisions.isEmpty {
            l.append("\n## Décisions\n")
            l += syn.decisions.map { "- ✓ **\($0.decision)**" + ($0.contexte.map { " — \($0)" } ?? "") }
        }
        if !syn.actions.isEmpty {
            l.append("\n## Actions\n\n| Action | Responsable | Échéance |\n|---|---|---|")
            l += syn.actions.map { "| \($0.action) | \($0.responsable ?? "—") | \($0.echeance ?? "—") |" }
        }
        if !syn.chapitres.isEmpty {
            l.append("\n## Chapitres\n")
            l += syn.chapitres.map { "- `\(hhmmss($0.t))` **\($0.titre)**" + ($0.resume?.isEmpty == false ? " — \($0.resume!)" : "") }
        }
        if !transcript.isEmpty {
            l.append("\n## Transcript\n")
            l += transcript.map { "`\(hhmmss($0.t0))` **\(names[$0.speaker] ?? $0.speaker)** : \($0.text)" }
        }
        return l.joined(separator: "\n") + "\n"
    }

    // MARK: - HTML

    /// Render a self-contained HTML report (inline CSS, no external requests).
    func html(_ transcript: [Utterance], _ syn: Synthesis) -> String {
        let names = displayNames(syn)
        let colorOf = speakerColors(syn)

        var sections: [String] = []
        if !syn.resume.isEmpty {
            sections.append(section("Résumé", syn.resume.map { "<p>\(esc($0))</p>" }.joined()))
        }
        if !syn.pointsCles.isEmpty { sections.append(section("Points clés", ul(syn.pointsCles))) }
        if !syn.decisions.isEmpty {
            let items = syn.decisions.map { "<li>✓ <strong>\(esc($0.decision))</strong>"
                + ($0.contexte.map { " — <span class='muted'>\(esc($0))</span>" } ?? "") + "</li>" }.joined()
            sections.append(section("Décisions", "<ul>\(items)</ul>"))
        }
        if !syn.actions.isEmpty {
            let rows = syn.actions.map {
                "<tr><td>\(esc($0.action))</td><td>\(esc($0.responsable ?? "—"))</td><td>\(esc($0.echeance ?? "—"))</td></tr>"
            }.joined()
            sections.append(section("Actions",
                "<table><thead><tr><th>Action</th><th>Responsable</th><th>Échéance</th></tr></thead><tbody>\(rows)</tbody></table>"))
        }
        if !syn.chapitres.isEmpty {
            let items = syn.chapitres.map { "<li><code>\(hhmmss($0.t))</code> <strong>\(esc($0.titre))</strong> <span class='muted'>\(esc($0.resume ?? ""))</span></li>" }.joined()
            sections.append(section("Chapitres", "<ul>\(items)</ul>"))
        }
        let transcriptRows = transcript.map { u in
            let color = colorOf[u.speaker] ?? "#555"
            return "<div class='utt'><code>\(hhmmss(u.t0))</code> <span class='spk' style='color:\(color)'>\(esc(names[u.speaker] ?? u.speaker))</span> \(esc(u.text))</div>"
        }.joined()
        sections.append(section("Transcript", "<div class='transcript'>\(transcriptRows)</div>"))

        let chips = syn.speakers.map { (sid, info) in
            "<span class='chip' style='background:\(colorOf[sid] ?? "#555")'>\(esc(info.name))</span>"
        }.joined()

        return """
        <!DOCTYPE html><html lang="fr"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>\(esc(syn.meta.titre)) — Compte-rendu</title>
        <style>
          :root{color-scheme:light dark}
          body{font-family:-apple-system,system-ui,sans-serif;max-width:820px;margin:0 auto;padding:24px;line-height:1.5}
          h1{font-size:1.8rem;margin:0 0 4px} h2{margin-top:2rem;border-bottom:1px solid #8884;padding-bottom:4px}
          .meta{color:#888;font-size:.9rem} .muted{color:#888}
          .chip{display:inline-block;color:#fff;border-radius:999px;padding:2px 10px;margin:2px;font-size:.85rem}
          table{width:100%;border-collapse:collapse} th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #8883}
          .utt{padding:3px 0} .utt code{color:#999;font-size:.8rem;margin-right:6px} .spk{font-weight:600;margin-right:4px}
          code{font-family:ui-monospace,monospace}
        </style></head><body>
        <h1>\(esc(syn.meta.titre))</h1>
        <div class="meta">\(esc(syn.meta.date ?? "")) · \(esc(syn.meta.lieu ?? "")) · \(esc(syn.meta.duree ?? ""))</div>
        <div>\(chips)</div>
        \(sections.joined())
        </body></html>
        """
    }

    // MARK: - Helpers

    private func section(_ title: String, _ inner: String) -> String {
        "<h2>\(esc(title))</h2>\(inner)"
    }

    private func ul(_ items: [String]) -> String {
        "<ul>" + items.map { "<li>\(esc($0))</li>" }.joined() + "</ul>"
    }

    private func displayNames(_ syn: Synthesis) -> [String: String] {
        syn.speakers.mapValues { $0.name }
    }

    private func speakerColors(_ syn: Synthesis) -> [String: String] {
        var out: [String: String] = [:]
        for (i, sid) in syn.speakers.keys.sorted().enumerated() {
            out[sid] = palette[i % palette.count]
        }
        return out
    }

    private func esc(_ s: String) -> String {
        s.replacingOccurrences(of: "&", with: "&amp;")
            .replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;")
    }

    private func hhmmss(_ seconds: Double) -> String {
        let s = Int(seconds)
        return String(format: "%d:%02d:%02d", s / 3600, (s % 3600) / 60, s % 60)
    }
}
