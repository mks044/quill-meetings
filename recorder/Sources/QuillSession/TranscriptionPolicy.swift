import Foundation

package enum TranscriptionOutputError: Error, CustomStringConvertible, Sendable {
    case noUsableSegments(String)

    package var description: String {
        switch self {
        case .noUsableSegments(let detail):
            return detail.isEmpty
                ? "transcription produced no usable speech segments"
                : "transcription produced no usable speech segments: \(detail)"
        }
    }
}

package enum TranscriptionAttemptPolicy {
    package static let maximumRetries = 1

    package static func shouldRetry(completedRetries: Int) -> Bool {
        completedRetries < maximumRetries
    }

    package static func requireUsableSegments(
        _ count: Int,
        failures: [String]
    ) throws {
        guard count > 0 else {
            throw TranscriptionOutputError.noUsableSegments(
                failures.joined(separator: "; ")
            )
        }
    }
}
