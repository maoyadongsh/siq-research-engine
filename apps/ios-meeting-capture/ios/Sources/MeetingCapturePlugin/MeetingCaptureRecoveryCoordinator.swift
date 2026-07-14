import Foundation

public final class MeetingCaptureRecoveryCoordinator {
    public static let shared = MeetingCaptureRecoveryCoordinator()

    private let keychain = MeetingCaptureKeychain()
    private var trustedOrigin: String?
    private(set) var stores: [String: MeetingCaptureStore] = [:]
    private(set) var uploaders: [String: MeetingCaptureUploader] = [:]
    private(set) var recoveryErrors: [MeetingCaptureError] = []

    private init() {}

    public func bootstrap(trustedAPIOrigin: String) {
        DispatchQueue.meetingCapture.async {
            _ = self.bootstrapNow(trustedAPIOrigin: trustedAPIOrigin)
        }
    }

    @discardableResult
    func bootstrapNow(
        trustedAPIOrigin: String
    ) -> [(store: MeetingCaptureStore, uploader: MeetingCaptureUploader?)] {
        if let trustedOrigin {
            guard trustedOrigin == trustedAPIOrigin else {
                recoveryErrors.append(.invalidArgument("trusted API origin changed"))
                return snapshot()
            }
            return snapshot()
        }
        do {
            let scanner = try MeetingCaptureStore()
            for captureId in try scanner.recoverableCaptureIds() {
                do {
                    let store = try MeetingCaptureStore()
                    let manifest = try store.recover(
                        captureId: captureId,
                        trustedAPIOrigin: trustedAPIOrigin
                    )
                    stores[captureId] = store
                    do {
                        let uploader = try MeetingCaptureUploader(store: store, keychain: keychain)
                        uploaders[captureId] = uploader
                        try uploader.schedulePendingUploads()
                        if manifest.state == .stopped {
                            try? uploader.requestSealWhenSynchronized()
                        }
                    } catch let error as MeetingCaptureError {
                        recoveryErrors.append(error)
                    } catch {
                        recoveryErrors.append(.storageUnavailable)
                    }
                } catch let error as MeetingCaptureError {
                    recoveryErrors.append(error)
                } catch {
                    recoveryErrors.append(.storageUnavailable)
                }
            }
            trustedOrigin = trustedAPIOrigin
        } catch let error as MeetingCaptureError {
            recoveryErrors.append(error)
        } catch {
            recoveryErrors.append(.storageUnavailable)
        }
        return snapshot()
    }

    func register(store: MeetingCaptureStore, uploader: MeetingCaptureUploader) throws {
        let captureId = try store.currentManifest().captureId
        stores[captureId] = store
        uploaders[captureId] = uploader
    }

    private func snapshot() -> [(store: MeetingCaptureStore, uploader: MeetingCaptureUploader?)] {
        stores.values.sorted {
            ((try? $0.currentManifest().updatedAt) ?? .distantPast) >
                ((try? $1.currentManifest().updatedAt) ?? .distantPast)
        }.map { store in
            let captureId = try? store.currentManifest().captureId
            return (store: store, uploader: captureId.flatMap { uploaders[$0] })
        }
    }
}
