//
//  AudioDecoder.swift
//  NotesHelper
//
//  Decodes any local audio file (m4a/mp3/wav/…) into a mono 16 kHz Float32
//  buffer using AVFoundation — the iOS equivalent of the CLI's ffmpeg resample
//  step, with no external binary. Everything stays on-device.
//
//  Author: Warith HARCHAOUI — https://linkedin.com/in/warith-harchaoui
//

import AVFoundation
import Foundation

/// Decodes and resamples audio to the pipeline's canonical format.
enum AudioDecoder {

    /// Errors raised while decoding.
    enum DecodeError: LocalizedError {
        case cannotOpen(URL)
        case cannotConvert

        var errorDescription: String? {
            switch self {
            case .cannotOpen(let url): return "Impossible d'ouvrir l'audio : \(url.lastPathComponent)"
            case .cannotConvert: return "Échec de conversion audio (rééchantillonnage 16 kHz mono)."
            }
        }
    }

    /// Load `url` as mono Float32 samples at ``DSP/sampleRate``.
    ///
    /// - Parameter url: A local audio file.
    /// - Returns: The decoded samples.
    /// - Throws: ``DecodeError`` if the file cannot be read or converted.
    ///
    /// - Note: Uses `AVAudioConverter` to go from the file's native format to a
    ///   16 kHz mono float layout in one pass. Large files are converted in a
    ///   single buffer sized from the source length scaled by the sample-rate
    ///   ratio, which is ample for meeting-length recordings.
    static func decode(_ url: URL) throws -> [Float] {
        guard let file = try? AVAudioFile(forReading: url) else {
            throw DecodeError.cannotOpen(url)
        }
        let inFormat = file.processingFormat

        // Target: 16 kHz, 1 channel, non-interleaved Float32.
        guard let outFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32,
                                            sampleRate: Double(DSP.sampleRate),
                                            channels: 1,
                                            interleaved: false),
              let converter = AVAudioConverter(from: inFormat, to: outFormat)
        else { throw DecodeError.cannotConvert }

        // Read the whole file into a source buffer.
        let srcCapacity = AVAudioFrameCount(file.length)
        guard srcCapacity > 0,
              let srcBuffer = AVAudioPCMBuffer(pcmFormat: inFormat, frameCapacity: srcCapacity)
        else { return [] }
        try file.read(into: srcBuffer)

        // Destination capacity scaled by the sample-rate ratio (+headroom).
        let ratio = outFormat.sampleRate / inFormat.sampleRate
        let dstCapacity = AVAudioFrameCount(Double(srcCapacity) * ratio) + 1024
        guard let dstBuffer = AVAudioPCMBuffer(pcmFormat: outFormat, frameCapacity: dstCapacity)
        else { throw DecodeError.cannotConvert }

        // Feed the source buffer exactly once, then signal end-of-stream.
        var fed = false
        var conversionError: NSError?
        let status = converter.convert(to: dstBuffer, error: &conversionError) { _, outStatus in
            if fed {
                outStatus.pointee = .endOfStream
                return nil
            }
            fed = true
            outStatus.pointee = .haveData
            return srcBuffer
        }
        if status == .error || conversionError != nil { throw DecodeError.cannotConvert }

        guard let channel = dstBuffer.floatChannelData else { return [] }
        let count = Int(dstBuffer.frameLength)
        return Array(UnsafeBufferPointer(start: channel[0], count: count))
    }
}
