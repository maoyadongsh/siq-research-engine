import Capacitor
import Foundation

@objc(MeetingCapturePlugin)
public final class MeetingCapturePlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "MeetingCapturePlugin"
    public let jsName = "MeetingCapture"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "prepare", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "start", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "pause", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "resume", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "stop", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "getStatus", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "getCheckpoints", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "getLocalPlaybackAsset", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "retryPendingUploads", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "recoverPendingCaptures", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "rollover", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "playLocalPlayback", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "pausePlayback", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "resumePlayback", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "seekPlayback", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "getPlaybackStatus", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "switchToServerPlayback", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "discardLocalCapture", returnType: CAPPluginReturnPromise)
    ]

    private let controller = MeetingCaptureController()

    public override func load() {
        controller.onEvent = { [weak self] name, payload in
            self?.notifyListeners(name, data: payload)
        }
        guard let trustedAPIOrigin = nonEmpty(
            Bundle.main.object(forInfoDictionaryKey: "SIQMeetingAPIOrigin") as? String
        ) else { return }
        DispatchQueue.meetingCapture.async {
            self.controller.bootstrapRecovery(trustedAPIOrigin: trustedAPIOrigin)
        }
    }

    @objc func prepare(_ call: CAPPluginCall) {
        guard let meetingId = nonEmpty(call.getString("meetingId")),
              let captureId = nonEmpty(call.getString("captureId")),
              let captureToken = nonEmpty(call.getString("captureToken")),
              let deviceInstallationId = nonEmpty(call.getString("deviceInstallationId")),
              deviceInstallationId.count >= 16,
              let apiBaseURL = nonEmpty(call.getString("apiBaseUrl")),
              let trustedAPIOrigin = nonEmpty(Bundle.main.object(forInfoDictionaryKey: "SIQMeetingAPIOrigin") as? String),
              let streamEpoch = call.getInt("streamEpoch"),
              let audioObject = call.getObject("audioConfig") else {
            reject(call, .invalidArgument("prepare options"))
            return
        }
        let encoding = audioObject["encoding"] as? String
        let sampleRate = numericInt(audioObject["sampleRate"])
        let channels = numericInt(audioObject["channels"])
        guard encoding == "pcm_s16le", sampleRate == meetingCaptureSampleRate, channels == 1 else {
            reject(call, .invalidArgument("audioConfig"))
            return
        }
        let audio = MeetingCaptureAudioConfiguration(
            batchDurationMs: boundedInt(audioObject["batchDurationMs"], fallback: 5_000, minimum: 1_000, maximum: 30_000)
        )
        let limits = MeetingCaptureLimits(
            maxBatchBytes: boundedInt(call.getInt("maxBatchBytes"), fallback: 1_048_576, minimum: 32_000, maximum: 8_388_608),
            maxTotalBytes: boundedInt(call.getInt("maxTotalBytes"), fallback: 1_500_000_000, minimum: 1_048_576, maximum: 2_000_000_000),
            maxDurationSeconds: boundedInt(call.getInt("maxDurationSeconds"), fallback: 14_400, minimum: 60, maximum: 28_800)
        )
        controller.prepare(
            meetingId: meetingId,
            captureId: captureId,
            captureToken: captureToken,
            userBearerToken: nonEmpty(call.getString("userBearerToken")),
            deviceInstallationId: deviceInstallationId,
            apiBaseURL: apiBaseURL,
            trustedAPIOrigin: trustedAPIOrigin,
            streamEpoch: streamEpoch,
            audio: audio,
            limits: limits
        ) { result in
            DispatchQueue.main.async {
                switch result {
                case .success(let status): call.resolve(["status": status.dictionary])
                case .failure(let error): self.reject(call, error)
                }
            }
        }
    }

    @objc func start(_ call: CAPPluginCall) {
        resolveStatus(call) { try controller.start() }
    }

    @objc func pause(_ call: CAPPluginCall) {
        let reason = nonEmpty(call.getString("reason")) ?? "user"
        DispatchQueue.meetingCapture.async {
            self.controller.pause(reason: reason) { result in
                DispatchQueue.main.async {
                    switch result {
                    case .success(let status): call.resolve(["status": status.dictionary])
                    case .failure(let error): self.reject(call, error)
                    }
                }
            }
        }
    }

    @objc func resume(_ call: CAPPluginCall) {
        resolveStatus(call) { try controller.resume() }
    }

    @objc func stop(_ call: CAPPluginCall) {
        DispatchQueue.meetingCapture.async {
            self.controller.stop { result in
                DispatchQueue.main.async {
                    switch result {
                    case .success(let stopped):
                        let playback: Any = stopped.1.map { $0.dictionary as Any } ?? NSNull()
                        call.resolve(["status": stopped.0.dictionary, "playbackAsset": playback])
                    case .failure(let error):
                        self.reject(call, error)
                    }
                }
            }
        }
    }

    @objc func getStatus(_ call: CAPPluginCall) {
        resolveStatus(call) { try controller.status() }
    }

    @objc func getCheckpoints(_ call: CAPPluginCall) {
        DispatchQueue.meetingCapture.async {
            do {
                try self.controller.checkpoints { result in
                    DispatchQueue.main.async {
                        switch result {
                        case .success(let checkpoints): call.resolve(["checkpoints": checkpoints])
                        case .failure(let error): self.reject(call, error)
                        }
                    }
                }
            } catch {
                DispatchQueue.main.async { self.reject(call, self.captureError(error)) }
            }
        }
    }

    @objc func getLocalPlaybackAsset(_ call: CAPPluginCall) {
        DispatchQueue.meetingCapture.async {
            let playback: Any = self.controller.playbackAsset().map { $0.dictionary as Any } ?? NSNull()
            DispatchQueue.main.async { call.resolve(["playbackAsset": playback]) }
        }
    }

    @objc func retryPendingUploads(_ call: CAPPluginCall) {
        resolveStatus(call) { try controller.retryPendingUploads() }
    }

    @objc func recoverPendingCaptures(_ call: CAPPluginCall) {
        DispatchQueue.meetingCapture.async {
            do {
                let captures = try self.controller.recoveredStatuses()
                DispatchQueue.main.async { call.resolve(["captures": captures]) }
            } catch {
                DispatchQueue.main.async { self.reject(call, self.captureError(error)) }
            }
        }
    }

    @objc func rollover(_ call: CAPPluginCall) {
        DispatchQueue.meetingCapture.async {
            do {
                try self.controller.rollover { result in
                    DispatchQueue.main.async {
                        switch result {
                        case .success(let response): call.resolve(["rollover": response.dictionary])
                        case .failure(let error): self.reject(call, error)
                        }
                    }
                }
            } catch {
                DispatchQueue.main.async { self.reject(call, self.captureError(error)) }
            }
        }
    }

    @objc func playLocalPlayback(_ call: CAPPluginCall) {
        guard let handle = nonEmpty(call.getString("handle")) else {
            reject(call, .invalidArgument("playback handle"))
            return
        }
        resolvePlaybackStatus(call) { try controller.playLocal(handle: handle) }
    }

    @objc func pausePlayback(_ call: CAPPluginCall) {
        resolvePlaybackStatus(call) { controller.pausePlayback() }
    }

    @objc func resumePlayback(_ call: CAPPluginCall) {
        resolvePlaybackStatus(call) { try controller.resumePlayback() }
    }

    @objc func seekPlayback(_ call: CAPPluginCall) {
        guard let positionMs = call.getInt("positionMs"), positionMs >= 0 else {
            reject(call, .invalidArgument("playback position"))
            return
        }
        resolvePlaybackStatus(call) { try controller.seekPlayback(positionMs: Int64(positionMs)) }
    }

    @objc func getPlaybackStatus(_ call: CAPPluginCall) {
        resolvePlaybackStatus(call) { controller.playbackStatus() }
    }

    @objc func switchToServerPlayback(_ call: CAPPluginCall) {
        guard let handle = nonEmpty(call.getString("handle")),
              let serverURL = nonEmpty(call.getString("serverUrl")) else {
            reject(call, .invalidArgument("server playback options"))
            return
        }
        DispatchQueue.meetingCapture.async {
            do {
                try self.controller.switchToServerPlayback(handle: handle, serverURL: serverURL) { result in
                    DispatchQueue.main.async {
                        switch result {
                        case .success(let status): call.resolve(["playback": status.dictionary])
                        case .failure(let error): self.reject(call, error)
                        }
                    }
                }
            } catch {
                DispatchQueue.main.async { self.reject(call, self.captureError(error)) }
            }
        }
    }

    @objc func discardLocalCapture(_ call: CAPPluginCall) {
        guard let confirmed = call.getBool("confirmedServerComplete") else {
            reject(call, .invalidArgument("confirmedServerComplete"))
            return
        }
        DispatchQueue.meetingCapture.async {
            self.controller.discard(confirmedServerComplete: confirmed) { result in
                DispatchQueue.main.async {
                    switch result {
                    case .success(let receipt):
                        call.resolve([
                            "discarded": true,
                            "cleanupReceipt": receipt.dictionary
                        ])
                    case .failure(let error):
                        self.reject(call, error)
                    }
                }
            }
        }
    }

    private func resolveStatus(_ call: CAPPluginCall, operation: @escaping () throws -> MeetingCaptureStatus) {
        DispatchQueue.meetingCapture.async {
            do {
                let status = try operation()
                DispatchQueue.main.async { call.resolve(["status": status.dictionary]) }
            } catch {
                DispatchQueue.main.async { self.reject(call, self.captureError(error)) }
            }
        }
    }

    private func resolvePlaybackStatus(
        _ call: CAPPluginCall,
        operation: @escaping () throws -> MeetingPlaybackStatus
    ) {
        DispatchQueue.meetingCapture.async {
            do {
                let status = try operation()
                DispatchQueue.main.async { call.resolve(["playback": status.dictionary]) }
            } catch {
                DispatchQueue.main.async { self.reject(call, self.captureError(error)) }
            }
        }
    }

    private func reject(_ call: CAPPluginCall, _ error: MeetingCaptureError) {
        call.reject(error.code, error.code)
    }

    private func captureError(_ error: Error) -> MeetingCaptureError {
        error as? MeetingCaptureError ?? .invalidState("native operation failed")
    }

    private func nonEmpty(_ value: String?) -> String? {
        guard let value = value?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty else { return nil }
        return value
    }

    private func boundedInt(_ value: Any?, fallback: Int, minimum: Int, maximum: Int) -> Int {
        let number = numericInt(value) ?? fallback
        return min(maximum, max(minimum, number))
    }

    private func numericInt(_ value: Any?) -> Int? {
        if let int = value as? Int { return int }
        if let number = value as? NSNumber { return number.intValue }
        if let double = value as? Double { return Int(double) }
        return nil
    }
}
