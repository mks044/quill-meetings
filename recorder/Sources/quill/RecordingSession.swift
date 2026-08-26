import Foundation
import QuillSession

/// One meeting recording: a timestamped folder holding two independent tracks
/// (mic = you, system = them) plus a meta.json written on clean stop. Tracks
/// are separate on purpose — whisper does better on clean single-source audio,
/// and two tracks give free two-party diarization.
final class RecordingSession {
    let dir: URL
    let startedAt = Date()

    private let mic = MicRecorder()
    private let system = SystemAudioRecorder()

    private static let folderFormat: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy.MM.dd-HHmm"
        f.locale = Locale(identifier: "en_US_POSIX")
        return f
    }()

    /// Create the session folder under `root` (yyyy.MM.dd-HHmm, suffixed on
    /// collision) without starting capture yet.
    init(root: URL) throws {
        let base = Self.folderFormat.string(from: startedAt)
        var candidate = root.appendingPathComponent(base, isDirectory: true)
        var n = 2
        while FileManager.default.fileExists(atPath: candidate.path) {
            candidate = root.appendingPathComponent("\(base)-\(n)", isDirectory: true)
            n += 1
        }
        try FileManager.default.createDirectory(at: candidate, withIntermediateDirectories: true)
        dir = candidate
    }

    /// Start both tracks. If the mic fails after the system tap started, the
    /// tap is torn down so we never run half a session silently.
    func start() throws {
        try system.start(writingTo: dir.appendingPathComponent("system.caf"))
        do {
            try mic.start(writingTo: dir.appendingPathComponent("mic.caf"))
            try SessionManifest.recording(startedAt: startedAt).write(to: dir)
        } catch {
            mic.stop()
            system.stop()
            throw error
        }
    }

    /// Stop both tracks and write meta.json.
    func stop() {
        mic.stop()
        system.stop()

        let ended = Date()

        do {
            // The tracks don't start on the same buffer; record how far each
            // lags the earliest so transcript timestamps share one clock.
            try SessionManifest.complete(
                startedAt: startedAt,
                endedAt: ended,
                micStartedAt: mic.firstBufferAt,
                systemStartedAt: system.firstBufferAt
            ).write(to: dir)
        } catch {
            FileHandle.standardError.write(Data(
                "failed to finalize \(dir.path)/meta.json: \(error)\n".utf8
            ))
        }
    }
}
