// Render the approved Finder disk-image background using system fonts only.
import AppKit

let output = CommandLine.arguments[1]
let appName = CommandLine.arguments[2]
let image = NSImage(size: NSSize(width: 640, height: 360))
image.lockFocus()
NSColor(calibratedWhite: 0.98, alpha: 1).setFill()
NSRect(x: 0, y: 0, width: 640, height: 360).fill()
func label(_ text: String, y: CGFloat, size: CGFloat, color: NSColor, bold: Bool = false) {
    let paragraph = NSMutableParagraphStyle()
    paragraph.alignment = .center
    let attrs: [NSAttributedString.Key: Any] = [
        .font: bold ? NSFont.systemFont(ofSize: size, weight: .semibold) : NSFont.systemFont(ofSize: size),
        .foregroundColor: color, .paragraphStyle: paragraph
    ]
    (text as NSString).draw(in: NSRect(x: 20, y: y, width: 600, height: 40), withAttributes: attrs)
}
label("Drag \(appName) to Applications", y: 282, size: 23, color: NSColor(calibratedWhite: 0.14, alpha: 1), bold: true)
label("→", y: 163, size: 38, color: .gray)
label("After copying, open \(appName) from Applications.", y: 67, size: 15, color: .darkGray)
label("You can then eject this installer.", y: 43, size: 15, color: .darkGray)
image.unlockFocus()
guard let tiff = image.tiffRepresentation, let bitmap = NSBitmapImageRep(data: tiff),
      let png = bitmap.representation(using: .png, properties: [:]) else { fatalError("Cannot render installer background") }
try png.write(to: URL(fileURLWithPath: output), options: .withoutOverwriting)
