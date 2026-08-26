import Darwin
import Foundation

package struct ProcessResult: Sendable {
    package let status: Int32
    package let stderr: String
}

package enum ProcessRunnerError: Error, CustomStringConvertible, Sendable {
    case invalidTimeout(TimeInterval)
    case captureSetupFailed(String)
    case timedOut(executable: String, seconds: TimeInterval, stderr: String)

    package var description: String {
        switch self {
        case .invalidTimeout(let seconds):
            return "invalid process timeout: \(seconds)s"
        case .captureSetupFailed(let path):
            return "could not create subprocess error log at \(path)"
        case .timedOut(let executable, let seconds, let stderr):
            let detail = stderr.trimmingCharacters(in: .whitespacesAndNewlines)
            let base = "\(executable) timed out after \(Int(seconds))s and was terminated"
            return detail.isEmpty ? base : "\(base): \(detail)"
        }
    }
}

/// Runs a subprocess without bounded pipes. Unused stdout goes to the null
/// device and stderr goes to a temporary file, so a verbose child can never
/// block while its parent waits. Every child also gets a hard wall-clock
/// deadline; TERM is followed by KILL if it ignores the grace period.
package enum ProcessRunner {
    private static let stderrLimit = 64 * 1024

    package static func run(
        _ executable: String,
        _ arguments: [String],
        timeout: TimeInterval,
        terminationGrace: TimeInterval = 5,
        pollInterval: TimeInterval = 0.05
    ) throws -> ProcessResult {
        guard timeout.isFinite, timeout > 0 else {
            throw ProcessRunnerError.invalidTimeout(timeout)
        }

        let fm = FileManager.default
        let stderrURL = fm.temporaryDirectory
            .appendingPathComponent("quill-process-\(UUID().uuidString).stderr")
        guard fm.createFile(
            atPath: stderrURL.path,
            contents: nil,
            attributes: [.posixPermissions: 0o600]
        ) else {
            throw ProcessRunnerError.captureSetupFailed(stderrURL.path)
        }

        let stderrHandle = try FileHandle(forWritingTo: stderrURL)
        var stderrClosed = false
        defer {
            if !stderrClosed { try? stderrHandle.close() }
            try? fm.removeItem(at: stderrURL)
        }

        func closeAndReadStderr() -> String {
            if !stderrClosed {
                try? stderrHandle.synchronize()
                try? stderrHandle.close()
                stderrClosed = true
            }
            return stderrTail(at: stderrURL)
        }

        let task = Process()
        task.executableURL = URL(fileURLWithPath: executable)
        task.arguments = arguments
        task.standardInput = FileHandle.nullDevice
        task.standardOutput = FileHandle.nullDevice
        task.standardError = stderrHandle
        try task.run()

        if !waitForExit(task, timeout: timeout, pollInterval: pollInterval) {
            task.terminate()
            if !waitForExit(
                task,
                timeout: max(terminationGrace, 0),
                pollInterval: pollInterval
            ) {
                _ = Darwin.kill(task.processIdentifier, SIGKILL)
                _ = waitForExit(task, timeout: 5, pollInterval: pollInterval)
            }
            throw ProcessRunnerError.timedOut(
                executable: URL(fileURLWithPath: executable).lastPathComponent,
                seconds: timeout,
                stderr: closeAndReadStderr()
            )
        }

        return ProcessResult(status: task.terminationStatus, stderr: closeAndReadStderr())
    }

    private static func waitForExit(
        _ task: Process,
        timeout: TimeInterval,
        pollInterval: TimeInterval
    ) -> Bool {
        let started = DispatchTime.now().uptimeNanoseconds
        let requestedNanoseconds = max(timeout, 0) * 1_000_000_000
        let limit = requestedNanoseconds >= Double(UInt64.max)
            ? UInt64.max
            : UInt64(requestedNanoseconds)
        let pause = max(min(pollInterval, 0.25), 0.005)

        while task.isRunning {
            let elapsed = DispatchTime.now().uptimeNanoseconds - started
            if elapsed >= limit { return false }
            Thread.sleep(forTimeInterval: pause)
        }
        return true
    }

    private static func stderrTail(at url: URL) -> String {
        guard let handle = try? FileHandle(forReadingFrom: url) else { return "" }
        defer { try? handle.close() }

        let end = (try? handle.seekToEnd()) ?? 0
        if end > UInt64(stderrLimit) {
            try? handle.seek(toOffset: end - UInt64(stderrLimit))
        } else {
            try? handle.seek(toOffset: 0)
        }
        let data = (try? handle.readToEnd()) ?? Data()
        return String(decoding: data, as: UTF8.self)
    }
}
