import Darwin
import Foundation

/// Trust the complete path, including directories traversed by symlinks. A
/// writable ancestor can replace an otherwise safe executable. Symlink modes do
/// not govern replacement, so their owner and containing directory are checked.
struct RuntimeSetupExecutable: Equatable, Sendable {
    let source: URL
    let url: URL
    private let identity: [UInt64]

    static func validate(_ source: URL) throws -> Self {
        guard source.isFileURL, source.path.hasPrefix("/"),
              !source.path.utf8.contains(0) else { throw RuntimeSetupProtocolError.invalidFrame }
        var pending = source.path.split(separator: "/").map(String.init)
        var resolved = "/"
        var links = 0
        try checkDirectory(resolved)
        while !pending.isEmpty {
            let component = pending.removeFirst()
            if component == "." { continue }
            if component == ".." {
                resolved = (resolved as NSString).deletingLastPathComponent
                if resolved.isEmpty { resolved = "/" }
                try checkDirectory(resolved)
                continue
            }
            let path = (resolved as NSString).appendingPathComponent(component)
            var info = stat()
            guard lstat(path, &info) == 0, trustedOwner(info.st_uid) else {
                throw RuntimeSetupProtocolError.invalidFrame
            }
            if info.st_mode & S_IFMT == S_IFLNK {
                links += 1
                guard links <= 40 else { throw RuntimeSetupProtocolError.invalidFrame }
                let target: String
                do { target = try FileManager.default.destinationOfSymbolicLink(atPath: path) }
                catch { throw RuntimeSetupProtocolError.invalidFrame }
                if target.hasPrefix("/") { resolved = "/" }
                pending = target.split(separator: "/").map(String.init) + pending
                continue
            }
            guard trustedPermissions(info.st_mode) else { throw RuntimeSetupProtocolError.invalidFrame }
            if !pending.isEmpty {
                guard info.st_mode & S_IFMT == S_IFDIR else { throw RuntimeSetupProtocolError.invalidFrame }
                resolved = path
                continue
            }
            guard info.st_mode & S_IFMT == S_IFREG, access(path, X_OK) == 0 else {
                throw RuntimeSetupProtocolError.invalidFrame
            }
            return Self(source: source, url: URL(fileURLWithPath: path), identity: [
                UInt64(info.st_dev), UInt64(info.st_ino), UInt64(info.st_size), UInt64(info.st_mode),
                UInt64(info.st_uid), UInt64(info.st_gid),
                UInt64(bitPattern: Int64(info.st_mtimespec.tv_sec)), UInt64(info.st_mtimespec.tv_nsec),
                UInt64(bitPattern: Int64(info.st_ctimespec.tv_sec)), UInt64(info.st_ctimespec.tv_nsec)
            ])
        }
        throw RuntimeSetupProtocolError.invalidFrame
    }

    private static func trustedOwner(_ owner: uid_t) -> Bool { owner == 0 || owner == getuid() }
    private static func trustedPermissions(_ mode: mode_t) -> Bool { mode & 0o022 == 0 }
    private static func checkDirectory(_ path: String) throws {
        var info = stat()
        guard lstat(path, &info) == 0, info.st_mode & S_IFMT == S_IFDIR,
              trustedOwner(info.st_uid), trustedPermissions(info.st_mode) else {
            throw RuntimeSetupProtocolError.invalidFrame
        }
    }
}
