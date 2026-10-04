import AppKit

/// Recovery guidance only. Copying a command never runs an installer.
@MainActor
final class LocalSessionSetup: NSObject {
    private(set) var isPresented = false
    private weak var feedback: NSTextField?
    static let installCommand = "brew install tmux"

    func present(initial: TmuxAvailability, recheck: () async -> TmuxAvailability,
                 respond: @MainActor (NSAlert) -> NSApplication.ModalResponse = { $0.runModal() }) async {
        guard !isPresented, initial != .available else { return }
        isPresented = true
        defer { isPresented = false }
        var state = initial
        var checked = false
        while state != .available {
            let alert = makeAlert(state: state, checked: checked)
            guard respond(alert) == .alertFirstButtonReturn else { return }
            state = await recheck()
            checked = true
        }
        let ready = NSAlert()
        ready.messageText = "Local sessions are ready"
        ready.informativeText = "tmux was found. You can now start a local session."
        ready.addButton(withTitle: "Continue")
        _ = respond(ready)
    }

    func makeAlert(state: TmuxAvailability, checked: Bool = false) -> NSAlert {
        let alert = NSAlert()
        alert.messageText = state == .missing ? "Set up local sessions" : "Could not check local sessions"
        alert.informativeText = state == .missing
            ? "DuckTerm needs tmux to run agents on this Mac. Your dashboard and remote sessions are still available.\n\nRun this command in Terminal, then choose Recheck."
            : "The tmux check did not complete successfully. This does not mean tmux is missing. Your dashboard and remote sessions are still available.\n\nChoose Recheck to try again."
        alert.addButton(withTitle: "Recheck")
        alert.addButton(withTitle: "Later")
        alert.buttons[1].keyEquivalent = "\u{1b}"
        let accessory = NSStackView()
        accessory.orientation = .vertical
        accessory.alignment = .leading
        accessory.spacing = 12
        if state == .missing {
            let command = NSTextField(labelWithString: Self.installCommand)
            command.font = .monospacedSystemFont(ofSize: 13, weight: .regular)
            command.isSelectable = true
            let copy = NSButton(title: "Copy", target: self, action: #selector(copyCommand))
            copy.bezelStyle = .rounded
            let row = NSStackView(views: [command, copy])
            row.spacing = 14
            accessory.addArrangedSubview(row)
            let help = NSTextField(wrappingLabelWithString: "If Homebrew isn't installed, set it up first.")
            help.font = .systemFont(ofSize: 12)
            accessory.addArrangedSubview(help)
            let link = NSButton(title: "Homebrew setup ↗", target: self, action: #selector(openHomebrew))
            link.bezelStyle = .inline
            accessory.addArrangedSubview(link)
        }
        let status = NSTextField(wrappingLabelWithString: checked
            ? (state == .missing ? "tmux still wasn't found. Finish installing it, then recheck." : "The check failed again. No sessions were changed.") : "")
        status.font = .systemFont(ofSize: 12)
        status.textColor = .secondaryLabelColor
        accessory.addArrangedSubview(status)
        feedback = status
        accessory.translatesAutoresizingMaskIntoConstraints = false
        accessory.widthAnchor.constraint(equalToConstant: 330).isActive = true
        accessory.layoutSubtreeIfNeeded()
        accessory.frame = NSRect(origin: .zero, size: accessory.fittingSize)
        alert.accessoryView = accessory
        return alert
    }

    @objc private func copyCommand() {
        NSPasteboard.general.clearContents()
        if NSPasteboard.general.setString(Self.installCommand, forType: .string) {
            feedback?.stringValue = "Command copied. Paste it into Terminal."
        } else {
            feedback?.stringValue = "Select the command above and copy it."
        }
    }

    @objc private func openHomebrew() {
        NSWorkspace.shared.open(URL(string: "https://brew.sh/")!)
    }
}
