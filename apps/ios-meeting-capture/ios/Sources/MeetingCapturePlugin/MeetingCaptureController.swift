import Foundation

final class MeetingCaptureController {
    typealias StopResult = (MeetingCaptureStatus, MeetingLocalPlaybackAsset?)

    private let keychain = MeetingCaptureKeychain()
    private let recorder = MeetingCaptureRecorder()
    private let serverClient = MeetingCaptureServerClient()
    private let playbackController = MeetingCapturePlaybackController()
    private var store: MeetingCaptureStore?
    private var uploader: MeetingCaptureUploader?
    private var recoveredStores: [String: MeetingCaptureStore] = [:]
    private var recoveredUploaders: [String: MeetingCaptureUploader] = [:]
    private var serverCheckpoints: [String: MeetingServerCheckpoint] = [:]
    private var foregroundBearerToken: String?
    private var recoveryBootstrapped = false
    private var userStopped = false
    private var stopRequested = false
    private var reconfigurationPending = false
    private var stopCompletions: [(Result<StopResult, MeetingCaptureError>) -> Void] = []

    var onEvent: ((String, [String: Any]) -> Void)?

    init() {
        recorder.onPCM = { [weak self] data, monotonicNs in
            DispatchQueue.meetingCapture.async { self?.consumePCM(data, monotonicNs: monotonicNs) }
        }
        recorder.onInterrupted = { [weak self] reason, _ in
            DispatchQueue.meetingCapture.async { self?.captureInterrupted(reason: reason) }
        }
        recorder.onInterruptionEnded = { [weak self] durationNs, shouldResume in
            DispatchQueue.meetingCapture.async {
                self?.interruptionEnded(durationNs: durationNs, shouldResume: shouldResume)
            }
        }
        recorder.onConfigurationChanged = { [weak self] reason in
            DispatchQueue.meetingCapture.async { self?.configurationChanged(reason: reason) }
        }
        recorder.onError = { [weak self] error in
            DispatchQueue.meetingCapture.async { self?.captureFailed(error) }
        }
    }

    func bootstrapRecovery(trustedAPIOrigin: String) {
        guard !recoveryBootstrapped else { return }
        recoveryBootstrapped = true
        let coordinator = MeetingCaptureRecoveryCoordinator.shared
        for recovered in coordinator.bootstrapNow(trustedAPIOrigin: trustedAPIOrigin) {
            do {
                let manifest = try recovered.store.currentManifest()
                recoveredStores[manifest.captureId] = recovered.store
                if let recoveredUploader = recovered.uploader {
                    configure(recoveredUploader, store: recovered.store)
                    recoveredUploaders[manifest.captureId] = recoveredUploader
                }
                if store == nil {
                    store = recovered.store
                    uploader = recovered.uploader
                    userStopped = manifest.state == .stopped
                }
            } catch let error as MeetingCaptureError {
                emit("capture.error", ["code": error.code, "recoverable": error.recoverable])
            } catch {
                emit("capture.error", [
                    "code": MeetingCaptureError.storageUnavailable.code,
                    "recoverable": true
                ])
            }
        }
        for error in coordinator.recoveryErrors {
            emit("capture.error", ["code": error.code, "recoverable": error.recoverable])
        }
    }

    func prepare(
        meetingId: String,
        captureId: String,
        captureToken: String,
        userBearerToken: String?,
        deviceInstallationId: String,
        apiBaseURL: String,
        trustedAPIOrigin: String,
        streamEpoch: Int,
        audio: MeetingCaptureAudioConfiguration,
        limits: MeetingCaptureLimits,
        completion: @escaping (Result<MeetingCaptureStatus, MeetingCaptureError>) -> Void
    ) {
        recorder.requestPermission { [weak self] permission in
            guard let self else { return }
            switch permission {
            case .failure(let error):
                completion(.failure(error))
            case .success:
                DispatchQueue.meetingCapture.async {
                    do {
                        if let activeStore = self.store {
                            let active = try activeStore.currentManifest()
                            guard active.captureId == captureId ||
                                    (active.state != .recording && active.state != .stopping && !self.stopRequested) else {
                                throw MeetingCaptureError.invalidState("another capture is still active")
                            }
                        }
                        if let existingStore = self.recoveredStores[captureId] {
                            let existing = try existingStore.prepare(
                                meetingId: meetingId,
                                captureId: captureId,
                                apiBaseURL: apiBaseURL,
                                trustedAPIOrigin: trustedAPIOrigin,
                                streamEpoch: streamEpoch,
                                audio: audio,
                                limits: limits
                            )
                            guard existing.state != .recording && existing.state != .stopping && !self.stopRequested else {
                                throw MeetingCaptureError.invalidState("capture is already active")
                            }
                            try self.keychain.store(
                                token: captureToken,
                                deviceInstallationId: deviceInstallationId,
                                captureId: captureId
                            )
                            self.clearForegroundAuthorization()
                            self.foregroundBearerToken = userBearerToken
                            self.store = existingStore
                            self.userStopped = existing.state == .stopped
                            if let existingUploader = self.recoveredUploaders[captureId] {
                                self.uploader = existingUploader
                            } else {
                                let existingUploader = try MeetingCaptureUploader(
                                    store: existingStore,
                                    keychain: self.keychain
                                )
                                self.configure(existingUploader, store: existingStore)
                                self.recoveredUploaders[captureId] = existingUploader
                                self.uploader = existingUploader
                            }
                            self.uploader?.setForegroundBearerToken(userBearerToken)
                            completion(.success(MeetingCaptureStatus(manifest: existing)))
                            try? self.uploader?.refreshCheckpointAndSchedule()
                            if existing.state == .stopped {
                                try? self.uploader?.requestSealWhenSynchronized()
                            }
                            return
                        }
                        let store = try MeetingCaptureStore()
                        let manifest = try store.prepare(
                            meetingId: meetingId,
                            captureId: captureId,
                            apiBaseURL: apiBaseURL,
                            trustedAPIOrigin: trustedAPIOrigin,
                            streamEpoch: streamEpoch,
                            audio: audio,
                            limits: limits
                        )
                        try self.keychain.store(
                            token: captureToken,
                            deviceInstallationId: deviceInstallationId,
                            captureId: captureId
                        )
                        let uploader = try MeetingCaptureUploader(store: store, keychain: self.keychain)
                        self.configure(uploader, store: store)
                        self.clearForegroundAuthorization()
                        self.foregroundBearerToken = userBearerToken
                        uploader.setForegroundBearerToken(userBearerToken)
                        self.store = store
                        self.uploader = uploader
                        self.recoveredStores[captureId] = store
                        self.recoveredUploaders[captureId] = uploader
                        try MeetingCaptureRecoveryCoordinator.shared.register(store: store, uploader: uploader)
                        self.userStopped = manifest.state == .stopped
                        self.stopRequested = false
                        completion(.success(MeetingCaptureStatus(manifest: manifest)))
                        try? uploader.schedulePendingUploads()
                    } catch let error as MeetingCaptureError {
                        completion(.failure(error))
                    } catch {
                        completion(.failure(.storageUnavailable))
                    }
                }
            }
        }
    }

    func start() throws -> MeetingCaptureStatus {
        guard let store else { throw MeetingCaptureError.invalidState("prepare required") }
        guard !stopRequested else { throw MeetingCaptureError.invalidState("stop is pending") }
        try store.startWriting()
        userStopped = false
        do {
            try recorder.start()
        } catch {
            try? store.updateState(.error, errorCode: "native_capture.audio_start_failed")
            clearForegroundAuthorization()
            throw MeetingCaptureError.invalidState("audio start failed")
        }
        let status = MeetingCaptureStatus(manifest: try store.currentManifest())
        emit("capture.started", status.dictionary)
        return status
    }

    func pause(
        reason: String,
        completion: @escaping (Result<MeetingCaptureStatus, MeetingCaptureError>) -> Void
    ) {
        guard let store else {
            completion(.failure(.invalidState("prepare required")))
            return
        }
        guard !stopRequested else {
            completion(.failure(.invalidState("stop is pending")))
            return
        }
        recorder.pauseAndDrain { [weak self, weak store] in
            DispatchQueue.meetingCapture.async {
                guard let self, let store, self.store === store else {
                    completion(.failure(.invalidState("capture changed while pause was pending")))
                    return
                }
                guard !self.stopRequested else {
                    completion(.failure(.invalidState("stop is pending")))
                    return
                }
                do {
                    try store.pause(reason: reason, interrupted: reason != "user")
                    let status = MeetingCaptureStatus(manifest: try store.currentManifest())
                    if reason != "user" {
                        self.emit("capture.interrupted", [
                            "reason": reason,
                            "startSample": status.recordedThroughSample
                        ])
                    }
                    completion(.success(status))
                } catch let error as MeetingCaptureError {
                    completion(.failure(error))
                } catch {
                    completion(.failure(.storageUnavailable))
                }
            }
        }
    }

    func resume() throws -> MeetingCaptureStatus {
        guard !userStopped, !stopRequested, let store else {
            throw MeetingCaptureError.invalidState("capture is stopped")
        }
        try store.startWriting()
        do {
            try recorder.resume()
        } catch {
            try? store.updateState(.error, errorCode: "native_capture.audio_resume_failed")
            clearForegroundAuthorization()
            throw MeetingCaptureError.invalidState("audio resume failed")
        }
        let status = MeetingCaptureStatus(manifest: try store.currentManifest())
        emit("capture.resumed", status.dictionary)
        return status
    }

    func stop(completion: @escaping (Result<StopResult, MeetingCaptureError>) -> Void) {
        guard let store else {
            completion(.failure(.invalidState("prepare required")))
            return
        }
        if userStopped {
            do {
                let result = try store.stop()
                try? uploader?.requestSealWhenSynchronized()
                clearForegroundAuthorization()
                completion(.success((MeetingCaptureStatus(manifest: result.0), result.1)))
            } catch let error as MeetingCaptureError {
                completion(.failure(error))
            } catch {
                completion(.failure(.storageUnavailable))
            }
            return
        }
        stopCompletions.append(completion)
        guard !stopRequested else { return }
        stopRequested = true
        recorder.stopAndDrain { [weak self] in
            DispatchQueue.meetingCapture.async { self?.finishStop() }
        }
    }

    func status() throws -> MeetingCaptureStatus {
        guard let store else { return MeetingCaptureStatus() }
        return MeetingCaptureStatus(manifest: try store.currentManifest())
    }

    func checkpoints(
        completion: @escaping (Result<[String: Any], MeetingCaptureError>) -> Void
    ) throws {
        guard let store, let uploader else {
            throw MeetingCaptureError.invalidState("prepare required")
        }
        try uploader.refreshCheckpointAndSchedule { result in
            switch result {
            case .success(let checkpoint):
                do { completion(.success(try store.checkpointDictionary(server: checkpoint))) }
                catch let error as MeetingCaptureError { completion(.failure(error)) }
                catch { completion(.failure(.storageUnavailable)) }
            case .failure(let error):
                completion(.failure(error))
            }
        }
    }

    func playbackAsset() -> MeetingLocalPlaybackAsset? {
        store?.playbackAsset()
    }

    func retryPendingUploads() throws -> MeetingCaptureStatus {
        guard let store, let uploader else {
            throw MeetingCaptureError.invalidState("prepare required")
        }
        try uploader.refreshCheckpointAndSchedule()
        if try store.currentManifest().state == .stopped {
            try uploader.requestSealWhenSynchronized()
        }
        return try status()
    }

    func recoveredStatuses() throws -> [[String: Any]] {
        try recoveredStores.values
            .map { MeetingCaptureStatus(manifest: try $0.currentManifest()).dictionary }
            .sorted {
                String(describing: $0["captureId"] ?? "") < String(describing: $1["captureId"] ?? "")
            }
    }

    func rollover(
        completion: @escaping (Result<MeetingServerRolloverResponse, MeetingCaptureError>) -> Void
    ) throws {
        guard let store, let uploader else {
            throw MeetingCaptureError.invalidState("prepare required")
        }
        let pending = try store.beginRollover()
        uploader.synchronize { [weak self] synchronized in
            DispatchQueue.meetingCapture.async {
                guard let self else { return }
                switch synchronized {
                case .success:
                    self.performRollover(
                        store: store,
                        uploader: uploader,
                        pending: pending,
                        completion: completion
                    )
                case .failure(let error):
                    completion(.failure(error))
                }
            }
        }
    }

    func playLocal(handle: String) throws -> MeetingPlaybackStatus {
        guard let store else { throw MeetingCaptureError.invalidState("prepare required") }
        return try playbackController.playLocal(store: store, handle: handle)
    }

    func pausePlayback() -> MeetingPlaybackStatus {
        playbackController.pause()
    }

    func seekPlayback(positionMs: Int64) throws -> MeetingPlaybackStatus {
        try playbackController.seek(positionMs: positionMs)
    }

    func playbackStatus() -> MeetingPlaybackStatus {
        playbackController.status()
    }

    func switchToServerPlayback(
        handle: String,
        serverURL: String,
        completion: @escaping (Result<MeetingPlaybackStatus, MeetingCaptureError>) -> Void
    ) throws {
        guard let store else { throw MeetingCaptureError.invalidState("prepare required") }
        let manifest = try store.currentManifest()
        guard serverCheckpoints[manifest.captureId]?.finalizationCheckpoint.serverPlaybackState == "ready" else {
            throw MeetingCaptureError.invalidState("server playback is not ready")
        }
        playbackController.switchToServer(
            store: store,
            handle: handle,
            serverURL: serverURL,
            completion: completion
        )
    }

    func discard(confirmedServerComplete: Bool) throws -> Bool {
        guard confirmedServerComplete else {
            throw MeetingCaptureError.invalidState("server ingest is incomplete")
        }
        throw MeetingCaptureError.invalidState("verified cleanup receipt is unavailable")
    }

    private func consumePCM(_ data: Data, monotonicNs: UInt64) {
        guard !userStopped, let store else { return }
        do {
            let batches = try store.appendPCM(data, capturedMonotonicNs: monotonicNs)
            let status = MeetingCaptureStatus(manifest: try store.currentManifest())
            emit("capture.progress", [
                "recordedThroughSample": status.recordedThroughSample,
                "manifestRevision": status.manifestRevision,
                "pendingUploadCount": status.pendingUploadCount
            ])
            for batch in batches {
                emit("batch.sealed", MeetingCaptureBatchEvent(batch: batch).dictionary)
            }
            if !batches.isEmpty { try? uploader?.schedulePendingUploads() }
        } catch let error as MeetingCaptureError {
            captureFailed(error)
        } catch {
            captureFailed(.storageUnavailable)
        }
    }

    private func captureInterrupted(reason: String) {
        guard !userStopped, !stopRequested, let store,
              (try? store.currentManifest().state) == .recording else { return }
        do {
            try store.pause(reason: reason, interrupted: true)
            let status = MeetingCaptureStatus(manifest: try store.currentManifest())
            emit("capture.interrupted", [
                "reason": reason,
                "startSample": status.recordedThroughSample
            ])
        } catch {
            captureFailed(.storageUnavailable)
        }
    }

    private func interruptionEnded(durationNs: UInt64, shouldResume: Bool) {
        guard !userStopped, !stopRequested, let store else { return }
        do {
            if let materialized = try store.recordGap(
                durationNs: durationNs,
                reason: "audio_session_interruption"
            ) {
                guard let streamEpoch = materialized.gap.streamEpoch,
                      let fromSequence = materialized.gap.fromSequence,
                      let toSequence = materialized.gap.toSequence else {
                    throw MeetingCaptureError.corruptManifest
                }
                emit("capture.gap.materialized", [
                    "reason": materialized.gap.reason,
                    "startSample": materialized.gap.startSample,
                    "endSample": materialized.gap.endSample,
                    "streamEpoch": streamEpoch,
                    "fromSequence": fromSequence,
                    "toSequence": toSequence
                ])
            }
            guard shouldResume else { return }
            _ = try resume()
        } catch let error as MeetingCaptureError {
            captureFailed(error)
        } catch {
            captureFailed(.invalidState("interruption recovery failed"))
        }
    }

    private func configurationChanged(reason: String) {
        guard !userStopped, !stopRequested, let store,
              (try? store.currentManifest().state) == .recording,
              !reconfigurationPending else { return }
        reconfigurationPending = true
        let recoveryStartedNs = DispatchTime.now().uptimeNanoseconds
        recorder.stopForReconfigurationAndDrain { [weak self, weak store] in
            DispatchQueue.meetingCapture.async {
                guard let self, let store else { return }
                self.finishConfigurationChange(
                    reason: reason,
                    recoveryStartedNs: recoveryStartedNs,
                    store: store
                )
            }
        }
    }

    private func finishConfigurationChange(
        reason: String,
        recoveryStartedNs: UInt64,
        store: MeetingCaptureStore
    ) {
        defer { reconfigurationPending = false }
        guard !userStopped, !stopRequested, self.store === store,
              (try? store.currentManifest().state) == .recording else { return }
        do {
            try store.pause(reason: reason, interrupted: true)
            let status = MeetingCaptureStatus(manifest: try store.currentManifest())
            emit("capture.interrupted", [
                "reason": reason,
                "startSample": status.recordedThroughSample
            ])
            let durationNs = DispatchTime.now().uptimeNanoseconds - recoveryStartedNs
            let gapReason = reason == "media_services_reset" ? "media_services_reset" : "route_change"
            if let materialized = try store.recordGap(durationNs: durationNs, reason: gapReason) {
                guard let streamEpoch = materialized.gap.streamEpoch,
                      let fromSequence = materialized.gap.fromSequence,
                      let toSequence = materialized.gap.toSequence else {
                    throw MeetingCaptureError.corruptManifest
                }
                emit("capture.gap.materialized", [
                    "reason": materialized.gap.reason,
                    "startSample": materialized.gap.startSample,
                    "endSample": materialized.gap.endSample,
                    "streamEpoch": streamEpoch,
                    "fromSequence": fromSequence,
                    "toSequence": toSequence
                ])
            }
            try store.startWriting()
            do {
                try recorder.start()
            } catch {
                try? store.pause(reason: reason, interrupted: true)
                throw MeetingCaptureError.invalidState("audio configuration restart failed")
            }
            emit("capture.resumed", MeetingCaptureStatus(manifest: try store.currentManifest()).dictionary)
        } catch let error as MeetingCaptureError {
            captureFailed(error)
        } catch {
            captureFailed(.invalidState("audio configuration recovery failed"))
        }
    }

    private func captureFailed(_ error: MeetingCaptureError) {
        if !error.recoverable {
            recorder.stop()
            try? store?.updateState(.error, errorCode: error.code)
            clearForegroundAuthorization()
        }
        emit("capture.error", ["code": error.code, "recoverable": error.recoverable])
    }

    private func finishStop() {
        guard let store else {
            resolveStopCompletions(.failure(.invalidState("prepare required")))
            return
        }
        do {
            let (manifest, asset) = try store.stop()
            userStopped = true
            stopRequested = false
            let status = MeetingCaptureStatus(manifest: manifest)
            emit("capture.stopped", status.dictionary)
            emit("local.playback.ready", asset.dictionary)
            try? uploader?.schedulePendingUploads()
            try? uploader?.requestSealWhenSynchronized()
            clearForegroundAuthorization()
            resolveStopCompletions(.success((status, asset)))
        } catch let error as MeetingCaptureError {
            stopRequested = false
            resolveStopCompletions(.failure(error))
        } catch {
            stopRequested = false
            resolveStopCompletions(.failure(.storageUnavailable))
        }
    }

    private func resolveStopCompletions(_ result: Result<StopResult, MeetingCaptureError>) {
        let completions = stopCompletions
        stopCompletions.removeAll()
        for completion in completions { completion(result) }
    }

    private func clearForegroundAuthorization() {
        foregroundBearerToken = nil
        uploader?.setForegroundBearerToken(nil)
    }

    private func configure(_ uploader: MeetingCaptureUploader, store: MeetingCaptureStore) {
        uploader.onBatchUploaded = { [weak self, weak store] batch in
            DispatchQueue.meetingCapture.async {
                guard let self, let store, self.store === store else { return }
                self.emit("batch.uploaded", MeetingCaptureBatchEvent(batch: batch).dictionary)
            }
        }
        uploader.onCheckpoint = { [weak self, weak store] checkpoint in
            DispatchQueue.meetingCapture.async {
                guard let self, let store else { return }
                self.serverCheckpoints[checkpoint.captureId] = checkpoint
                if self.store === store,
                   let dictionary = try? store.checkpointDictionary(server: checkpoint) {
                    self.emit("capture.checkpoint", dictionary)
                }
            }
        }
        uploader.onSealed = { [weak self, weak store] checkpoint in
            DispatchQueue.meetingCapture.async {
                guard let self, let store, self.store === store else { return }
                guard let manifest = try? store.currentManifest() else { return }
                self.emit("capture.synced", [
                    "captureId": manifest.captureId,
                    "ingestComplete": checkpoint.finalizationCheckpoint.ingestComplete,
                    "serverPlaybackState": checkpoint.finalizationCheckpoint.serverPlaybackState
                ])
            }
        }
        uploader.onError = { [weak self, weak store] error in
            DispatchQueue.meetingCapture.async {
                guard let self, let store, self.store === store else { return }
                self.captureFailed(error)
            }
        }
    }

    private func performRollover(
        store: MeetingCaptureStore,
        uploader: MeetingCaptureUploader,
        pending: MeetingCapturePendingRollover,
        completion: @escaping (Result<MeetingServerRolloverResponse, MeetingCaptureError>) -> Void
    ) {
        do {
            let manifest = try store.currentManifest()
            serverClient.rollover(
                manifest: manifest,
                pending: pending,
                userBearerToken: foregroundBearerToken
            ) { [weak self] result in
                DispatchQueue.meetingCapture.async {
                    guard let self else { return }
                    switch result {
                    case .success(let response):
                        do {
                            try store.completeRollover(response)
                            self.serverCheckpoints[response.captureId] = response.checkpoint
                            try uploader.schedulePendingUploads()
                            completion(.success(response))
                        } catch let error as MeetingCaptureError {
                            completion(.failure(error))
                        } catch {
                            completion(.failure(.storageUnavailable))
                        }
                    case .failure(let error):
                        completion(.failure(error))
                    }
                }
            }
        } catch let error as MeetingCaptureError {
            completion(.failure(error))
        } catch {
            completion(.failure(.storageUnavailable))
        }
    }

    private func emit(_ name: String, _ payload: [String: Any]) {
        DispatchQueue.main.async { [weak self] in self?.onEvent?(name, payload) }
    }
}
