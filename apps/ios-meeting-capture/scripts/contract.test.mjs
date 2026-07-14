import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const repo = resolve(root, '../..')
const swiftRoot = join(root, 'ios/Sources/MeetingCapturePlugin')

function read(relativePath) {
  return readFileSync(join(root, relativePath), 'utf8')
}

function allSwift() {
  return readdirSync(swiftRoot)
    .filter((name) => name.endsWith('.swift'))
    .map((name) => readFileSync(join(swiftRoot, name), 'utf8'))
    .join('\n')
}

test('Capacitor bridge freezes the complete native capture lifecycle', () => {
  const source = read('ios/Sources/MeetingCapturePlugin/MeetingCapturePlugin.swift')
  for (const method of [
    'prepare',
    'start',
    'pause',
    'resume',
    'stop',
    'getStatus',
    'getCheckpoints',
    'getLocalPlaybackAsset',
    'retryPendingUploads',
    'recoverPendingCaptures',
    'rollover',
    'playLocalPlayback',
    'pausePlayback',
    'seekPlayback',
    'getPlaybackStatus',
    'switchToServerPlayback',
    'discardLocalCapture',
  ]) {
    assert.match(source, new RegExp(`CAPPluginMethod\\(name: "${method}"`))
  }
  assert.match(source, /public let jsName = "MeetingCapture"/)
})

test('recorder uses native audio input and handles iOS interruption surfaces', () => {
  const source = read('ios/Sources/MeetingCapturePlugin/MeetingCaptureRecorder.swift')
  const controller = read('ios/Sources/MeetingCapturePlugin/MeetingCaptureController.swift')
  assert.match(source, /AVAudioSession\.sharedInstance\(\)/)
  assert.match(source, /AVAudioEngine\(\)/)
  assert.match(source, /installTap\(onBus:/)
  assert.match(source, /pcmFormatInt16/)
  assert.match(source, /sampleRate: Double\(meetingCaptureSampleRate\)/)
  assert.match(source, /interruptionNotification/)
  assert.match(source, /routeChangeNotification/)
  assert.match(source, /mediaServicesWereResetNotification/)
  assert.match(source, /requestRecordPermission/)
  assert.match(source, /stopForReconfigurationAndDrain/)
  assert.match(source, /func pauseAndDrain[\s\S]*conversionQueue\.async/)
  assert.match(source, /stopInput\(\)[\s\S]*conversionQueue\.async/)
  assert.match(source, /conversionQueue\.async[\s\S]*self\?\.converter = nil/)
  assert.match(source, /conversionQueue\.async[\s\S]*onInterrupted/)
  assert.match(source, /conversionQueue\.async[\s\S]*onInterruptionEnded/)
  assert.match(controller, /recorder\.stopForReconfigurationAndDrain/)
  assert.match(controller, /recorder\.pauseAndDrain[\s\S]*try store\.pause/)
  assert.doesNotMatch(controller, /recorder\.pause\(\)[\s\S]{0,120}try store\.pause/)
  assert.match(
    controller,
    /try store\.startWriting\(\)[\s\S]*try recorder\.start\(\)/,
  )
})

test('local durability uses protected non-backed-up files and atomic fsynced metadata', () => {
  const source = read('ios/Sources/MeetingCapturePlugin/MeetingCaptureStore.swift')
  assert.match(source, /completeUntilFirstUserAuthentication/)
  assert.match(source, /isExcludedFromBackup = true/)
  assert.match(source, /synchronize\(\)/)
  assert.match(source, /replaceItemAt/)
  assert.match(source, /persistBatchSidecar/)
  assert.match(source, /SHA256\.hash/)
  assert.match(source, /storageQuotaExceeded/)
  assert.match(source, /UUID\(uuidString: captureId\)/)
  assert.match(source, /standardizedFileURL/)
  assert.match(source, /resolvingSymlinksInPath/)
  assert.match(source, /isSymbolicLinkKey/)
  assert.match(source, /trustedAPIOrigin/)
  assert.match(allSwift(), /siq\.meeting\.native_capture\.manifest\.v1/)
  assert.match(source, /withoutEscapingSlashes/)
  assert.match(source, /clearOpenBatchMemory\(\)/)
  assert.match(
    source,
    /guard batchPartialURL == nil,[\s\S]*openBatchJournalURL\.path[\s\S]*throw MeetingCaptureError\.corruptManifest/,
  )
})

test('capture token is Keychain-only and background uploads use file tasks', () => {
  const keychain = read('ios/Sources/MeetingCapturePlugin/MeetingCaptureKeychain.swift')
  const uploader = read('ios/Sources/MeetingCapturePlugin/MeetingCaptureUploader.swift')
  const models = read('ios/Sources/MeetingCapturePlugin/MeetingCaptureModels.swift')
  const manifestBlock = models.slice(
    models.indexOf('struct MeetingCaptureManifest'),
    models.indexOf('struct MeetingCaptureStatus'),
  )

  assert.match(keychain, /kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly/)
  assert.match(uploader, /URLSessionConfiguration\.background/)
  assert.match(uploader, /sessionSendsLaunchEvents = true/)
  assert.match(uploader, /uploadTask\(with: request, fromFile: fileURL\)/)
  assert.match(uploader, /forHTTPHeaderField: "Authorization"/)
  assert.match(uploader, /forHTTPHeaderField: "X-SIQ-Device-Installation-Id"/)
  assert.match(keychain, /deviceInstallationId/)
  assert.match(uploader, /maxResponseBytes = 65_536/)
  assert.match(uploader, /sessionIdentifierPrefix/)
  assert.match(uploader, /manifest\.captureId.*batch\.streamEpoch.*batch\.sequence.*batch\.sha256/s)
  assert.match(uploader, /restoredTasksLoaded/)
  assert.match(uploader, /JSONDecoder\(\)\.decode\(BatchACK\.self/)
  assert.match(uploader, /ack\.capture_id == taskCaptureId/)
  assert.match(uploader, /localBatch\.sha256/)
  assert.match(uploader, /completionHandler\(nil\)/)
  assert.doesNotMatch(manifestBlock, /token|authorization/i)
  assert.doesNotMatch(keychain, /userBearerToken|foregroundBearerToken/)
  assert.match(allSwift(), /foregroundBearerToken/)
  assert.match(allSwift(), /applyUserAuthorization/)
  assert.doesNotMatch(allSwift(), /[?&](?:token|authorization)=/i)
  const controller = read('ios/Sources/MeetingCapturePlugin/MeetingCaptureController.swift')
  const finishStop = controller.slice(
    controller.indexOf('private func finishStop'),
    controller.indexOf('private func resolveStopCompletions'),
  )
  assert.match(finishStop, /clearForegroundAuthorization\(\)/)
  assert.match(
    controller,
    /private func clearForegroundAuthorization\(\)[\s\S]*foregroundBearerToken = nil[\s\S]*uploader\?\.setForegroundBearerToken\(nil\)/,
  )
})

test('playback stays behind an opaque handle and never crosses the bridge as a file path', () => {
  const models = read('ios/Sources/MeetingCapturePlugin/MeetingCaptureModels.swift')
  const assetBlock = models.slice(
    models.indexOf('struct MeetingLocalPlaybackAsset'),
    models.indexOf('struct MeetingCaptureBatchEvent'),
  )
  assert.match(assetBlock, /capture-asset:/)
  assert.match(assetBlock, /"handle": handle/)
  assert.doesNotMatch(assetBlock, /path|url/i)
  assert.doesNotMatch(allSwift(), /file:\/\//)
  const player = read('ios/Sources/MeetingCapturePlugin/MeetingCapturePlaybackController.swift')
  assert.match(player, /AVAudioPlayer\(contentsOf:/)
  assert.match(player, /AVPlayerItem\(asset: AVURLAsset\(url:/)
  assert.match(player, /let switchAtSeconds = self\.currentSeconds\(\)/)
  assert.match(player, /candidate\.seek\(to: CMTime\(seconds: switchAtSeconds/)
  assert.match(player, /query\[0\]\.name == "playback_ticket"/)
  assert.match(player, /self\.localPlayer\?\.pause\(\)/)
})

test('host declares honest microphone and background audio capabilities', () => {
  const info = read('ios/App/App/Info.plist')
  const appDelegate = read('ios/App/App/AppDelegate.swift')
  const privacy = read('ios/App/App/PrivacyInfo.xcprivacy')
  assert.match(info, /<key>NSMicrophoneUsageDescription<\/key>/)
  assert.match(info, /<key>SIQMeetingAPIOrigin<\/key>/)
  assert.match(info, /<key>SIQAuthCSRFCookieName<\/key>/)
  assert.match(info, /<key>UIBackgroundModes<\/key>[\s\S]*<string>audio<\/string>/)
  assert.match(appDelegate, /handleEventsForBackgroundURLSession/)
  assert.match(appDelegate, /MeetingCaptureBackgroundEvents\.shared/)
  assert.match(privacy, /NSPrivacyCollectedDataTypeAudioData/)
  assert.match(privacy, /<key>NSPrivacyTracking<\/key>\s*<false\/>/)
  assert.match(privacy, /NSPrivacyAccessedAPICategoryDiskSpace[\s\S]*85F4\.1/)
  assert.match(privacy, /NSPrivacyAccessedAPICategorySystemBootTime[\s\S]*35F9\.1/)
})

test('cleanup and server readiness stay fail-closed without authenticated server proof', () => {
  const controller = read('ios/Sources/MeetingCapturePlugin/MeetingCaptureController.swift')
  const store = read('ios/Sources/MeetingCapturePlugin/MeetingCaptureStore.swift')
  const uploader = read('ios/Sources/MeetingCapturePlugin/MeetingCaptureUploader.swift')
  const serverClient = read('ios/Sources/MeetingCapturePlugin/MeetingCaptureServerClient.swift')

  assert.match(controller, /verified cleanup receipt is unavailable/)
  assert.match(controller, /stopAndDrain/)
  assert.match(controller, /try uploader\.refreshCheckpointAndSchedule/)
  assert.match(
    controller,
    /serverCheckpoints\[manifest\.captureId\]\?\.finalizationCheckpoint\.serverPlaybackState == "ready"/,
  )
  assert.match(store, /"ingestComplete": server\.finalizationCheckpoint\.ingestComplete/)
  assert.match(
    store,
    /"serverPlaybackState": server\.finalizationCheckpoint\.serverPlaybackState/,
  )
  assert.match(store, /"finalization": "authenticated_server_checkpoint"/)
  assert.doesNotMatch(store, /"ingestComplete": (?:true|false)/)
  assert.match(
    uploader,
    /serverClient\.fetchCheckpoint\([\s\S]*token: credentials\.token,[\s\S]*deviceInstallationId: credentials\.deviceInstallationId/,
  )
  assert.match(
    uploader,
    /serverClient\.seal\([\s\S]*token: credentials\.token,[\s\S]*deviceInstallationId: credentials\.deviceInstallationId/,
  )
  assert.match(serverClient, /forHTTPHeaderField: "Authorization"/)
  assert.match(serverClient, /response\.url == request\.url/)
  assert.match(serverClient, /data\.count <= Self\.maxResponseBytes/)
  assert.match(serverClient, /completionHandler\(nil\)/)
  assert.match(uploader, /body\.count \+ data\.count <= Self\.maxResponseBytes/)
  assert.match(uploader, /completionHandler\(nil\)/)
})

test('cold-start recovery restores durable sessions without starting the recorder', () => {
  const recovery = read('ios/Sources/MeetingCapturePlugin/MeetingCaptureRecoveryCoordinator.swift')
  const appDelegate = read('ios/App/App/AppDelegate.swift')
  const store = read('ios/Sources/MeetingCapturePlugin/MeetingCaptureStore.swift')
  assert.match(appDelegate, /MeetingCaptureRecoveryCoordinator\.shared\.bootstrap/)
  assert.match(recovery, /recoverableCaptureIds\(\)/)
  assert.match(recovery, /MeetingCaptureUploader\(store: store, keychain: keychain\)/)
  assert.match(recovery, /schedulePendingUploads\(\)/)
  assert.doesNotMatch(recovery, /MeetingCaptureRecorder|requestPermission|recorder\.|\.start\(\)/)
  assert.match(store, /recovered\.state = \.interrupted/)
  assert.match(store, /recovered\.interruptionReason = "process_recovered"/)
  assert.match(store, /MeetingCaptureOpenBatchJournal/)
  assert.match(store, /let recoveredSamples = min\(/)
  assert.match(store, /_ = try sealOpenBatch\(\)/)
  assert.match(store, /MeetingCapturePendingGap/)
  assert.match(store, /Data\(repeating: 0, count:/)
  assert.match(store, /manifestEntries: entries/)
  assert.match(store, /pendingServerGaps/)
  assert.match(store, /flatMap \{ \$0\.manifestEntries \?\? \[\] \}/)
  assert.match(store, /current\.pendingGap = nil/)
})

test('authenticated checkpoint reconciliation drives strict ordered retransmission and seal', () => {
  const client = read('ios/Sources/MeetingCapturePlugin/MeetingCaptureServerClient.swift')
  const uploader = read('ios/Sources/MeetingCapturePlugin/MeetingCaptureUploader.swift')
  const store = read('ios/Sources/MeetingCapturePlugin/MeetingCaptureStore.swift')
  assert.match(client, /suffix: "checkpoint"/)
  assert.match(client, /forHTTPHeaderField: "Authorization"/)
  assert.match(client, /forHTTPHeaderField: "X-SIQ-Device-Installation-Id"/)
  assert.match(store, /checkpoint\.epochs/)
  assert.match(store, /missingSequenceRanges/)
  assert.match(uploader, /httpMaximumConnectionsPerHost = 1/)
  assert.match(uploader, /guard restoredTasksLoaded, scheduledKeys\.isEmpty/)
  assert.match(uploader, /queuedKeys\.removeFirst\(\)/)
  assert.match(uploader, /requestSealWhenSynchronized/)
  assert.match(client, /func seal\(/)
  assert.match(client, /BoundaryPayload\(boundary\)/)
  assert.match(client, /func declareGap\(/)
  assert.match(client, /suffix: "gaps"/)
  assert.match(client, /reason: "system_interruption"/)
  assert.match(uploader, /declareNextGap\(after:/)
  assert.match(uploader, /markGapServerDeclared/)
})

test('rollover is foreground-authenticated, replayable, and fences the next local epoch', () => {
  const client = read('ios/Sources/MeetingCapturePlugin/MeetingCaptureServerClient.swift')
  const controller = read('ios/Sources/MeetingCapturePlugin/MeetingCaptureController.swift')
  const store = read('ios/Sources/MeetingCapturePlugin/MeetingCaptureStore.swift')
  assert.match(client, /synchronizeWebSessionCookies/)
  assert.match(client, /forHTTPHeaderField: "X-CSRF-Token"/)
  assert.match(client, /SIQAuthCSRFCookieName/)
  assert.match(client, /suffix: "rollover"/)
  assert.match(client, /token: nil/)
  assert.match(client, /pending\.idempotencyKey/)
  assert.match(controller, /uploader\.synchronize/)
  assert.match(store, /current\.pendingRollover = pending/)
  assert.match(store, /current\.streamEpoch = pending\.nextEpoch/)
  assert.match(store, /response\.streamEpoch == pending\.nextEpoch/)
  assert.match(store, /current\.pendingRollover = nil/)
  assert.match(store, /try freezeGaps\(epoch:/)
})

test('getStatus and getCheckpoints use persisted and authenticated authorities', () => {
  const controller = read('ios/Sources/MeetingCapturePlugin/MeetingCaptureController.swift')
  const plugin = read('ios/Sources/MeetingCapturePlugin/MeetingCapturePlugin.swift')
  const store = read('ios/Sources/MeetingCapturePlugin/MeetingCaptureStore.swift')
  assert.match(controller, /MeetingCaptureStatus\(manifest: try store\.currentManifest\(\)\)/)
  assert.match(controller, /refreshCheckpointAndSchedule/)
  assert.match(controller, /checkpointDictionary\(server: checkpoint\)/)
  assert.match(plugin, /controller\.bootstrapRecovery/)
  assert.match(plugin, /try self\.controller\.checkpoints \{ result in/)
  assert.match(allSwift(), /case eventCursor = "event_cursor"/)
  assert.match(store, /"eventCursor": server\.realtimeCheckpoint\.eventCursor/)
})

test('web adapter fails closed and uses backend start/end missing ranges', () => {
  const adapter = readFileSync(
    join(repo, 'apps/web/src/features/meeting-transcription/captureAdapter.ts'),
    'utf8',
  )
  const nativeTypes = readFileSync(
    join(repo, 'apps/web/src/features/meeting-transcription/nativeCapture.ts'),
    'utf8',
  )
  const nativeApi = readFileSync(
    join(repo, 'apps/web/src/features/meeting-transcription/nativeCaptureApi.ts'),
    'utf8',
  )
  assert.match(adapter, /if \(!input\.nativeFeatureEnabled\)/)
  assert.match(adapter, /runtime\.platform !== 'ios'/)
  assert.match(adapter, /pluginAvailable/)
  assert.doesNotMatch(adapter, /userAgent|navigator\.platform/)
  assert.match(nativeTypes, /missingSequenceRanges: Array<\{ start: number; end: number \}>/)
  assert.match(nativeApi, /missing_sequence_ranges: Array<\{ start: number; end: number \}>/)
  assert.match(nativeApi, /manifest_entries: NativeCaptureManifestEntry\[\]/)
})

test('Swift and backend retain the same canonical manifest digest vector', () => {
  const expected = '9abc5bec51abd3bccf0074243c26a4096f487b3b96875cf669d2053bb9e74c58'
  const swiftTest = read('ios/Tests/MeetingCapturePluginTests/MeetingCaptureStoreTests.swift')
  const backendTest = readFileSync(
    join(repo, 'apps/api/tests/test_meeting_native_capture.py'),
    'utf8',
  )
  assert.match(swiftTest, new RegExp(expected))
  assert.match(backendTest, new RegExp(expected))
})
