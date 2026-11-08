import Darwin
import XCTest
@testable import Duckterm

final class RuntimeSetupExecutableTests: XCTestCase {
    private func fixture() throws -> (URL, URL) {
        let root = try runtimeSetupTestTemporaryDirectory()
            .appendingPathComponent("runtime-executable-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false,
                                               attributes: [.posixPermissions: 0o700])
        addTeardownBlock { try? FileManager.default.removeItem(at: root) }
        let executable = root.appendingPathComponent("agent")
        try Data("#!/bin/sh\nexit 0\n".utf8).write(to: executable)
        XCTAssertEqual(chmod(executable.path, 0o700), 0)
        return (root, executable)
    }

    func testAcceptsSafeUserExecutableAndRootOwnedSystemExecutable() throws {
        let (_, executable) = try fixture()
        XCTAssertEqual(try RuntimeSetupExecutable.validate(executable).url, executable)
        XCTAssertEqual(try RuntimeSetupExecutable.validate(URL(fileURLWithPath: "/bin/sh")).url.path, "/bin/sh")
    }

    func testRejectsWritableExecutableAndEveryWritableAncestor() throws {
        let (root, executable) = try fixture()
        for permissions in [0o720, 0o702, 0o777] {
            XCTAssertEqual(chmod(executable.path, mode_t(permissions)), 0)
            XCTAssertThrowsError(try RuntimeSetupExecutable.validate(executable))
        }
        XCTAssertEqual(chmod(executable.path, 0o700), 0)
        let directory = root.appendingPathComponent("safe-bin")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false,
                                               attributes: [.posixPermissions: 0o700])
        let nested = directory.appendingPathComponent("agent")
        try FileManager.default.moveItem(at: executable, to: nested)
        for permissions in [0o770, 0o707] {
            XCTAssertEqual(chmod(root.path, mode_t(permissions)), 0)
            XCTAssertThrowsError(try RuntimeSetupExecutable.validate(nested))
        }
        XCTAssertEqual(chmod(root.path, 0o700), 0)
        XCTAssertNoThrow(try RuntimeSetupExecutable.validate(nested))
    }

    func testCanonicalizesSafeRelativeAndDirectorySymlinks() throws {
        let (root, executable) = try fixture()
        let link = root.appendingPathComponent("chosen")
        try FileManager.default.createSymbolicLink(atPath: link.path, withDestinationPath: "agent")
        XCTAssertEqual(try RuntimeSetupExecutable.validate(link).url, executable)
        let directoryLink = root.appendingPathComponent("bin")
        try FileManager.default.createSymbolicLink(atPath: directoryLink.path, withDestinationPath: ".")
        XCTAssertEqual(try RuntimeSetupExecutable.validate(directoryLink.appendingPathComponent("chosen")).url, executable)
    }

    func testSymlinksCannotHideUnsafeIntermediateDirectoryOrGlobalTempRoot() throws {
        let (root, _) = try fixture()
        let unsafe = root.appendingPathComponent("writable")
        try FileManager.default.createDirectory(at: unsafe, withIntermediateDirectories: false,
                                               attributes: [.posixPermissions: 0o770])
        let indirect = unsafe.appendingPathComponent("shell")
        try FileManager.default.createSymbolicLink(atPath: indirect.path, withDestinationPath: "/bin/sh")
        let chosen = root.appendingPathComponent("chosen")
        try FileManager.default.createSymbolicLink(atPath: chosen.path, withDestinationPath: "writable/shell")
        XCTAssertEqual(chosen.resolvingSymlinksInPath().path, "/bin/sh")
        XCTAssertThrowsError(try RuntimeSetupExecutable.validate(chosen))
        let globalLink = URL(fileURLWithPath: "/private/tmp/runtime-unsafe-\(UUID().uuidString)")
        try FileManager.default.createSymbolicLink(atPath: globalLink.path, withDestinationPath: "/bin/sh")
        defer { try? FileManager.default.removeItem(at: globalLink) }
        XCTAssertThrowsError(try RuntimeSetupExecutable.validate(globalLink))
    }

    func testRejectsMissingDirectoryNonExecutableAndSymlinkCycle() throws {
        let (root, executable) = try fixture()
        XCTAssertThrowsError(try RuntimeSetupExecutable.validate(root))
        XCTAssertThrowsError(try RuntimeSetupExecutable.validate(root.appendingPathComponent("missing")))
        XCTAssertEqual(chmod(executable.path, 0o600), 0)
        XCTAssertThrowsError(try RuntimeSetupExecutable.validate(executable))
        let loop = root.appendingPathComponent("loop")
        try FileManager.default.createSymbolicLink(atPath: loop.path, withDestinationPath: "loop")
        XCTAssertThrowsError(try RuntimeSetupExecutable.validate(loop))
    }

    func testUnsafeSavedPathNeverBecomesNewSessionDefault() throws {
        let (root, executable) = try fixture()
        let name = "RuntimeExecutableTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: name))
        defer { defaults.removePersistentDomain(forName: name) }
        let preferences = RuntimeSetupPreferences(home: root, defaults: defaults, newWorkspace: false)
        let command = RuntimeSetupPreferences.quote(executable.path)
        _ = try preferences.complete(agent: "codex", command: command, executable: executable)
        XCTAssertEqual(preferences.state()["default_command"] as? String, command)
        XCTAssertEqual(chmod(root.path, 0o770), 0)
        XCTAssertNil(preferences.selectedExecutable(agent: "codex"))
        XCTAssertNil(preferences.state()["default_command"])
        XCTAssertNil(preferences.state()["default_agent"])
        XCTAssertEqual(preferences.state()["completed"] as? Bool, true)
        XCTAssertThrowsError(try preferences.complete(agent: "codex", command: command, executable: executable))
        XCTAssertNil(try preferences.complete()["default_command"])
        XCTAssertNil(defaults.dictionary(forKey: preferences.key)?["default_executable"])
    }

    func testLegacyUntrackedPickedCommandDoesNotBypassSavedPathValidation() throws {
        let (root, executable) = try fixture()
        let name = "RuntimeExecutableTests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: name))
        defer { defaults.removePersistentDomain(forName: name) }
        let preferences = RuntimeSetupPreferences(home: root, defaults: defaults, newWorkspace: false)
        defaults.set(["version": 1, "completed": true, "default_agent": "codex",
                      "default_command": RuntimeSetupPreferences.quote(executable.path)], forKey: preferences.key)
        XCTAssertNil(preferences.state()["default_command"])
        XCTAssertThrowsError(try preferences.complete(agent: "codex", command: RuntimeSetupPreferences.quote(executable.path)))
        XCTAssertEqual(try preferences.complete(agent: "codex", command: "codex")["default_command"] as? String, "codex")
    }
}

/// Ask Darwin for the user's private temp root rather than trusting a test
/// runner's TMPDIR (which may point into globally writable /private/tmp).
func runtimeSetupTestTemporaryDirectory() throws -> URL {
    let size = confstr(_CS_DARWIN_USER_TEMP_DIR, nil, 0)
    guard size > 0 else { throw RuntimeSetupProtocolError.invalidFrame }
    var bytes = [CChar](repeating: 0, count: size)
    guard confstr(_CS_DARWIN_USER_TEMP_DIR, &bytes, size) == size,
          let canonical = realpath(bytes, nil) else { throw RuntimeSetupProtocolError.invalidFrame }
    defer { free(canonical) }
    return URL(fileURLWithPath: String(cString: canonical), isDirectory: true)
}
