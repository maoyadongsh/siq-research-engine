import Foundation
import XCTest
@testable import MeetingCapturePlugin

final class MeetingCaptureStoreTests: XCTestCase {
    func testStopIsIdempotentAndReturnsOnlyAnOpaquePlaybackHandle() throws {
        let captureId = "11111111-1111-4111-8111-111111111111"
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let store = try MeetingCaptureStore(rootURL: root)
        _ = try store.prepare(
            meetingId: "meeting-1",
            captureId: captureId,
            apiBaseURL: "https://example.test/api/meetings/v1",
            trustedAPIOrigin: "https://example.test",
            streamEpoch: 1,
            audio: MeetingCaptureAudioConfiguration(batchDurationMs: 1_000),
            limits: MeetingCaptureLimits(maxBatchBytes: 64_000, maxTotalBytes: 1_000_000, maxDurationSeconds: 60)
        )
        try store.startWriting()
        _ = try store.appendPCM(Data(repeating: 1, count: 32_000), capturedMonotonicNs: 1)

        let first = try store.stop()
        let second = try store.stop()

        XCTAssertEqual(first.1.handle, "capture-asset:\(captureId)")
        XCTAssertEqual(second.1.handle, first.1.handle)
        XCTAssertFalse(first.1.dictionary.description.contains(root.path))
        XCTAssertEqual(first.1.durationMs, 1_000)
        let boundary = try store.canonicalBoundary()
        XCTAssertEqual(boundary.finalSequence, 0)
        XCTAssertEqual(boundary.recordedThroughSample, 16_000)
        XCTAssertEqual(boundary.entries.count, 1)
        XCTAssertEqual(
            boundary.manifestSHA256,
            "9abc5bec51abd3bccf0074243c26a4096f487b3b96875cf669d2053bb9e74c58"
        )
    }

    func testManifestAndBatchSidecarsNeverContainCaptureCredentials() throws {
        let captureId = "22222222-2222-4222-8222-222222222222"
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let store = try MeetingCaptureStore(rootURL: root)
        _ = try store.prepare(
            meetingId: "meeting-2",
            captureId: captureId,
            apiBaseURL: "https://example.test/api/meetings/v1",
            trustedAPIOrigin: "https://example.test",
            streamEpoch: 1,
            audio: MeetingCaptureAudioConfiguration(batchDurationMs: 1_000),
            limits: MeetingCaptureLimits(maxBatchBytes: 32_000, maxTotalBytes: 1_000_000, maxDurationSeconds: 60)
        )
        try store.startWriting()
        _ = try store.appendPCM(Data(repeating: 2, count: 32_000), capturedMonotonicNs: 2)

        let captureDirectory = root.appendingPathComponent(captureId, isDirectory: true)
        let text = try FileManager.default.contentsOfDirectory(at: captureDirectory, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "json" }
            .map { try String(contentsOf: $0, encoding: .utf8) }
            .joined()

        XCTAssertFalse(text.localizedCaseInsensitiveContains("captureToken"))
        XCTAssertFalse(text.localizedCaseInsensitiveContains("authorization"))
    }

    func testPrepareRejectsUnsafeCaptureComponentsAndUntrustedOrigins() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let store = try MeetingCaptureStore(rootURL: root)
        let audio = MeetingCaptureAudioConfiguration(batchDurationMs: 1_000)
        let limits = MeetingCaptureLimits(maxBatchBytes: 32_000, maxTotalBytes: 1_000_000, maxDurationSeconds: 60)

        XCTAssertThrowsError(try store.prepare(
            meetingId: "meeting-3",
            captureId: "../outside",
            apiBaseURL: "https://example.test/api/meetings/v1",
            trustedAPIOrigin: "https://example.test",
            streamEpoch: 1,
            audio: audio,
            limits: limits
        ))
        XCTAssertThrowsError(try store.prepare(
            meetingId: "meeting-3",
            captureId: "33333333-3333-4333-8333-333333333333",
            apiBaseURL: "https://attacker.test/api/meetings/v1",
            trustedAPIOrigin: "https://example.test",
            streamEpoch: 1,
            audio: audio,
            limits: limits
        ))
    }

    func testPrepareRejectsCaptureDirectorySymlinks() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let outside = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer {
            try? FileManager.default.removeItem(at: root)
            try? FileManager.default.removeItem(at: outside)
        }
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: outside, withIntermediateDirectories: true)
        let captureId = "44444444-4444-4444-8444-444444444444"
        try FileManager.default.createSymbolicLink(
            at: root.appendingPathComponent(captureId),
            withDestinationURL: outside
        )
        let store = try MeetingCaptureStore(rootURL: root)

        XCTAssertThrowsError(try store.prepare(
            meetingId: "meeting-4",
            captureId: captureId,
            apiBaseURL: "https://example.test/api/meetings/v1",
            trustedAPIOrigin: "https://example.test",
            streamEpoch: 1,
            audio: MeetingCaptureAudioConfiguration(),
            limits: MeetingCaptureLimits()
        ))
    }

    func testColdRecoverySealsTheCommonOpenBatchAndDoesNotResumeRecording() throws {
        let captureId = "55555555-5555-4555-8555-555555555555"
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        var active: MeetingCaptureStore? = try MeetingCaptureStore(rootURL: root)
        _ = try active?.prepare(
            meetingId: "meeting-5",
            captureId: captureId,
            apiBaseURL: "https://example.test/api/meetings/v1",
            trustedAPIOrigin: "https://example.test",
            streamEpoch: 1,
            audio: MeetingCaptureAudioConfiguration(batchDurationMs: 1_000),
            limits: MeetingCaptureLimits(maxBatchBytes: 64_000, maxTotalBytes: 1_000_000, maxDurationSeconds: 60)
        )
        try active?.startWriting()
        _ = try active?.appendPCM(Data(repeating: 3, count: 16_000), capturedMonotonicNs: 3)
        XCTAssertEqual(try active?.currentManifest().batches.count, 0)
        active = nil

        let recovered = try MeetingCaptureStore(rootURL: root)
        XCTAssertEqual(try recovered.recoverableCaptureIds(), [captureId])
        let manifest = try recovered.recover(
            captureId: captureId,
            trustedAPIOrigin: "https://example.test"
        )

        XCTAssertEqual(manifest.state, .interrupted)
        XCTAssertEqual(manifest.interruptionReason, "process_recovered")
        XCTAssertEqual(manifest.batches.count, 1)
        XCTAssertEqual(manifest.batches[0].sampleCount, 8_000)
        XCTAssertEqual(manifest.recordedThroughSample, 8_000)
        XCTAssertFalse(FileManager.default.fileExists(
            atPath: root.appendingPathComponent(captureId).appendingPathComponent("open-batch.json").path
        ))
    }

    func testRolloverBoundaryIsPersistedAndIdempotentBeforeTheServerReply() throws {
        let captureId = "66666666-6666-4666-8666-666666666666"
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let store = try MeetingCaptureStore(rootURL: root)
        _ = try store.prepare(
            meetingId: "meeting-6",
            captureId: captureId,
            apiBaseURL: "https://example.test/api/meetings/v1",
            trustedAPIOrigin: "https://example.test",
            streamEpoch: 1,
            audio: MeetingCaptureAudioConfiguration(batchDurationMs: 1_000),
            limits: MeetingCaptureLimits(maxBatchBytes: 64_000, maxTotalBytes: 1_000_000, maxDurationSeconds: 60)
        )
        try store.startWriting()
        _ = try store.appendPCM(Data(repeating: 4, count: 32_000), capturedMonotonicNs: 4)

        let first = try store.beginRollover()
        let replay = try store.beginRollover()
        let manifest = try store.currentManifest()

        XCTAssertEqual(replay, first)
        XCTAssertEqual(first.expectedEpoch, 1)
        XCTAssertEqual(first.nextEpoch, 2)
        XCTAssertEqual(first.boundary.finalSequence, 0)
        XCTAssertEqual(first.boundary.recordedThroughSample, 16_000)
        XCTAssertEqual(manifest.streamEpoch, 2)
        XCTAssertEqual(manifest.streamEpochStartSample, 16_000)
        XCTAssertEqual(manifest.nextSequence, 0)
        XCTAssertEqual(manifest.pendingRollover, first)
    }

    func testAuthenticatedServerCheckpointReconcilesLocalOutboxAndShapesAuthorities() throws {
        let captureId = "77777777-7777-4777-8777-777777777777"
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let store = try MeetingCaptureStore(rootURL: root)
        _ = try store.prepare(
            meetingId: "meeting-7",
            captureId: captureId,
            apiBaseURL: "https://example.test/api/meetings/v1",
            trustedAPIOrigin: "https://example.test",
            streamEpoch: 1,
            audio: MeetingCaptureAudioConfiguration(batchDurationMs: 1_000),
            limits: MeetingCaptureLimits(maxBatchBytes: 64_000, maxTotalBytes: 1_000_000, maxDurationSeconds: 60)
        )
        try store.startWriting()
        _ = try store.appendPCM(Data(repeating: 5, count: 32_000), capturedMonotonicNs: 5)
        XCTAssertEqual(try store.pendingBatches().count, 1)
        let checkpoint = serverCheckpoint(captureId: captureId, meetingId: "meeting-7")

        try store.reconcile(checkpoint)
        let dictionary = try store.checkpointDictionary(server: checkpoint)
        let ingest = try XCTUnwrap(dictionary["ingest"] as? [String: Any])
        let authority = try XCTUnwrap(dictionary["authority"] as? [String: Any])

        XCTAssertEqual(try store.pendingBatches().count, 0)
        XCTAssertEqual(ingest["highestUploadedSequence"] as? Int, 0)
        XCTAssertEqual(ingest["persistedThroughSample"] as? Int64, 16_000)
        XCTAssertEqual(authority["capture"] as? String, "local_manifest")
        XCTAssertEqual(authority["ingest"] as? String, "authenticated_server_checkpoint")

        try store.reconcile(serverCheckpoint(
            captureId: captureId,
            meetingId: "meeting-7",
            received: false
        ))
        XCTAssertEqual(try store.pendingBatches().count, 1)
    }

    private func serverCheckpoint(
        captureId: String,
        meetingId: String,
        received: Bool = true
    ) -> MeetingServerCheckpoint {
        MeetingServerCheckpoint(
            schemaVersion: meetingCaptureSchemaVersion,
            captureId: captureId,
            meetingId: meetingId,
            captureCheckpoint: MeetingServerCaptureCheckpoint(
                state: "active",
                recordedThroughSample: nil,
                lastSealedEpoch: nil,
                manifestRevision: nil
            ),
            ingestCheckpoint: MeetingServerIngestCheckpoint(
                persistedThroughSample: received ? 16_000 : 0,
                accountedThroughSample: received ? 16_000 : 0,
                highestReceivedSample: received ? 16_000 : 0,
                receivedBatches: received ? 1 : 0,
                receivedBytes: received ? 32_000 : 0,
                missingSampleRanges: received ? [] : [["start": 0, "end": 16_000]],
                audioMissingSampleRanges: received ? [] : [["start": 0, "end": 16_000]],
                acceptedGaps: 0,
                ingestComplete: false
            ),
            realtimeCheckpoint: MeetingServerRealtimeCheckpoint(
                streamEpoch: 1,
                lastAckedSequence: -1,
                stableOrdinal: 0
            ),
            finalizationCheckpoint: MeetingServerFinalizationCheckpoint(
                captureSealed: false,
                ingestComplete: false,
                hasUnrecoverableGaps: false,
                packagingState: nil,
                packagingAttempt: 0,
                packagingErrorCode: nil,
                wavSHA256: nil,
                wavByteSize: nil,
                serverPlaybackState: "pending_upload",
                postprocessState: "not_started"
            ),
            epochs: [
                MeetingServerEpochCheckpoint(
                    streamEpoch: 1,
                    state: "active",
                    highestContiguousSequence: received ? 0 : -1,
                    highestReceivedSequence: received ? 0 : -1,
                    declaredLastSequence: received ? nil : 0,
                    recordedThroughSample: nil,
                    missingSequenceRanges: received ? [] : [["start": 0, "end": 0]]
                )
            ]
        )
    }
}
