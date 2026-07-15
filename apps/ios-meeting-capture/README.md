# SIQ iOS Meeting Capture

This directory is the isolated M8 native-capture build target. It contains a Capacitor 8 bridge contract and an iOS 15+ Swift Package. It is not imported by the default web build, and the existing `AudioWorklet + WebSocket + IndexedDB` path remains the only default path.

## Frozen boundary

- A user must explicitly call `prepare` and `start` while the app is in the foreground. The plugin never starts on launch, push, process recovery, or remote input.
- `AVAudioSession` and `AVAudioEngine` are owned by Swift. Capture, file writes, manifest updates, and background uploads do not require a live WebView callback.
- PCM S16LE, 16 kHz, mono batches use sample offsets as their timeline identity. Interruptions create explicit gaps; clock changes cannot move the sample timeline backwards.
- Each batch is fsynced, SHA-256 sealed, paired with an atomic sidecar, and then committed to the atomic manifest. A continuous protected WAV is maintained for immediate local playback.
- The store derives `siq.meeting.native_capture.manifest.v1` canonical entries and digest using the backend's sorted compact JSON contract. Stop queues the authenticated capture-token seal only after the ordered outbox is empty. A successful seal is followed by idempotent user-session gap declarations, so an interruption is not misreported as received audio.
- The bridge returns only `capture-asset:<capture-id>`. It never returns an absolute sandbox path or a general `file://` URL.
- Capture tokens and their bound installation IDs are stored together in Keychain with `AfterFirstUnlockThisDeviceOnly`. Tokens are sent only in `Authorization`; every capture-scoped request also sends `X-SIQ-Device-Installation-Id`. Neither value enters manifests, filenames, task descriptions, events, or error payloads.
- Each capture uses its own background `URLSession`; restored task keys include capture ID, epoch, sequence, and SHA-256. Cold launch enumerates protected capture directories, reconstructs matching sessions, validates every restored request against the manifest, and resumes the ordered outbox without starting the microphone. A batch becomes locally ACKed only after a bounded JSON response exactly matches the local capture, coordinates, digest, and byte size. Redirects, empty 2xx responses, malformed ACKs, and server checkpoints that no longer prove a batch durable leave or return it to the pending outbox.
- `getCheckpoints` performs an authenticated server read and combines four explicit authorities: the local capture manifest and the server ingest, realtime, and finalization checkpoints. It does not substitute local upload-task completion for server durability. Foreground rollover first reconciles and drains the old epoch, persists one replayable request boundary/key, uses the WebView user session for the control-plane call, and fences new local batches behind the new epoch until the server reply is validated.
- An audio interruption first persists a pending gap. The local playback WAV receives deterministic silence so playback time stays continuous, while the upload manifest receives virtual, non-uploaded sequence entries. Rollover/seal freezes those entries and the server receives an explicit `system_interruption` gap only after final seal. The UI receives the exact sample and sequence range.
- The playback bridge consumes only `capture-asset:<capture-id>`. `AVAudioPlayer` owns local playback; once an authenticated server Range URL is ready, `AVPlayer` prepares it, seeks to the current local position, and switches only if the latest generation is still current. A failed or stale switch preserves the local player.
- Local deletion is fail-closed behind an authenticated cleanup receipt. The bridge boolean is only user intent; native code first refreshes the capture-token checkpoint, requires sealed ingest, ready server packaging/playback, an empty server missing range, and an exact local/server WAV SHA-256 and byte-size match. It atomically persists the receipt before cancelling uploads, removing the capture token, and deleting the protected directory. A cold-start recovery completes any staged cleanup before it creates an uploader.
- `gap` and `rollover` are foreground, user-session APIs. Capture-token scope remains limited to batch upload, checkpoint read, and seal; it is not expanded to meeting control. Parent-domain session cookies are copied only for the configured trusted API host.
- Foreground control calls support either the trusted WebView cookie session plus a matching CSRF header, or the current user bearer supplied by the shell. The bearer is memory-only: it is not written to Keychain, manifests, filenames, background tasks, events, or crash payloads. A cold recovery can resume capture-token uploads and seal, but waits for foreground reauthentication before any pending user-session gap call.
- The checked-in bridge validates `SIQMeetingAPIOrigin` as an origin-only HTTPS URL and injects it at document start as an immutable `__SIQ_NATIVE_CONFIG__`. The shared Web API client accepts that override only from the `capacitor://localhost` shell; ordinary Web deployments retain their existing same-origin `/api` behavior.

## Source layout

- `src/`: the typed Capacitor bridge consumed by the native shell.
- `ios/Sources/MeetingCapturePlugin/`: recorder, durable store/outbox, Keychain, background uploader, controller, and Capacitor plugin.
- `ios/App/App.xcodeproj`: a checked-in, standalone Capacitor SPM application target linked to the repository's local `SIQMeetingCapture` Swift package.
- `ios/App/App/`: the native host, explicit plugin bridge registration, microphone/background declarations, privacy manifest, and background-session completion forwarding.
- `ios/Tests/`: simulator/Xcode unit tests for persistence and opaque playback handles.
- `scripts/contract.test.mjs`: Linux-safe static contract checks. These checks do not claim that iOS background recording works.

## Local checks

```bash
npm install --ignore-scripts
npm run check
npm --prefix ../web run build
npm run ios:sync
```

`ios:sync` copies the current `apps/web/dist` bundle and refreshes Capacitor's generated runtime files; these generated files remain ignored. On macOS, open `ios/App/App.xcodeproj`, set the `SIQ_MEETING_API_ORIGIN` build setting to the exact trusted HTTPS origin, choose a signing team, and run the app plus Swift tests from Xcode. The app target already links the local Swift package, registers `MeetingCapturePlugin` explicitly, includes the privacy manifest, and declares background audio. The plugin rejects API URLs with another origin, user info, query, fragment, or path. The checked-in `capacitor.config.ts` freezes the application identity and web bundle location.

## Parameters requiring real-device freeze

The current prototype uses `playAndRecord`, `spokenAudio`, a preferred 16 kHz sample rate, a 20 ms preferred I/O buffer, 5-second batches, and `completeUntilFirstUserAuthentication` file protection. These are provisional. MT-081 and MT-086 must freeze them only after supported-device tests confirm audio quality, Bluetooth behavior, locked-device writes, energy use, temperature, and the security tradeoff.

Pure Web, PWA, Simulator, and WKWebView-only results are not evidence for locked-screen capture. Force quit, device reboot, OS process termination, or revoked microphone permission stop capture; the product must not claim otherwise.

## Release evidence still required

Before enabling `SIQ_MEETING_IOS_NATIVE_CAPTURE_ENABLED`, retain per-device evidence for locked-screen runs at 1, 10, 30, and 60 minutes plus a 4-hour soak. Verify sample counts, batch hashes, gaps, duplicate suppression, Wi-Fi/cellular transitions, 30-minute offline recovery, calls and route changes, low-power and low-disk behavior, crash/upgrade recovery, local playback P95 under 2 seconds, energy, thermal state, storage, and upload traffic.

The privacy manifest and microphone/background-audio wording are review inputs, not App Store approval. Legal/privacy review, signing, provisioning, supported-device matrix, Xcode compilation, and physical-device results are mandatory release gates.

This remains an isolated implementation candidate, not an M8 release claim. Linux checks cover the bridge contract and Swift source invariants; they do not type-check Apple frameworks. The checked-in XCTest suite covers idempotent stop, opaque playback handles, canonical digests, open-batch crash recovery, persistent rollover boundaries, bidirectional server checkpoint reconciliation, interruption-gap materialization, and staged cleanup recovery, but it still must run under Xcode. The cleanup receipt is an authenticated durability proof derived from the existing checkpoint contract; a separately signed server deletion endpoint is not currently part of the backend contract. Signing, provisioning, supported-device Xcode compilation, security/privacy review, App Store review, and the physical-device matrix below still block enabling the feature flag.
