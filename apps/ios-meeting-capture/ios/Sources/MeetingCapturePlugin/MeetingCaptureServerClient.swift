import Foundation
import WebKit

final class MeetingCaptureServerClient: NSObject, URLSessionTaskDelegate {
    private struct BoundaryPayload: Encodable {
        let expected_epoch: Int
        let final_sequence: Int
        let recorded_through_sample: Int64
        let manifest_revision: Int
        let manifest_sha256: String
        let manifest_entries: [MeetingCaptureCanonicalEntry]

        init(_ boundary: MeetingCaptureBoundary) {
            expected_epoch = boundary.expectedEpoch
            final_sequence = boundary.finalSequence
            recorded_through_sample = boundary.recordedThroughSample
            manifest_revision = boundary.manifestRevision
            manifest_sha256 = boundary.manifestSHA256
            manifest_entries = boundary.entries
        }
    }

    private static let maxResponseBytes = 262_144
    private lazy var session: URLSession = {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.waitsForConnectivity = true
        configuration.httpCookieStorage = .shared
        configuration.httpShouldSetCookies = true
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.urlCache = nil
        return URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
    }()

    func fetchCheckpoint(
        manifest: MeetingCaptureManifest,
        token: String,
        deviceInstallationId: String,
        completion: @escaping (Result<MeetingServerCheckpoint, MeetingCaptureError>) -> Void
    ) {
        do {
            let request = try captureRequest(
                manifest: manifest,
                suffix: "checkpoint",
                method: "GET",
                token: token,
                deviceInstallationId: deviceInstallationId
            )
            perform(request, as: MeetingServerCheckpoint.self, completion: completion)
        } catch let error as MeetingCaptureError {
            completion(.failure(error))
        } catch {
            completion(.failure(.transportUnavailable))
        }
    }

    func seal(
        manifest: MeetingCaptureManifest,
        boundary: MeetingCaptureBoundary,
        token: String,
        deviceInstallationId: String,
        completion: @escaping (Result<MeetingServerSealResponse, MeetingCaptureError>) -> Void
    ) {
        do {
            var request = try captureRequest(
                manifest: manifest,
                suffix: "seal",
                method: "POST",
                token: token,
                deviceInstallationId: deviceInstallationId
            )
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONEncoder().encode(BoundaryPayload(boundary))
            perform(request, as: MeetingServerSealResponse.self, completion: completion)
        } catch let error as MeetingCaptureError {
            completion(.failure(error))
        } catch {
            completion(.failure(.transportUnavailable))
        }
    }

    func rollover(
        manifest: MeetingCaptureManifest,
        pending: MeetingCapturePendingRollover,
        completion: @escaping (Result<MeetingServerRolloverResponse, MeetingCaptureError>) -> Void
    ) {
        synchronizeWebSessionCookies(for: manifest) { [weak self] in
            guard let self else { return }
            do {
                var request = try self.captureRequest(
                    manifest: manifest,
                    suffix: "rollover",
                    method: "POST",
                    token: nil,
                    deviceInstallationId: nil
                )
                request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                request.setValue(pending.idempotencyKey, forHTTPHeaderField: "Idempotency-Key")
                request.setValue(try self.origin(for: manifest.apiBaseURL), forHTTPHeaderField: "Origin")
                request.httpBody = try JSONEncoder().encode(BoundaryPayload(pending.boundary))
                self.perform(request, as: MeetingServerRolloverResponse.self, completion: completion)
            } catch let error as MeetingCaptureError {
                completion(.failure(error))
            } catch {
                completion(.failure(.transportUnavailable))
            }
        }
    }

    private func captureRequest(
        manifest: MeetingCaptureManifest,
        suffix: String,
        method: String,
        token: String?,
        deviceInstallationId: String?
    ) throws -> URLRequest {
        guard var endpoint = URL(string: manifest.apiBaseURL) else {
            throw MeetingCaptureError.transportUnavailable
        }
        for component in [
            "sessions", manifest.meetingId, "native-captures", manifest.captureId, suffix
        ] {
            endpoint.appendPathComponent(component)
        }
        var request = URLRequest(url: endpoint)
        request.httpMethod = method
        request.timeoutInterval = 60
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let token, let deviceInstallationId {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
            request.setValue(deviceInstallationId, forHTTPHeaderField: "X-SIQ-Device-Installation-Id")
        }
        return request
    }

    private func perform<Response: Decodable>(
        _ request: URLRequest,
        as type: Response.Type,
        completion: @escaping (Result<Response, MeetingCaptureError>) -> Void
    ) {
        session.dataTask(with: request) { data, response, error in
            guard error == nil, let response = response as? HTTPURLResponse else {
                completion(.failure(.transportUnavailable))
                return
            }
            guard response.url == request.url else {
                completion(.failure(.serverResponseInvalid))
                return
            }
            switch response.statusCode {
            case 200:
                break
            case 401, 403:
                completion(.failure(.tokenUnavailable))
                return
            case 409:
                completion(.failure(.serverConflict))
                return
            default:
                completion(.failure(.transportUnavailable))
                return
            }
            guard response.value(forHTTPHeaderField: "Content-Type")?.lowercased()
                    .hasPrefix("application/json") == true,
                  let data,
                  data.count <= Self.maxResponseBytes,
                  let decoded = try? JSONDecoder().decode(type, from: data) else {
                completion(.failure(.serverResponseInvalid))
                return
            }
            completion(.success(decoded))
        }.resume()
    }

    private func synchronizeWebSessionCookies(
        for manifest: MeetingCaptureManifest,
        completion: @escaping () -> Void
    ) {
        guard let host = URL(string: manifest.apiBaseURL)?.host?.lowercased() else {
            completion()
            return
        }
        WKWebsiteDataStore.default().httpCookieStore.getAllCookies { cookies in
            for cookie in cookies {
                let domain = cookie.domain.lowercased()
                    .trimmingCharacters(in: CharacterSet(charactersIn: "."))
                guard host == domain || host.hasSuffix(".\(domain)") else { continue }
                HTTPCookieStorage.shared.setCookie(cookie)
            }
            completion()
        }
    }

    private func origin(for value: String) throws -> String {
        guard let components = URLComponents(string: value),
              let scheme = components.scheme?.lowercased(),
              let host = components.host?.lowercased(),
              scheme == "https" else {
            throw MeetingCaptureError.transportUnavailable
        }
        let port = components.port.map { ":\($0)" } ?? ""
        return "\(scheme)://\(host)\(port)"
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
}
