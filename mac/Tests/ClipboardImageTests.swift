import AppKit
import Foundation

@main struct ClipboardImageTests {
    static func main() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("clipboard-tests-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 320, pixelsHigh: 160, bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
        NSColor.white.setFill(); NSRect(x: 0, y: 0, width: 320, height: 160).fill()
        NSColor.systemBlue.setFill(); NSBezierPath(ovalIn: NSRect(x: 20, y: 20, width: 100, height: 100)).fill()
        ("K7D4" as NSString).draw(at: NSPoint(x: 145, y: 55), withAttributes: [.font: NSFont.boldSystemFont(ofSize: 32), .foregroundColor: NSColor.black])
        NSGraphicsContext.restoreGraphicsState()
        let png = bitmap.representation(using: .png, properties: [:])!
        let board = NSPasteboard.withUniqueName()
        defer { board.releaseGlobally() }
        board.setString("Screenshot.png", forType: .string)
        board.setData(png, forType: .png)
        let mixed = try ClipboardImage.png(from: board)!
        precondition(NSBitmapImageRep(data: mixed)?.pixelsWide == 320)
        print("PASS: mixed filename text plus image resolves to readable image")
        let source = root.appendingPathComponent("Screenshot with spaces.png")
        try png.write(to: source)
        board.clearContents(); board.writeObjects([source as NSURL]); board.setString(source.lastPathComponent, forType: .string)
        let finder = try ClipboardImage.png(from: board)!
        let pasted = try ClipboardImage.save(finder, in: root.appendingPathComponent("pastes"))
        let savedData = try Data(contentsOf: pasted)
        precondition(NSBitmapImageRep(data: savedData)?.pixelsHigh == 160)
        let mode = try FileManager.default.attributesOfItem(atPath: pasted.path)[.posixPermissions] as! NSNumber
        precondition(mode.intValue == 0o600)
        print("PASS: Finder image file URL preferred over basename; private PNG saved")
        board.clearContents(); board.setData(bitmap.tiffRepresentation!, forType: .tiff)
        let tiffImage = try ClipboardImage.png(from: board)
        precondition(tiffImage != nil)
        board.clearContents(); board.setString("ordinary text", forType: .string)
        let textImage = try ClipboardImage.png(from: board)
        precondition(textImage == nil)
        board.clearContents(); board.setData(Data("broken".utf8), forType: .png)
        do { _ = try ClipboardImage.png(from: board); fatalError("Invalid image accepted") } catch { print("PASS: invalid image reports failure") }
        let link = root.appendingPathComponent("linked.png")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: source)
        do { _ = try ClipboardImage.read(link); fatalError("Symlink accepted") } catch { print("PASS: symlink rejected") }
        do { _ = try ClipboardImage.encode(Data(count: ClipboardImage.maxBytes + 1)); fatalError("Oversized image accepted") } catch { print("PASS: bounded image data") }
        if let destination = ProcessInfo.processInfo.environment["RT_CLIPBOARD_TEST_IMAGE"] {
            try mixed.write(to: URL(fileURLWithPath: destination))
        }
        print("All native clipboard checks passed")
    }
}
