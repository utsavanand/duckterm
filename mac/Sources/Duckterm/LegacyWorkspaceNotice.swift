import AppKit
import Darwin

/// Read-only advice for editions that previously shared Full's workspace.
/// The normal backend still owns every directory/marker creation and refusal.
struct LegacyWorkspaceNotice: Equatable {
    let edition: String

    static func detect(profile: AppProfile?, isTest: Bool,
                       userHome: URL = URL(fileURLWithPath: NSHomeDirectory())) -> Self? {
        guard !isTest, let profile, ["lite", "code"].contains(profile.edition),
              profile.id == profile.edition,
              profile.home.standardizedFileURL == userHome.appendingPathComponent(".duckterm-\(profile.edition)").standardizedFileURL else { return nil }
        // lstat also sees dangling symlinks: an existing path must go through
        // the backend's ownership refusal, not this first-workspace notice.
        var info = stat()
        guard lstat(profile.home.path, &info) != 0, errno == ENOENT,
              let data = legacyMetadata(at: userHome.appendingPathComponent(".duckterm/edition.json")),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              object["id"] as? String == profile.edition else { return nil }
        return Self(edition: profile.edition)
    }

    private static func legacyMetadata(at url: URL) -> Data? {
        // This tiny enum file is read before launch on the UI thread. A FIFO,
        // device, or oversized file must not block the notice or app startup.
        let descriptor = Darwin.open(url.path, O_RDONLY | O_NONBLOCK | O_CLOEXEC)
        guard descriptor >= 0 else { return nil }
        defer { Darwin.close(descriptor) }
        var info = stat()
        guard fstat(descriptor, &info) == 0, info.st_mode & S_IFMT == S_IFREG,
              info.st_size <= 4096 else { return nil }
        let file = FileHandle(fileDescriptor: descriptor, closeOnDealloc: false)
        guard let data = try? file.read(upToCount: 4097), data.count <= 4096 else { return nil }
        return data
    }

    var name: String { "DuckTerm \(edition.capitalized)" }
    var title: String { "Start a separate \(name) workspace?" }
    var home: String { "~/.duckterm-\(edition)" }
    var downloadURL: URL? {
        edition == "lite" ? URL(string: "https://github.com/utsavanand/duckterm/releases/download/v0.4.178/DuckTerm-Lite-macos-arm64.zip") : nil
    }
    var paragraphs: [String] {
        [
            "This version of \(name) keeps its sessions in a new workspace, \(home).",
            "Your earlier \(edition.capitalized) workspace in ~/.duckterm is not moved or changed. Agents still running there keep running.",
            "To go back to that work, open your earlier \(name) app." + (edition == "lite"
                ? " If this download replaced it, download DuckTerm Lite 0.4.178 again and keep it under a different name. That version runs with the DuckTerm command-line tool you already installed." : ""),
        ]
    }
}

/// The modal runs a nested event loop. Reopen events must remain blocked until
/// it returns, and Cancel is final even if another reopen event was queued.
@MainActor
final class WorkspaceLaunchAdmission {
    private enum State { case pending, presenting, allowed, cancelled }
    private var state = State.pending
    var canStart: Bool { state == .allowed }

    func decide(notice: LegacyWorkspaceNotice?, present: (LegacyWorkspaceNotice) -> Bool) -> Bool {
        guard state == .pending else { return canStart }
        state = .presenting
        let accepted = notice.map(present) ?? true
        state = accepted ? .allowed : .cancelled
        return canStart
    }
}

/// A native, square-edged modal matching the approved first-launch notice.
@MainActor
final class LegacyWorkspaceNoticeController: NSObject, NSTextViewDelegate {
    let panel: NSPanel
    private let notice: LegacyWorkspaceNotice
    private let openURL: (URL) -> Void

    init(notice: LegacyWorkspaceNotice, icon: NSImage? = nil,
         openURL: @escaping (URL) -> Void = { NSWorkspace.shared.open($0) }) {
        self.notice = notice
        self.openURL = openURL
        panel = WorkspaceNoticePanel(contentRect: .zero, styleMask: [.borderless], backing: .buffered, defer: false)
        super.init()
        panel.title = notice.title
        panel.level = .modalPanel
        panel.isReleasedWhenClosed = false
        panel.isMovableByWindowBackground = true
        panel.setAccessibilitySubrole(.dialog)
        panel.setAccessibilityLabel(notice.title)
        buildContent(icon: icon ?? NSApp.applicationIconImage)
    }

    func runModal() -> Bool {
        panel.center()
        panel.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        let accepted = NSApp.runModal(for: panel) == .OK
        panel.orderOut(nil)
        return accepted
    }

    @objc private func startWorkspace(_ sender: Any?) { NSApp.stopModal(withCode: .OK) }
    @objc private func cancelWorkspace(_ sender: Any?) { NSApp.stopModal(withCode: .cancel) }

    func textView(_ textView: NSTextView, clickedOnLink link: Any, at charIndex: Int) -> Bool {
        // Help does not accept the notice or end its modal session.
        if let url = link as? URL, url == notice.downloadURL { openURL(url) }
        return true
    }

    private func buildContent(icon: NSImage?) {
        let width: CGFloat = 406
        let inset: CGFloat = 17
        let column = width - inset * 2
        let content = WorkspaceNoticeBackground(frame: .zero)
        let iconView = NSImageView(frame: NSRect(x: (width - 64) / 2, y: 21, width: 64, height: 64))
        iconView.image = icon
        iconView.imageScaling = .scaleProportionallyUpOrDown
        content.addSubview(iconView)

        let title = NSTextField(wrappingLabelWithString: notice.title)
        title.font = .systemFont(ofSize: 13, weight: .bold)
        title.alignment = .center
        title.textColor = WorkspaceNoticeColors.text
        title.frame = NSRect(x: inset, y: 95, width: column, height: 19)
        content.addSubview(title)

        let text = NSTextView(frame: NSRect(x: inset, y: 120, width: column, height: 200))
        text.isEditable = false
        text.isSelectable = true
        text.drawsBackground = false
        text.textContainerInset = .zero
        text.textContainer?.lineFragmentPadding = 0
        text.delegate = self
        let style = NSMutableParagraphStyle()
        style.alignment = .center
        style.minimumLineHeight = 15.4
        style.maximumLineHeight = 15.4
        style.paragraphSpacing = 8
        let body = NSMutableAttributedString(string: notice.paragraphs.joined(separator: "\n"), attributes: [
            .font: NSFont.systemFont(ofSize: 11, weight: .regular), .foregroundColor: WorkspaceNoticeColors.body, .paragraphStyle: style,
        ])
        let plain = body.string as NSString
        for path in [notice.home, "~/.duckterm"] {
            // Match the whole displayed path, not the prefix of the named home.
            let range = plain.range(of: path, options: .backwards)
            if range.location != NSNotFound { body.addAttribute(.font, value: NSFont.monospacedSystemFont(ofSize: 11, weight: .regular), range: range) }
        }
        if let url = notice.downloadURL {
            body.addAttribute(.link, value: url, range: plain.range(of: "download DuckTerm Lite 0.4.178"))
        }
        text.linkTextAttributes = [.foregroundColor: WorkspaceNoticeColors.link, .cursor: NSCursor.pointingHand]
        text.textStorage?.setAttributedString(body)
        text.layoutManager?.ensureLayout(for: text.textContainer!)
        let bodyHeight = ceil(text.layoutManager!.usedRect(for: text.textContainer!).height)
        text.setFrameSize(NSSize(width: column, height: bodyHeight))
        content.addSubview(text)

        let start = WorkspaceNoticeButton(title: "Start a separate workspace", target: self, action: #selector(startWorkspace(_:)))
        start.primary = true
        start.keyEquivalent = "\r"
        start.frame = NSRect(x: inset, y: text.frame.maxY + 14, width: column, height: 28)
        let cancel = WorkspaceNoticeButton(title: "Cancel", target: self, action: #selector(cancelWorkspace(_:)))
        cancel.keyEquivalent = "\u{1b}"
        cancel.frame = NSRect(x: inset, y: start.frame.maxY + 8, width: column, height: 28)
        content.addSubview(start)
        content.addSubview(cancel)
        start.nextKeyView = cancel
        cancel.nextKeyView = text
        text.nextKeyView = start
        panel.initialFirstResponder = start
        panel.defaultButtonCell = start.cell as? NSButtonCell
        content.frame = NSRect(x: 0, y: 0, width: width, height: cancel.frame.maxY + 17)
        panel.setContentSize(content.frame.size)
        panel.contentView = content
    }
}

private final class WorkspaceNoticePanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { true }
}

private enum WorkspaceNoticeColors {
    static let panel = color(light: 0xf6f6f6, dark: 0x2c2c2e)
    static let text = color(light: 0x1d1d1f, dark: 0xf5f5f7)
    static let body = color(light: 0x3c3c43, dark: 0xc7c7cc)
    static let button = color(light: 0xffffff, dark: 0x4a4a4c)
    static let link = color(light: 0x0068da, dark: 0x4fa3ff)

    private static func color(light: UInt32, dark: UInt32) -> NSColor {
        NSColor(name: nil) { appearance in
            let rgb = appearance.bestMatch(from: [.aqua, .darkAqua]) == .darkAqua ? dark : light
            return NSColor(srgbRed: CGFloat((rgb >> 16) & 255) / 255,
                           green: CGFloat((rgb >> 8) & 255) / 255, blue: CGFloat(rgb & 255) / 255, alpha: 1)
        }
    }
}

private final class WorkspaceNoticeBackground: NSView {
    override var isFlipped: Bool { true }
    override func draw(_ dirtyRect: NSRect) {
        WorkspaceNoticeColors.panel.setFill()
        bounds.fill()
        NSColor.separatorColor.setStroke()
        NSBezierPath(rect: bounds.insetBy(dx: 0.5, dy: 0.5)).stroke()
    }
}

private final class WorkspaceNoticeButton: NSButton {
    var primary = false
    override func draw(_ dirtyRect: NSRect) {
        let fill: NSColor = primary ? .systemBlue : WorkspaceNoticeColors.button
        (isHighlighted ? fill.withSystemEffect(.pressed) : fill).setFill()
        bounds.fill()
        if !primary {
            NSColor.separatorColor.setStroke()
            NSBezierPath(rect: bounds.insetBy(dx: 0.5, dy: 0.5)).stroke()
        }
        let label = NSAttributedString(string: title, attributes: [
            .font: NSFont.systemFont(ofSize: 13, weight: .regular), .foregroundColor: primary ? NSColor.white : WorkspaceNoticeColors.text,
        ])
        let size = label.size()
        label.draw(at: NSPoint(x: (bounds.width - size.width) / 2, y: (bounds.height - size.height) / 2))
    }
    override func drawFocusRingMask() { bounds.fill() }
    override var focusRingMaskBounds: NSRect { bounds }
}
