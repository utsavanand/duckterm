import Darwin
import Foundation
import Security

/// Terminal agents outlive the GUI. Their executables must not keep a temporary
/// Gatekeeper mount busy or disappear when the downloaded app is replaced.
/// Retain the complete signed bundle so its resource seal, relative libraries,
/// CLI profile and licenses stay intact. The retained GUI is never launched.
struct StableRuntimeStore: Sendable {
    let home: URL

    struct Failure: LocalizedError {
        let reason: String
        var errorDescription: String? {
            "Could not prepare Duckterm's private runtime: \(reason). Check storage permissions or reinstall the app."
        }
    }

    struct Identity: Equatable {
        let profile: String
        let edition: String
        let bundleID: String
        let codeHash: String
    }

    /// Only packaged apps opt in; source/debug layouts retain their old path.
    func runtime(bundle: URL, resources: URL, bundled: Bool, profile: String?) throws -> BundledRuntime {
        if !bundled {
            let runtime = BundledRuntime(resources: resources)
            try runtime.validate()
            return runtime
        }
        guard let profile else { throw Failure(reason: "the app has no desktop profile") }
        return try prepare(bundle: bundle, profile: profile)
    }

    func prepare(bundle: URL, profile: String) throws -> BundledRuntime {
        let source = bundle.standardizedFileURL
        let identity = try inspect(source)
        guard identity.profile == profile else { throw Failure(reason: "the app profile does not match") }

        // Resolve only the trusted home prefix (for relocated homes or /tmp).
        // Refuse symlinks and foreign/writable ancestors beneath it.
        var directory = home.resolvingSymlinksInPath().standardizedFileURL
        try checkDirectory(directory)
        for component in ["Library", "Application Support", "Duckterm", "Runtime.noindex", "v1", profile] {
            directory.appendPathComponent(component, isDirectory: true)
            if mkdir(directory.path, 0o700) != 0 && errno != EEXIST {
                throw Failure(reason: "cannot create the runtime directory")
            }
            try checkDirectory(directory)
        }
        let lock = open(directory.appendingPathComponent(".lock").path,
                        O_RDWR | O_CREAT | O_NOFOLLOW | O_CLOEXEC | O_NONBLOCK, 0o600)
        guard lock >= 0 else { throw Failure(reason: "cannot open the runtime lock") }
        defer { close(lock) }
        var attributes = stat()
        guard fstat(lock, &attributes) == 0, attributes.st_mode & S_IFMT == S_IFREG,
              attributes.st_uid == getuid(), attributes.st_mode & 0o022 == 0,
              attributes.st_nlink == 1 else { throw Failure(reason: "unsafe runtime lock") }
        guard flock(lock, LOCK_EX) == 0 else { throw Failure(reason: "cannot lock the runtime directory") }
        defer { flock(lock, LOCK_UN) }

        let version = directory.appendingPathComponent(identity.codeHash, isDirectory: true)
        let retained = version.appendingPathComponent("Duckterm Runtime.app", isDirectory: true)
        if exists(version) {
            try checkDirectory(version)
            try validateRetained(retained, identity: identity)
            return BundledRuntime(resources: retained.appendingPathComponent("Contents/Resources"))
        }

        // Publish only a fully copied and validated version. Never overwrite or
        // garbage-collect an old version: a live tmux/logger may still use it.
        let staging = directory.appendingPathComponent(".staging-" + UUID().uuidString, isDirectory: true)
        guard mkdir(staging.path, 0o700) == 0 else { throw Failure(reason: "cannot stage the runtime") }
        defer { try? FileManager.default.removeItem(at: staging) }
        let copied = staging.appendingPathComponent(retained.lastPathComponent, isDirectory: true)
        let copy = Process()
        copy.executableURL = URL(fileURLWithPath: "/usr/bin/ditto")
        copy.arguments = ["--rsrc", "--extattr", "--acl", "--qtn", source.path, copied.path]
        // A fixed system tool; no PATH, agent enrollment, Python environment or
        // owner credentials are needed to copy code. Preserve quarantine.
        copy.environment = ["PATH": "/usr/bin:/bin:/usr/sbin:/sbin"]
        copy.standardOutput = FileHandle.nullDevice
        copy.standardError = FileHandle.nullDevice
        try copy.run()
        copy.waitUntilExit()
        guard copy.terminationReason == .exit, copy.terminationStatus == 0 else {
            throw Failure(reason: "copying the signed runtime failed")
        }
        try validateRetained(copied, identity: identity)
        guard rename(staging.path, version.path) == 0 else {
            throw Failure(reason: "publishing the runtime failed")
        }
        return BundledRuntime(resources: retained.appendingPathComponent("Contents/Resources"))
    }

    private func validateRetained(_ app: URL, identity: Identity) throws {
        try checkTree(app, owned: true)
        guard try inspect(app) == identity else { throw Failure(reason: "the retained runtime identity changed") }
        try BundledRuntime(resources: app.appendingPathComponent("Contents/Resources")).validate()
    }

    private func inspect(_ app: URL) throws -> Identity {
        try checkTree(app, owned: false)
        var code: SecStaticCode?
        guard SecStaticCodeCreateWithPath(app as CFURL, [], &code) == errSecSuccess, let code else {
            throw Failure(reason: "the app is not signed")
        }
        let flags = SecCSFlags(rawValue: kSecCSStrictValidate | kSecCSCheckAllArchitectures | kSecCSCheckNestedCode)
        guard SecStaticCodeCheckValidity(code, flags, nil) == errSecSuccess else {
            throw Failure(reason: "the app signature is invalid")
        }
        var signing: CFDictionary?
        guard SecCodeCopySigningInformation(code, SecCSFlags(rawValue: kSecCSSigningInformation), &signing) == errSecSuccess,
              let information = signing as? [String: Any],
              let hash = information[kSecCodeInfoUnique as String] as? Data, !hash.isEmpty else {
            throw Failure(reason: "the app has no signed identity")
        }
        let data = try Data(contentsOf: app.appendingPathComponent("Contents/Info.plist"))
        guard let info = try PropertyListSerialization.propertyList(from: data, format: nil) as? [String: Any],
              info["DucktermBundledRuntime"] as? Bool == true,
              let profile = info["DucktermProfile"] as? String,
              let edition = info["DucktermEdition"] as? String,
              let bundleID = info["CFBundleIdentifier"] as? String,
              ["full": "full", "lite": "lite", "code": "code",
               "test": "full", "test-lite": "lite", "test-code": "code"][profile] == edition else {
            throw Failure(reason: "the signed app has an invalid desktop profile")
        }
        return Identity(profile: profile, edition: edition, bundleID: bundleID,
                        codeHash: hash.map { String(format: "%02x", $0) }.joined())
    }

    private func exists(_ path: URL) -> Bool {
        var attributes = stat()
        return lstat(path.path, &attributes) == 0
    }

    private func checkDirectory(_ path: URL) throws {
        var attributes = stat()
        guard lstat(path.path, &attributes) == 0, attributes.st_mode & S_IFMT == S_IFDIR,
              attributes.st_uid == getuid(), attributes.st_mode & 0o022 == 0 else {
            throw Failure(reason: "unsafe runtime directory")
        }
    }

    /// Preserve Python's relative symlinks, but reject links to the removable
    /// source (including absolute internal links), special files and escapes.
    private func checkTree(_ root: URL, owned: Bool) throws {
        var rootAttributes = stat()
        guard lstat(root.path, &rootAttributes) == 0, rootAttributes.st_mode & S_IFMT == S_IFDIR else {
            throw Failure(reason: "the runtime bundle is not a directory")
        }
        var failed = false
        guard let entries = FileManager.default.enumerator(at: root, includingPropertiesForKeys: nil,
                                                           errorHandler: { _, _ in failed = true; return false }) else {
            throw Failure(reason: "cannot read the runtime bundle")
        }
        let physicalRoot = root.resolvingSymlinksInPath().path + "/"
        for path in [root] + entries.compactMap({ $0 as? URL }) {
            var attributes = stat()
            guard lstat(path.path, &attributes) == 0 else { throw Failure(reason: "unreadable runtime file") }
            let type = attributes.st_mode & S_IFMT
            guard [S_IFDIR, S_IFREG, S_IFLNK].contains(type) else { throw Failure(reason: "special file in runtime") }
            if type == S_IFLNK {
                let target = try FileManager.default.destinationOfSymbolicLink(atPath: path.path)
                guard !target.hasPrefix("/"), path.resolvingSymlinksInPath().path.hasPrefix(physicalRoot),
                      FileManager.default.fileExists(atPath: path.path) else {
                    throw Failure(reason: "external or broken runtime symlink")
                }
            }
            if owned && (attributes.st_uid != getuid() || (type != S_IFLNK && attributes.st_mode & 0o022 != 0)) {
                throw Failure(reason: "unsafe retained runtime permissions")
            }
        }
        if failed { throw Failure(reason: "cannot enumerate the runtime bundle") }
    }
}
