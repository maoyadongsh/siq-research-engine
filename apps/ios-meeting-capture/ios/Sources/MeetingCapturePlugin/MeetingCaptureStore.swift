import CryptoKit
import Foundation

final class MeetingCaptureStore {
    private let fileManager: FileManager
    private let rootURL: URL
    private var manifest: MeetingCaptureManifest?
    private var captureURL: URL?
    private var batchHandle: FileHandle?
    private var batchPartialURL: URL?
    private var batchByteCount = 0
    private var batchFirstSample: Int64 = 0
    private var batchCapturedMonotonicNs: UInt64 = 0
    private var playbackHandle: FileHandle?

    init(fileManager: FileManager = .default, rootURL: URL? = nil) throws {
        self.fileManager = fileManager
        let candidate: URL
        if let rootURL {
            candidate = rootURL
        } else {
            guard let applicationSupport = fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first else {
                throw MeetingCaptureError.storageUnavailable
            }
            candidate = applicationSupport.appendingPathComponent("SIQMeetingCaptures", isDirectory: true)
        }
        let standardized = candidate.standardizedFileURL
        self.rootURL = standardized.deletingLastPathComponent()
            .resolvingSymlinksInPath()
            .appendingPathComponent(standardized.lastPathComponent, isDirectory: true)
        try rejectSymbolicLink(self.rootURL)
        try protectDirectory(self.rootURL)
        guard self.rootURL.resolvingSymlinksInPath().standardizedFileURL == self.rootURL else {
            throw MeetingCaptureError.storageUnavailable
        }
    }

    func recoverableCaptureIds() throws -> [String] {
        let keys: Set<URLResourceKey> = [.isDirectoryKey, .isRegularFileKey, .isSymbolicLinkKey]
        return try fileManager.contentsOfDirectory(
            at: rootURL,
            includingPropertiesForKeys: Array(keys),
            options: [.skipsHiddenFiles]
        )
        .compactMap { candidate -> (String, Date)? in
            guard let values = try? candidate.resourceValues(forKeys: keys),
                  values.isDirectory == true,
                  values.isSymbolicLink != true,
                  let parsed = UUID(uuidString: candidate.lastPathComponent),
                  parsed.uuidString.lowercased() == candidate.lastPathComponent.lowercased() else {
                return nil
            }
            let manifest = candidate.appendingPathComponent("manifest.json")
            guard let manifestValues = try? manifest.resourceValues(forKeys: keys) else { return nil }
            guard manifestValues.isRegularFile == true, manifestValues.isSymbolicLink != true else { return nil }
            guard let attributes = try? fileManager.attributesOfItem(atPath: manifest.path) else { return nil }
            return (candidate.lastPathComponent, attributes[.modificationDate] as? Date ?? .distantPast)
        }
        .sorted { $0.1 > $1.1 }
        .map(\.0)
    }

    func recover(captureId: String, trustedAPIOrigin: String) throws -> MeetingCaptureManifest {
        guard let parsed = UUID(uuidString: captureId),
              parsed.uuidString.lowercased() == captureId.lowercased() else {
            throw MeetingCaptureError.invalidArgument("capture identity")
        }
        let directory = rootURL.appendingPathComponent(captureId, isDirectory: true).standardizedFileURL
        guard directory.deletingLastPathComponent().standardizedFileURL == rootURL,
              directory.path.hasPrefix(rootURL.path + "/") else {
            throw MeetingCaptureError.storageUnavailable
        }
        try rejectSymbolicLink(directory)
        guard directory.resolvingSymlinksInPath().standardizedFileURL == directory else {
            throw MeetingCaptureError.storageUnavailable
        }
        captureURL = directory
        var recovered = try readManifest()
        guard recovered.schemaVersion == meetingCaptureSchemaVersion,
              recovered.captureId == captureId,
              !recovered.meetingId.isEmpty,
              recovered.apiBaseURL == validatedAPIEndpoint(
                  recovered.apiBaseURL,
                  trustedOrigin: trustedAPIOrigin
              ).absoluteString,
              recovered.streamEpoch >= 1,
              recovered.audio.encoding == "pcm_s16le",
              recovered.audio.sampleRate == meetingCaptureSampleRate,
              recovered.audio.channels == 1,
              recovered.playbackFileName == "capture.wav",
              recovered.batches.allSatisfy({ isSafeCaptureFilename($0.fileName) }) else {
            throw MeetingCaptureError.corruptManifest
        }
        manifest = try mergeDurableBatchSidecars(into: recovered)
        try recoverOpenBatchIfNeeded()
        _ = try materializePendingGapIfNeeded()
        try recoverFinalizedPlaybackIfNeeded()
        recovered = manifest!
        if recovered.state == .recording || recovered.state == .stopping {
            recovered.state = .interrupted
            recovered.interruptionReason = "process_recovered"
            recovered.manifestRevision += 1
            manifest = recovered
            try persistManifest()
        }
        return manifest!
    }

    func prepare(
        meetingId: String,
        captureId: String,
        apiBaseURL: String,
        trustedAPIOrigin: String,
        streamEpoch: Int,
        audio: MeetingCaptureAudioConfiguration,
        limits: MeetingCaptureLimits
    ) throws -> MeetingCaptureManifest {
        guard !meetingId.isEmpty,
              let parsedCaptureId = UUID(uuidString: captureId),
              parsedCaptureId.uuidString.lowercased() == captureId.lowercased(),
              captureId.count == 36,
              streamEpoch >= 1 else {
            throw MeetingCaptureError.invalidArgument("capture identity")
        }
        let endpoint = try validatedAPIEndpoint(apiBaseURL, trustedOrigin: trustedAPIOrigin)
        let directory = rootURL.appendingPathComponent(captureId, isDirectory: true).standardizedFileURL
        guard directory.deletingLastPathComponent().standardizedFileURL == rootURL,
              directory.path.hasPrefix(rootURL.path + "/") else {
            throw MeetingCaptureError.storageUnavailable
        }
        try rejectSymbolicLink(directory)
        try protectDirectory(directory)
        guard directory.resolvingSymlinksInPath().standardizedFileURL == directory else {
            throw MeetingCaptureError.storageUnavailable
        }
        captureURL = directory

        if fileManager.fileExists(atPath: manifestURL.path) {
            let recovered = try readManifest()
            guard recovered.meetingId == meetingId,
                  recovered.captureId == captureId,
                  recovered.apiBaseURL == endpoint.absoluteString,
                  recovered.streamEpoch == streamEpoch,
                  recovered.audio == audio,
                  recovered.limits == limits,
                  recovered.playbackFileName == "capture.wav",
                  recovered.batches.allSatisfy({ isSafeCaptureFilename($0.fileName) }) else {
                throw MeetingCaptureError.corruptManifest
            }
            manifest = try mergeDurableBatchSidecars(into: recovered)
            try recoverOpenBatchIfNeeded()
            _ = try materializePendingGapIfNeeded()
            try recoverFinalizedPlaybackIfNeeded()
            if manifest?.state == .recording || manifest?.state == .stopping {
                manifest?.state = .interrupted
                manifest?.interruptionReason = "process_recovered"
                manifest?.manifestRevision += 1
                try persistManifest()
            }
            return manifest!
        }

        let now = Date()
        let created = MeetingCaptureManifest(
            meetingId: meetingId,
            captureId: captureId,
            apiBaseURL: endpoint.absoluteString,
            state: .prepared,
            streamEpoch: streamEpoch,
            streamEpochStartSample: 0,
            audio: audio,
            limits: limits,
            recordedThroughSample: 0,
            recordedAudioSamples: 0,
            nextSequence: 0,
            lastSealedSequence: -1,
            manifestRevision: 1,
            playbackFileName: "capture.wav",
            localPlaybackReady: false,
            batches: [],
            gaps: [],
            createdAt: now,
            updatedAt: now
        )
        manifest = created
        try createPlaybackFileIfNeeded()
        try persistManifest()
        return created
    }

    func startWriting() throws {
        guard var current = manifest else { throw MeetingCaptureError.invalidState("prepare required") }
        guard current.state == .prepared || current.state == .paused || current.state == .interrupted else {
            throw MeetingCaptureError.invalidState("capture cannot start")
        }
        try createPlaybackFileIfNeeded()
        current.state = .recording
        current.interruptionReason = nil
        current.updatedAt = Date()
        manifest = current
        try persistManifest()
    }

    func appendPCM(_ data: Data, capturedMonotonicNs: UInt64) throws -> [MeetingCaptureBatch] {
        guard !data.isEmpty else { return [] }
        guard data.count.isMultiple(of: 2) else { throw MeetingCaptureError.invalidArgument("unaligned PCM") }
        guard var current = manifest, current.state == .recording else {
            throw MeetingCaptureError.invalidState("capture is not recording")
        }
        let existingBytes = Int(current.recordedAudioSamples * 2)
        guard existingBytes + data.count <= current.limits.maxTotalBytes else {
            throw MeetingCaptureError.storageQuotaExceeded
        }
        let nextAudioSamples = current.recordedAudioSamples + Int64(data.count / 2)
        let maxDurationSamples = Int64(current.limits.maxDurationSeconds * current.audio.sampleRate)
        guard nextAudioSamples <= maxDurationSamples,
              current.recordedThroughSample + Int64(data.count / 2) <= maxDurationSamples else {
            throw MeetingCaptureError.storageQuotaExceeded
        }
        try assertAvailableCapacity(requiredBytes: max(data.count * 4, current.limits.maxBatchBytes * 2))

        var sealed: [MeetingCaptureBatch] = []
        var cursor = data.startIndex
        let targetBatchBytes = targetBatchBytes(current)
        while cursor < data.endIndex {
            if batchHandle == nil {
                try openBatch(firstSample: current.recordedThroughSample, capturedMonotonicNs: capturedMonotonicNs)
            }
            let available = targetBatchBytes - batchByteCount
            let length = min(available, data.distance(from: cursor, to: data.endIndex))
            let end = data.index(cursor, offsetBy: length)
            let slice = Data(data[cursor..<end])
            try batchHandle?.write(contentsOf: slice)
            try playbackHandle?.write(contentsOf: slice)
            batchByteCount += slice.count
            let samples = Int64(slice.count / 2)
            current.recordedAudioSamples += samples
            current.recordedThroughSample += samples
            current.updatedAt = Date()
            manifest = current
            cursor = end
            if batchByteCount >= targetBatchBytes, let batch = try sealOpenBatch() {
                sealed.append(batch)
                current = manifest!
            }
        }
        try batchHandle?.synchronize()
        try playbackHandle?.synchronize()
        try persistManifest()
        return sealed
    }

    func pause(reason: String?, interrupted: Bool) throws {
        guard var current = manifest, current.state == .recording else {
            throw MeetingCaptureError.invalidState("capture is not recording")
        }
        if let batch = try sealOpenBatch() {
            current = manifest!
            _ = batch
        }
        current.state = interrupted ? .interrupted : .paused
        current.interruptionReason = interrupted ? reason : nil
        current.updatedAt = Date()
        manifest = current
        try persistManifest()
    }

    func recordGap(
        durationNs: UInt64,
        reason: String
    ) throws -> MeetingCaptureGapMaterialization? {
        guard durationNs > 0, var current = manifest else { return nil }
        guard current.pendingGap == nil,
              [.paused, .interrupted].contains(current.state),
              ["audio_session_interruption", "media_services_reset", "route_change"]
                .contains(reason) else {
            throw MeetingCaptureError.invalidState("capture gap cannot be materialized")
        }
        let rawMissingSamples = Int64((Double(durationNs) / 1_000_000_000) * Double(current.audio.sampleRate))
        let missingSamples = (rawMissingSamples + 15) / 16 * 16
        guard missingSamples > 0 else { return nil }
        let maxDurationSamples = Int64(current.limits.maxDurationSeconds * current.audio.sampleRate)
        guard current.recordedThroughSample + missingSamples <= maxDurationSamples,
              current.recordedAudioSamples + missingSamples <= maxDurationSamples,
              (current.recordedAudioSamples + missingSamples) * 2 <= Int64(current.limits.maxTotalBytes) else {
            throw MeetingCaptureError.storageQuotaExceeded
        }
        try assertAvailableCapacity(requiredBytes: max(Int(missingSamples * 2), 64 * 1_024 * 1_024))
        let start = current.recordedThroughSample
        current.pendingGap = MeetingCapturePendingGap(
            streamEpoch: current.streamEpoch,
            fromSequence: current.nextSequence,
            startSample: start,
            endSample: start + missingSamples,
            reason: reason,
            detectedMonotonicNs: DispatchTime.now().uptimeNanoseconds,
            returnState: current.state
        )
        current.manifestRevision += 1
        current.updatedAt = Date()
        manifest = current
        try persistManifest()
        return try materializePendingGapIfNeeded()
    }

    func stop() throws -> (MeetingCaptureManifest, MeetingLocalPlaybackAsset) {
        guard var current = manifest else { throw MeetingCaptureError.invalidState("prepare required") }
        if current.state == .stopped, let asset = playbackAsset() {
            return (current, asset)
        }
        guard [.recording, .paused, .interrupted, .stopping].contains(current.state) else {
            throw MeetingCaptureError.invalidState("capture cannot stop")
        }
        current.state = .stopping
        current.updatedAt = Date()
        manifest = current
        try persistManifest()
        _ = try sealOpenBatch()
        try finalizePlaybackFile()
        current = manifest!
        current.state = .stopped
        current.localPlaybackReady = true
        current.interruptionReason = nil
        current.manifestRevision += 1
        current.updatedAt = Date()
        manifest = current
        try persistManifest()
        guard let asset = playbackAsset() else { throw MeetingCaptureError.storageUnavailable }
        return (current, asset)
    }

    func updateState(_ state: MeetingCaptureLifecycle, errorCode: String? = nil) throws {
        guard var current = manifest else { throw MeetingCaptureError.invalidState("prepare required") }
        current.state = state
        current.errorCode = errorCode
        current.updatedAt = Date()
        manifest = current
        try persistManifest()
    }

    func markUploaded(epoch: Int, sequence: Int) throws -> MeetingCaptureBatch? {
        guard var current = manifest,
              let index = current.batches.firstIndex(where: { $0.streamEpoch == epoch && $0.sequence == sequence }) else {
            return nil
        }
        if !current.batches[index].uploaded {
            current.batches[index].uploaded = true
            current.manifestRevision += 1
            current.updatedAt = Date()
            manifest = current
            try persistBatchSidecar(current.batches[index])
            try persistManifest()
        }
        return current.batches[index]
    }

    func reconcile(_ checkpoint: MeetingServerCheckpoint) throws {
        guard var current = manifest,
              checkpoint.schemaVersion == meetingCaptureSchemaVersion,
              checkpoint.captureId == current.captureId,
              checkpoint.meetingId == current.meetingId else {
            throw MeetingCaptureError.serverResponseInvalid
        }
        guard checkpoint.ingestCheckpoint.persistedThroughSample >= 0,
              checkpoint.ingestCheckpoint.accountedThroughSample >= 0,
              checkpoint.ingestCheckpoint.highestReceivedSample >= 0,
              checkpoint.ingestCheckpoint.receivedBatches >= 0,
              checkpoint.ingestCheckpoint.receivedBytes >= 0,
              Set(["not_ready", "pending_upload", "pending_packaging", "packaging", "ready", "failed"])
                .contains(checkpoint.finalizationCheckpoint.serverPlaybackState) else {
            throw MeetingCaptureError.serverResponseInvalid
        }
        var seenEpochs = Set<Int>()
        for epoch in checkpoint.epochs {
            let declaredUpper = epoch.declaredLastSequence ?? epoch.highestReceivedSequence
            guard epoch.streamEpoch >= 1,
                  seenEpochs.insert(epoch.streamEpoch).inserted,
                  ["active", "rolled_over", "sealed"].contains(epoch.state),
                  epoch.highestContiguousSequence >= -1,
                  epoch.highestReceivedSequence >= epoch.highestContiguousSequence,
                  declaredUpper >= epoch.highestReceivedSequence,
                  epoch.recordedThroughSample.map { $0 >= 0 && $0.isMultiple(of: 16) } ?? true,
                  epoch.missingSequenceRanges.allSatisfy({ range in
                      guard range.count == 2,
                            let start = range["start"], let end = range["end"] else { return false }
                      return start >= 0 && start <= end && end <= declaredUpper
                  }) else {
                throw MeetingCaptureError.serverResponseInvalid
            }
        }
        var changed = false
        for epoch in checkpoint.epochs {
            let missing = epoch.missingSequenceRanges
            for index in current.batches.indices where current.batches[index].streamEpoch == epoch.streamEpoch {
                let sequence = current.batches[index].sequence
                let serverHasBatch = sequence <= epoch.highestReceivedSequence && !missing.contains(where: {
                    guard let start = $0["start"], let end = $0["end"] else { return true }
                    return start <= sequence && sequence <= end
                })
                if current.batches[index].uploaded != serverHasBatch {
                    current.batches[index].uploaded = serverHasBatch
                    try persistBatchSidecar(current.batches[index])
                    changed = true
                }
            }
        }
        if changed {
            current.manifestRevision += 1
            current.updatedAt = Date()
            manifest = current
            try persistManifest()
        }
    }

    func currentManifest() throws -> MeetingCaptureManifest {
        guard let manifest else { throw MeetingCaptureError.invalidState("prepare required") }
        return manifest
    }

    func pendingBatches() throws -> [(MeetingCaptureBatch, URL)] {
        guard let manifest, captureURL != nil else { return [] }
        return try manifest.batches
            .filter { !$0.uploaded }
            .map { batch in
                let url = try safeCaptureFileURL(batch.fileName)
                let values = try url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
                guard values.isRegularFile == true, values.isSymbolicLink != true else {
                    throw MeetingCaptureError.corruptManifest
                }
                return (batch, url)
            }
    }

    func playbackAsset() -> MeetingLocalPlaybackAsset? {
        guard let manifest, manifest.localPlaybackReady else { return nil }
        return MeetingLocalPlaybackAsset(
            captureId: manifest.captureId,
            durationMs: manifest.recordedAudioSamples * 1_000 / Int64(manifest.audio.sampleRate)
        )
    }

    func playbackURL(for handle: String) throws -> URL {
        guard let manifest, manifest.localPlaybackReady,
              handle == MeetingLocalPlaybackAsset(
                  captureId: manifest.captureId,
                  durationMs: 0
              ).handle else {
            throw MeetingCaptureError.invalidArgument("playback handle")
        }
        let url = try safeCaptureFileURL(manifest.playbackFileName)
        let values = try url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
        guard values.isRegularFile == true, values.isSymbolicLink != true else {
            throw MeetingCaptureError.storageUnavailable
        }
        return url
    }

    func beginRollover() throws -> MeetingCapturePendingRollover {
        guard var current = manifest else {
            throw MeetingCaptureError.invalidState("prepare required")
        }
        if let pending = current.pendingRollover { return pending }
        guard [.recording, .paused, .interrupted].contains(current.state),
              current.streamEpoch < Int.max else {
            throw MeetingCaptureError.invalidState("capture cannot roll over")
        }
        _ = try sealOpenBatch()
        current = try currentManifest()
        let boundary = try canonicalBoundary()
        try freezeGaps(epoch: current.streamEpoch, manifestRevision: boundary.manifestRevision)
        current = try currentManifest()
        let pending = MeetingCapturePendingRollover(
            expectedEpoch: current.streamEpoch,
            nextEpoch: current.streamEpoch + 1,
            idempotencyKey: UUID().uuidString.lowercased(),
            boundary: boundary,
            createdAt: Date()
        )
        current.pendingRollover = pending
        current.streamEpoch = pending.nextEpoch
        current.streamEpochStartSample = current.recordedThroughSample
        current.nextSequence = 0
        current.lastSealedSequence = -1
        current.manifestRevision += 1
        current.updatedAt = Date()
        manifest = current
        try persistManifest()
        return pending
    }

    func completeRollover(_ response: MeetingServerRolloverResponse) throws {
        guard var current = manifest,
              let pending = current.pendingRollover,
              response.schemaVersion == meetingCaptureSchemaVersion,
              response.captureId == current.captureId,
              response.previousEpoch == pending.expectedEpoch,
              response.streamEpoch == pending.nextEpoch,
              response.checkpoint.captureId == current.captureId,
              response.checkpoint.meetingId == current.meetingId,
              response.streamTicket.count >= 32,
              response.checkpoint.realtimeCheckpoint.streamEpoch == response.streamEpoch,
              response.captureOffsetMs == pending.boundary.recordedThroughSample * 1_000 /
                Int64(current.audio.sampleRate),
              validRolloverWebsocketURL(response, meetingId: current.meetingId) else {
            throw MeetingCaptureError.serverResponseInvalid
        }
        current.pendingRollover = nil
        current.manifestRevision += 1
        current.updatedAt = Date()
        manifest = current
        try persistManifest()
        try reconcile(response.checkpoint)
    }

    func beginFinalSealBoundary() throws -> MeetingCaptureBoundary {
        guard var current = manifest, current.state == .stopped else {
            throw MeetingCaptureError.invalidState("capture is not stopped")
        }
        if let boundary = current.finalSealBoundary { return boundary }
        let boundary = try canonicalBoundary()
        try freezeGaps(epoch: current.streamEpoch, manifestRevision: boundary.manifestRevision)
        current = try currentManifest()
        current.finalSealBoundary = boundary
        current.updatedAt = Date()
        manifest = current
        try persistManifest()
        return boundary
    }

    func pendingServerGaps() throws -> [MeetingCaptureGap] {
        try currentManifest().gaps.filter { gap in
            gap.serverDeclared != true && gap.streamEpoch != nil &&
                gap.fromSequence != nil && gap.toSequence != nil &&
                gap.idempotencyKey?.isEmpty == false && gap.sealedManifestRevision != nil
        }.sorted {
            ($0.streamEpoch ?? 0, $0.fromSequence ?? 0) <
                ($1.streamEpoch ?? 0, $1.fromSequence ?? 0)
        }
    }

    func markGapServerDeclared(idempotencyKey: String) throws {
        guard var current = manifest,
              let index = current.gaps.firstIndex(where: {
                  $0.idempotencyKey == idempotencyKey
              }) else {
            throw MeetingCaptureError.serverResponseInvalid
        }
        current.gaps[index].serverDeclared = true
        current.updatedAt = Date()
        manifest = current
        try persistManifest()
    }

    private func freezeGaps(epoch: Int, manifestRevision: Int) throws {
        guard var current = manifest else { throw MeetingCaptureError.invalidState("prepare required") }
        var changed = false
        for index in current.gaps.indices where current.gaps[index].streamEpoch == epoch {
            if let existing = current.gaps[index].sealedManifestRevision, existing != manifestRevision {
                throw MeetingCaptureError.corruptManifest
            }
            if current.gaps[index].sealedManifestRevision == nil {
                current.gaps[index].sealedManifestRevision = manifestRevision
                changed = true
            }
        }
        if changed {
            current.updatedAt = Date()
            manifest = current
            try persistManifest()
        }
    }

    private func validRolloverWebsocketURL(
        _ response: MeetingServerRolloverResponse,
        meetingId: String
    ) -> Bool {
        guard let components = URLComponents(string: response.websocketURL),
              components.scheme == nil,
              components.host == nil,
              components.fragment == nil,
              components.percentEncodedPath == "/api/meetings/v1/sessions/\(meetingId)/audio",
              let query = components.queryItems,
              query.count == 1,
              query[0].name == "ticket",
              query[0].value == response.streamTicket,
              let expiresAt = serverDate(response.streamTicketExpiresAt),
              expiresAt > Date() else {
            return false
        }
        return true
    }

    private func serverDate(_ value: String) -> Date? {
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return fractional.date(from: value) ?? ISO8601DateFormatter().date(from: value)
    }

    func canonicalBoundary() throws -> MeetingCaptureBoundary {
        let current = try currentManifest()
        guard current.manifestRevision >= 1, current.manifestRevision <= Int(Int32.max) else {
            throw MeetingCaptureError.corruptManifest
        }
        let batches = current.batches
            .filter { $0.streamEpoch == current.streamEpoch }
            .sorted { $0.sequence < $1.sequence }
        var entries = try batches.map { batch -> MeetingCaptureCanonicalEntry in
            guard batch.capturedMonotonicNs <= UInt64(Int64.max) else {
                throw MeetingCaptureError.corruptManifest
            }
            return MeetingCaptureCanonicalEntry(
                sequence: batch.sequence,
                first_sample: batch.firstSample,
                sample_count: batch.sampleCount,
                captured_monotonic_ns: batch.capturedMonotonicNs,
                encoding: current.audio.encoding,
                sample_rate: current.audio.sampleRate,
                channels: current.audio.channels,
                sha256: batch.sha256
            )
        }
        entries.append(contentsOf: current.gaps
            .filter { $0.streamEpoch == current.streamEpoch }
            .flatMap { $0.manifestEntries ?? [] })
        entries.sort { $0.sequence < $1.sequence }
        let finalSequence = entries.last?.sequence ?? -1
        if !entries.isEmpty {
            guard entries.enumerated().allSatisfy({ offset, entry in
                entry.sequence == offset
            }) else {
                throw MeetingCaptureError.corruptManifest
            }
            var cursor = current.streamEpochStartSample ?? entries[0].first_sample
            guard entries[0].first_sample == cursor else { throw MeetingCaptureError.corruptManifest }
            for entry in entries {
                guard entry.first_sample == cursor else { throw MeetingCaptureError.corruptManifest }
                cursor += entry.sample_count
            }
            guard cursor == current.recordedThroughSample else { throw MeetingCaptureError.corruptManifest }
        }
        let canonical = MeetingCaptureCanonicalManifest(
            expected_epoch: current.streamEpoch,
            final_sequence: finalSequence,
            recorded_through_sample: current.recordedThroughSample,
            manifest_revision: current.manifestRevision,
            entries: entries
        )
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        let encoded = try encoder.encode(canonical)
        let digest = SHA256.hash(data: encoded).map { String(format: "%02x", $0) }.joined()
        return MeetingCaptureBoundary(
            expectedEpoch: current.streamEpoch,
            finalSequence: finalSequence,
            recordedThroughSample: current.recordedThroughSample,
            manifestRevision: current.manifestRevision,
            manifestSHA256: digest,
            entries: entries
        )
    }

    func checkpointDictionary(server: MeetingServerCheckpoint) throws -> [String: Any] {
        let current = try currentManifest()
        guard server.captureId == current.captureId, server.meetingId == current.meetingId else {
            throw MeetingCaptureError.serverResponseInvalid
        }
        let checkpointEpoch = current.pendingRollover?.expectedEpoch ?? current.streamEpoch
        guard let epoch = server.epochs.first(where: { $0.streamEpoch == checkpointEpoch }) else {
            throw MeetingCaptureError.serverResponseInvalid
        }
        return [
            "capture": [
                "recordedThroughSample": current.recordedThroughSample,
                "lastSealedSequence": current.lastSealedSequence,
                "manifestRevision": current.manifestRevision,
                "gapCount": current.gaps.count,
                "materializedGapSamples": current.gaps.reduce(0) {
                    $0 + ($1.endSample - $1.startSample)
                }
            ],
            "ingest": [
                "highestUploadedSequence": epoch.highestReceivedSequence,
                "highestContiguousSequence": epoch.highestContiguousSequence,
                "persistedThroughSample": server.ingestCheckpoint.persistedThroughSample,
                "missingSequenceRanges": epoch.missingSequenceRanges
            ],
            "realtime": [
                "streamEpoch": server.realtimeCheckpoint.streamEpoch,
                "lastAckedSequence": server.realtimeCheckpoint.lastAckedSequence,
                "consumedThroughSample": NSNull(),
                "stableOrdinal": server.realtimeCheckpoint.stableOrdinal,
                "eventCursor": server.realtimeCheckpoint.eventCursor
            ],
            "finalization": [
                "sealedThroughSample": server.captureCheckpoint.recordedThroughSample.map { $0 as Any } ?? NSNull(),
                "ingestComplete": server.finalizationCheckpoint.ingestComplete,
                "localPlaybackReady": current.localPlaybackReady,
                "serverPlaybackState": server.finalizationCheckpoint.serverPlaybackState,
                "postprocessState": server.finalizationCheckpoint.postprocessState
            ],
            "authority": [
                "capture": "local_manifest",
                "ingest": "authenticated_server_checkpoint",
                "realtime": "authenticated_server_checkpoint",
                "finalization": "authenticated_server_checkpoint"
            ]
        ]
    }

    private var manifestURL: URL {
        captureURL!.appendingPathComponent("manifest.json")
    }

    private var playbackPartialURL: URL {
        captureURL!.appendingPathComponent("capture.partial.wav")
    }

    private var openBatchJournalURL: URL {
        captureURL!.appendingPathComponent("open-batch.json")
    }

    private func openBatch(firstSample: Int64, capturedMonotonicNs: UInt64) throws {
        guard let manifest, let captureURL else { throw MeetingCaptureError.invalidState("prepare required") }
        let fileName = "batch-e\(manifest.streamEpoch)-s\(manifest.nextSequence)-f\(firstSample).partial"
        let url = captureURL.appendingPathComponent(fileName)
        guard !fileManager.fileExists(atPath: openBatchJournalURL.path) else {
            throw MeetingCaptureError.corruptManifest
        }
        guard !fileManager.fileExists(atPath: url.path),
              fileManager.createFile(atPath: url.path, contents: nil) else {
            throw MeetingCaptureError.storageUnavailable
        }
        do {
            try protectFile(url)
            let journal = MeetingCaptureOpenBatchJournal(
                streamEpoch: manifest.streamEpoch,
                sequence: manifest.nextSequence,
                firstSample: firstSample,
                capturedMonotonicNs: capturedMonotonicNs,
                fileName: fileName,
                createdAt: Date()
            )
            let encoder = JSONEncoder()
            encoder.dateEncodingStrategy = .iso8601
            encoder.outputFormatting = [.sortedKeys]
            try writeAtomic(try encoder.encode(journal), to: openBatchJournalURL)
            batchHandle = try FileHandle(forWritingTo: url)
            batchPartialURL = url
            batchByteCount = 0
            batchFirstSample = firstSample
            batchCapturedMonotonicNs = capturedMonotonicNs
        } catch {
            try? fileManager.removeItem(at: url)
            throw error
        }
    }

    private func sealOpenBatch() throws -> MeetingCaptureBatch? {
        guard let handle = batchHandle else {
            guard batchPartialURL == nil,
                  !fileManager.fileExists(atPath: openBatchJournalURL.path) else {
                throw MeetingCaptureError.corruptManifest
            }
            return nil
        }
        guard let partialURL = batchPartialURL, batchByteCount > 0,
              var current = manifest, let captureURL else {
            throw MeetingCaptureError.corruptManifest
        }
        try padOpenBatchToMillisecond()
        current = manifest!
        let sealedByteCount = batchByteCount
        let sealedFirstSample = batchFirstSample
        let sealedCapturedMonotonicNs = batchCapturedMonotonicNs
        do {
            try handle.synchronize()
            try handle.close()
        } catch {
            try? handle.close()
            clearOpenBatchMemory()
            throw error
        }
        clearOpenBatchMemory()
        let sequence = current.nextSequence
        let finalName = "batch-e\(current.streamEpoch)-s\(sequence)-f\(sealedFirstSample).pcm"
        let finalURL = captureURL.appendingPathComponent(finalName)
        try fileManager.moveItem(at: partialURL, to: finalURL)
        try protectFile(finalURL)
        let bytes = try Data(contentsOf: finalURL, options: .mappedIfSafe)
        let digest = SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
        current.manifestRevision += 1
        let batch = MeetingCaptureBatch(
            streamEpoch: current.streamEpoch,
            sequence: sequence,
            firstSample: sealedFirstSample,
            sampleCount: Int64(sealedByteCount / 2),
            capturedMonotonicNs: sealedCapturedMonotonicNs,
            byteSize: sealedByteCount,
            sha256: digest,
            manifestRevision: current.manifestRevision,
            idempotencyKey: UUID().uuidString.lowercased(),
            fileName: finalName,
            uploaded: false
        )
        current.batches.append(batch)
        current.nextSequence += 1
        current.lastSealedSequence = sequence
        current.updatedAt = Date()
        manifest = current
        try persistBatchSidecar(batch)
        try persistManifest()
        if fileManager.fileExists(atPath: openBatchJournalURL.path) {
            try rejectSymbolicLink(openBatchJournalURL)
            try fileManager.removeItem(at: openBatchJournalURL)
        }
        return batch
    }

    private func clearOpenBatchMemory() {
        batchHandle = nil
        batchPartialURL = nil
        batchByteCount = 0
        batchFirstSample = 0
        batchCapturedMonotonicNs = 0
    }

    private func padOpenBatchToMillisecond() throws {
        guard let handle = batchHandle, let playbackHandle, var current = manifest else { return }
        let sampleRemainder = batchByteCount / 2 % 16
        guard sampleRemainder != 0 else { return }
        let padSamples = 16 - sampleRemainder
        let pad = Data(repeating: 0, count: padSamples * 2)
        guard current.recordedAudioSamples + Int64(padSamples) <=
                Int64(current.limits.maxDurationSeconds * current.audio.sampleRate),
              current.recordedThroughSample + Int64(padSamples) <=
                Int64(current.limits.maxDurationSeconds * current.audio.sampleRate),
              Int(current.recordedAudioSamples * 2) + pad.count <= current.limits.maxTotalBytes else {
            throw MeetingCaptureError.storageQuotaExceeded
        }
        try handle.write(contentsOf: pad)
        try playbackHandle.write(contentsOf: pad)
        batchByteCount += pad.count
        current.recordedAudioSamples += Int64(padSamples)
        current.recordedThroughSample += Int64(padSamples)
        current.updatedAt = Date()
        manifest = current
    }

    private func createPlaybackFileIfNeeded() throws {
        guard playbackHandle == nil else { return }
        if !fileManager.fileExists(atPath: playbackPartialURL.path) {
            guard fileManager.createFile(
                atPath: playbackPartialURL.path,
                contents: wavHeader(dataBytes: 0)
            ) else {
                throw MeetingCaptureError.storageUnavailable
            }
            try protectFile(playbackPartialURL)
        }
        try rejectSymbolicLink(playbackPartialURL)
        let handle = try FileHandle(forWritingTo: playbackPartialURL)
        try handle.seekToEnd()
        playbackHandle = handle
    }

    private func finalizePlaybackFile() throws {
        guard let captureURL, let manifest else { throw MeetingCaptureError.invalidState("prepare required") }
        try playbackHandle?.synchronize()
        try playbackHandle?.close()
        playbackHandle = nil
        let dataBytes = UInt32(clamping: manifest.recordedAudioSamples * 2)
        let patchHandle = try FileHandle(forWritingTo: playbackPartialURL)
        try patchHandle.seek(toOffset: 0)
        try patchHandle.write(contentsOf: wavHeader(dataBytes: dataBytes))
        try patchHandle.synchronize()
        try patchHandle.close()
        let finalURL = captureURL.appendingPathComponent(manifest.playbackFileName)
        if fileManager.fileExists(atPath: finalURL.path) { try fileManager.removeItem(at: finalURL) }
        try fileManager.moveItem(at: playbackPartialURL, to: finalURL)
        try protectFile(finalURL)
    }

    private func wavHeader(dataBytes: UInt32) -> Data {
        var data = Data()
        data.append(contentsOf: Array("RIFF".utf8))
        data.appendLittleEndian(36 &+ dataBytes)
        data.append(contentsOf: Array("WAVEfmt ".utf8))
        data.appendLittleEndian(UInt32(16))
        data.appendLittleEndian(UInt16(1))
        data.appendLittleEndian(UInt16(1))
        data.appendLittleEndian(UInt32(meetingCaptureSampleRate))
        data.appendLittleEndian(UInt32(meetingCaptureSampleRate * 2))
        data.appendLittleEndian(UInt16(2))
        data.appendLittleEndian(UInt16(16))
        data.append(contentsOf: Array("data".utf8))
        data.appendLittleEndian(dataBytes)
        return data
    }

    private func persistManifest() throws {
        guard var current = manifest else { return }
        current.updatedAt = Date()
        manifest = current
        try writeAtomic(encodedManifest(current), to: manifestURL)
    }

    private func readManifest() throws -> MeetingCaptureManifest {
        do {
            try rejectSymbolicLink(manifestURL)
            let decoder = JSONDecoder()
            decoder.dateDecodingStrategy = .iso8601
            return try decoder.decode(MeetingCaptureManifest.self, from: Data(contentsOf: manifestURL))
        } catch {
            throw MeetingCaptureError.corruptManifest
        }
    }

    private func encodedManifest(_ value: MeetingCaptureManifest) throws -> Data {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.sortedKeys]
        return try encoder.encode(value)
    }

    private func persistBatchSidecar(_ batch: MeetingCaptureBatch) throws {
        guard let captureURL else { return }
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let url = captureURL.appendingPathComponent("\(batch.fileName).json")
        try writeAtomic(try encoder.encode(batch), to: url)
    }

    private func mergeDurableBatchSidecars(into input: MeetingCaptureManifest) throws -> MeetingCaptureManifest {
        guard let captureURL else { return input }
        var output = input
        let decoder = JSONDecoder()
        let files = try fileManager.contentsOfDirectory(at: captureURL, includingPropertiesForKeys: nil)
        for url in files where url.lastPathComponent.hasSuffix(".pcm.json") {
            let sidecarValues = try url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
            guard sidecarValues.isRegularFile == true, sidecarValues.isSymbolicLink != true else {
                throw MeetingCaptureError.corruptManifest
            }
            guard let batch = try? decoder.decode(MeetingCaptureBatch.self, from: Data(contentsOf: url)) else {
                throw MeetingCaptureError.corruptManifest
            }
            guard batch.fileName == url.deletingPathExtension().lastPathComponent,
                  isSafeCaptureFilename(batch.fileName),
                  batch.streamEpoch >= 1,
                  batch.sequence >= 0,
                  batch.firstSample >= 0,
                  batch.sampleCount > 0,
                  Int64(batch.byteSize) == batch.sampleCount * 2,
                  batch.manifestRevision >= 1,
                  batch.idempotencyKey.count >= 1,
                  batch.idempotencyKey.count <= 128,
                  batch.sha256.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil,
                  batch.fileName == "batch-e\(batch.streamEpoch)-s\(batch.sequence)-f\(batch.firstSample).pcm" else {
                throw MeetingCaptureError.corruptManifest
            }
            if let index = output.batches.firstIndex(where: {
                $0.streamEpoch == batch.streamEpoch && $0.sequence == batch.sequence
            }) {
                let existing = output.batches[index]
                guard existing.firstSample == batch.firstSample,
                      existing.sampleCount == batch.sampleCount,
                      existing.capturedMonotonicNs == batch.capturedMonotonicNs,
                      existing.byteSize == batch.byteSize,
                      existing.sha256 == batch.sha256,
                      existing.manifestRevision == batch.manifestRevision,
                      existing.idempotencyKey == batch.idempotencyKey,
                      existing.fileName == batch.fileName else {
                    throw MeetingCaptureError.corruptManifest
                }
                var merged = batch
                merged.uploaded = existing.uploaded || batch.uploaded
                output.batches[index] = merged
            } else {
                output.batches.append(batch)
            }
        }
        output.batches.sort { ($0.streamEpoch, $0.sequence) < ($1.streamEpoch, $1.sequence) }
        try validateRecoveredBatchFiles(output.batches)
        let currentEpochSequences = output.batches
            .filter { $0.streamEpoch == output.streamEpoch }
            .map(\.sequence)
        output.nextSequence = max(output.nextSequence, (currentEpochSequences.max() ?? -1) + 1)
        output.lastSealedSequence = max(output.lastSealedSequence, currentEpochSequences.max() ?? -1)
        manifest = output
        try persistManifest()
        return output
    }

    private func materializePendingGapIfNeeded() throws -> MeetingCaptureGapMaterialization? {
        guard var current = manifest, let pending = current.pendingGap else { return nil }
        guard current.streamEpoch == pending.streamEpoch,
              current.recordedThroughSample >= pending.startSample,
              current.recordedThroughSample <= pending.endSample,
              pending.startSample.isMultiple(of: 16),
              pending.endSample.isMultiple(of: 16),
              pending.endSample > pending.startSample,
              current.nextSequence >= pending.fromSequence,
              batchHandle == nil else {
            throw MeetingCaptureError.corruptManifest
        }
        try createPlaybackFileIfNeeded()
        guard let playbackHandle else { throw MeetingCaptureError.storageUnavailable }
        let persistedEntries = pending.manifestEntries ?? []
        var entryCursor = pending.startSample
        let maximumGapEntrySamples = Int64(targetBatchBytes(current) / 2)
        guard pending.fromSequence >= 0,
              pending.fromSequence <= Int.max - persistedEntries.count,
              persistedEntries.enumerated().allSatisfy({ offset, entry in
            guard entry.sequence == pending.fromSequence + offset,
                  entry.first_sample == entryCursor,
                  entry.sample_count > 0,
                  entry.sample_count <= maximumGapEntrySamples,
                  entry.first_sample <= pending.endSample - entry.sample_count else {
                return false
            }
            entryCursor += entry.sample_count
            let zeroBytes = Data(repeating: 0, count: Int(entry.sample_count * 2))
            let digest = SHA256.hash(data: zeroBytes).map { String(format: "%02x", $0) }.joined()
            return entry.captured_monotonic_ns == pending.detectedMonotonicNs &&
                entry.encoding == current.audio.encoding && entry.sample_rate == current.audio.sampleRate &&
                entry.channels == current.audio.channels && entry.sha256 == digest
        }) else {
            throw MeetingCaptureError.corruptManifest
        }
        let persistedGapSamples = persistedEntries.reduce(Int64(0)) { $0 + $1.sample_count }
        let expectedRecorded = pending.startSample + persistedGapSamples
        guard current.recordedThroughSample == expectedRecorded,
              current.nextSequence == pending.fromSequence + persistedEntries.count,
              current.recordedAudioSamples >= persistedGapSamples else {
            throw MeetingCaptureError.corruptManifest
        }
        let expectedPlaybackBytes = UInt64(44 + current.recordedAudioSamples * 2)
        let durablePlaybackBytes = try playbackHandle.seekToEnd()
        guard durablePlaybackBytes >= expectedPlaybackBytes else {
            throw MeetingCaptureError.corruptManifest
        }
        try playbackHandle.truncate(atOffset: expectedPlaybackBytes)
        try playbackHandle.seekToEnd()
        var remaining = pending.endSample - current.recordedThroughSample
        let chunkSamples = Int64(targetBatchBytes(current) / 2)
        while remaining > 0 {
            let samples = min(remaining, chunkSamples)
            guard samples > 0, samples.isMultiple(of: 16), samples <= Int64(Int.max / 2) else {
                throw MeetingCaptureError.corruptManifest
            }
            let silenceByteCount = Int(samples * 2)
            let playbackEnd = UInt64(44 + (current.recordedAudioSamples + samples) * 2)
            try playbackHandle.truncate(atOffset: playbackEnd)
            try playbackHandle.seekToEnd()
            try playbackHandle.synchronize()
            let silence = Data(repeating: 0, count: silenceByteCount)
            let digest = SHA256.hash(data: silence).map { String(format: "%02x", $0) }.joined()
            let entry = MeetingCaptureCanonicalEntry(
                sequence: current.nextSequence,
                first_sample: current.recordedThroughSample,
                sample_count: samples,
                captured_monotonic_ns: pending.detectedMonotonicNs,
                encoding: current.audio.encoding,
                sample_rate: current.audio.sampleRate,
                channels: current.audio.channels,
                sha256: digest
            )
            current.recordedAudioSamples += samples
            current.recordedThroughSample += samples
            current.nextSequence += 1
            current.lastSealedSequence = entry.sequence
            current.manifestRevision += 1
            guard var persistedGap = current.pendingGap else {
                throw MeetingCaptureError.corruptManifest
            }
            var persistedEntries = persistedGap.manifestEntries ?? []
            persistedEntries.append(entry)
            persistedGap.manifestEntries = persistedEntries
            current.pendingGap = persistedGap
            current.updatedAt = Date()
            manifest = current
            try persistManifest()
            remaining -= samples
        }
        current = try currentManifest()
        let entries = current.pendingGap?.manifestEntries ?? []
        let toSequence = current.nextSequence - 1
        guard current.recordedThroughSample == pending.endSample,
              toSequence >= pending.fromSequence,
              entries.count == toSequence - pending.fromSequence + 1,
              entries.enumerated().allSatisfy({ offset, entry in
                  entry.sequence == pending.fromSequence + offset
              }) else {
            throw MeetingCaptureError.corruptManifest
        }
        let gap = MeetingCaptureGap(
            startSample: pending.startSample,
            endSample: pending.endSample,
            reason: pending.reason,
            detectedMonotonicNs: pending.detectedMonotonicNs,
            streamEpoch: pending.streamEpoch,
            fromSequence: pending.fromSequence,
            toSequence: toSequence,
            manifestEntries: entries,
            idempotencyKey: UUID().uuidString.lowercased(),
            sealedManifestRevision: nil,
            serverDeclared: false
        )
        if !current.gaps.contains(gap) { current.gaps.append(gap) }
        current.pendingGap = nil
        current.state = pending.returnState
        current.interruptionReason = pending.reason
        current.errorCode = nil
        current.manifestRevision += 1
        current.updatedAt = Date()
        manifest = current
        try persistManifest()
        return MeetingCaptureGapMaterialization(gap: gap, entries: entries)
    }

    private func targetBatchBytes(_ manifest: MeetingCaptureManifest) -> Int {
        min(
            manifest.limits.maxBatchBytes,
            max(
                32_000,
                manifest.audio.batchDurationMs * manifest.audio.sampleRate *
                    manifest.audio.channels * 2 / 1_000
            )
        ) / 32 * 32
    }

    private func recoverOpenBatchIfNeeded() throws {
        guard var current = manifest, let captureURL else { return }
        let files = try fileManager.contentsOfDirectory(
            at: captureURL,
            includingPropertiesForKeys: [.fileSizeKey, .isRegularFileKey, .isSymbolicLinkKey],
            options: [.skipsHiddenFiles]
        )
        var partials = files.filter {
            $0.lastPathComponent.hasPrefix("batch-e") && $0.pathExtension == "partial"
        }
        let hasJournal = fileManager.fileExists(atPath: openBatchJournalURL.path)
        guard hasJournal || !partials.isEmpty else { return }
        guard hasJournal else {
            if partials.count == 1 {
                let values = try partials[0].resourceValues(
                    forKeys: [.fileSizeKey, .isRegularFileKey, .isSymbolicLinkKey]
                )
                guard values.isRegularFile == true, values.isSymbolicLink != true else {
                    throw MeetingCaptureError.corruptManifest
                }
                if values.fileSize != 0 { throw MeetingCaptureError.corruptManifest }
                try fileManager.removeItem(at: partials[0])
                return
            }
            throw MeetingCaptureError.corruptManifest
        }
        try rejectSymbolicLink(openBatchJournalURL)
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        guard let journal = try? decoder.decode(
            MeetingCaptureOpenBatchJournal.self,
            from: Data(contentsOf: openBatchJournalURL)
        ), journal.streamEpoch >= 1, journal.sequence >= 0,
           journal.firstSample >= 0, isSafeCaptureFilename(journal.fileName) else {
            throw MeetingCaptureError.corruptManifest
        }
        if current.batches.contains(where: {
            $0.streamEpoch == journal.streamEpoch && $0.sequence == journal.sequence
        }) {
            guard partials.isEmpty else { throw MeetingCaptureError.corruptManifest }
            try fileManager.removeItem(at: openBatchJournalURL)
            return
        }
        let expectedPartial = try safeCaptureFileURL(journal.fileName)
        if !fileManager.fileExists(atPath: expectedPartial.path) {
            guard partials.isEmpty else { throw MeetingCaptureError.corruptManifest }
            let finalName = "batch-e\(journal.streamEpoch)-s\(journal.sequence)-f\(journal.firstSample).pcm"
            let finalURL = try safeCaptureFileURL(finalName)
            guard fileManager.fileExists(atPath: finalURL.path) else {
                throw MeetingCaptureError.corruptManifest
            }
            try fileManager.moveItem(at: finalURL, to: expectedPartial)
            partials = [expectedPartial]
        }
        guard partials.count == 1,
              partials[0].standardizedFileURL == expectedPartial.standardizedFileURL,
              current.streamEpoch == journal.streamEpoch,
              current.nextSequence == journal.sequence,
              current.recordedThroughSample >= journal.firstSample else {
            throw MeetingCaptureError.corruptManifest
        }
        let partialValues = try expectedPartial.resourceValues(
            forKeys: [.fileSizeKey, .isRegularFileKey, .isSymbolicLinkKey]
        )
        guard partialValues.isRegularFile == true,
              partialValues.isSymbolicLink != true,
              let partialBytes = partialValues.fileSize else {
            throw MeetingCaptureError.corruptManifest
        }
        if partialBytes == 0, current.recordedThroughSample == journal.firstSample {
            try fileManager.removeItem(at: expectedPartial)
            try fileManager.removeItem(at: openBatchJournalURL)
            return
        }
        guard partialBytes > 0,
              partialBytes <= current.limits.maxBatchBytes,
              partialBytes.isMultiple(of: 2) else {
            throw MeetingCaptureError.corruptManifest
        }
        try rejectSymbolicLink(playbackPartialURL)
        let playbackValues = try playbackPartialURL.resourceValues(
            forKeys: [.fileSizeKey, .isRegularFileKey, .isSymbolicLinkKey]
        )
        guard playbackValues.isRegularFile == true,
              playbackValues.isSymbolicLink != true,
              let playbackBytes = playbackValues.fileSize,
              playbackBytes >= 44 else {
            throw MeetingCaptureError.corruptManifest
        }
        let manifestOpenSamples = current.recordedThroughSample - journal.firstSample
        guard manifestOpenSamples >= 0,
              manifestOpenSamples <= Int64(partialBytes / 2),
              current.recordedAudioSamples >= manifestOpenSamples else {
            throw MeetingCaptureError.corruptManifest
        }
        let previousAudioSamples = current.recordedAudioSamples - manifestOpenSamples
        let playbackOpenSamples = Int64((playbackBytes - 44) / 2) - previousAudioSamples
        let recoveredSamples = min(Int64(partialBytes / 2), playbackOpenSamples)
        guard recoveredSamples >= manifestOpenSamples, recoveredSamples > 0 else {
            throw MeetingCaptureError.corruptManifest
        }
        let recoveredBytes = Int(recoveredSamples * 2)
        let recoveredPlaybackBytes = UInt64(44 + (previousAudioSamples + recoveredSamples) * 2)
        let recoveredBatchHandle = try FileHandle(forWritingTo: expectedPartial)
        try recoveredBatchHandle.truncate(atOffset: UInt64(recoveredBytes))
        try recoveredBatchHandle.seekToEnd()
        let recoveredPlaybackHandle = try FileHandle(forWritingTo: playbackPartialURL)
        try recoveredPlaybackHandle.truncate(atOffset: recoveredPlaybackBytes)
        try recoveredPlaybackHandle.seekToEnd()
        current.recordedAudioSamples = previousAudioSamples + recoveredSamples
        current.recordedThroughSample = journal.firstSample + recoveredSamples
        current.updatedAt = Date()
        manifest = current
        batchHandle = recoveredBatchHandle
        batchPartialURL = expectedPartial
        batchByteCount = recoveredBytes
        batchFirstSample = journal.firstSample
        batchCapturedMonotonicNs = journal.capturedMonotonicNs
        playbackHandle = recoveredPlaybackHandle
        _ = try sealOpenBatch()
    }

    private func recoverFinalizedPlaybackIfNeeded() throws {
        guard var current = manifest, captureURL != nil else { return }
        let finalURL = try safeCaptureFileURL(current.playbackFileName)
        let hasFinal = fileManager.fileExists(atPath: finalURL.path)
        if current.state == .stopped || hasFinal {
            guard hasFinal else { throw MeetingCaptureError.corruptManifest }
            try rejectSymbolicLink(finalURL)
            let values = try finalURL.resourceValues(
                forKeys: [.fileSizeKey, .isRegularFileKey, .isSymbolicLinkKey]
            )
            let expectedSize = 44 + current.recordedAudioSamples * 2
            guard values.isRegularFile == true,
                  values.isSymbolicLink != true,
                  values.fileSize.map { Int64($0) } == expectedSize,
                  !fileManager.fileExists(atPath: playbackPartialURL.path),
                  try hasValidWAVHeader(finalURL, dataBytes: current.recordedAudioSamples * 2),
                  current.state == .stopping || current.state == .stopped else {
                throw MeetingCaptureError.corruptManifest
            }
            if current.state == .stopping || !current.localPlaybackReady {
                current.state = .stopped
                current.localPlaybackReady = true
                current.interruptionReason = nil
                current.manifestRevision += 1
                current.updatedAt = Date()
                manifest = current
                try persistManifest()
            }
        } else {
            let partialValues = try playbackPartialURL.resourceValues(
                forKeys: [.isRegularFileKey, .isSymbolicLinkKey]
            )
            guard partialValues.isRegularFile == true, partialValues.isSymbolicLink != true else {
                throw MeetingCaptureError.corruptManifest
            }
        }
    }

    private func hasValidWAVHeader(_ url: URL, dataBytes: Int64) throws -> Bool {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        let header = try handle.read(upToCount: 44) ?? Data()
        guard header.count == 44,
              String(decoding: header[0..<4], as: UTF8.self) == "RIFF",
              String(decoding: header[8..<12], as: UTF8.self) == "WAVE",
              String(decoding: header[36..<40], as: UTF8.self) == "data" else {
            return false
        }
        let declared = header[40..<44].enumerated().reduce(UInt32(0)) { value, entry in
            value | (UInt32(entry.element) << UInt32(entry.offset * 8))
        }
        return declared == UInt32(clamping: dataBytes)
    }

    private func writeAtomic(_ data: Data, to destination: URL) throws {
        let temporary = destination.deletingLastPathComponent()
            .appendingPathComponent(".\(destination.lastPathComponent).\(UUID().uuidString).tmp")
        guard fileManager.createFile(atPath: temporary.path, contents: nil) else {
            throw MeetingCaptureError.storageUnavailable
        }
        try protectFile(temporary)
        let handle = try FileHandle(forWritingTo: temporary)
        try handle.write(contentsOf: data)
        try handle.synchronize()
        try handle.close()
        if fileManager.fileExists(atPath: destination.path) {
            try rejectSymbolicLink(destination)
            _ = try fileManager.replaceItemAt(destination, withItemAt: temporary)
        } else {
            try fileManager.moveItem(at: temporary, to: destination)
        }
        try protectFile(destination)
    }

    private func protectDirectory(_ url: URL) throws {
        try rejectSymbolicLink(url)
        try fileManager.createDirectory(at: url, withIntermediateDirectories: true, attributes: [
            .protectionKey: FileProtectionType.completeUntilFirstUserAuthentication
        ])
        try rejectSymbolicLink(url)
        try excludeFromBackup(url)
    }

    private func protectFile(_ url: URL) throws {
        try rejectSymbolicLink(url)
        try fileManager.setAttributes([
            .protectionKey: FileProtectionType.completeUntilFirstUserAuthentication
        ], ofItemAtPath: url.path)
        try excludeFromBackup(url)
    }

    private func excludeFromBackup(_ input: URL) throws {
        var url = input
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try url.setResourceValues(values)
    }

    private func assertAvailableCapacity(requiredBytes: Int) throws {
        let values = try rootURL.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey])
        if let available = values.volumeAvailableCapacityForImportantUsage,
           available < Int64(max(requiredBytes, 64 * 1_024 * 1_024)) {
            throw MeetingCaptureError.storageQuotaExceeded
        }
    }

    private func rejectSymbolicLink(_ url: URL) throws {
        guard fileManager.fileExists(atPath: url.path) else { return }
        let values = try url.resourceValues(forKeys: [.isSymbolicLinkKey])
        if values.isSymbolicLink == true { throw MeetingCaptureError.storageUnavailable }
    }

    private func isSafeCaptureFilename(_ name: String) -> Bool {
        !name.isEmpty &&
            name == URL(fileURLWithPath: name).lastPathComponent &&
            !name.hasPrefix(".") &&
            !name.contains("/") &&
            !name.contains("\\")
    }

    private func safeCaptureFileURL(_ name: String) throws -> URL {
        guard let captureURL, isSafeCaptureFilename(name) else {
            throw MeetingCaptureError.corruptManifest
        }
        let url = captureURL.appendingPathComponent(name).standardizedFileURL
        guard url.deletingLastPathComponent().standardizedFileURL == captureURL else {
            throw MeetingCaptureError.corruptManifest
        }
        return url
    }

    private func validateRecoveredBatchFiles(_ batches: [MeetingCaptureBatch]) throws {
        var identities = Set<String>()
        for batch in batches {
            let identity = "\(batch.streamEpoch):\(batch.sequence)"
            guard identities.insert(identity).inserted,
                  batch.streamEpoch >= 1,
                  batch.sequence >= 0,
                  batch.firstSample >= 0,
                  batch.sampleCount > 0,
                  Int64(batch.byteSize) == batch.sampleCount * 2,
                  batch.manifestRevision >= 1,
                  batch.idempotencyKey.count >= 1,
                  batch.idempotencyKey.count <= 128,
                  batch.sha256.range(of: "^[0-9a-f]{64}$", options: .regularExpression) != nil,
                  batch.fileName == "batch-e\(batch.streamEpoch)-s\(batch.sequence)-f\(batch.firstSample).pcm" else {
                throw MeetingCaptureError.corruptManifest
            }
            let url = try safeCaptureFileURL(batch.fileName)
            let values = try url.resourceValues(forKeys: [.fileSizeKey, .isRegularFileKey, .isSymbolicLinkKey])
            guard values.isRegularFile == true,
                  values.isSymbolicLink != true,
                  values.fileSize == batch.byteSize else {
                throw MeetingCaptureError.corruptManifest
            }
            let bytes = try Data(contentsOf: url, options: .mappedIfSafe)
            let digest = SHA256.hash(data: bytes).map { String(format: "%02x", $0) }.joined()
            guard digest == batch.sha256 else { throw MeetingCaptureError.corruptManifest }
        }
    }

    private func validatedAPIEndpoint(_ value: String, trustedOrigin: String) throws -> URL {
        guard let endpoint = URLComponents(string: value),
              let trusted = URLComponents(string: trustedOrigin),
              endpoint.scheme?.lowercased() == "https",
              trusted.scheme?.lowercased() == "https",
              endpoint.user == nil,
              endpoint.password == nil,
              endpoint.query == nil,
              endpoint.fragment == nil,
              trusted.user == nil,
              trusted.password == nil,
              trusted.query == nil,
              trusted.fragment == nil,
              endpoint.host?.lowercased() == trusted.host?.lowercased(),
              (endpoint.port ?? 443) == (trusted.port ?? 443),
              trusted.percentEncodedPath.isEmpty || trusted.percentEncodedPath == "/",
              endpoint.percentEncodedPath == "/api/meetings/v1",
              let url = endpoint.url else {
            throw MeetingCaptureError.invalidArgument("untrusted apiBaseUrl")
        }
        return url
    }

}

private extension Data {
    mutating func appendLittleEndian<T: FixedWidthInteger>(_ value: T) {
        var littleEndian = value.littleEndian
        Swift.withUnsafeBytes(of: &littleEndian) { append(contentsOf: $0) }
    }
}
