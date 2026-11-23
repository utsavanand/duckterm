// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "Duckterm",
    platforms: [.macOS(.v13)],
    dependencies: [.package(url: "https://github.com/sparkle-project/Sparkle", exact: "2.10.1")],
    targets: [
        .executableTarget(
            name: "Duckterm",
            dependencies: [.product(name: "Sparkle", package: "Sparkle")],
            path: "Sources/Duckterm"
        ),
        .testTarget(name: "DucktermTests", dependencies: ["Duckterm"], path: "Tests/DucktermTests")
    ]
)
