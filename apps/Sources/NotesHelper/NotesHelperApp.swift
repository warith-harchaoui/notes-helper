//
//  NotesHelperApp.swift
//  NotesHelper
//
//  Application entry point for both macOS and iOS. A single window/scene hosting
//  the capture-to-report flow.
//
//  Author: Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
//

import SwiftUI

/// The NotesHelper app — fully-local diarized meeting recorder.
@main
struct NotesHelperApp: App {
    var body: some Scene {
        WindowGroup {
            ContentView()
        }
        #if os(macOS)
        // A single resizable window suits the desktop report view.
        .windowResizability(.contentMinSize)
        #endif
    }
}
