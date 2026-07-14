import Foundation
import Security

final class MeetingCaptureKeychain {
    private let service = "com.siqresearch.meeting-capture.capture-token"

    private struct Credentials: Codable {
        let token: String
        let deviceInstallationId: String
    }

    func store(token: String, deviceInstallationId: String, captureId: String) throws {
        guard !token.isEmpty, deviceInstallationId.count >= 16, !captureId.isEmpty else {
            throw MeetingCaptureError.invalidArgument("capture credentials")
        }
        let data = try JSONEncoder().encode(Credentials(
            token: token,
            deviceInstallationId: deviceInstallationId
        ))
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: captureId
        ]
        SecItemDelete(query as CFDictionary)
        var item = query
        item[kSecValueData as String] = data
        item[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        guard SecItemAdd(item as CFDictionary, nil) == errSecSuccess else {
            throw MeetingCaptureError.tokenUnavailable
        }
    }

    func credentials(captureId: String) throws -> (token: String, deviceInstallationId: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: captureId,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]
        var result: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
              let data = result as? Data,
              let credentials = try? JSONDecoder().decode(Credentials.self, from: data),
              !credentials.token.isEmpty,
              credentials.deviceInstallationId.count >= 16 else {
            throw MeetingCaptureError.tokenUnavailable
        }
        return (credentials.token, credentials.deviceInstallationId)
    }

    func remove(captureId: String) {
        SecItemDelete([
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: captureId
        ] as CFDictionary)
    }
}
