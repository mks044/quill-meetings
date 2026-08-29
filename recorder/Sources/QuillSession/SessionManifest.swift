import Foundation

/// Crash-recovery metadata for a recording directory. A provisional manifest
/// is written as soon as both capture engines start, then atomically replaced
/// with final timing on clean stop. If the Mac loses power, the provisional
/// file is enough for the next launch to discover and transcribe the CAFs.
package struct SessionManifest: Codable, Equatable, Sendable {
    package enum State: String, Codable, Sendable {
        case recording
        case complete
    }

    package struct Files: Codable, Equatable, Sendable {
        package let mic: String
        package let system: String
    }

    package struct StartOffsets: Codable, Equatable, Sendable {
        package let mic: Int
        package let system: Int
    }

    package let state: State
    package let started: String
    package let ended: String?
    package let durationSeconds: Int
    package let files: Files
    package let startOffsetMs: StartOffsets

    private enum CodingKeys: String, CodingKey {
        case state
        case started
        case ended
        case durationSeconds = "duration_seconds"
        case files
        case startOffsetMs = "start_offset_ms"
    }

    package static func recording(startedAt: Date) -> SessionManifest {
        SessionManifest(
            state: .recording,
            started: timestamp(startedAt),
            ended: nil,
            durationSeconds: 0,
            files: Files(mic: "mic.caf", system: "system.caf"),
            startOffsetMs: StartOffsets(mic: 0, system: 0)
        )
    }

    package static func complete(
        startedAt: Date,
        endedAt: Date,
        micStartedAt: Date?,
        systemStartedAt: Date?
    ) -> SessionManifest {
        let micStart = micStartedAt ?? startedAt
        let systemStart = systemStartedAt ?? startedAt
        let earliest = min(micStart, systemStart)

        return SessionManifest(
            state: .complete,
            started: timestamp(startedAt),
            ended: timestamp(endedAt),
            durationSeconds: max(0, Int(endedAt.timeIntervalSince(startedAt))),
            files: Files(mic: "mic.caf", system: "system.caf"),
            startOffsetMs: StartOffsets(
                mic: max(0, Int(micStart.timeIntervalSince(earliest) * 1000)),
                system: max(0, Int(systemStart.timeIntervalSince(earliest) * 1000))
            )
        )
    }

    package func write(to directory: URL) throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(self).write(
            to: directory.appendingPathComponent("meta.json"),
            options: .atomic
        )
    }

    private static func timestamp(_ date: Date) -> String {
        ISO8601DateFormatter().string(from: date)
    }
}

/// Durable state for the post-capture transcription pipeline. The dashboard
/// can ingest this small manifest before transcript.json exists, and a restart
/// can report the last known state without relying on an in-memory process.
package struct TranscriptionManifest: Codable, Equatable, Sendable {
    package enum State: String, Codable, Sendable {
        case queued
        case transcribing
        case ready
        case failed
    }

    package let state: State
    package let updated: String
    package let error: String?

    package static func make(
        _ state: State,
        at date: Date = Date(),
        error: String? = nil
    ) -> TranscriptionManifest {
        let boundedError = error.map {
            String($0.replacingOccurrences(of: "\n", with: " ").prefix(500))
        }
        return TranscriptionManifest(
            state: state,
            updated: ISO8601DateFormatter().string(from: date),
            error: boundedError
        )
    }

    package func write(to directory: URL) throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(self).write(
            to: directory.appendingPathComponent("transcription.json"),
            options: .atomic
        )
    }
}
