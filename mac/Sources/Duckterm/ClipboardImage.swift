import AppKit
import Darwin
import ImageIO

/// Resolve image representations before accompanying filename text. Called only
/// for terminal targets; ordinary editors keep the standard paste responder.
enum ClipboardImage {
    static let maxBytes = 10_000_000
    static let extensions: Set<String> = ["png", "jpg", "jpeg", "gif", "webp", "tif", "tiff", "heic", "bmp"]

    enum Failure: LocalizedError {
        case unreadable, invalid, tooLarge, changedTarget
        var errorDescription: String? {
            switch self {
            case .unreadable: return "The copied image file could not be read. Copy a local image file rather than an alias or symbolic link."
            case .invalid: return "The clipboard image could not be decoded or converted to PNG."
            case .tooLarge: return "The pasted image must be 10 MB or less and no larger than 60 million pixels."
            case .changedTarget: return "The selected terminal changed. Select the intended terminal and paste again."
            }
        }
    }

    static func png(from pasteboard: NSPasteboard) throws -> Data? {
        // Prefer the original Finder file over any thumbnail representation.
        let urls = pasteboard.readObjects(forClasses: [NSURL.self], options: [.urlReadingFileURLsOnly: true]) as? [URL] ?? []
        for url in urls where extensions.contains(url.pathExtension.lowercased()) {
            return try encode(read(url))
        }
        for type in [NSPasteboard.PasteboardType.png, .tiff] {
            if let data = pasteboard.data(forType: type) { return try encode(data) }
        }
        return nil
    }

    static func read(_ url: URL) throws -> Data {
        guard url.isFileURL, url.host == nil || url.host == "" || url.host == "localhost" else { throw Failure.unreadable }
        let info = try url.resourceValues(forKeys: [.isAliasFileKey, .isSymbolicLinkKey])
        guard info.isAliasFile != true, info.isSymbolicLink != true else { throw Failure.unreadable }
        let descriptor = open(url.path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK)
        guard descriptor >= 0 else { throw Failure.unreadable }
        let handle = FileHandle(fileDescriptor: descriptor, closeOnDealloc: true)
        defer { try? handle.close() }
        var infoAtOpen = stat()
        guard fstat(descriptor, &infoAtOpen) == 0, infoAtOpen.st_mode & S_IFMT == S_IFREG else { throw Failure.unreadable }
        let data = try handle.read(upToCount: maxBytes + 1) ?? Data()
        guard data.count <= maxBytes else { throw Failure.tooLarge }
        return data
    }

    static func encode(_ data: Data) throws -> Data {
        guard data.count <= maxBytes else { throw Failure.tooLarge }
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
              let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let width = properties[kCGImagePropertyPixelWidth] as? NSNumber,
              let height = properties[kCGImagePropertyPixelHeight] as? NSNumber else { throw Failure.invalid }
        guard width.doubleValue > 0, height.doubleValue > 0,
              width.doubleValue * height.doubleValue <= 60_000_000 else { throw Failure.tooLarge }
        guard let image = CGImageSourceCreateImageAtIndex(source, 0, nil),
              let png = NSBitmapImageRep(cgImage: image).representation(using: .png, properties: [:]) else { throw Failure.invalid }
        guard png.count <= maxBytes else { throw Failure.tooLarge }
        return png
    }

    static func save(_ data: Data, in directory: URL) throws -> URL {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let path = directory.appendingPathComponent("paste-\(UUID().uuidString).png")
        try data.write(to: path, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: path.path)
        return path
    }
}
