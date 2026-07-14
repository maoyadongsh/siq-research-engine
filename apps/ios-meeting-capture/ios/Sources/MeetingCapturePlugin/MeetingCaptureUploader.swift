import Foundation

public final class MeetingCaptureBackgroundEvents {
    public static let shared = MeetingCaptureBackgroundEvents()
    private let lock = NSLock()
    private var completionHandlers: [String: () -> Void] = [:]

    private init() {}

    public func retainCompletionHandler(identifier: String, handler: @escaping () -> Void) {
        lock.lock()
        completionHandlers[identifier] = handler
        lock.unlock()
    }

    func finish(identifier: String) {
        lock.lock()
        let handler = completionHandlers.removeValue(forKey: identifier)
        lock.unlock()
        DispatchQueue.main.async { handler?() }
    }
}

final class MeetingCaptureUploader: NSObject, URLSessionDelegate, URLSessionTaskDelegate, URLSessionDataDelegate {
    private struct BatchACK: Decodable {
        let capture_id: String
        let stream_epoch: Int
        let sequence: Int
        let sha256: String
        let byte_size: Int
        let checkpoint: MeetingServerCheckpoint
    }

    static let sessionIdentifierPrefix = "com.siqresearch.meeting-capture.upload.v1"
    private static let maxResponseBytes = 65_536

    private let store: MeetingCaptureStore
    private let keychain: MeetingCaptureKeychain
    private let serverClient = MeetingCaptureServerClient()
    private let captureId: String
    private let sessionIdentifier: String
    private let lock = NSLock()
    private var scheduledKeys = Set<String>()
    private var responseBodies: [Int: Data] = [:]
    private var oversizedResponses = Set<Int>()
    private var restoredTasksLoaded = false
    private var pendingScheduleRequested = false
    private var queuedKeys: [String] = []
    private var completedDuringRestore = Set<String>()
    private var sealRequested = false
    private var sealInFlight = false
    private var lastCheckpoint: MeetingServerCheckpoint?
    private var checkpointRequestsInFlight = 0
    private var synchronizationCompletions: [
        (Result<MeetingServerCheckpoint, MeetingCaptureError>) -> Void
    ] = []
    private var foregroundBearerToken: String?
    private lazy var session: URLSession = {
        let configuration = URLSessionConfiguration.background(withIdentifier: sessionIdentifier)
        configuration.sessionSendsLaunchEvents = true
        configuration.isDiscretionary = false
        configuration.waitsForConnectivity = true
        configuration.allowsCellularAccess = true
        configuration.httpMaximumConnectionsPerHost = 1
        return URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
    }()

    var onBatchUploaded: ((MeetingCaptureBatch) -> Void)?
    var onCheckpoint: ((MeetingServerCheckpoint) -> Void)?
    var onSealed: ((MeetingServerCheckpoint) -> Void)?
    var onError: ((MeetingCaptureError) -> Void)?

    init(store: MeetingCaptureStore, keychain: MeetingCaptureKeychain) throws {
        self.store = store
        self.keychain = keychain
        captureId = try store.currentManifest().captureId
        sessionIdentifier = "\(Self.sessionIdentifierPrefix).\(captureId)"
        super.init()
        session.getAllTasks { [weak self] tasks in
            guard let self else { return }
            DispatchQueue.meetingCapture.async {
                self.restore(tasks: tasks)
            }
        }
    }

    private func restore(tasks: [URLSessionTask]) {
        do {
            let manifest = try store.currentManifest()
            let batches = Dictionary(uniqueKeysWithValues: manifest.batches.map {
                (taskKey(captureId: manifest.captureId, batch: $0), $0)
            })
            var restored = Set<String>()
            var invalid: [URLSessionTask] = []
            for task in tasks {
                guard let key = task.taskDescription,
                      let batch = batches[key],
                      !batch.uploaded,
                      task.originalRequest?.httpMethod == "PUT",
                      task.originalRequest?.url == batchEndpoint(manifest: manifest, batch: batch),
                      task.originalRequest?.value(forHTTPHeaderField: "Authorization")?.hasPrefix("Bearer ") == true,
                      (task.originalRequest?.value(
                          forHTTPHeaderField: "X-SIQ-Device-Installation-Id"
                      )?.count ?? 0) >= 16 else {
                    invalid.append(task)
                    continue
                }
                restored.insert(key)
            }
            lock.lock()
            scheduledKeys.formUnion(restored.subtracting(completedDuringRestore))
            completedDuringRestore.removeAll()
            restoredTasksLoaded = true
            let shouldSchedule = pendingScheduleRequested
            pendingScheduleRequested = false
            lock.unlock()
            for task in invalid { task.cancel() }
            if shouldSchedule { try schedulePendingUploads() }
            drainQueue()
        } catch let error as MeetingCaptureError {
            onError?(error)
        } catch {
            onError?(.storageUnavailable)
        }
    }

    func schedulePendingUploads() throws {
        lock.lock()
        guard restoredTasksLoaded else {
            pendingScheduleRequested = true
            lock.unlock()
            return
        }
        lock.unlock()
        let manifest = try store.currentManifest()
        let candidates = try store.pendingBatches().filter { batch, _ in
            guard let pending = manifest.pendingRollover else { return true }
            return batch.streamEpoch <= pending.expectedEpoch
        }.sorted {
            ($0.0.streamEpoch, $0.0.sequence) < ($1.0.streamEpoch, $1.0.sequence)
        }
        lock.lock()
        let known = scheduledKeys.union(queuedKeys)
        queuedKeys.append(contentsOf: candidates.compactMap { batch, _ in
            let key = taskKey(captureId: captureId, batch: batch)
            return known.contains(key) ? nil : key
        })
        lock.unlock()
        drainQueue()
    }

    func setForegroundBearerToken(_ value: String?) {
        let cleaned = value?.trimmingCharacters(in: .whitespacesAndNewlines)
        foregroundBearerToken = cleaned?.isEmpty == false ? cleaned : nil
    }

    func refreshCheckpointAndSchedule(
        completion: ((Result<MeetingServerCheckpoint, MeetingCaptureError>) -> Void)? = nil
    ) throws {
        let manifest = try store.currentManifest()
        let credentials = try keychain.credentials(captureId: manifest.captureId)
        checkpointRequestsInFlight += 1
        serverClient.fetchCheckpoint(
            manifest: manifest,
            token: credentials.token,
            deviceInstallationId: credentials.deviceInstallationId
        ) { [weak self] result in
            DispatchQueue.meetingCapture.async {
                guard let self else { return }
                self.checkpointRequestsInFlight = max(0, self.checkpointRequestsInFlight - 1)
                switch result {
                case .success(let checkpoint):
                    do {
                        try self.store.reconcile(checkpoint)
                        self.lastCheckpoint = checkpoint
                        self.onCheckpoint?(checkpoint)
                        try self.schedulePendingUploads()
                        completion?(.success(checkpoint))
                    } catch let error as MeetingCaptureError {
                        self.onError?(error)
                        completion?(.failure(error))
                    } catch {
                        self.onError?(.storageUnavailable)
                        completion?(.failure(.storageUnavailable))
                    }
                case .failure(let error):
                    self.onError?(error)
                    self.resolveSynchronizations(.failure(error))
                    completion?(.failure(error))
                }
            }
        }
    }

    func synchronize(
        completion: @escaping (Result<MeetingServerCheckpoint, MeetingCaptureError>) -> Void
    ) {
        synchronizationCompletions.append(completion)
        do {
            try refreshCheckpointAndSchedule()
        } catch let error as MeetingCaptureError {
            resolveSynchronizations(.failure(error))
        } catch {
            resolveSynchronizations(.failure(.storageUnavailable))
        }
    }

    func requestSealWhenSynchronized() throws {
        lock.lock()
        sealRequested = true
        lock.unlock()
        try refreshCheckpointAndSchedule()
    }

    func invalidate() {
        session.invalidateAndCancel()
    }

    private func schedule(
        batch: MeetingCaptureBatch,
        fileURL: URL,
        manifest: MeetingCaptureManifest,
        token: String,
        deviceInstallationId: String
    ) throws {
        let key = taskKey(captureId: manifest.captureId, batch: batch)
        lock.lock()
        let alreadyScheduled = scheduledKeys.contains(key)
        if !alreadyScheduled { scheduledKeys.insert(key) }
        lock.unlock()
        guard !alreadyScheduled else { return }

        do {
            guard let endpoint = batchEndpoint(manifest: manifest, batch: batch) else {
                throw MeetingCaptureError.transportUnavailable
            }
            var request = URLRequest(url: endpoint)
            request.httpMethod = "PUT"
            request.timeoutInterval = 120
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            request.setValue(deviceInstallationId, forHTTPHeaderField: "X-SIQ-Device-Installation-Id")
            request.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
            request.setValue(batch.idempotencyKey, forHTTPHeaderField: "Idempotency-Key")
            request.setValue(String(batch.firstSample), forHTTPHeaderField: "X-SIQ-First-Sample")
            request.setValue(String(batch.sampleCount), forHTTPHeaderField: "X-SIQ-Sample-Count")
            request.setValue(String(batch.capturedMonotonicNs), forHTTPHeaderField: "X-SIQ-Captured-Monotonic-Ns")
            request.setValue(manifest.audio.encoding, forHTTPHeaderField: "X-SIQ-Audio-Encoding")
            request.setValue(String(manifest.audio.sampleRate), forHTTPHeaderField: "X-SIQ-Sample-Rate")
            request.setValue(String(manifest.audio.channels), forHTTPHeaderField: "X-SIQ-Channels")
            request.setValue(batch.sha256, forHTTPHeaderField: "X-SIQ-SHA256")
            request.setValue(String(batch.manifestRevision), forHTTPHeaderField: "X-SIQ-Manifest-Revision")
            request.setValue(String(batch.byteSize), forHTTPHeaderField: "Content-Length")
            let task = session.uploadTask(with: request, fromFile: fileURL)
            task.taskDescription = key
            task.resume()
        } catch {
            lock.lock()
            scheduledKeys.remove(key)
            lock.unlock()
            throw error
        }
    }

    private func taskKey(captureId: String, batch: MeetingCaptureBatch) -> String {
        "\(captureId):\(batch.streamEpoch):\(batch.sequence):\(batch.sha256)"
    }

    private func batchEndpoint(manifest: MeetingCaptureManifest, batch: MeetingCaptureBatch) -> URL? {
        guard var endpoint = URL(string: manifest.apiBaseURL) else { return nil }
        for component in [
            "sessions", manifest.meetingId, "native-captures", manifest.captureId,
            "batches", String(batch.streamEpoch), String(batch.sequence)
        ] {
            endpoint.appendPathComponent(component)
        }
        return endpoint
    }

    private func drainQueue() {
        lock.lock()
        guard restoredTasksLoaded, scheduledKeys.isEmpty else {
            lock.unlock()
            return
        }
        guard let nextKey = queuedKeys.first else {
            let checkpointReady = checkpointRequestsInFlight == 0
            let shouldSeal = checkpointReady && sealRequested && !sealInFlight
            lock.unlock()
            if checkpointReady, let lastCheckpoint {
                resolveSynchronizations(.success(lastCheckpoint))
            }
            if shouldSeal { attemptSeal() }
            return
        }
        queuedKeys.removeFirst()
        lock.unlock()
        do {
            let manifest = try store.currentManifest()
            let credentials = try keychain.credentials(captureId: manifest.captureId)
            guard let entry = try store.pendingBatches().first(where: {
                taskKey(captureId: manifest.captureId, batch: $0.0) == nextKey
            }) else {
                drainQueue()
                return
            }
            try schedule(
                batch: entry.0,
                fileURL: entry.1,
                manifest: manifest,
                token: credentials.token,
                deviceInstallationId: credentials.deviceInstallationId
            )
        } catch let error as MeetingCaptureError {
            lock.lock()
            queuedKeys.insert(nextKey, at: 0)
            lock.unlock()
            onError?(error)
            resolveSynchronizations(.failure(error))
        } catch {
            lock.lock()
            queuedKeys.insert(nextKey, at: 0)
            lock.unlock()
            onError?(.storageUnavailable)
            resolveSynchronizations(.failure(.storageUnavailable))
        }
    }

    private func attemptSeal() {
        do {
            let manifest = try store.currentManifest()
            guard manifest.state == .stopped,
                  manifest.pendingRollover == nil,
                  try store.pendingBatches().isEmpty else { return }
            let credentials = try keychain.credentials(captureId: manifest.captureId)
            let boundary = try store.beginFinalSealBoundary()
            lock.lock()
            guard sealRequested, !sealInFlight else {
                lock.unlock()
                return
            }
            sealInFlight = true
            lock.unlock()
            serverClient.seal(
                manifest: manifest,
                boundary: boundary,
                token: credentials.token,
                deviceInstallationId: credentials.deviceInstallationId
            ) { [weak self] result in
                DispatchQueue.meetingCapture.async {
                    guard let self else { return }
                    self.lock.lock()
                    self.sealInFlight = false
                    if case .success = result { self.sealRequested = false }
                    self.lock.unlock()
                    switch result {
                    case .success(let response):
                        do {
                            let manifest = try self.store.currentManifest()
                            guard response.schemaVersion == meetingCaptureSchemaVersion,
                                  response.capture.id == manifest.captureId,
                                  response.capture.meetingId == manifest.meetingId,
                                  response.capture.state == "sealed",
                                  response.checkpoint.captureId == manifest.captureId,
                                  response.checkpoint.meetingId == manifest.meetingId else {
                                throw MeetingCaptureError.serverResponseInvalid
                            }
                            try self.store.reconcile(response.checkpoint)
                            self.lastCheckpoint = response.checkpoint
                            self.onCheckpoint?(response.checkpoint)
                            self.declareNextGap(after: response)
                        } catch let error as MeetingCaptureError {
                            self.onError?(error)
                        } catch {
                            self.onError?(.storageUnavailable)
                        }
                    case .failure(let error):
                        self.onError?(error)
                    }
                }
            }
        } catch let error as MeetingCaptureError {
            onError?(error)
        } catch {
            onError?(.storageUnavailable)
        }
    }

    private func declareNextGap(after sealResponse: MeetingServerSealResponse) {
        do {
            let gaps = try store.pendingServerGaps()
            guard let gap = gaps.first else {
                foregroundBearerToken = nil
                onSealed?(lastCheckpoint ?? sealResponse.checkpoint)
                return
            }
            let manifest = try store.currentManifest()
            serverClient.declareGap(
                manifest: manifest,
                gap: gap,
                userBearerToken: foregroundBearerToken
            ) { [weak self] result in
                DispatchQueue.meetingCapture.async {
                    guard let self else { return }
                    switch result {
                    case .success(let response):
                        do {
                            guard response.schemaVersion == meetingCaptureSchemaVersion,
                                  response.captureId == manifest.captureId,
                                  response.checkpoint.captureId == manifest.captureId,
                                  response.checkpoint.meetingId == manifest.meetingId,
                                  let key = gap.idempotencyKey else {
                                throw MeetingCaptureError.serverResponseInvalid
                            }
                            try self.store.markGapServerDeclared(idempotencyKey: key)
                            try self.store.reconcile(response.checkpoint)
                            self.lastCheckpoint = response.checkpoint
                            self.onCheckpoint?(response.checkpoint)
                            self.declareNextGap(after: sealResponse)
                        } catch let error as MeetingCaptureError {
                            self.onError?(error)
                        } catch {
                            self.onError?(.storageUnavailable)
                        }
                    case .failure(let error):
                        self.onError?(error)
                    }
                }
            }
        } catch let error as MeetingCaptureError {
            onError?(error)
        } catch {
            onError?(.storageUnavailable)
        }
    }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        didCompleteWithError error: Error?
    ) {
        guard let key = task.taskDescription else { return }
        let components = key.split(separator: ":", omittingEmptySubsequences: false)
        guard components.count == 4,
              let epoch = Int(components[1]),
              let sequence = Int(components[2]) else { return }
        let taskCaptureId = String(components[0])
        let taskSHA256 = String(components[3])
        lock.lock()
        let tracked = scheduledKeys.remove(key) != nil
        let restoring = !restoredTasksLoaded
        if restoring && !tracked { completedDuringRestore.insert(key) }
        let body = responseBodies.removeValue(forKey: task.taskIdentifier) ?? Data()
        let oversized = oversizedResponses.remove(task.taskIdentifier) != nil
        lock.unlock()
        guard tracked || restoring else { return }

        if error != nil || oversized {
            onError?(.transportUnavailable)
            requeueAfterFailure(key)
            return
        }
        guard let response = task.response as? HTTPURLResponse else {
            onError?(.transportUnavailable)
            requeueAfterFailure(key)
            return
        }
        switch response.statusCode {
        case 200:
            guard response.url == task.originalRequest?.url,
                  response.value(forHTTPHeaderField: "Content-Type")?.lowercased().hasPrefix("application/json") == true,
                  let ack = try? JSONDecoder().decode(BatchACK.self, from: body),
                  ack.capture_id == taskCaptureId,
                  ack.capture_id == captureId,
                  ack.stream_epoch == epoch,
                  ack.sequence == sequence,
                  ack.sha256.lowercased() == taskSHA256.lowercased() else {
                onError?(.transportUnavailable)
                requeueAfterFailure(key)
                return
            }
            DispatchQueue.meetingCapture.async { [weak self] in
                self?.accept(ack: ack, taskSHA256: taskSHA256)
            }
        case 401, 403:
            onError?(.tokenUnavailable)
            requeueAfterFailure(key)
        case 409:
            onError?(.serverConflict)
            requeueAfterFailure(key)
        default:
            onError?(.transportUnavailable)
            requeueAfterFailure(key)
        }
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        lock.lock()
        defer { lock.unlock() }
        guard !oversizedResponses.contains(dataTask.taskIdentifier) else { return }
        var body = responseBodies[dataTask.taskIdentifier] ?? Data()
        guard body.count + data.count <= Self.maxResponseBytes else {
            responseBodies.removeValue(forKey: dataTask.taskIdentifier)
            oversizedResponses.insert(dataTask.taskIdentifier)
            return
        }
        body.append(data)
        responseBodies[dataTask.taskIdentifier] = body
    }

    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping (URLRequest?) -> Void
    ) {
        completionHandler(nil)
    }

    func urlSessionDidFinishEvents(forBackgroundURLSession session: URLSession) {
        MeetingCaptureBackgroundEvents.shared.finish(identifier: sessionIdentifier)
    }

    private func requeueAfterFailure(_ key: String) {
        DispatchQueue.meetingCapture.async { [weak self] in
            guard let self else { return }
            self.lock.lock()
            if !self.queuedKeys.contains(key) { self.queuedKeys.insert(key, at: 0) }
            self.lock.unlock()
            self.resolveSynchronizations(.failure(.transportUnavailable))
        }
    }

    private func resolveSynchronizations(
        _ result: Result<MeetingServerCheckpoint, MeetingCaptureError>
    ) {
        let completions = synchronizationCompletions
        synchronizationCompletions.removeAll()
        for completion in completions { completion(result) }
    }

    private func accept(ack: BatchACK, taskSHA256: String) {
        do {
            let manifest = try store.currentManifest()
            guard manifest.captureId == ack.capture_id,
                  let localBatch = manifest.batches.first(where: {
                    $0.streamEpoch == ack.stream_epoch && $0.sequence == ack.sequence
                  }),
                  localBatch.sha256.lowercased() == taskSHA256.lowercased(),
                  localBatch.byteSize == ack.byte_size,
                  ack.checkpoint.captureId == ack.capture_id,
                  ack.checkpoint.meetingId == manifest.meetingId else {
                onError?(.transportUnavailable)
                return
            }
            try store.reconcile(ack.checkpoint)
            lastCheckpoint = ack.checkpoint
            onCheckpoint?(ack.checkpoint)
            if let batch = try store.markUploaded(epoch: ack.stream_epoch, sequence: ack.sequence) {
                onBatchUploaded?(batch)
            }
            try schedulePendingUploads()
        } catch {
            onError?(.storageUnavailable)
        }
    }
}
