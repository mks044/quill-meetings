import XCTest
@testable import QuillSession

final class TranscriptionPolicyTests: XCTestCase {
    func testEveryFailureGetsExactlyOneAutomaticRetry() {
        XCTAssertTrue(TranscriptionAttemptPolicy.shouldRetry(completedRetries: 0))
        XCTAssertFalse(TranscriptionAttemptPolicy.shouldRetry(completedRetries: 1))
        XCTAssertFalse(TranscriptionAttemptPolicy.shouldRetry(completedRetries: 2))
    }

    func testEmptyOutputIsAVisiblePipelineFailure() {
        XCTAssertThrowsError(
            try TranscriptionAttemptPolicy.requireUsableSegments(
                0,
                failures: ["mic.caf: decoder stopped", "system.caf: no speech"]
            )
        ) { error in
            XCTAssertEqual(
                String(describing: error),
                "transcription produced no usable speech segments: "
                    + "mic.caf: decoder stopped; system.caf: no speech"
            )
        }
        XCTAssertNoThrow(
            try TranscriptionAttemptPolicy.requireUsableSegments(1, failures: [])
        )
    }
}
