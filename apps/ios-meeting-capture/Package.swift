// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "SIQMeetingCapture",
    platforms: [.iOS(.v15)],
    products: [
        .library(name: "SIQMeetingCapture", targets: ["MeetingCapturePlugin"])
    ],
    dependencies: [
        .package(url: "https://github.com/ionic-team/capacitor-swift-pm.git", from: "8.4.1")
    ],
    targets: [
        .target(
            name: "MeetingCapturePlugin",
            dependencies: [
                .product(name: "Capacitor", package: "capacitor-swift-pm"),
                .product(name: "Cordova", package: "capacitor-swift-pm")
            ],
            path: "ios/Sources/MeetingCapturePlugin"
        ),
        .testTarget(
            name: "MeetingCapturePluginTests",
            dependencies: ["MeetingCapturePlugin"],
            path: "ios/Tests/MeetingCapturePluginTests"
        )
    ]
)
