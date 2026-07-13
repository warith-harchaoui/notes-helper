//
//  ReportWebView.swift
//  NotesHelper
//
//  A SwiftUI wrapper around WKWebView that renders the self-contained
//  `report.html`. The report ships its own vendored assets, so the web view is
//  granted read access only to the report's directory — and, because the page
//  makes zero external requests, nothing is ever fetched from the network.
//
//  Author: Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
//

import SwiftUI
import WebKit

#if os(macOS)
/// macOS bridge for a local-file WKWebView.
struct ReportWebView: NSViewRepresentable {
    /// URL of the local `report.html` to display.
    let reportURL: URL

    func makeNSView(context: Context) -> WKWebView { WKWebView() }

    func updateNSView(_ webView: WKWebView, context: Context) {
        // Grant read access to the whole report directory so the page can load
        // its sibling `assets/` (fonts + Tailwind) — all local.
        webView.loadFileURL(reportURL, allowingReadAccessTo: reportURL.deletingLastPathComponent())
    }
}
#else
/// iOS bridge for a local-file WKWebView.
struct ReportWebView: UIViewRepresentable {
    /// URL of the local `report.html` to display.
    let reportURL: URL

    func makeUIView(context: Context) -> WKWebView { WKWebView() }

    func updateUIView(_ webView: WKWebView, context: Context) {
        webView.loadFileURL(reportURL, allowingReadAccessTo: reportURL.deletingLastPathComponent())
    }
}
#endif
