import AVFoundation
import Foundation

final class MeetingCapturePlaybackController {
    private var handle: String?
    private var localPlayer: AVAudioPlayer?
    private var serverPlayer: AVPlayer?
    private var pendingServerPlayer: AVPlayer?
    private var pendingObservation: NSKeyValueObservation?
    private var source = "none"
    private var fallbackDurationMs: Int64 = 0

    func playLocal(store: MeetingCaptureStore, handle requestedHandle: String) throws -> MeetingPlaybackStatus {
        if handle != requestedHandle || localPlayer == nil {
            let url = try store.playbackURL(for: requestedHandle)
            let player = try AVAudioPlayer(contentsOf: url)
            guard player.prepareToPlay() else { throw MeetingCaptureError.storageUnavailable }
            localPlayer = player
            handle = requestedHandle
            fallbackDurationMs = Int64(player.duration * 1_000)
        }
        serverPlayer?.pause()
        serverPlayer = nil
        source = "local"
        guard localPlayer?.play() == true else { throw MeetingCaptureError.storageUnavailable }
        return status()
    }

    func pause() -> MeetingPlaybackStatus {
        localPlayer?.pause()
        serverPlayer?.pause()
        return status()
    }

    func seek(positionMs: Int64) throws -> MeetingPlaybackStatus {
        guard positionMs >= 0 else { throw MeetingCaptureError.invalidArgument("playback position") }
        let seconds = Double(positionMs) / 1_000
        if source == "server", let serverPlayer {
            serverPlayer.seek(to: CMTime(seconds: seconds, preferredTimescale: 1_000))
        } else if let localPlayer {
            localPlayer.currentTime = min(seconds, localPlayer.duration)
        } else {
            throw MeetingCaptureError.invalidState("playback is not prepared")
        }
        return status()
    }

    func switchToServer(
        store: MeetingCaptureStore,
        handle requestedHandle: String,
        serverURL value: String,
        completion: @escaping (Result<MeetingPlaybackStatus, MeetingCaptureError>) -> Void
    ) {
        do {
            let manifest = try store.currentManifest()
            guard requestedHandle == store.playbackAsset()?.handle else {
                throw MeetingCaptureError.invalidArgument("playback handle")
            }
            let serverURL = try validatedServerPlaybackURL(value, manifest: manifest)
            if handle == nil {
                let localURL = try store.playbackURL(for: requestedHandle)
                localPlayer = try AVAudioPlayer(contentsOf: localURL)
                fallbackDurationMs = Int64((localPlayer?.duration ?? 0) * 1_000)
                handle = requestedHandle
            }
            let shouldResume = isPlaying()
            let item = AVPlayerItem(asset: AVURLAsset(url: serverURL))
            let candidate = AVPlayer(playerItem: item)
            pendingServerPlayer = candidate
            pendingObservation = item.observe(\.status, options: [.initial, .new]) { [weak self] item, _ in
                guard let self else { return }
                switch item.status {
                case .readyToPlay:
                    DispatchQueue.meetingCapture.async {
                        guard self.pendingServerPlayer === candidate else { return }
                        self.pendingObservation = nil
                        self.pendingServerPlayer = nil
                        let switchAtSeconds = self.currentSeconds()
                        candidate.seek(to: CMTime(seconds: switchAtSeconds, preferredTimescale: 1_000)) { _ in
                            DispatchQueue.meetingCapture.async {
                                self.serverPlayer = candidate
                                self.source = "server"
                                self.handle = requestedHandle
                                self.localPlayer?.pause()
                                if shouldResume { candidate.play() }
                                completion(.success(self.status()))
                            }
                        }
                    }
                case .failed:
                    DispatchQueue.meetingCapture.async {
                        guard self.pendingServerPlayer === candidate else { return }
                        self.pendingObservation = nil
                        self.pendingServerPlayer = nil
                        completion(.failure(.transportUnavailable))
                    }
                default:
                    break
                }
            }
        } catch let error as MeetingCaptureError {
            completion(.failure(error))
        } catch {
            completion(.failure(.transportUnavailable))
        }
    }

    func status() -> MeetingPlaybackStatus {
        let durationMs: Int64
        if source == "local", let localPlayer {
            durationMs = Int64(localPlayer.duration * 1_000)
        } else {
            let seconds = serverPlayer?.currentItem?.duration.seconds ?? .nan
            durationMs = seconds.isFinite && seconds >= 0 ? Int64(seconds * 1_000) : fallbackDurationMs
        }
        return MeetingPlaybackStatus(
            handle: handle,
            source: source,
            positionMs: Int64(max(0, currentSeconds()) * 1_000),
            durationMs: max(0, durationMs),
            playing: isPlaying(),
            serverReady: source == "server"
        )
    }

    private func currentSeconds() -> Double {
        if source == "server", let seconds = serverPlayer?.currentTime().seconds, seconds.isFinite {
            return max(0, seconds)
        }
        return max(0, localPlayer?.currentTime ?? 0)
    }

    private func isPlaying() -> Bool {
        source == "server" ? (serverPlayer?.rate ?? 0) != 0 : (localPlayer?.isPlaying ?? false)
    }

    private func validatedServerPlaybackURL(_ value: String, manifest: MeetingCaptureManifest) throws -> URL {
        guard let candidate = URLComponents(string: value),
              let base = URLComponents(string: manifest.apiBaseURL),
              candidate.scheme?.lowercased() == "https",
              candidate.host?.lowercased() == base.host?.lowercased(),
              (candidate.port ?? 443) == (base.port ?? 443),
              candidate.user == nil,
              candidate.password == nil,
              candidate.fragment == nil,
              candidate.percentEncodedPath == "/api/meetings/v1/sessions/\(manifest.meetingId)/audio",
              let query = candidate.queryItems,
              query.count == 1,
              query[0].name == "playback_ticket",
              query[0].value?.isEmpty == false,
              let url = candidate.url else {
            throw MeetingCaptureError.invalidArgument("server playback URL")
        }
        return url
    }
}
