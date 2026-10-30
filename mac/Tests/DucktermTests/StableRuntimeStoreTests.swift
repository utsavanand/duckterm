import Darwin
import Foundation
import XCTest
@testable import Duckterm

/// Signed disposable fixtures only; no AppKit startup or owner's runtime/home.
final class StableRuntimeStoreTests: XCTestCase {
    private var scratch: URL!
    private var home: URL!
    private var store: StableRuntimeStore { StableRuntimeStore(home: home) }

    override func setUpWithError() throws {
        scratch = FileManager.default.temporaryDirectory.appendingPathComponent("duckterm-runtime-" + UUID().uuidString)
        home = scratch.appendingPathComponent("home")
        try FileManager.default.createDirectory(at: home, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
    }

    override func tearDownWithError() throws { try FileManager.default.removeItem(at: scratch) }

    @discardableResult
    private func run(_ executable: String, _ arguments: [String]) throws -> String {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        process.environment = ["PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": home.path]
        let output = Pipe()
        process.standardOutput = output
        process.standardError = output
        try process.run()
        let result = String(decoding: output.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
        process.waitUntilExit()
        guard process.terminationStatus == 0 else {
            throw NSError(domain: "fixture", code: Int(process.terminationStatus),
                          userInfo: [NSLocalizedDescriptionKey: result])
        }
        return result
    }

    private func write(_ path: URL, _ text: String, executable: Bool = false) throws {
        try FileManager.default.createDirectory(at: path.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data(text.utf8).write(to: path)
        if executable { try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: path.path) }
    }

    private func sign(_ app: URL) throws {
        try run("/usr/bin/codesign", ["--force", "--sign", "-", app.path])
    }

    private func fixture(profile: String = "lite", edition: String = "lite", payload: String = "first") throws -> URL {
        let app = scratch.appendingPathComponent("Download \(UUID().uuidString).app")
        let contents = app.appendingPathComponent("Contents")
        try FileManager.default.createDirectory(at: contents.appendingPathComponent("MacOS"), withIntermediateDirectories: true)
        // A copied system executable is re-signed solely as fixture bundle code;
        // it is never launched. No compiler, developer certificate or keychain.
        try FileManager.default.copyItem(at: URL(fileURLWithPath: "/usr/bin/true"),
                                         to: contents.appendingPathComponent("MacOS/Fixture"))
        let info: [String: Any] = ["CFBundleIdentifier": "com.duckterm.runtime-fixture.\(profile)",
                                   "CFBundleExecutable": "Fixture", "CFBundlePackageType": "APPL",
                                   "CFBundleVersion": "1", "DucktermBundledRuntime": true,
                                   "DucktermEdition": edition, "DucktermProfile": profile]
        try PropertyListSerialization.data(fromPropertyList: info, format: .xml, options: 0)
            .write(to: contents.appendingPathComponent("Info.plist"))
        let resources = contents.appendingPathComponent("Resources")
        for path in ["python/bin/python3.13", "tmux/bin/tmux", "jq/bin/jq", "bin/duckterm"] {
            try write(resources.appendingPathComponent(path), "#!/bin/sh\nprintf '%s\\n' runtime-fixture\n", executable: true)
        }
        try write(resources.appendingPathComponent("python/lib/python3.13/site-packages/duckterm/cli.py"), payload)
        try write(resources.appendingPathComponent("python/lib/python3.13/site-packages/duckterm/dashboard/index.html"), "fixture")
        try write(resources.appendingPathComponent("python/licenses/LICENSE"), "fixture license")
        try FileManager.default.createSymbolicLink(atPath: resources.appendingPathComponent("python/bin/python3").path,
                                                   withDestinationPath: "python3.13")
        try sign(app)
        return app
    }

    private func app(_ runtime: BundledRuntime) -> URL {
        runtime.resources.deletingLastPathComponent().deletingLastPathComponent()
    }

    private func profileDirectory(_ profile: String = "lite") -> URL {
        home.appendingPathComponent("Library/Application Support/Duckterm/Runtime.noindex/v1/" + profile)
    }

    func testSignedCopySurvivesSourceRemovalAndPreservesLinksMetadataAndTools() throws {
        let source = try fixture()
        try run("/usr/bin/xattr", ["-w", "com.duckterm.runtime-test", "metadata", source.path])
        let runtime = try store.prepare(bundle: source, profile: "lite")
        XCTAssertTrue(runtime.resources.path.hasPrefix(home.resolvingSymlinksInPath().path + "/"))
        XCTAssertFalse(runtime.resources.path.hasPrefix(source.path))
        XCTAssertEqual(try run("/usr/bin/xattr", ["-p", "com.duckterm.runtime-test", app(runtime).path]), "metadata\n")
        XCTAssertEqual(try FileManager.default.destinationOfSymbolicLink(atPath:
                        runtime.resources.appendingPathComponent("python/bin/python3").path), "python3.13")
        try FileManager.default.removeItem(at: source)
        try runtime.validate()
        try run("/usr/bin/codesign", ["--verify", "--deep", "--strict", "--all-architectures", app(runtime).path])
        // Only our shell stub executes; the retained native GUI never does.
        XCTAssertEqual(try run(runtime.python.path, []), "runtime-fixture\n")
        let env = runtime.environment(inheriting: ["PATH": "/project/bin", "PYTHONPATH": "/project/modules"])
        for key in ["DUCKTERM_BUNDLED_TMUX", "DUCKTERM_BUNDLED_CLI", "DUCKTERM_BUNDLED_JQ"] {
            XCTAssertTrue(env[key]!.hasPrefix(runtime.resources.path + "/"))
        }
        XCTAssertEqual(env["PYTHONPATH"], "/project/modules")
        XCTAssertFalse(env["PATH"]!.contains("python/bin"))
    }

    func testMovedSourceReusesSameCopyAndNewSignedPayloadKeepsOldVersion() throws {
        let source = try fixture()
        let first = try store.prepare(bundle: source, profile: "lite")
        let moved = scratch.appendingPathComponent("Installed App.app")
        try FileManager.default.moveItem(at: source, to: moved)
        XCTAssertEqual(try store.prepare(bundle: moved, profile: "lite").resources, first.resources)
        // Keep CFBundleVersion identical: identity is the signed content, not a label.
        let changed = moved.appendingPathComponent("Contents/Resources/python/lib/python3.13/site-packages/duckterm/cli.py")
        try write(changed, "second")
        try sign(moved)
        let second = try store.prepare(bundle: moved, profile: "lite")
        XCTAssertNotEqual(second.resources, first.resources)
        XCTAssertEqual(try String(contentsOf: first.resources.appendingPathComponent("python/lib/python3.13/site-packages/duckterm/cli.py")), "first")
        try first.validate()
    }

    func testProfilesStaySeparateAndMismatchFailsBeforeCacheCreation() throws {
        let lite = try fixture()
        XCTAssertThrowsError(try store.prepare(bundle: lite, profile: "code"))
        XCTAssertFalse(FileManager.default.fileExists(atPath: profileDirectory("code").path))
        let first = try store.prepare(bundle: lite, profile: "lite")
        for (profile, edition) in [("code", "code"), ("full", "full"), ("test-lite", "lite")] {
            let source = try fixture(profile: profile, edition: edition)
            let runtime = try store.prepare(bundle: source, profile: profile)
            XCTAssertNotEqual(runtime.resources, first.resources)
            XCTAssertTrue(runtime.resources.path.contains("/v1/\(profile)/"))
        }
    }

    // Only the disposable fixture receives this attribute. Neither the source
    // nor retained app is executed, and no security metadata is removed.
    func testQuarantineIsPreservedWithoutLaunchingTheRetainedApp() throws {
        let source = try fixture()
        let quarantine = "0081;00000001;DucktermFixture;" + UUID().uuidString
        try run("/usr/bin/xattr", ["-w", "com.apple.quarantine", quarantine, source.path])
        let runtime = try store.prepare(bundle: source, profile: "lite")
        // ditto asks macOS to preserve quarantine; the OS can normalize its
        // timestamp/agent fields and add flags. Do not demand byte identity or
        // rewrite the OS result: require retained flags and origin association.
        let copied = try run("/usr/bin/xattr", ["-p", "com.apple.quarantine", app(runtime).path])
            .trimmingCharacters(in: .whitespacesAndNewlines).components(separatedBy: ";")
        XCTAssertEqual(copied.count, 4)
        XCTAssertEqual((UInt16(copied.first ?? "", radix: 16) ?? 0) & 0x81, 0x81)
        XCTAssertEqual(copied.last, quarantine.components(separatedBy: ";").last)
        XCTAssertEqual(try run("/usr/bin/xattr", ["-p", "com.apple.quarantine", source.path]), quarantine + "\n")
    }

    func testConcurrentPreparationPublishesOneCompleteVersion() throws {
        let source = try fixture()
        let prepared = expectation(description: "both callers")
        prepared.expectedFulfillmentCount = 2
        let lock = NSLock()
        var outputs: [URL] = []
        var failures: [Error] = []
        let store = self.store
        for _ in 0..<2 {
            DispatchQueue.global().async {
                do {
                    let runtime = try store.prepare(bundle: source, profile: "lite")
                    lock.lock(); outputs.append(runtime.resources); lock.unlock()
                } catch { lock.lock(); failures.append(error); lock.unlock() }
                prepared.fulfill()
            }
        }
        wait(for: [prepared], timeout: 30)
        XCTAssertTrue(failures.isEmpty, "\(failures)")
        XCTAssertEqual(outputs.count, 2)
        XCTAssertEqual(Set(outputs).count, 1)
        let children = try FileManager.default.contentsOfDirectory(atPath: profileDirectory().path)
        XCTAssertEqual(children.filter { $0 != ".lock" }.count, 1)
    }

    func testModifiedSourceAndModifiedRetainedCodeAreRefused() throws {
        let source = try fixture()
        let runtime = try store.prepare(bundle: source, profile: "lite")
        let path = "python/lib/python3.13/site-packages/duckterm/cli.py"
        try write(runtime.resources.appendingPathComponent(path), "tampered cache")
        XCTAssertThrowsError(try store.prepare(bundle: source, profile: "lite"))
        XCTAssertEqual(try String(contentsOf: runtime.resources.appendingPathComponent(path)), "tampered cache")
        try write(source.appendingPathComponent("Contents/Resources/" + path), "tampered source")
        XCTAssertThrowsError(try store.prepare(bundle: source, profile: "lite"))
    }

    func testSymlinkedHomeWorksButSymlinkedCacheAncestorDoesNot() throws {
        let source = try fixture()
        let alias = scratch.appendingPathComponent("home-alias")
        try FileManager.default.createSymbolicLink(at: alias, withDestinationURL: home)
        let runtime = try StableRuntimeStore(home: alias).prepare(bundle: source, profile: "lite")
        XCTAssertEqual(runtime.resources, try store.prepare(bundle: source, profile: "lite").resources)
        let other = scratch.appendingPathComponent("other-home")
        try FileManager.default.createDirectory(at: other, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        try FileManager.default.createSymbolicLink(at: other.appendingPathComponent("Library"), withDestinationURL: home)
        XCTAssertThrowsError(try StableRuntimeStore(home: other).prepare(bundle: source, profile: "lite"))
    }

    func testWritableParentAndSymlinkLockAreRefused() throws {
        let source = try fixture()
        let directory = profileDirectory()
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        try FileManager.default.setAttributes([.posixPermissions: 0o777], ofItemAtPath: directory.path)
        XCTAssertThrowsError(try store.prepare(bundle: source, profile: "lite"))
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)
        let victim = scratch.appendingPathComponent("untouched")
        try write(victim, "untouched")
        try FileManager.default.createSymbolicLink(at: directory.appendingPathComponent(".lock"), withDestinationURL: victim)
        XCTAssertThrowsError(try store.prepare(bundle: source, profile: "lite"))
        XCTAssertEqual(try String(contentsOf: victim), "untouched")
    }

    func testExternalAndAbsoluteInternalSymlinksAreRejected() throws {
        for external in [true, false] {
            let source = try fixture()
            let link = source.appendingPathComponent("Contents/Resources/unsafe")
            let target = external ? "/usr/bin/true" : source.appendingPathComponent("Contents/MacOS/Fixture").path
            try FileManager.default.createSymbolicLink(atPath: link.path, withDestinationPath: target)
            XCTAssertThrowsError(try store.prepare(bundle: source, profile: "lite")) { error in
                XCTAssertTrue(error.localizedDescription.contains("symlink"))
            }
        }
    }

    func testIncompleteSignedCopyIsNeverPublishedAndStagingIsRemoved() throws {
        let source = try fixture()
        try FileManager.default.removeItem(at: source.appendingPathComponent("Contents/Resources/tmux/bin/tmux"))
        try sign(source)
        XCTAssertThrowsError(try store.prepare(bundle: source, profile: "lite"))
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: profileDirectory().path), [".lock"])
    }

    func testUnbundledDevelopmentLayoutDoesNotCopyOrRequireSigning() throws {
        let source = try fixture()
        let resources = source.appendingPathComponent("Contents/Resources")
        try FileManager.default.removeItem(at: source.appendingPathComponent("Contents/_CodeSignature"))
        let runtime = try store.runtime(bundle: source, resources: resources, bundled: false, profile: nil)
        XCTAssertEqual(runtime.resources, resources)
        XCTAssertFalse(FileManager.default.fileExists(atPath: home.appendingPathComponent("Library").path))
    }
}
