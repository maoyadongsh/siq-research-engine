import Capacitor
import MeetingCapturePlugin
import UIKit

@UIApplicationMain
class AppDelegate: UIResponder, UIApplicationDelegate {
    var window: UIWindow?

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]?
    ) -> Bool {
        if let trustedAPIOrigin = Bundle.main.object(forInfoDictionaryKey: "SIQMeetingAPIOrigin") as? String,
           !trustedAPIOrigin.isEmpty {
            // Restores durable upload sessions only. It never starts or resumes the recorder.
            MeetingCaptureRecoveryCoordinator.shared.bootstrap(trustedAPIOrigin: trustedAPIOrigin)
        }
        return true
    }

    func application(
        _ application: UIApplication,
        handleEventsForBackgroundURLSession identifier: String,
        completionHandler: @escaping () -> Void
    ) {
        MeetingCaptureBackgroundEvents.shared.retainCompletionHandler(
            identifier: identifier,
            handler: completionHandler
        )
    }

    func application(
        _ app: UIApplication,
        open url: URL,
        options: [UIApplication.OpenURLOptionsKey: Any] = [:]
    ) -> Bool {
        ApplicationDelegateProxy.shared.application(app, open: url, options: options)
    }

    func application(
        _ application: UIApplication,
        continue userActivity: NSUserActivity,
        restorationHandler: @escaping ([UIUserActivityRestoring]?) -> Void
    ) -> Bool {
        ApplicationDelegateProxy.shared.application(
            application,
            continue: userActivity,
            restorationHandler: restorationHandler
        )
    }
}
