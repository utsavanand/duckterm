import AppKit
import CoreImage

/// Unattached views can be rendered for review without creating or ordering a window.
@MainActor
final class DuckyPairingView: NSView {
    enum Action: String { case copy, cancel, approve, deny, restart, done }
    var onAction: ((Action) -> Void)?
    private(set) var preferredFocus: NSView?
    private var countdown: NSTextField?
    private var expiresAt: Int?
    private var pendingExpiry = false
    private let width: CGFloat = 406
    private let inset: CGFloat = 17
    override var isFlipped: Bool { true }

    init(state: DuckyPairingModel.State, busy: Bool = false, icon: NSImage?, now: Date = Date()) {
        super.init(frame: .zero)
        let column = width - inset * 2
        let iconView = NSImageView(frame: NSRect(x: (width - 64) / 2, y: 21, width: 64, height: 64))
        iconView.image = icon; iconView.imageScaling = .scaleProportionallyUpOrDown
        addSubview(iconView)
        var title = "Pair iPhone", body = "", buttons: [(String, Action, Bool)] = []
        var expires: Int?, qr: String?, device: DuckyPairingDevice?
        switch state {
        case .opening:
            body = "Preparing invitation…"; buttons = [("Cancel", .cancel, false)]
        case .invitation(let invitation):
            body = "Scan with your iPhone camera."; qr = invitation.link; expires = invitation.expiresAt
            buttons = [("Copy link", .copy, true), ("Cancel", .cancel, false)]
        case .pending(let pending):
            title = "\(pending.name) wants to view your agents"
            body = "Read-only access to folders and agent status."; device = pending; expires = pending.expiresAt
            buttons = [("Deny", .deny, false), ("Approve", .approve, true)]
        case .approved(let device):
            title = "\(device.name) is paired"; body = "Open Ducky to view your agents."
            buttons = [("Done", .done, true)]
        case .denied:
            title = "Request denied"; body = "This iPhone cannot view your agents."
            buttons = [("New invitation", .restart, true), ("Done", .done, false)]
        case .expired(let pending):
            title = pending ? "Request expired" : "Invitation expired"
            body = pending ? "Create a new invitation to try again." : "Create a new invitation to pair your iPhone."
            buttons = [("New invitation", .restart, true), ("Close", .done, false)]
        case .error(let code):
            title = "Pairing is unavailable"
            switch code {
            case "pairing_busy": body = "Another pairing is already open for this workspace."
            case "approval_unavailable": body = "Couldn’t confirm approval. Try pairing again."
            default: body = "Couldn’t connect to DuckTerm. Try again."
            }
            buttons = [("Try again", .restart, true), ("Close", .done, false)]
        case .closed: break
        }
        var y = addLabel(title, y: 95, font: .systemFont(ofSize: 13, weight: .bold), color: PairingColors.text) + 8
        y = addLabel(body, y: y, font: .systemFont(ofSize: 11), color: PairingColors.body)
        if let qr {
            let image = DuckyQRCodeView(link: qr, frame: NSRect(x: (width - 208) / 2, y: y + 14, width: 208, height: 208))
            addSubview(image); y = image.frame.maxY
        }
        if let device {
            y += 16; addSeparator(y: y); y += 12
            y = addLabel("Match the device ID on your iPhone.", y: y, font: .systemFont(ofSize: 11), color: PairingColors.body) + 7
            y = addLabel(device.id, y: y, font: .monospacedSystemFont(ofSize: 11, weight: .regular), color: PairingColors.text, selectable: true) + 12
            addSeparator(y: y)
        }
        if let expires {
            y += device == nil ? 10 : 16
            let seconds = max(0, expires - Int(now.timeIntervalSince1970))
            let prefix = device == nil ? "Expires in" : "Request expires in"
            y = addLabel("\(prefix) \(seconds / 60):\(String(format: "%02d", seconds % 60))", y: y,
                         font: .monospacedDigitSystemFont(ofSize: 11, weight: .regular), color: PairingColors.body)
            countdown = subviews.last as? NSTextField; expiresAt = expires; pendingExpiry = device != nil
        }
        y += 14
        var controls: [NSButton] = []
        for (index, entry) in buttons.enumerated() {
            let button = PairingButton(title: entry.0, target: self, action: #selector(performAction(_:)))
            button.identifier = NSUserInterfaceItemIdentifier(entry.1.rawValue); button.primary = entry.2
            button.setAccessibilityLabel(entry.0)
            let horizontal = device != nil
            let buttonWidth = horizontal ? (column - 8) / 2 : column
            button.frame = NSRect(x: inset + (horizontal ? CGFloat(index) * (buttonWidth + 8) : 0),
                                  y: y, width: buttonWidth, height: 28)
            if entry.1 == .approve { button.isEnabled = !busy; button.keyEquivalent = "" }
            if entry.1 == .deny || (device == nil && index == 0) { button.keyEquivalent = "\r"; preferredFocus = button }
            if entry.1 == .cancel || entry.1 == .done { button.keyEquivalent = "\u{1b}" }
            addSubview(button); controls.append(button)
            if !horizontal { y += 36 }
        }
        if device != nil { y += 28 } else if !buttons.isEmpty { y -= 8 }
        for (index, button) in controls.enumerated() { button.nextKeyView = controls[(index + 1) % controls.count] }
        frame = NSRect(x: 0, y: 0, width: width, height: y + 17)
        setAccessibilityRole(.group)
    }
    func updateCountdown(now: Date = Date()) {
        guard let expiresAt, let countdown else { return }
        let seconds = max(0, expiresAt - Int(now.timeIntervalSince1970))
        countdown.stringValue = "\(pendingExpiry ? "Request expires in" : "Expires in") \(seconds / 60):\(String(format: "%02d", seconds % 60))"
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    override func draw(_ dirtyRect: NSRect) {
        PairingColors.panel.setFill(); bounds.fill()
        NSColor.separatorColor.setStroke(); NSBezierPath(rect: bounds.insetBy(dx: 0.5, dy: 0.5)).stroke()
    }
    override func cancelOperation(_ sender: Any?) { onAction?(.cancel) }
    @objc private func performAction(_ sender: NSButton) {
        guard let id = sender.identifier?.rawValue, let action = Action(rawValue: id) else { return }
        onAction?(action)
    }
    private func addLabel(_ value: String, y: CGFloat, font: NSFont, color: NSColor, selectable: Bool = false) -> CGFloat {
        let label = NSTextField(wrappingLabelWithString: value)
        label.font = font; label.textColor = color; label.alignment = .center
        label.isSelectable = selectable
        let column = width - inset * 2
        let height = ceil((value as NSString).boundingRect(with: NSSize(width: column, height: 500),
            options: [.usesLineFragmentOrigin, .usesFontLeading], attributes: [.font: font]).height)
        label.frame = NSRect(x: inset, y: y, width: column, height: max(height, 16))
        addSubview(label)
        return label.frame.maxY
    }
    private func addSeparator(y: CGFloat) {
        let line = NSBox(frame: NSRect(x: inset, y: y, width: width - inset * 2, height: 1))
        line.boxType = .separator; addSubview(line)
    }
}

private enum PairingColors {
    static let panel = color(light: 0xf6f6f6, dark: 0x2c2c2e)
    static let text = color(light: 0x1d1d1f, dark: 0xf5f5f7)
    static let body = color(light: 0x3c3c43, dark: 0xc7c7cc)
    static let button = color(light: 0xffffff, dark: 0x4a4a4c)
    private static func color(light: UInt32, dark: UInt32) -> NSColor {
        NSColor(name: nil) { appearance in
            let rgb = appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua ? dark : light
            return NSColor(srgbRed: CGFloat((rgb >> 16) & 255) / 255,
                           green: CGFloat((rgb >> 8) & 255) / 255, blue: CGFloat(rgb & 255) / 255, alpha: 1)
        }
    }
}

private final class PairingButton: NSButton {
    var primary = false
    override func draw(_ dirtyRect: NSRect) {
        let fill: NSColor = primary ? .systemBlue : PairingColors.button
        (isHighlighted ? fill.withSystemEffect(.pressed) : fill).withAlphaComponent(isEnabled ? 1 : 0.5).setFill()
        bounds.fill()
        if !primary { NSColor.separatorColor.setStroke(); NSBezierPath(rect: bounds.insetBy(dx: 0.5, dy: 0.5)).stroke() }
        let label = NSAttributedString(string: title, attributes: [
            .font: NSFont.systemFont(ofSize: 13), .foregroundColor: primary ? NSColor.white : PairingColors.text,
        ])
        let size = label.size()
        label.draw(at: NSPoint(x: (bounds.width - size.width) / 2, y: (bounds.height - size.height) / 2))
    }
    override func drawFocusRingMask() { bounds.fill() }
    override var focusRingMaskBounds: NSRect { bounds }
}

private final class DuckyQRCodeView: NSView {
    private let image: NSImage?
    init(link: String, frame: NSRect) {
        let filter = CIFilter(name: "CIQRCodeGenerator")
        filter?.setValue(Data(link.utf8), forKey: "inputMessage")
        filter?.setValue("M", forKey: "inputCorrectionLevel")
        if let output = filter?.outputImage, let bitmap = CIContext(options: [.useSoftwareRenderer: true]).createCGImage(output, from: output.extent) {
            image = NSImage(cgImage: bitmap, size: NSSize(width: output.extent.width, height: output.extent.height))
        } else { image = nil }
        super.init(frame: frame)
        setAccessibilityRole(.image); setAccessibilityLabel("Ducky invitation QR code")
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    override func draw(_ dirtyRect: NSRect) {
        NSColor.white.setFill(); bounds.fill()
        NSGraphicsContext.current?.imageInterpolation = .none
        image?.draw(in: bounds.insetBy(dx: 16, dy: 16))
    }
}
