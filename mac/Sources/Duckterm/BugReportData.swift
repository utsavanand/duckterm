import Foundation
import Darwin

/// Only fixed diagnostic events enter this buffer. Never feed terminal output,
/// request bodies, environment values, paths, or credentials into it.
final class AppDiagnostics {
    static let shared = AppDiagnostics()
    private let lock = NSLock()
    private var entries: [String] = []

    func record(_ event: String, code: Int? = nil) {
        lock.lock()
        defer { lock.unlock() }
        let suffix = code.map { " (code \($0))" } ?? ""
        entries.append("\(ISO8601DateFormatter().string(from: Date())) \(event)\(suffix)")
        if entries.count > 100 { entries.removeFirst(entries.count - 100) }
    }

    func text() -> String {
        lock.lock()
        defer { lock.unlock() }
        return entries.joined(separator: "\n")
    }
}

enum BugReportError: LocalizedError {
    case invalidFile, tooLarge, tooMany, missingSummary, archiveFailed
    var errorDescription: String? {
        switch self {
        case .invalidFile: return "Choose a regular file, not a folder, alias, or symbolic link."
        case .tooLarge: return "Attachments must total 15 MB or less, with no file larger than 5 MB."
        case .tooMany: return "You can add up to five extra files."
        case .missingSummary: return "Add a short summary before creating the report."
        case .archiveFailed: return "The report could not be saved. Try another location."
        }
    }
}

struct BugAttachment {
    let name: String
    let data: Data
}

struct BugReportData {
    static let maxFileBytes = 5 * 1024 * 1024
    static let maxTotalBytes = 15 * 1024 * 1024
    var attachments: [BugAttachment] = []

    mutating func add(_ url: URL) throws {
        guard attachments.count < 5 else { throw BugReportError.tooMany }
        let info = try url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey, .isAliasFileKey, .fileSizeKey])
        guard info.isRegularFile == true, info.isSymbolicLink != true, info.isAliasFile != true else {
            throw BugReportError.invalidFile
        }
        guard (info.fileSize ?? Int.max) <= Self.maxFileBytes else { throw BugReportError.tooLarge }
        // Never follow a link swapped in after the picker. Bound the read even if
        // the selected file grows after its size check.
        let descriptor = open(url.path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        guard descriptor >= 0 else { throw BugReportError.invalidFile }
        let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        defer { try? handle.close() }
        var opened = stat()
        guard fstat(descriptor, &opened) == 0,
              opened.st_mode & S_IFMT == S_IFREG else { throw BugReportError.invalidFile }
        // Read once on selection: the reviewed attachment cannot later change on disk.
        let data = try handle.read(upToCount: Self.maxFileBytes + 1) ?? Data()
        guard data.count <= Self.maxFileBytes,
              attachments.reduce(data.count, { $0 + $1.data.count }) <= Self.maxTotalBytes else {
            throw BugReportError.tooLarge
        }
        attachments.append(BugAttachment(name: url.lastPathComponent, data: data))
    }

    func write(to directory: URL, summary: String, details: String, replyTo: String,
               screenshot: Data?, diagnostics: String?) throws -> [URL] {
        guard !summary.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            throw BugReportError.missingSummary
        }
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        var files: [URL] = []
        func save(_ name: String, _ data: Data) throws {
            let path = directory.appendingPathComponent(name)
            try data.write(to: path, options: .atomic)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: path.path)
            files.append(path)
        }
        let body = "Summary: \(summary.prefix(200))\n\n\(details.prefix(20000))\n\nReply to: \(replyTo.prefix(254))\n"
        try save("report.txt", Data(body.utf8))
        if let screenshot { try save("screenshot.png", screenshot) }
        if let diagnostics { try save("diagnostics.txt", Data(diagnostics.utf8)) }
        for (index, attachment) in attachments.enumerated() {
            // Prefix makes duplicate names safe; lastPathComponent prevents traversal.
            let name = URL(fileURLWithPath: attachment.name).lastPathComponent
            try save("attachment-\(index + 1)-\(name)", attachment.data)
        }
        return files
    }
}
