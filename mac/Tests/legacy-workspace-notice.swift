import AppKit

/// Compile alongside AppIdentity.swift and LegacyWorkspaceNotice.swift, then
/// run with <AppIcon.icns> <screenshot-directory>. No backend or owner data is
/// used: the production controller receives a fixture notice and URL opener.
@main
struct LegacyWorkspaceNoticeUITests {
    @MainActor
    static func check(_ condition: Bool, _ message: String, line: UInt = #line) {
        precondition(condition, message, line: line)
    }

    @MainActor
    static func snapshot(_ panel: NSPanel, to url: URL) throws {
        let view = panel.contentView!
        view.displayIfNeeded()
        let size = view.bounds.size
        let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size.width * 2),
            pixelsHigh: Int(size.height * 2), bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
            isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
        bitmap.size = size
        view.cacheDisplay(in: view.bounds, to: bitmap)
        try bitmap.representation(using: .png, properties: [:])!.write(to: url)
    }

    @MainActor
    static func run(icon: NSImage, output: URL) {
        for edition in ["lite", "code"] {
            for dark in [false, true] {
                let notice = LegacyWorkspaceNotice(edition: edition)
                var opened: [URL] = []
                let controller = LegacyWorkspaceNoticeController(notice: notice, icon: icon, openURL: { opened.append($0) })
                let panel = controller.panel
                panel.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
                let admission = WorkspaceLaunchAdmission()
                var exercised = false
                let exercise = Timer(timeInterval: 0.15, repeats: false) { _ in
                    MainActor.assumeIsolated {
                        check(panel.isVisible && NSApp.modalWindow === panel, "Notice must be an active native modal")
                        check(!admission.canStart, "Startup is blocked while the notice is visible")
                        check(panel.styleMask == .borderless, "Panel must have square corners")
                        let content = panel.contentView!
                        check(content.bounds.width == 406, "Keep the approved real-size width")
                        if edition == "lite" { check(content.bounds.height == 355, "Keep the approved Lite panel height") }
                        let text = content.subviews.compactMap { $0 as? NSTextView }.first!
                        check(text.string == notice.paragraphs.joined(separator: "\n"), "Visible copy must match the reviewed copy")
                        let buttons = content.subviews.compactMap { $0 as? NSButton }
                        check(buttons.map(\.title) == ["Start a separate workspace", "Cancel"], "No migration or destructive action")
                        check(buttons.allSatisfy { content.bounds.contains($0.frame) }, "Buttons must fit the panel")
                        let used = text.layoutManager!.usedRect(for: text.textContainer!)
                        check(used.height <= text.bounds.height, "All copy must fit without scrolling or clipping")
                        if let url = notice.downloadURL {
                            check(controller.textView(text, clickedOnLink: url, at: 0), "Help is handled by the native controller")
                            check(opened == [url], "Only the approved Lite download opens")
                            check(NSApp.modalWindow === panel && !admission.canStart, "Help leaves the choice pending")
                            _ = controller.textView(text, clickedOnLink: URL(string: "https://example.invalid/")!, at: 0)
                            check(opened == [url], "Unexpected links must not open")
                        } else {
                            text.textStorage!.enumerateAttribute(.link, in: NSRange(location: 0, length: text.textStorage!.length)) { link, _, _ in
                                check(link == nil, "Code has no public download link")
                            }
                        }
                        do { try snapshot(panel, to: output.appendingPathComponent("legacy-notice-\(edition)-\(dark ? "dark" : "light").png")) }
                        catch { fatalError("Could not save real native rendering: \(error)") }
                        exercised = true
                        // Exercise real AppKit key dispatch, not the controller's
                        // private action: Escape cancels, Return accepts.
                        let key = dark ? "\r" : "\u{1b}"
                        let event = NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: [], timestamp: 0,
                            windowNumber: panel.windowNumber, context: nil, characters: key, charactersIgnoringModifiers: key,
                            isARepeat: false, keyCode: dark ? 36 : 53)!
                        check(panel.performKeyEquivalent(with: event), "Return and Escape must work in the borderless native modal")
                    }
                }
                RunLoop.main.add(exercise, forMode: .modalPanel)
                let accepted = admission.decide(notice: notice) { _ in controller.runModal() }
                check(exercised, "Native modal test must actually execute")
                check(accepted == dark && admission.canStart == dark, "Only Start accepts startup")
                check(!panel.isVisible && NSApp.modalWindow == nil, "Choice must dismiss the modal")
                check(admission.decide(notice: notice) { _ in fatalError("Decision cannot be reopened") } == dark,
                      "A reopen event cannot change the decision")
                print("PASS: \(edition) \(dark ? "dark / Return" : "light / Escape"), \(panel.frame.width)x\(panel.frame.height) pt, square panel/buttons, complete copy, modal help and admission")
            }
        }
    }

    static func main() {
        MainActor.assumeIsolated {
            check(CommandLine.arguments.count == 3, "Expected icon and screenshot directory arguments")
            let icon = NSImage(contentsOfFile: CommandLine.arguments[1])!
            let output = URL(fileURLWithPath: CommandLine.arguments[2], isDirectory: true)
            try! FileManager.default.createDirectory(at: output, withIntermediateDirectories: true)
            let app = NSApplication.shared
            app.setActivationPolicy(.accessory)
            DispatchQueue.main.async {
                run(icon: icon, output: output)
                exit(0)
            }
            DispatchQueue.global().asyncAfter(deadline: .now() + 15) {
                fputs("FAIL: native notice modal did not finish in 15 seconds\n", stderr)
                exit(1)
            }
            app.run()
        }
    }
}
