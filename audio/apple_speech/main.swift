// Vox voice listener: on-device Apple Speech recognition, JSON-lines over stdout.
//
// Apple's SFSpeechAudioBufferRecognitionRequest only finalizes a transcription when
// the REQUEST ends -- it has no concept of "the user paused, that's an utterance
// boundary" on its own. So we do our own endpointing: once the live transcript stops
// changing for `endpointMs`, we synthesize a final, end that request, and immediately
// start a fresh one on the SAME still-running audio engine, so there's no gap in
// listening between one utterance and the next.
//
// Protocol (stdout, one JSON object per line, flushed immediately):
//   {"type":"partial","id":<n>,"text":"..."}
//   {"type":"final","id":<n>,"text":"...","confidence":0.0-1.0}
//   {"type":"level","value":0.0-1.0}
//   {"type":"status","value":"listening"|"paused"|"error","detail":"..."}
// stdin: lines "pause" / "resume" (pause = stop sending audio, no output).
import AVFoundation
import Foundation
import Speech

// MARK: - JSON output

// emit() is called from the audio tap's realtime thread (levels) AND the main thread
// (partials/finals/status) -- without serializing, two concurrent prints can interleave
// their bytes mid-line and corrupt the JSON stream. One serial queue for all stdout
// writes keeps each line atomic regardless of which thread produced it.
let emitQueue = DispatchQueue(label: "com.equivox.listener.emit")

func emit(_ dict: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: dict) else { return }
    guard let str = String(data: data, encoding: .utf8) else { return }
    emitQueue.async {
        print(str)
        fflush(stdout)
    }
}

func emitPartial(id: Int, text: String) {
    emit(["type": "partial", "id": id, "text": text])
}

func emitFinal(id: Int, text: String, confidence: Double) {
    emit(["type": "final", "id": id, "text": text, "confidence": confidence])
}

func emitLevel(_ value: Double) {
    emit(["type": "level", "value": value])
}

func emitStatus(_ value: String, detail: String = "") {
    emit(["type": "status", "value": value, "detail": detail])
}

// MARK: - CLI args

var allowServer = false
var endpointMs = 700

func parseArgs() {
    let args = CommandLine.arguments
    var i = 1
    while i < args.count {
        switch args[i] {
        case "--allow-server":
            allowServer = true
        case "--endpoint-ms":
            if i + 1 < args.count, let v = Int(args[i + 1]) {
                endpointMs = v
                i += 1
            }
        default:
            break
        }
        i += 1
    }
}

// MARK: - Authorization

func requestMicAuth(_ completion: @escaping (Bool) -> Void) {
    switch AVCaptureDevice.authorizationStatus(for: .audio) {
    case .authorized:
        completion(true)
    case .notDetermined:
        AVCaptureDevice.requestAccess(for: .audio) { granted in
            DispatchQueue.main.async { completion(granted) }
        }
    default:
        completion(false)
    }
}

func requestSpeechAuth(_ completion: @escaping (Bool) -> Void) {
    SFSpeechRecognizer.requestAuthorization { status in
        DispatchQueue.main.async { completion(status == .authorized) }
    }
}

// MARK: - Listener

final class Listener {
    static let contextualStrings = [
        "Vox", "DuckDuckGo", "Wikipedia", "YouTube", "scroll", "click", "next field",
        "new tab", "close tab", "go back", "high contrast", "physiotherapy", "Edmonton",
    ]
    static let periodicRestartInterval: TimeInterval = 50.0  // avoid recognizer time limits
    static let endpointCheckInterval: TimeInterval = 0.1
    static let levelEmitInterval: TimeInterval = 0.1  // ~10/s

    let recognizer: SFSpeechRecognizer
    let engine = AVAudioEngine()

    var utteranceId = 0
    var currentRequest: SFSpeechAudioBufferRecognitionRequest?
    var currentTask: SFSpeechRecognitionTask?
    var lastText = ""
    var lastUpdateTime = Date()
    var lastVoiceActivity = Date.distantPast
    var noiseFloor: Float = 0.001
    var hasFinalizedCurrent = false
    var paused = false
    var lastLevelEmit = Date.distantPast

    init(recognizer: SFSpeechRecognizer) {
        self.recognizer = recognizer
    }

    func start() {
        setupAudioEngine()
        beginNewRequest()
        startEndpointTimer()
        startPeriodicRestartTimer()
        startStdinReader()
    }

    private func setupAudioEngine() {
        let inputNode = engine.inputNode
        let format = inputNode.outputFormat(forBus: 0)
        inputNode.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak self] buffer, _ in
            guard let self = self else { return }
            self.emitLevelFromBuffer(buffer)
            if self.paused { return }
            self.currentRequest?.append(buffer)
        }
        engine.prepare()
        do {
            try engine.start()
        } catch {
            emitStatus("error", detail: "could not start audio engine: \(error.localizedDescription)")
            exit(1)
        }
    }

    private func emitLevelFromBuffer(_ buffer: AVAudioPCMBuffer) {
        guard let channelData = buffer.floatChannelData else { return }
        let frameLength = Int(buffer.frameLength)
        guard frameLength > 0 else { return }
        var sum: Float = 0
        let samples = channelData[0]
        for i in 0..<frameLength { sum += samples[i] * samples[i] }
        let rms = sqrt(sum / Float(frameLength))
        let now = Date()
        guard now.timeIntervalSince(lastLevelEmit) >= Listener.levelEmitInterval else { return }
        lastLevelEmit = now
        // A stalled partial is not silence. Keep the recognizer request alive
        // while audio energy indicates speech, even if no new words arrive.
        let speechThreshold = max(Float(0.006), noiseFloor * 3)
        if rms > speechThreshold {
            DispatchQueue.main.async { [weak self] in self?.lastVoiceActivity = now }
        } else {
            noiseFloor = noiseFloor * 0.95 + rms * 0.05
        }
        // typical speech RMS on a laptop mic is well under 1.0 -- scale up so the
        // level is actually visible in the UI, then clamp.
        emitLevel(min(1.0, Double(rms) * 8))
    }

    private func beginNewRequest() {
        utteranceId += 1
        let id = utteranceId
        lastText = ""
        lastUpdateTime = Date()
        hasFinalizedCurrent = false

        let req = SFSpeechAudioBufferRecognitionRequest()
        req.shouldReportPartialResults = true
        if #available(macOS 13.0, *) {
            req.addsPunctuation = false
        }
        req.requiresOnDeviceRecognition = !allowServer
        req.contextualStrings = Listener.contextualStrings
        currentRequest = req

        currentTask = recognizer.recognitionTask(with: req) { [weak self] result, error in
            guard let self = self, id == self.utteranceId else { return }
            if let result = result {
                let text = result.bestTranscription.formattedString
                if text != self.lastText {
                    self.lastText = text
                    self.lastUpdateTime = Date()
                    emitPartial(id: id, text: text)
                }
                if result.isFinal {
                    self.finalize(id: id, text: text, confidence: Listener.confidence(of: result))
                }
            }
            if let error = error {
                self.handleError(error, id: id)
            }
        }
    }

    private func finalize(id: Int, text: String, confidence: Double) {
        guard id == utteranceId, !hasFinalizedCurrent else { return }
        hasFinalizedCurrent = true
        if !text.trimmingCharacters(in: .whitespaces).isEmpty {
            emitFinal(id: id, text: text, confidence: confidence)
        }
        currentRequest?.endAudio()
        currentTask?.finish()
        beginNewRequest()
    }

    private func restartSilently() {
        guard !hasFinalizedCurrent else { return }
        hasFinalizedCurrent = true
        currentRequest?.endAudio()
        currentTask?.cancel()
        beginNewRequest()
    }

    private func handleError(_ error: Error, id: Int) {
        guard id == utteranceId, !hasFinalizedCurrent else { return }
        let nsError = error as NSError
        // benign noise from our own restarts/short silences -- don't spam "error"
        // status for these, just quietly start the next utterance.
        let text = lastText
        hasFinalizedCurrent = true
        if !text.trimmingCharacters(in: .whitespaces).isEmpty {
            emitFinal(id: id, text: text, confidence: 0.5)
        } else if nsError.domain != "kAFAssistantErrorDomain" {
            emitStatus("error", detail: nsError.localizedDescription)
        }
        currentRequest?.endAudio()
        currentTask?.cancel()
        beginNewRequest()
    }

    private func startEndpointTimer() {
        let timer = Timer(timeInterval: Listener.endpointCheckInterval, repeats: true) { [weak self] _ in
            guard let self = self, !self.paused else { return }
            let elapsedMs = Date().timeIntervalSince(max(self.lastUpdateTime, self.lastVoiceActivity)) * 1000
            if !self.lastText.isEmpty && elapsedMs >= Double(endpointMs) {
                self.finalize(id: self.utteranceId, text: self.lastText, confidence: 0.9)
            }
        }
        RunLoop.main.add(timer, forMode: .common)
    }

    private func startPeriodicRestartTimer() {
        let timer = Timer(timeInterval: Listener.periodicRestartInterval, repeats: true) { [weak self] _ in
            guard let self = self, !self.paused else { return }
            if !self.lastText.isEmpty {
                self.finalize(id: self.utteranceId, text: self.lastText, confidence: 0.8)
            } else {
                self.restartSilently()
            }
        }
        RunLoop.main.add(timer, forMode: .common)
    }

    private func startStdinReader() {
        FileHandle.standardInput.readabilityHandler = { [weak self] handle in
            guard let self = self else { return }
            let data = handle.availableData
            guard !data.isEmpty, let str = String(data: data, encoding: .utf8) else { return }
            for rawLine in str.split(separator: "\n") {
                let cmd = rawLine.trimmingCharacters(in: .whitespaces).lowercased()
                if cmd == "pause" {
                    self.paused = true
                    emitStatus("paused")
                } else if cmd == "resume" {
                    self.paused = false
                    emitStatus("listening")
                }
            }
        }
    }

    static func confidence(of result: SFSpeechRecognitionResult) -> Double {
        let segments = result.bestTranscription.segments
        guard !segments.isEmpty else { return 0.5 }
        let total = segments.reduce(0.0) { $0 + Double($1.confidence) }
        return total / Double(segments.count)
    }
}

// MARK: - Entry point

parseArgs()

// Listener itself is only ever reached via weak captures (the audio tap closure, its
// timers, the recognition task callback) so nothing keeps it alive on its own -- without
// a strong top-level reference it's deallocated (tearing down its AVAudioEngine) the
// moment the speech-auth callback below returns, right after start() kicks everything off.
var activeListener: Listener?

signal(SIGTERM) { _ in exit(0) }
signal(SIGINT) { _ in exit(0) }

requestMicAuth { micGranted in
    guard micGranted else {
        emitStatus(
            "error",
            detail: "Microphone access denied. Enable it in System Settings -> Privacy & Security -> "
                + "Microphone for this app (it may be listed as Terminal/iTerm/Visual Studio Code)."
        )
        exit(1)
    }
    requestSpeechAuth { speechGranted in
        guard speechGranted else {
            emitStatus(
                "error",
                detail: "Speech recognition access denied. Enable it in System Settings -> Privacy & "
                    + "Security -> Speech Recognition for this app."
            )
            exit(1)
        }
        guard let recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US")) else {
            emitStatus("error", detail: "en-US speech recognizer is unavailable on this system.")
            exit(1)
        }
        if !recognizer.supportsOnDeviceRecognition && !allowServer {
            emitStatus(
                "error",
                detail: "On-device speech recognition isn't available. Enable Dictation in System "
                    + "Settings -> Keyboard and download the English model, or pass --allow-server to "
                    + "use Apple's server-based recognition instead."
            )
            exit(1)
        }
        let listener = Listener(recognizer: recognizer)
        activeListener = listener
        emitStatus("listening")
        listener.start()
    }
}

RunLoop.main.run()
