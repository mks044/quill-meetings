import Foundation
import XCTest
@testable import QuillSession

final class SessionManifestTests: XCTestCase {
    func testRecordingManifestIsImmediatelyRecoverable() throws {
        let started = Date(timeIntervalSince1970: 1_700_000_000)
        let manifest = SessionManifest.recording(startedAt: started)

        XCTAssertEqual(manifest.state, .recording)
        XCTAssertNil(manifest.ended)
        XCTAssertEqual(manifest.durationSeconds, 0)
        XCTAssertEqual(manifest.files.mic, "mic.caf")
        XCTAssertEqual(manifest.files.system, "system.caf")
        XCTAssertEqual(manifest.startOffsetMs.mic, 0)
        XCTAssertEqual(manifest.startOffsetMs.system, 0)

        let directory = try temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        try manifest.write(to: directory)

        let data = try Data(contentsOf: directory.appendingPathComponent("meta.json"))
        let json = try XCTUnwrap(
            JSONSerialization.jsonObject(with: data) as? [String: Any]
        )
        XCTAssertEqual(json["state"] as? String, "recording")
        XCTAssertNil(json["ended"])
    }

    func testCompleteManifestPreservesDurationAndTrackOffsets() {
        let started = Date(timeIntervalSince1970: 1_700_000_000)
        let ended = started.addingTimeInterval(65.9)
        let manifest = SessionManifest.complete(
            startedAt: started,
            endedAt: ended,
            micStartedAt: started.addingTimeInterval(0.25),
            systemStartedAt: started
        )

        XCTAssertEqual(manifest.state, .complete)
        XCTAssertNotNil(manifest.ended)
        XCTAssertEqual(manifest.durationSeconds, 65)
        XCTAssertEqual(manifest.startOffsetMs.mic, 250)
        XCTAssertEqual(manifest.startOffsetMs.system, 0)
    }

    func testTranscriptionManifestWritesAtomicPipelineState() throws {
        let directory = try temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let updated = Date(timeIntervalSince1970: 1_700_000_100)

        try TranscriptionManifest.make(.transcribing, at: updated).write(to: directory)
        let url = directory.appendingPathComponent("transcription.json")
        var decoded = try JSONDecoder().decode(
            TranscriptionManifest.self,
            from: Data(contentsOf: url)
        )
        XCTAssertEqual(decoded.state, .transcribing)
        XCTAssertNil(decoded.error)

        try TranscriptionManifest.make(
            .failed,
            at: updated,
            error: String(repeating: "x", count: 600) + "\nsecret-tail"
        ).write(to: directory)
        decoded = try JSONDecoder().decode(
            TranscriptionManifest.self,
            from: Data(contentsOf: url)
        )
        XCTAssertEqual(decoded.state, .failed)
        XCTAssertEqual(decoded.error?.count, 500)
        XCTAssertFalse(decoded.error?.contains("\n") ?? true)
    }

    private func temporaryDirectory() throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("quill-session-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }
}
