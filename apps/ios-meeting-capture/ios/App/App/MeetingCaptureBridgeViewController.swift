import Capacitor
import MeetingCapturePlugin
import WebKit

/// The native capture package is kept in this repository rather than installed
/// as a separate npm plugin. Register it explicitly so `cap sync` cannot omit it
/// from capacitor.config.json's generated package class list.
final class MeetingCaptureBridgeViewController: CAPBridgeViewController {
    override func webViewConfiguration(for instanceConfiguration: InstanceConfiguration) -> WKWebViewConfiguration {
        let configuration = super.webViewConfiguration(for: instanceConfiguration)
        guard let apiOrigin = Self.configuredAPIOrigin() else { return configuration }
        guard
            let encoded = try? JSONSerialization.data(withJSONObject: ["SIQ_API_BASE": apiOrigin]),
            let payload = String(data: encoded, encoding: .utf8)
        else { return configuration }

        let source = """
        Object.defineProperty(globalThis, '__SIQ_NATIVE_CONFIG__', {
          value: Object.freeze(\(payload)),
          writable: false,
          configurable: false,
          enumerable: false
        });
        """
        configuration.userContentController.addUserScript(
            WKUserScript(source: source, injectionTime: .atDocumentStart, forMainFrameOnly: true)
        )
        return configuration
    }

    override func capacitorDidLoad() {
        super.capacitorDidLoad()
        guard bridge?.plugin(withName: "MeetingCapture") == nil else { return }
        bridge?.registerPluginInstance(MeetingCapturePlugin())
    }

    private static func configuredAPIOrigin() -> String? {
        guard
            let rawValue = Bundle.main.object(forInfoDictionaryKey: "SIQMeetingAPIOrigin") as? String
        else { return nil }
        let raw = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !raw.isEmpty, !raw.contains("$("), var components = URLComponents(string: raw) else {
            return nil
        }
        guard
            components.scheme?.lowercased() == "https",
            components.host?.isEmpty == false,
            components.user == nil,
            components.password == nil,
            components.query == nil,
            components.fragment == nil,
            components.path.isEmpty || components.path == "/"
        else { return nil }
        components.scheme = "https"
        components.path = ""
        guard let origin = components.url?.absoluteString else { return nil }
        return origin.hasSuffix("/") ? String(origin.dropLast()) : origin
    }
}
