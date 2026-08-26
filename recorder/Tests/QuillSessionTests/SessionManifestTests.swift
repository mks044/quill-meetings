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

    private func temporaryDirectory() throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("quill-session-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }
}
