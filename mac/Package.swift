// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "Duckterm",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "Duckterm",
            path: "Sources/Duckterm"
        )
    ]
)
