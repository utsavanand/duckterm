// swift-tools-version:5.9
import PackageDescription
let package = Package(name: "DuckyCore", platforms: [.iOS(.v16), .macOS(.v13)],
    products: [.library(name: "DuckyCore", targets: ["DuckyCore"])],
    targets: [.target(name: "DuckyCore"), .testTarget(name: "DuckyCoreTests", dependencies: ["DuckyCore"])])
