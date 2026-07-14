# SIQ iOS Meeting Capture

This directory is the isolated M8 native-capture build target. It contains a Capacitor 8 bridge contract and an iOS 15+ Swift Package. It is not imported by the default web build, and the existing `AudioWorklet + WebSocket + IndexedDB` path remains the only default path.

## Frozen boundary

- A user must explicitly call `prepare` and `start` while the app is in the foreground. The plugin never starts on launch, push, process recovery, or remote input.
- `AVAudioSession` and `AVAudioEngine` are owned by Swift. Capture, file writes, manifest updates, and background uploads do not require a live WebView callback.
- PCM S16LE, 16 kHz, mono batches use sample offsets as their timeline identity. Interruptions create explicit gaps; clock changes cannot move the sample timeline backwards.
- Each batch is fsynced, SHA-256 sealed, paired with an atomic sidecar, and then committed to the atomic manifest. A continuous protected WAV is maintained for immediate local playback.
- The store can derive `siq.meeting.native_capture.manifest.v1` canonical entries and digest using the backend's sorted compact JSON contract. This is covered by a fixed digest vector, but no seal request is sent yet.
- The bridge returns only `capture-asset:<capture-id>`. It never returns an absolute sandbox path or a general `file://` URL.
- Capture tokens and their bound installation IDs are stored together in Keychain with `AfterFirstUnlockThisDeviceOnly`. Tokens are sent only in `Authorization`; every capture-scoped request also sends `X-SIQ-Device-Installation-Id`. Neither value enters manifests, filenames, task descriptions, events, or error payloads.
- Each capture uses its own background `URLSession`; restored task keys include capture ID, epoch, sequence, and SHA-256. A batch becomes locally ACKed only after a bounded JSON response exactly matches the local capture, coordinates, digest, and byte size. Redirects, empty 2xx responses, and malformed ACKs leave the batch pending.
- Local deletion is deliberately disabled in this skeleton. The bridge boolean is only user intent and cannot prove server durability. Deletion must remain fail-closed until the native layer validates an authenticated server checkpoint or signed cleanup receipt.
- `gap` and `rollover` are foreground, main-session APIs. Capture-token scope is limited to batch upload, checkpoint read, and seal; it must not be expanded to meeting control.

## Source layout

- `src/`: the typed Capacitor bridge consumed by the native shell.
- `ios/Sources/MeetingCapturePlugin/`: recorder, durable store/outbox, Keychain, background uploader, controller, and Capacitor plugin.
- `ios/App/App/`: host integration templates for microphone disclosure, background audio, privacy manifest, and background-session completion forwarding.
- `ios/Tests/`: simulator/Xcode unit tests for persistence and opaque playback handles.
- `scripts/contract.test.mjs`: Linux-safe static contract checks. These checks do not claim that iOS background recording works.

## Local checks

```bash
npm install --ignore-scripts
npm run check
```

On macOS, attach `Package.swift` to the dedicated Capacitor iOS target, set the `SIQ_MEETING_API_ORIGIN` build setting to the exact trusted HTTPS origin, add the host template keys and AppDelegate forwarding, then run the Swift tests from Xcode. The plugin rejects API URLs with another origin, user info, query, fragment, or path. The checked-in `capacitor.config.ts` freezes the application identity and web bundle location; generation and signing of the Xcode project remain release-environment work.

## Parameters requiring real-device freeze

The current prototype uses `playAndRecord`, `spokenAudio`, a preferred 16 kHz sample rate, a 20 ms preferred I/O buffer, 5-second batches, and `completeUntilFirstUserAuthentication` file protection. These are provisional. MT-081 and MT-086 must freeze them only after supported-device tests confirm audio quality, Bluetooth behavior, locked-device writes, energy use, temperature, and the security tradeoff.

Pure Web, PWA, Simulator, and WKWebView-only results are not evidence for locked-screen capture. Force quit, device reboot, OS process termination, or revoked microphone permission stop capture; the product must not claim otherwise.

## Release evidence still required

Before enabling `SIQ_MEETING_IOS_NATIVE_CAPTURE_ENABLED`, retain per-device evidence for locked-screen runs at 1, 10, 30, and 60 minutes plus a 4-hour soak. Verify sample counts, batch hashes, gaps, duplicate suppression, Wi-Fi/cellular transitions, 30-minute offline recovery, calls and route changes, low-power and low-disk behavior, crash/upgrade recovery, local playback P95 under 2 seconds, energy, thermal state, storage, and upload traffic.

The privacy manifest and microphone/background-audio wording are review inputs, not App Store approval. Legal/privacy review, signing, provisioning, supported-device matrix, Xcode compilation, and physical-device results are mandatory release gates.

This is an isolation skeleton, not an M8 completion claim. The current implementation does not yet rebuild every orphan capture and background session before the WebView loads, submit persisted gaps, call server seal, reconcile the authoritative server checkpoint, consume a verified cleanup receipt, or prove the crash window around an open partial batch. Consequently `ingestComplete` stays `false`, `serverPlaybackState` stays `not_ready`, and local cleanup stays disabled. Those integrations plus Xcode and real-device evidence block enabling the feature flag.
