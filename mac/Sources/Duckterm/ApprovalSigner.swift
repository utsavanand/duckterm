import CryptoKit
import Foundation
import LocalAuthentication
import Security

/// The owner's Approve key. A P-256 key in the Secure Enclave whose every
/// signature needs user presence (Touch ID, or the login password where there
/// is no sensor). The server holds the public key and accepts an approval only
/// with a signature over the exact challenge it issued, so a session holding
/// the install token still cannot approve.
///
/// The key's blob is only usable by this Mac's Secure Enclave and still asks
/// for Touch ID when used, so it can live in a plain file.
///
/// Binding is the security boundary, so it never goes over HTTP, where every
/// session's install token would reach it. The app writes the public key into
/// the server's home once, at launch, when no key is bound yet.
enum ApprovalSigner {
    static let header = "duckterm-approve v1"
    static let fields = ["repo", "github", "pr", "head", "folder", "release", "nonce", "expires"]

    struct Request: Equatable {
        let pr: String
        let github: String
        let head: String
        let folder: String

        /// Shown in the system prompt as "DuckTerm is trying to …". Built
        /// here from the signed payload, never from text the page supplies.
        var reason: String {
            "approve PR #\(pr) in \(github) at \(head.prefix(7)): merge and release"
        }
    }

    enum SignerError: LocalizedError {
        case unavailable, malformed
        var errorDescription: String? {
            switch self {
            case .unavailable: return "This Mac has no Secure Enclave for Touch ID approval"
            case .malformed: return "That is not an approval challenge from this DuckTerm"
            }
        }
    }

    /// Parses a challenge exactly as the server writes it; anything else is refused.
    static func parse(_ payload: String) -> Request? {
        guard payload.utf8.count <= 4096 else { return nil }
        let lines = payload.components(separatedBy: "\n")
        guard lines.count == fields.count + 1, lines[0] == header else { return nil }
        var values: [String: String] = [:]
        for (name, line) in zip(fields, lines.dropFirst()) {
            guard line.hasPrefix(name + "=") else { return nil }
            let value = String(line.dropFirst(name.count + 1))
            guard !value.isEmpty else { return nil }
            values[name] = value
        }
        guard let pr = values["pr"], pr.allSatisfy(\.isNumber),
              let head = values["head"], head.count == 40, head.allSatisfy(\.isHexDigit),
              let github = values["github"], github.split(separator: "/").count == 2,
              let folder = values["folder"] else { return nil }
        return Request(pr: pr, github: github, head: head, folder: folder)
    }

    static var available: Bool { SecureEnclave.isAvailable }

    static var keyURL: URL {
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        return support.appendingPathComponent(AppIdentity.name).appendingPathComponent("approval-key.se")
    }

    private static func loadOrCreate(context: LAContext? = nil) throws -> SecureEnclave.P256.Signing.PrivateKey {
        guard SecureEnclave.isAvailable else { throw SignerError.unavailable }
        if let blob = try? Data(contentsOf: keyURL) {
            return try SecureEnclave.P256.Signing.PrivateKey(dataRepresentation: blob, authenticationContext: context)
        }
        var error: Unmanaged<CFError>?
        guard let access = SecAccessControlCreateWithFlags(
            nil, kSecAttrAccessibleWhenUnlockedThisDeviceOnly, [.privateKeyUsage, .userPresence], &error
        ) else { throw error!.takeRetainedValue() as Error }
        let key = try SecureEnclave.P256.Signing.PrivateKey(accessControl: access, authenticationContext: context)
        try FileManager.default.createDirectory(at: keyURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try key.dataRepresentation.write(to: keyURL, options: [.atomic])
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: keyURL.path)
        return key
    }

    /// The file the server reads the bound key from.
    static func boundKeyURL(environment: [String: String] = ProcessInfo.processInfo.environment) -> URL {
        let home = environment["DUCKTERM_HOME"] ?? (NSHomeDirectory() + "/.duckterm")
        return URL(fileURLWithPath: home).appendingPathComponent("approval-key.pem")
    }

    static func pem(_ der: Data) -> String {
        let body = der.base64EncodedString()
        let lines = stride(from: 0, to: body.count, by: 64).map { start -> String in
            let from = body.index(body.startIndex, offsetBy: start)
            return String(body[from..<(body.index(from, offsetBy: 64, limitedBy: body.endIndex) ?? body.endIndex)])
        }
        return (["-----BEGIN PUBLIC KEY-----"] + lines + ["-----END PUBLIC KEY-----", ""]).joined(separator: "\n")
    }

    /// Writes this Mac's public key as the bound key unless one is already
    /// bound. An existing file is never replaced: rebinding means deleting it
    /// by hand, and a key that isn't this Mac's shows as a mismatch in the
    /// Approve dialog.
    @discardableResult
    static func bindIfUnbound(at file: URL = boundKeyURL()) throws -> Bool {
        guard available else { return false }
        // attributesOfItem does not follow a final symlink, so a planted link
        // counts as bound and is never written through.
        if (try? FileManager.default.attributesOfItem(atPath: file.path)) != nil { return false }
        let der = try loadOrCreate().publicKey.derRepresentation
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        guard FileManager.default.createFile(atPath: file.path, contents: Data(pem(der).utf8),
                                             attributes: [.posixPermissions: 0o600]) else { throw SignerError.unavailable }
        return true
    }

    /// Base64 DER SubjectPublicKeyInfo, created on first use. Reading the
    /// public half never prompts.
    static func publicKey() throws -> String {
        try loadOrCreate().publicKey.derRepresentation.base64EncodedString()
    }

    /// Base64 DER ECDSA signature over the payload. Blocks on the Touch ID
    /// prompt, so it runs off the main thread.
    static func sign(_ payload: String) async throws -> String {
        guard let request = parse(payload) else { throw SignerError.malformed }
        return try await Task.detached(priority: .userInitiated) {
            let context = LAContext()
            context.localizedReason = request.reason
            let key = try loadOrCreate(context: context)
            return try key.signature(for: Data(payload.utf8)).derRepresentation.base64EncodedString()
        }.value
    }
}
