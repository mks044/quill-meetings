import XCTest
@testable import QuillProcess

final class ProcessRunnerTests: XCTestCase {
    func testLargeStdoutAndStderrCannotDeadlock() throws {
        let result = try ProcessRunner.run(
            "/bin/sh",
            [
                "-c",
                "dd if=/dev/zero bs=1024 count=1024 2>/dev/null; "
                    + "dd if=/dev/zero bs=1024 count=1024 1>&2 2>/dev/null; "
                    + "printf 'stderr-finished\\n' >&2",
            ],
            timeout: 5
        )

        XCTAssertEqual(result.status, 0)
        XCTAssertTrue(result.stderr.hasSuffix("stderr-finished\n"))
        XCTAssertLessThanOrEqual(result.stderr.utf8.count, 64 * 1024)
    }

    func testNonzeroExitReturnsStatusAndStderr() throws {
        let result = try ProcessRunner.run(
            "/bin/sh",
            ["-c", "printf 'expected failure' >&2; exit 7"],
            timeout: 5
        )

        XCTAssertEqual(result.status, 7)
        XCTAssertEqual(result.stderr, "expected failure")
    }

    func testTimeoutTerminatesChildPromptly() {
        let started = Date()

        XCTAssertThrowsError(
            try ProcessRunner.run(
                "/bin/sleep",
                ["30"],
                timeout: 0.15,
                terminationGrace: 0.1,
                pollInterval: 0.01
            )
        ) { error in
            guard case ProcessRunnerError.timedOut(let executable, _, _) = error else {
                return XCTFail("unexpected error: \(error)")
            }
            XCTAssertEqual(executable, "sleep")
        }

        XCTAssertLessThan(Date().timeIntervalSince(started), 2)
    }

    func testTimeoutForceKillsChildThatIgnoresTerminate() {
        let started = Date()

        XCTAssertThrowsError(
            try ProcessRunner.run(
                "/bin/sh",
                ["-c", "trap '' TERM; while :; do :; done"],
                timeout: 0.1,
                terminationGrace: 0.05,
                pollInterval: 0.01
            )
        ) { error in
            guard case ProcessRunnerError.timedOut(let executable, _, _) = error else {
                return XCTFail("unexpected error: \(error)")
            }
            XCTAssertEqual(executable, "sh")
        }

        XCTAssertLessThan(Date().timeIntervalSince(started), 2)
    }
}
