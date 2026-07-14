import Foundation

let meetingCaptureSchemaVersion = "siq.meeting.native_capture.v1"
let meetingCaptureSampleRate = 16_000

enum MeetingCaptureLifecycle: String, Codable {
    case idle
    case prepared
    case recording
    case paused
    case interrupted
    case stopping
    case stopped
    case error
}

enum MeetingCaptureError: Error {
    case invalidArgument(String)
    case invalidState(String)
    case microphoneDenied
    case storageUnavailable
    case storageQuotaExceeded
    case corruptManifest
    case tokenUnavailable
    case transportUnavailable
    case serverConflict
    case serverResponseInvalid
    case userSessionUnavailable

    var code: String {
        switch self {
        case .invalidArgument: return "native_capture.invalid_argument"
        case .invalidState: return "native_capture.invalid_state"
        case .microphoneDenied: return "native_capture.microphone_denied"
        case .storageUnavailable: return "native_capture.storage_unavailable"
        case .storageQuotaExceeded: return "native_capture.storage_quota_exceeded"
        case .corruptManifest: return "native_capture.corrupt_manifest"
        case .tokenUnavailable: return "native_capture.token_unavailable"
        case .transportUnavailable: return "native_capture.transport_unavailable"
        case .serverConflict: return "native_capture.server_conflict"
        case .serverResponseInvalid: return "native_capture.server_response_invalid"
        case .userSessionUnavailable: return "native_capture.user_session_unavailable"
        }
    }

    var recoverable: Bool {
        switch self {
        case .microphoneDenied, .storageQuotaExceeded, .corruptManifest, .serverConflict,
             .serverResponseInvalid:
            return false
        default:
            return true
        }
    }
}

struct MeetingCaptureAudioConfiguration: Codable, Equatable {
    var encoding = "pcm_s16le"
    var sampleRate = meetingCaptureSampleRate
    var channels = 1
    var batchDurationMs = 5_000
}

struct MeetingCaptureLimits: Codable, Equatable {
    var maxBatchBytes = 1_048_576
    var maxTotalBytes = 1_500_000_000
    var maxDurationSeconds = 14_400
}

struct MeetingCaptureBatch: Codable, Equatable {
    var streamEpoch: Int
    var sequence: Int
    var firstSample: Int64
    var sampleCount: Int64
    var capturedMonotonicNs: UInt64
    var byteSize: Int
    var sha256: String
    var manifestRevision: Int
    var idempotencyKey: String
    var fileName: String
    var uploaded: Bool
}

struct MeetingCaptureOpenBatchJournal: Codable, Equatable {
    var streamEpoch: Int
    var sequence: Int
    var firstSample: Int64
    var capturedMonotonicNs: UInt64
    var fileName: String
    var createdAt: Date
}

struct MeetingCaptureCanonicalEntry: Codable, Equatable {
    let sequence: Int
    let first_sample: Int64
    let sample_count: Int64
    let captured_monotonic_ns: UInt64
    let encoding: String
    let sample_rate: Int
    let channels: Int
    let sha256: String
}

struct MeetingCaptureCanonicalManifest: Codable, Equatable {
    let schema_version = "siq.meeting.native_capture.manifest.v1"
    let expected_epoch: Int
    let final_sequence: Int
    let recorded_through_sample: Int64
    let manifest_revision: Int
    let entries: [MeetingCaptureCanonicalEntry]
}

struct MeetingCaptureBoundary: Codable, Equatable {
    let expectedEpoch: Int
    let finalSequence: Int
    let recordedThroughSample: Int64
    let manifestRevision: Int
    let manifestSHA256: String
    let entries: [MeetingCaptureCanonicalEntry]
}

struct MeetingCapturePendingRollover: Codable, Equatable {
    let expectedEpoch: Int
    let nextEpoch: Int
    let idempotencyKey: String
    let boundary: MeetingCaptureBoundary
    let createdAt: Date
}

struct MeetingCaptureGap: Codable, Equatable {
    var startSample: Int64
    var endSample: Int64
    var reason: String
    var detectedMonotonicNs: UInt64
    var streamEpoch: Int? = nil
    var fromSequence: Int? = nil
    var toSequence: Int? = nil
    var manifestEntries: [MeetingCaptureCanonicalEntry]? = nil
    var idempotencyKey: String? = nil
    var sealedManifestRevision: Int? = nil
    var serverDeclared: Bool? = nil
}

struct MeetingCapturePendingGap: Codable, Equatable {
    var streamEpoch: Int
    var fromSequence: Int
    var startSample: Int64
    var endSample: Int64
    var reason: String
    var detectedMonotonicNs: UInt64
    var returnState: MeetingCaptureLifecycle
    var manifestEntries: [MeetingCaptureCanonicalEntry]? = nil
}

struct MeetingCaptureGapMaterialization {
    let gap: MeetingCaptureGap
    let entries: [MeetingCaptureCanonicalEntry]
}

struct MeetingCaptureManifest: Codable, Equatable {
    var schemaVersion = meetingCaptureSchemaVersion
    var meetingId: String
    var captureId: String
    var apiBaseURL: String
    var state: MeetingCaptureLifecycle
    var streamEpoch: Int
    var streamEpochStartSample: Int64? = nil
    var audio: MeetingCaptureAudioConfiguration
    var limits: MeetingCaptureLimits
    var recordedThroughSample: Int64
    var recordedAudioSamples: Int64
    var nextSequence: Int
    var lastSealedSequence: Int
    var manifestRevision: Int
    var playbackFileName: String
    var localPlaybackReady: Bool
    var interruptionReason: String? = nil
    var errorCode: String? = nil
    var batches: [MeetingCaptureBatch]
    var gaps: [MeetingCaptureGap]
    var pendingGap: MeetingCapturePendingGap? = nil
    var pendingRollover: MeetingCapturePendingRollover? = nil
    var finalSealBoundary: MeetingCaptureBoundary? = nil
    var createdAt: Date
    var updatedAt: Date
}

struct MeetingServerEpochCheckpoint: Codable, Equatable {
    let streamEpoch: Int
    let state: String
    let highestContiguousSequence: Int
    let highestReceivedSequence: Int
    let declaredLastSequence: Int?
    let recordedThroughSample: Int64?
    let missingSequenceRanges: [[String: Int]]

    enum CodingKeys: String, CodingKey {
        case streamEpoch = "stream_epoch"
        case state
        case highestContiguousSequence = "highest_contiguous_sequence"
        case highestReceivedSequence = "highest_received_sequence"
        case declaredLastSequence = "declared_last_sequence"
        case recordedThroughSample = "recorded_through_sample"
        case missingSequenceRanges = "missing_sequence_ranges"
    }
}

struct MeetingServerCaptureCheckpoint: Codable, Equatable {
    let state: String
    let recordedThroughSample: Int64?
    let lastSealedEpoch: Int?
    let manifestRevision: Int?

    enum CodingKeys: String, CodingKey {
        case state
        case recordedThroughSample = "recorded_through_sample"
        case lastSealedEpoch = "last_sealed_epoch"
        case manifestRevision = "manifest_revision"
    }
}

struct MeetingServerIngestCheckpoint: Codable, Equatable {
    let persistedThroughSample: Int64
    let accountedThroughSample: Int64
    let highestReceivedSample: Int64
    let receivedBatches: Int
    let receivedBytes: Int64
    let missingSampleRanges: [[String: Int64]]
    let audioMissingSampleRanges: [[String: Int64]]
    let acceptedGaps: Int
    let ingestComplete: Bool

    enum CodingKeys: String, CodingKey {
        case persistedThroughSample = "persisted_through_sample"
        case accountedThroughSample = "accounted_through_sample"
        case highestReceivedSample = "highest_received_sample"
        case receivedBatches = "received_batches"
        case receivedBytes = "received_bytes"
        case missingSampleRanges = "missing_sample_ranges"
        case audioMissingSampleRanges = "audio_missing_sample_ranges"
        case acceptedGaps = "accepted_gaps"
        case ingestComplete = "ingest_complete"
    }
}

struct MeetingServerRealtimeCheckpoint: Codable, Equatable {
    let streamEpoch: Int
    let lastAckedSequence: Int
    let stableOrdinal: Int
    let eventCursor: Int

    enum CodingKeys: String, CodingKey {
        case streamEpoch = "stream_epoch"
        case lastAckedSequence = "last_acked_sequence"
        case stableOrdinal = "stable_ordinal"
        case eventCursor = "event_cursor"
    }
}

struct MeetingServerFinalizationCheckpoint: Codable, Equatable {
    let captureSealed: Bool
    let ingestComplete: Bool
    let hasUnrecoverableGaps: Bool
    let packagingState: String?
    let packagingAttempt: Int
    let packagingErrorCode: String?
    let wavSHA256: String?
    let wavByteSize: Int64?
    let serverPlaybackState: String
    let postprocessState: String

    enum CodingKeys: String, CodingKey {
        case captureSealed = "capture_sealed"
        case ingestComplete = "ingest_complete"
        case hasUnrecoverableGaps = "has_unrecoverable_gaps"
        case packagingState = "packaging_state"
        case packagingAttempt = "packaging_attempt"
        case packagingErrorCode = "packaging_error_code"
        case wavSHA256 = "wav_sha256"
        case wavByteSize = "wav_byte_size"
        case serverPlaybackState = "server_playback_state"
        case postprocessState = "postprocess_state"
    }
}

struct MeetingServerCheckpoint: Codable, Equatable {
    let schemaVersion: String
    let captureId: String
    let meetingId: String
    let captureCheckpoint: MeetingServerCaptureCheckpoint
    let ingestCheckpoint: MeetingServerIngestCheckpoint
    let realtimeCheckpoint: MeetingServerRealtimeCheckpoint
    let finalizationCheckpoint: MeetingServerFinalizationCheckpoint
    let epochs: [MeetingServerEpochCheckpoint]

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case captureId = "capture_id"
        case meetingId = "meeting_id"
        case captureCheckpoint = "capture_checkpoint"
        case ingestCheckpoint = "ingest_checkpoint"
        case realtimeCheckpoint = "realtime_checkpoint"
        case finalizationCheckpoint = "finalization_checkpoint"
        case epochs
    }
}

struct MeetingServerCaptureStatus: Codable, Equatable {
    let id: String
    let meetingId: String
    let state: String
    let currentEpoch: Int
    let sealedThroughSample: Int64?
    let ingestComplete: Bool
    let serverPlaybackState: String

    enum CodingKeys: String, CodingKey {
        case id
        case meetingId = "meeting_id"
        case state
        case currentEpoch = "current_epoch"
        case sealedThroughSample = "sealed_through_sample"
        case ingestComplete = "ingest_complete"
        case serverPlaybackState = "server_playback_state"
    }
}

struct MeetingServerSealResponse: Codable, Equatable {
    let schemaVersion: String
    let capture: MeetingServerCaptureStatus
    let checkpoint: MeetingServerCheckpoint
    let replayed: Bool

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case capture
        case checkpoint
        case replayed
    }
}

struct MeetingServerGapResponse: Codable, Equatable {
    let schemaVersion: String
    let captureId: String
    let gapId: String
    let replayed: Bool
    let checkpoint: MeetingServerCheckpoint

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case captureId = "capture_id"
        case gapId = "gap_id"
        case replayed
        case checkpoint
    }
}

struct MeetingServerRolloverResponse: Codable, Equatable {
    let schemaVersion: String
    let captureId: String
    let previousEpoch: Int
    let streamEpoch: Int
    let streamTicket: String
    let streamTicketExpiresAt: String
    let websocketURL: String
    let lastAckedSequence: Int
    let captureOffsetMs: Int64
    let reconnectWindowSeconds: Int
    let replayed: Bool
    let checkpoint: MeetingServerCheckpoint

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case captureId = "capture_id"
        case previousEpoch = "previous_epoch"
        case streamEpoch = "stream_epoch"
        case streamTicket = "stream_ticket"
        case streamTicketExpiresAt = "stream_ticket_expires_at"
        case websocketURL = "ws_url"
        case lastAckedSequence = "last_acked_sequence"
        case captureOffsetMs = "capture_offset_ms"
        case reconnectWindowSeconds = "reconnect_window_seconds"
        case replayed
        case checkpoint
    }

    var dictionary: [String: Any] {
        [
            "captureId": captureId,
            "previousEpoch": previousEpoch,
            "streamEpoch": streamEpoch,
            "streamTicket": streamTicket,
            "streamTicketExpiresAt": streamTicketExpiresAt,
            "wsUrl": websocketURL,
            "lastAckedSequence": lastAckedSequence,
            "captureOffsetMs": captureOffsetMs,
            "reconnectWindowSeconds": reconnectWindowSeconds,
            "replayed": replayed
        ]
    }
}

struct MeetingCaptureStatus {
    var state: MeetingCaptureLifecycle = .idle
    var meetingId: String?
    var captureId: String?
    var streamEpoch = 0
    var recordedThroughSample: Int64 = 0
    var lastSealedSequence = -1
    var manifestRevision = 0
    var pendingUploadCount = 0
    var gapCount = 0
    var materializedGapSamples: Int64 = 0
    var localPlaybackReady = false
    var interruptionReason: String?
    var errorCode: String?

    init() {}

    init(manifest: MeetingCaptureManifest) {
        state = manifest.state
        meetingId = manifest.meetingId
        captureId = manifest.captureId
        streamEpoch = manifest.streamEpoch
        recordedThroughSample = manifest.recordedThroughSample
        lastSealedSequence = manifest.lastSealedSequence
        manifestRevision = manifest.manifestRevision
        pendingUploadCount = manifest.batches.filter { !$0.uploaded }.count
        gapCount = manifest.gaps.count
        materializedGapSamples = manifest.gaps.reduce(0) { $0 + ($1.endSample - $1.startSample) }
        localPlaybackReady = manifest.localPlaybackReady
        interruptionReason = manifest.interruptionReason
        errorCode = manifest.errorCode
    }

    var dictionary: [String: Any] {
        [
            "schemaVersion": meetingCaptureSchemaVersion,
            "adapter": "ios_native",
            "state": state.rawValue,
            "meetingId": meetingId.map { $0 as Any } ?? NSNull(),
            "captureId": captureId.map { $0 as Any } ?? NSNull(),
            "streamEpoch": streamEpoch,
            "recordedThroughSample": recordedThroughSample,
            "lastSealedSequence": lastSealedSequence,
            "manifestRevision": manifestRevision,
            "pendingUploadCount": pendingUploadCount,
            "gapCount": gapCount,
            "materializedGapSamples": materializedGapSamples,
            "localPlaybackReady": localPlaybackReady,
            "interruptionReason": interruptionReason.map { $0 as Any } ?? NSNull(),
            "errorCode": errorCode.map { $0 as Any } ?? NSNull()
        ]
    }
}

struct MeetingLocalPlaybackAsset {
    let captureId: String
    let durationMs: Int64

    var handle: String { "capture-asset:\(captureId)" }

    var dictionary: [String: Any] {
        [
            "handle": handle,
            "mediaType": "audio/wav",
            "durationMs": durationMs
        ]
    }
}

struct MeetingPlaybackStatus {
    let handle: String?
    let source: String
    let positionMs: Int64
    let durationMs: Int64
    let playing: Bool
    let serverReady: Bool

    var dictionary: [String: Any] {
        [
            "handle": handle.map { $0 as Any } ?? NSNull(),
            "source": source,
            "positionMs": positionMs,
            "durationMs": durationMs,
            "playing": playing,
            "serverReady": serverReady
        ]
    }
}

struct MeetingCaptureBatchEvent {
    let batch: MeetingCaptureBatch

    var dictionary: [String: Any] {
        [
            "streamEpoch": batch.streamEpoch,
            "sequence": batch.sequence,
            "firstSample": batch.firstSample,
            "sampleCount": batch.sampleCount,
            "manifestRevision": batch.manifestRevision
        ]
    }
}

extension DispatchQueue {
    static let meetingCapture = DispatchQueue(label: "com.siqresearch.meeting-capture.state", qos: .userInitiated)
}
