import AVFoundation
import Foundation
import QuillProcess

/// OpenAI Whisper large-v3-turbo via the whisper.cpp CLI (Homebrew build,
/// Metal-accelerated). Multilingual with per-track language auto-detection,
/// so Russian/English (and mixed) meetings transcribe correctly.
///
/// The engine shells out instead of linking whisper.cpp: upstream removed
/// Swift package support, the brew formula is the maintained macOS build,
/// and process isolation means a decoder crash can never take the daemon
/// down. The model file (~1.6 GB) downloads once into quill's own cache;
/// after that everything runs offline.
actor WhisperEngine: TranscriptionEngine {
    private static let conversionTimeout: TimeInterval = 5 * 60
    private static let defaultDecodeTimeout: TimeInterval = 30 * 60

    enum EngineError: Error, CustomStringConvertible {
        case cliMissing
        case unreadableAudio(URL, Error?)
        case conversionFailed(URL, String)
        case decodeFailed(URL, String)
        case downloadFailed(String)

        var description: String {
            switch self {
            case .cliMissing:
                return "whisper-cli not found — brew install whisper-cpp"
            case .unreadableAudio(let url, let e):
                return "unreadable or empty audio \(url.lastPathComponent)"
                    + (e.map { ": \($0)" } ?? "")
            case .conversionFailed(let url, let msg):
                return "afconvert failed for \(url.lastPathComponent): \(msg)"
            case .decodeFailed(let url, let msg):
                return "whisper failed on \(url.lastPathComponent): \(msg)"
            case .downloadFailed(let msg):
                return "whisper model download failed: \(msg)"
            }
        }
    }

    nonisolated let name = "whisper"
    nonisolated let model = "large-v3-turbo"

    static let modelRemote = URL(
        string: "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-large-v3-turbo.bin"
    )!

    static var modelPath: URL {
        modelsDir.appendingPathComponent("ggml-large-v3-turbo.bin")
    }

    /// Silero VAD model: suppresses Whisper's known hallucination-on-silence
    /// (it invents repeated phrases over mute stretches of a meeting).
    static let vadRemote = URL(
        string: "https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v5.1.2.bin"
    )!

    static var vadPath: URL {
        modelsDir.appendingPathComponent("ggml-silero-v5.1.2.bin")
    }

    static var modelsDir: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/quill/models", isDirectory: true)
    }

    /// Homebrew install locations (launchd/LaunchServices contexts have a
    /// minimal PATH, so no `env` lookup).
    static func cliPath() -> String? {
        ["/opt/homebrew/bin/whisper-cli", "/usr/local/bin/whisper-cli"]
            .first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    func prepare() async throws {
        guard Self.cliPath() != nil else { throw EngineError.cliMissing }
        if !FileManager.default.fileExists(atPath: Self.modelPath.path) {
            notifyUser(
                title: "quill — downloading Whisper model",
                body: "one-time 1.6 GB download; transcription starts when it finishes"
            )
            try await Self.fetch(Self.modelRemote, to: Self.modelPath)
        }
        if !FileManager.default.fileExists(atPath: Self.vadPath.path) {
            try await Self.fetch(Self.vadRemote, to: Self.vadPath)
        }
    }

    private static func fetch(_ remote: URL, to path: URL) async throws {
        try FileManager.default.createDirectory(
            at: path.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        let (tmp, response) = try await URLSession.shared.download(from: remote)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            throw EngineError.downloadFailed(
                "HTTP \((response as? HTTPURLResponse)?.statusCode ?? -1) for \(remote.lastPathComponent)"
            )
        }
        try? FileManager.default.removeItem(at: path)
        try FileManager.default.moveItem(at: tmp, to: path)
    }

    func transcribe(_ audio: URL) async throws -> [TranscriptSegment] {
        guard let cli = Self.cliPath() else { throw EngineError.cliMissing }

        // Refuse tracks whisper can't read rather than crashing mid-queue.
        do {
            let probe = try AVAudioFile(forReading: audio)
            guard probe.length > 0 else { throw EngineError.unreadableAudio(audio, nil) }
        } catch let error as EngineError {
            throw error
        } catch {
            throw EngineError.unreadableAudio(audio, error)
        }

        let work = FileManager.default.temporaryDirectory
            .appendingPathComponent("quill-whisper-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: work, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: work) }

        // whisper-cli wants PCM WAV; the tracks are AAC CAF. afconvert ships
        // with macOS: 16 kHz mono 16-bit little-endian.
        let wav = work.appendingPathComponent("audio.wav")
        let convert = try ProcessRunner.run(
            "/usr/bin/afconvert",
            ["-f", "WAVE", "-d", "LEI16@16000", "-c", "1", audio.path, wav.path],
            timeout: Self.conversionTimeout
        )
        guard convert.status == 0 else {
            throw EngineError.conversionFailed(audio, convert.stderr)
        }

        let outBase = work.appendingPathComponent("out").path
        // -mc 0: decode each window with fresh context. The default carries
        // prior output as the next window's prompt, so one garbled stretch
        // (music, crosstalk) degrades style — punctuation, casing — for the
        // rest of a long recording.
        let decode = try ProcessRunner.run(
            cli,
            [
                "-m", Self.modelPath.path,
                "-f", wav.path,
                "-l", "auto",
                "--vad", "-vm", Self.vadPath.path,
                "-mc", "0",
                "-oj",
                "-of", outBase,
                "-np",
            ],
            timeout: Self.decodeTimeout
        )
        guard decode.status == 0 else {
            throw EngineError.decodeFailed(audio, decode.stderr)
        }

        let json = try Data(contentsOf: URL(fileURLWithPath: outBase + ".json"))
        return try Self.segments(fromWhisperJSON: json)
    }

    func release() async {
        // Nothing held between calls — the CLI process owns the model per run.
    }

    // MARK: -

    private struct WhisperOutput: Decodable {
        struct Segment: Decodable {
            struct Offsets: Decodable {
                let from: Int
                let to: Int
            }
            let offsets: Offsets
            let text: String
        }
        let transcription: [Segment]
    }

    static func segments(fromWhisperJSON data: Data) throws -> [TranscriptSegment] {
        let out = try JSONDecoder().decode(WhisperOutput.self, from: data)
        return out.transcription.compactMap {
            let text = $0.text.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !text.isEmpty else { return nil }
            return TranscriptSegment(
                start: TimeInterval($0.offsets.from) / 1000,
                end: TimeInterval($0.offsets.to) / 1000,
                text: text
            )
        }
    }

    private static var decodeTimeout: TimeInterval {
        guard
            let raw = ProcessInfo.processInfo.environment["QUILL_WHISPER_TIMEOUT_SECONDS"],
            let configured = TimeInterval(raw),
            configured.isFinite
        else { return defaultDecodeTimeout }
        return min(max(configured, 60), 6 * 60 * 60)
    }
}
