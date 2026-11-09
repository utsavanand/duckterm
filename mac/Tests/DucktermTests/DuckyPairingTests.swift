import AppKit
import Darwin
import XCTest
@testable import Duckterm

final class DuckyPairingTests: XCTestCase {
    private let deviceID = String(repeating: "a", count: 32)
    private let now = Date(timeIntervalSince1970: 1000)
    private var invite: DuckyPairingInvitation { .init(link: "ducky://pair?invite=PREVIEW_ONLY", expiresAt: 1100) }
    private var device: DuckyPairingDevice { .init(id: deviceID, name: "Test iPhone", expiresAt: 1090) }

    func testFramingAcceptsPartialReadsAndRejectsUnboundedOrMalformedInput() throws {
        let json = #"{"version":1,"state":"invitation","link":"ducky://pair?invite=PREVIEW_ONLY","expires_at":1100}"#
        var frames = DuckyPairingFrames()
        XCTAssertEqual(try frames.append(Data(json.prefix(30).utf8)), [])
        XCTAssertFalse(frames.complete)
        XCTAssertEqual(try frames.append(Data((json.dropFirst(30) + "\n").utf8)), [.invitation(invite)])
        XCTAssertTrue(frames.complete)
        for bad in [json.replacingOccurrences(of: "\"version\":1", with: "\"version\":2"),
                    json.replacingOccurrences(of: "ducky://pair", with: "https://pair"),
                    json.replacingOccurrences(of: "PREVIEW_ONLY", with: "PREVIEW_ONLY&extra=1"),
                    #"{"version":1,"state":"error","code":"secret raw error"}"#,
                    #"{"version":1,"state":"expired","phase":"unknown"}"#] {
            XCTAssertThrowsError(try DuckyPairingEvent.decode(Data(bad.utf8)))
        }
        var oversized = DuckyPairingFrames()
        XCTAssertThrowsError(try oversized.append(Data(repeating: 97, count: 8193)))
        var excessive = DuckyPairingFrames()
        XCTAssertThrowsError(try excessive.append(Data(repeating: 10, count: 65537)))
    }

    func testDeviceNamesCannotInjectControlsOrBidiAndIDsAreExact() throws {
        for name in ["", "Phone\nApprove", "Phone\u{202e}Approve", String(repeating: "a", count: 81)] {
            let data = try JSONSerialization.data(withJSONObject: ["version": 1, "state": "pending",
                "device": ["id": deviceID, "name": name, "expires_at": 1090]])
            XCTAssertThrowsError(try DuckyPairingEvent.decode(data))
        }
        XCTAssertFalse(DuckyPairingDevice(id: deviceID + "\n", name: "Phone", expiresAt: 1090).valid)
        XCTAssertTrue(DuckyPairingDevice(id: deviceID, name: "José’s iPhone 📱", expiresAt: 1090).valid)
    }

    @MainActor func testApprovalRequiresAuthenticationAndMatchingBackendConfirmation() async {
        let transport = FakePairingTransport()
        var authenticate = false, authCalls = 0
        let model = DuckyPairingModel(makeTransport: { transport }, authenticate: { _ in authCalls += 1; return authenticate }, now: { self.now })
        model.start(); transport.onEvent?(.invitation(invite)); transport.onEvent?(.pending(device))
        XCTAssertEqual(transport.commands, [])
        model.approve(); await settle { !model.busy }
        XCTAssertEqual(authCalls, 1); XCTAssertEqual(transport.commands, [])
        authenticate = true
        model.approve(); model.approve()
        await settle { transport.commands.count == 1 }
        XCTAssertEqual(authCalls, 2)
        XCTAssertEqual(transport.commands, ["approve:\(deviceID)"])
        transport.onEvent?(.approved(.init(id: deviceID, name: device.name, expiresAt: nil)))
        XCTAssertEqual(model.state, .approved(.init(id: deviceID, name: device.name, expiresAt: nil)))
        XCTAssertTrue(transport.cancelled, "Terminal success must retire the owned process")
        model.close()
    }

    @MainActor func testUnsolicitedApprovalIsRejectedAndCeremonyCancelled() async {
        let transport = FakePairingTransport()
        let model = DuckyPairingModel(makeTransport: { transport }, authenticate: { _ in true }, now: { self.now })
        model.start(); transport.onEvent?(.invitation(invite)); transport.onEvent?(.pending(device))
        transport.onEvent?(.approved(device))
        XCTAssertEqual(model.state, .error("approval_unavailable")); XCTAssertTrue(transport.cancelled)
        XCTAssertEqual(transport.commands, [])
    }

    @MainActor func testConversationSelectionIsExplicitAndFrozenDuringAuthentication() async {
        let transport = FakePairingTransport(), auth = SuspendedPairingAuthentication()
        var reasons: [String] = []
        let model = DuckyPairingModel(makeTransport: { transport }, authenticate: { reason in
            reasons.append(reason); return await auth.wait()
        }, now: { self.now })
        model.start(); transport.onEvent?(.invitation(invite)); transport.onEvent?(.pending(device))
        XCTAssertFalse(model.allowsConversations)
        model.setAllowsConversations(true); model.approve()
        await settle { auth.continuation != nil }
        model.setAllowsConversations(false)
        XCTAssertTrue(model.allowsConversations, "Scope cannot change underneath authentication")
        auth.finish(true)
        await settle { transport.commands.count == 1 }
        XCTAssertEqual(transport.scopes, [DuckyPairingModel.conversationCapabilities.sorted()])
        XCTAssertEqual(reasons, ["allow Test iPhone to view status and use conversations"])
        let confirmed = DuckyPairingDevice(id: deviceID, name: device.name, expiresAt: nil,
                                          capabilities: DuckyPairingModel.conversationCapabilities.sorted())
        transport.onEvent?(.approved(confirmed))
        XCTAssertEqual(model.state, .approved(confirmed))
        model.start()
        XCTAssertFalse(model.allowsConversations, "Each new ceremony starts status-only")
        model.close()
    }

    @MainActor func testUncheckedApprovalIsStatusOnlyAndRejectsExpandedConfirmation() async {
        let transport = FakePairingTransport()
        let model = DuckyPairingModel(makeTransport: { transport }, authenticate: { _ in true }, now: { self.now })
        model.start(); transport.onEvent?(.invitation(invite)); transport.onEvent?(.pending(device)); model.approve()
        await settle { transport.commands.count == 1 }
        XCTAssertEqual(transport.scopes, [["status:read"]])
        transport.onEvent?(.approved(.init(id: deviceID, name: device.name, expiresAt: nil,
                                          capabilities: DuckyPairingModel.conversationCapabilities.sorted())))
        XCTAssertEqual(model.state, .error("approval_unavailable"))
    }

    @MainActor func testRejectedAuthenticationAndDeviceReplacementCannotApproveConversations() async {
        let transport = FakePairingTransport(), auth = SuspendedPairingAuthentication()
        let model = DuckyPairingModel(makeTransport: { transport }, authenticate: { _ in await auth.wait() }, now: { self.now })
        model.start(); transport.onEvent?(.invitation(invite)); transport.onEvent?(.pending(device))
        model.setAllowsConversations(true); model.approve()
        await settle { auth.continuation != nil }; auth.finish(false)
        await settle { !model.busy }
        XCTAssertTrue(transport.commands.isEmpty)
        model.approve(); await settle { auth.continuation != nil }
        transport.onEvent?(.pending(.init(id: String(repeating: "b", count: 32), name: "Other phone", expiresAt: 1090)))
        auth.finish(true); await Task.yield()
        XCTAssertTrue(transport.commands.isEmpty)
        XCTAssertEqual(model.state, .error("pairing_failed"))
    }

    func testUnknownOrDuplicatedConfirmationScopesAreRejected() throws {
        for scopes in [["status:read", "admin"], ["session:send", "session:send"], ["analysis:folder", "analysis:folder"]] {
            let data = try JSONSerialization.data(withJSONObject: ["version": 1, "state": "approved",
                "device": ["id": deviceID, "name": "Phone", "capabilities": scopes]])
            XCTAssertThrowsError(try DuckyPairingEvent.decode(data))
        }
    }

    @MainActor func testAnalysisDefaultsAndIndependentEditionGates() async throws {
        for (id, features, expected) in [
            ("lite", ["folder_chat", "oracle"], Set<String>()),
            ("code", ["folder_chat", "oracle"], Set(["analysis:folder"])),
            ("full", ["folder_chat", "oracle"], Set(["analysis:folder", "analysis:oracle"])),
            ("full", [], Set<String>())
        ] {
            let edition = try DesktopEdition(response: ["id": id, "features": features])
            let transport = FakePairingTransport()
            let model = DuckyPairingModel(makeTransport: { transport }, authenticate: { _ in true }, now: { self.now }, verifiedEdition: { edition })
            model.start(); transport.onEvent?(.invitation(invite)); transport.onEvent?(.pending(device))
            XCTAssertFalse(model.allowsFolderQuestions); XCTAssertFalse(model.allowsOracleQuestions); XCTAssertFalse(model.allowsConversations)
            model.setAllowsFolderQuestions(true); model.setAllowsOracleQuestions(true); model.approve()
            await settle { transport.commands.count == 1 }
            XCTAssertEqual(transport.scopes, [expected.union(["status:read"]).sorted()])
            model.close()
        }
        let transport = FakePairingTransport()
        let unknown = DuckyPairingModel(makeTransport: { transport }, authenticate: { _ in true }, now: { self.now })
        unknown.start(); transport.onEvent?(.invitation(invite)); transport.onEvent?(.pending(device))
        unknown.setAllowsFolderQuestions(true); unknown.setAllowsOracleQuestions(true)
        unknown.approve(); await settle { transport.commands.count == 1 }
        XCTAssertEqual(transport.scopes, [["status:read"]]); unknown.close()
    }

    @MainActor func testAnalysisSelectionAndExactIdentityAreFrozenAcrossAuthentication() async throws {
        let edition = try DesktopEdition(response: ["id":"full", "features":["folder_chat", "oracle"]])
        let transport = FakePairingTransport(), auth = SuspendedPairingAuthentication()
        var reasons: [String] = []
        let model = DuckyPairingModel(makeTransport: { transport }, authenticate: { reason in reasons.append(reason); return await auth.wait() }, now: { self.now }, verifiedEdition: { edition })
        model.start(); transport.onEvent?(.invitation(invite)); transport.onEvent?(.pending(device))
        model.setAllowsFolderQuestions(true); model.approve(); await settle { auth.continuation != nil }
        model.setAllowsFolderQuestions(false); model.setAllowsOracleQuestions(true); model.setAllowsConversations(true)
        auth.finish(true); await settle { transport.commands.count == 1 }
        XCTAssertEqual(reasons, ["allow Test iPhone to view status and ask folder questions"])
        XCTAssertEqual(transport.scopes, [["analysis:folder", "status:read"]])
        transport.onEvent?(.approved(.init(id: deviceID, name: device.name, expiresAt: nil, capabilities: ["analysis:oracle", "status:read"])))
        XCTAssertEqual(model.state, .error("approval_unavailable"))
    }

    @MainActor func testRejectedAuthenticationOrChangedEditionCannotApproveAnalysis() async throws {
        var edition: DesktopEdition? = try DesktopEdition(response: ["id":"full", "features":["folder_chat", "oracle"]])
        let transport = FakePairingTransport(), auth = SuspendedPairingAuthentication()
        let model = DuckyPairingModel(makeTransport: { transport }, authenticate: { _ in await auth.wait() }, now: { self.now }, verifiedEdition: { edition })
        model.start(); transport.onEvent?(.invitation(invite)); transport.onEvent?(.pending(device))
        model.setAllowsOracleQuestions(true); model.approve(); await settle { auth.continuation != nil }
        auth.finish(false); await settle { !model.busy }; XCTAssertTrue(transport.commands.isEmpty)
        model.approve(); await settle { auth.continuation != nil }; edition = nil
        auth.finish(true); await settle { model.state == .error("approval_unavailable") }
        XCTAssertTrue(transport.commands.isEmpty)
    }

    @MainActor func testRepairPreservesExactExistingScopesAndRequiresNewAuthorizationRevision() async throws {
        let edition = try DesktopEdition(response: ["id":"full", "features":["folder_chat", "oracle"]])
        let transport = FakePairingTransport()
        let model = DuckyPairingModel(makeTransport: { transport }, authenticate: { _ in true }, now: { self.now }, verifiedEdition: { edition })
        let scopes = ["analysis:folder", "conversation:read", "status:read"]
        let repair = DuckyPairingDevice(id: deviceID, name: device.name, expiresAt: 1090, capabilities: scopes, rePair: true, authorizationRevision: 7)
        model.start(); transport.onEvent?(.invitation(invite)); transport.onEvent?(.pending(repair))
        XCTAssertTrue(model.allowsConversations); XCTAssertTrue(model.allowsFolderQuestions); XCTAssertFalse(model.allowsOracleQuestions)
        model.approve(); await settle { transport.commands.count == 1 }
        XCTAssertEqual(transport.scopes, [scopes], "A partial legacy conversation grant must not silently gain sending")
        transport.onEvent?(.approved(.init(id: deviceID, name: device.name, expiresAt: nil, capabilities: scopes, authorizationRevision: 7)))
        XCTAssertEqual(model.state, .error("approval_unavailable"))
        model.start(); transport.onEvent?(.invitation(invite)); transport.onEvent?(.pending(repair)); model.approve()
        await settle { transport.commands.count == 2 }
        let approved = DuckyPairingDevice(id: deviceID, name: device.name, expiresAt: nil, capabilities: scopes, authorizationRevision: 8)
        transport.onEvent?(.approved(approved)); XCTAssertEqual(model.state, .approved(approved))
        model.start(); transport.onEvent?(.invitation(invite))
        transport.onEvent?(.pending(.init(id: deviceID, name: device.name, expiresAt: 1090, capabilities: scopes)))
        XCTAssertFalse(model.allowsConversations); XCTAssertFalse(model.allowsFolderQuestions)
        model.close()
    }

    @MainActor func testApprovedQuestionControlsMatchEditionAndSingleDisclosure() throws {
        for id in ["lite", "code", "full"] {
            let edition = try DesktopEdition(response: ["id":id, "features":["folder_chat", "oracle"]])
            let availability = DuckyPairingAvailability(edition: edition)
            let view = DuckyPairingView(state: .pending(device), availability: availability, icon: nil, now: now)
            let controls = view.subviews.compactMap { $0 as? NSButton }
            let folder = controls.first { $0.identifier?.rawValue == "folderQuestions" }
            let oracle = controls.first { $0.identifier?.rawValue == "oracleQuestions" }
            XCTAssertEqual(folder != nil, id != "lite"); XCTAssertEqual(oracle != nil, id != "lite")
            if id != "lite" { XCTAssertEqual(folder?.state, .off); XCTAssertEqual(oracle?.state, .off); XCTAssertEqual(oracle?.isEnabled, id == "full") }
            XCTAssertFalse(view.subviews.contains { $0.identifier?.rawValue == "analysisDisclosure" })
            let selected = DuckyPairingView(state: .pending(device), allowsFolderQuestions: true, allowsOracleQuestions: true, availability: availability, icon: nil, now: now)
            let labels = selected.subviews.compactMap { $0 as? NSTextField }.filter { $0.identifier?.rawValue == "analysisDisclosure" }
            XCTAssertEqual(labels.count, id == "lite" ? 0 : 1)
            if let disclosure = labels.first { XCTAssertTrue(disclosure.stringValue.contains("recent terminal output")); XCTAssertTrue(disclosure.stringValue.contains("API credits")) }
            for label in selected.subviews.compactMap({ $0 as? NSTextField }) {
                let required = label.cell?.cellSize(forBounds: NSRect(x: 0, y: 0, width: label.frame.width, height: 500)).height ?? 0
                XCTAssertGreaterThanOrEqual(label.frame.height, required, "Full disclosure and captions must fit their native text cells")
            }
            XCTAssertNil(view.window); XCTAssertTrue(selected.subviews.allSatisfy { selected.bounds.contains($0.frame) })
            let busy = DuckyPairingView(state: .pending(device), busy: true, availability: availability, icon: nil, now: now)
            XCTAssertTrue(busy.subviews.compactMap { $0 as? NSButton }.filter { ["folderQuestions", "oracleQuestions"].contains($0.identifier?.rawValue ?? "") }.allSatisfy { !$0.isEnabled })
        }
    }

    @MainActor func testExpiryDuringAuthenticationCannotApprove() async {
        let transport = FakePairingTransport(), auth = SuspendedPairingAuthentication()
        var clock = now
        let model = DuckyPairingModel(makeTransport: { transport }, authenticate: { _ in await auth.wait() }, now: { clock })
        model.start(); transport.onEvent?(.invitation(invite)); transport.onEvent?(.pending(device)); model.approve()
        await settle { auth.continuation != nil }
        clock = Date(timeIntervalSince1970: 1090); model.tick(); auth.finish(true)
        await Task.yield()
        XCTAssertEqual(model.state, .expired(pending: true)); XCTAssertEqual(transport.commands, [])
        XCTAssertTrue(transport.cancelled)
    }

    @MainActor func testDenyWhileAuthenticationPendingWinsAndCloseInvalidatesOldCallbacks() async {
        let transport = FakePairingTransport(), auth = SuspendedPairingAuthentication()
        let model = DuckyPairingModel(makeTransport: { transport }, authenticate: { _ in await auth.wait() }, now: { self.now })
        model.start(); transport.onEvent?(.invitation(invite)); transport.onEvent?(.pending(device)); model.approve()
        await settle { auth.continuation != nil }
        model.deny(); auth.finish(true); await Task.yield()
        XCTAssertEqual(transport.commands, ["deny:\(deviceID)"])
        let oldCallback = transport.onEvent
        model.close(); oldCallback?(.approved(device))
        XCTAssertEqual(model.state, .closed); XCTAssertTrue(transport.cancelled)
    }

    @MainActor func testSentDecisionAwaitsAuthoritativeResultAcrossExpiryButHasDeadline() async {
        let transport = FakePairingTransport()
        var clock = Date(timeIntervalSince1970: 1089)
        let model = DuckyPairingModel(makeTransport: { transport }, authenticate: { _ in true }, now: { clock })
        model.start(); transport.onEvent?(.invitation(invite)); transport.onEvent?(.pending(device)); model.approve()
        await settle { transport.commands.count == 1 }
        clock = Date(timeIntervalSince1970: 1091); model.tick()
        XCTAssertEqual(model.state, .pending(device))
        clock = Date(timeIntervalSince1970: 1095); model.tick()
        XCTAssertEqual(model.state, .error("approval_unavailable")); XCTAssertTrue(transport.cancelled)
    }

    @MainActor func testMissingListenerExpiresAndCancelsStartup() async {
        let transport = FakePairingTransport()
        var clock = now
        let model = DuckyPairingModel(makeTransport: { transport }, authenticate: { _ in true }, now: { clock })
        model.start(); clock = Date(timeIntervalSince1970: 1046); model.tick()
        XCTAssertEqual(model.state, .error("listener_unavailable")); XCTAssertTrue(transport.cancelled)
    }

    @MainActor func testPendingViewUsesDenyAsDefaultAndCountdownPreservesControls() async {
        let view = DuckyPairingView(state: .pending(device), icon: nil, now: now)
        let controls = view.subviews.compactMap { $0 as? NSButton }
        let deny = controls.first { $0.identifier?.rawValue == "deny" }
        let approve = controls.first { $0.identifier?.rawValue == "approve" }
        XCTAssertEqual(deny?.keyEquivalent, "\r"); XCTAssertEqual(approve?.keyEquivalent, "")
        XCTAssertTrue(view.preferredFocus === deny)
        view.updateCountdown(now: Date(timeIntervalSince1970: 1001))
        XCTAssertTrue(view.subviews.contains { $0 === deny }); XCTAssertTrue(view.subviews.contains { $0 === approve })
        XCTAssertEqual(view.frame.width, 406)
        XCTAssertNil(view.window, "View inspection must never create a window")
        let busy = DuckyPairingView(state: .pending(device), busy: true, icon: nil, now: now)
        XCTAssertFalse((busy.subviews.first { $0.identifier?.rawValue == "approve" } as? NSButton)?.isEnabled ?? true)
        let checkbox = controls.first { $0.identifier?.rawValue == "conversations" }
        XCTAssertEqual(checkbox?.state, .off)
        XCTAssertEqual(checkbox?.title, "Allow conversations with agents")
        XCTAssertFalse((busy.subviews.first { $0.identifier?.rawValue == "conversations" } as? NSButton)?.isEnabled ?? true)
        let checked = DuckyPairingView(state: .pending(device), allowsConversations: true, icon: nil, now: now)
        XCTAssertEqual((checked.subviews.first { $0.identifier?.rawValue == "conversations" } as? NSButton)?.state, .on)
        var changed = false
        checked.onConversationsChanged = { changed = $0 }
        let checkedControl = checked.subviews.first { $0.identifier?.rawValue == "conversations" } as? NSButton
        checkedControl?.state = .off
        if let control = checkedControl, let action = control.action { checked.perform(action, with: control) }
        XCTAssertFalse(changed)
        checkedControl?.state = .on
        if let control = checkedControl, let action = control.action { checked.perform(action, with: control) }
        XCTAssertTrue(changed)
        XCTAssertTrue(view.subviews.allSatisfy { view.bounds.contains($0.frame) })
    }

    @MainActor func testEnvironmentDoesNotInheritSessionCredentialsOrForeignProfiles() async {
        let root = URL(fileURLWithPath: "/private/tmp/pairing-env-fixture")
        let profile = AppProfile(id: "test-lite", edition: "lite", instance: "test-lite", home: root,
                                 tmuxSocket: "test-lite", port: 12345)
        let inherited = ["HOME": "/owner", "PATH": "/evil", "PYTHONPATH": "/evil", "DYLD_INSERT_LIBRARIES": "/evil",
            "DUCKTERM_OWNER_TOKEN": "secret", "DUCKTERM_SESSION_TOKEN": "secret", "DUCKTERM_HOME": "/wrong",
            "OPENAI_API_KEY": "secret", "CODEX_HOME": "/wrong", "LANG": "en_US.UTF-8"]
        let env = DuckyPairingProcess.environment(inheriting: inherited, home: root, profile: profile)
        XCTAssertEqual(env["HOME"], root.path); XCTAssertEqual(env["DUCKTERM_HOME"], root.path)
        XCTAssertEqual(env["DUCKTERM_NO_KEYCHAIN"], "1"); XCTAssertEqual(env["DUCKTERM_SUMMARIZER"], "off")
        for key in ["PYTHONPATH", "DYLD_INSERT_LIBRARIES", "DUCKTERM_OWNER_TOKEN", "DUCKTERM_SESSION_TOKEN", "OPENAI_API_KEY", "CODEX_HOME"] {
            XCTAssertNil(env[key], key)
        }
        XCTAssertFalse(env["PATH"]?.contains("/evil") ?? true)
    }

    @MainActor func testOwnedSubprocessReceivesCancelAndCleansItsFixture() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("pairing-native-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let marker = root.appendingPathComponent("cleanup")
        let pidFile = root.appendingPathComponent("child.pid")
        let script = #"""
        printf '%s' "$$" > "$2"
        printf '%s\n' '{"version":1,"state":"invitation","link":"ducky://pair?invite=PREVIEW_ONLY","expires_at":1100}'
        IFS= read -r command
        printf '%s' "$command" > "$1"
        printf '%s\n' '{"version":1,"state":"cancelled"}'
        """#
        let transport = DuckyPairingProcess(command: {
            .init(executable: URL(fileURLWithPath: "/bin/sh"), arguments: ["-c", script, "pairing-test", marker.path, pidFile.path],
                  environment: ["PATH": "/usr/bin:/bin", "HOME": root.path])
        })
        var events: [DuckyPairingEvent] = []
        transport.onEvent = { events.append($0) }
        try await transport.start()
        await settle { events == [.invitation(self.invite)] }
        transport.cancel()
        await settle { FileManager.default.fileExists(atPath: marker.path) }
        let data = try Data(contentsOf: marker)
        let command = try JSONSerialization.jsonObject(with: data) as? [String: String]
        XCTAssertEqual(command, ["action": "cancel"])
        let pid = try XCTUnwrap(Int32(String(contentsOf: pidFile, encoding: .utf8)))
        await settle { kill(pid, 0) == -1 && errno == ESRCH }
    }

    @MainActor func testOwnedSubprocessGetsOnlyExplicitApprovedScopes() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("pairing-scopes-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let marker = root.appendingPathComponent("command.json"), pidFile = root.appendingPathComponent("child.pid")
        let script = #"""
        printf '%s' "$$" > "$2"
        printf '%s\n' '{"version":1,"state":"invitation","link":"ducky://pair?invite=PREVIEW_ONLY","expires_at":1100}'
        IFS= read -r command
        printf '%s' "$command" > "$1"
        printf '%s\n' '{"version":1,"state":"cancelled"}'
        """#
        let transport = DuckyPairingProcess(command: {
            .init(executable: URL(fileURLWithPath: "/bin/sh"), arguments: ["-c", script, "pairing-test", marker.path, pidFile.path],
                  environment: ["PATH": "/usr/bin:/bin", "HOME": root.path])
        })
        var events: [DuckyPairingEvent] = []
        transport.onEvent = { events.append($0) }
        try await transport.start(); await settle { events == [.invitation(self.invite)] }
        XCTAssertThrowsError(try transport.send(action: "approve", deviceID: deviceID, capabilities: ["admin"]))
        XCTAssertThrowsError(try transport.send(action: "deny", deviceID: deviceID, capabilities: ["session:send"]))
        try transport.send(action: "approve", deviceID: deviceID, capabilities: ["analysis:folder", "analysis:oracle", "status:read"])
        await settle { events.last == .cancelled }
        let body = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: marker)) as? [String: Any])
        XCTAssertEqual(Set(body.keys), ["action", "device_id", "capabilities"])
        XCTAssertEqual(body["action"] as? String, "approve")
        XCTAssertEqual(body["device_id"] as? String, deviceID)
        XCTAssertEqual(body["capabilities"] as? [String], ["analysis:folder", "analysis:oracle", "status:read"])
        let pid = try XCTUnwrap(Int32(String(contentsOf: pidFile, encoding: .utf8)))
        await settle { kill(pid, 0) == -1 && errno == ESRCH }
    }

    @MainActor func testTerminalFrameReapsAChildThatNeverExits() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("pairing-reaper-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let pidFile = root.appendingPathComponent("child.pid")
        let script = #"""
        printf '%s' "$$" > "$1"
        printf '%s\n' '{"version":1,"state":"invitation","link":"ducky://pair?invite=PREVIEW_ONLY","expires_at":1100}'
        IFS= read -r command
        printf '%s\n' '{"version":1,"state":"cancelled"}'
        trap '' TERM
        exec /bin/sleep 30
        """#
        let transport = DuckyPairingProcess(command: {
            .init(executable: URL(fileURLWithPath: "/bin/sh"), arguments: ["-c", script, "pairing-test", pidFile.path],
                  environment: ["PATH": "/usr/bin:/bin", "HOME": root.path])
        })
        var events: [DuckyPairingEvent] = []
        transport.onEvent = { events.append($0) }
        try await transport.start()
        await settle { events == [.invitation(self.invite)] }
        try transport.send(action: "cancel", deviceID: nil)
        await settle { events.last == .cancelled }
        let pid = try XCTUnwrap(Int32(String(contentsOf: pidFile, encoding: .utf8)))
        await settle({ kill(pid, 0) == -1 && errno == ESRCH }, attempts: 1800)
    }

    @MainActor func testStartupCancellationAllowsPreparationToFinishThenObserveEOF() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("pairing-startup-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let marker = root.appendingPathComponent("cleanup"), pidFile = root.appendingPathComponent("child.pid")
        let script = #"""
        printf '%s' "$$" > "$2"
        /bin/sleep 3.2
        /bin/cat > /dev/null
        printf 'cleaned' > "$1"
        """#
        let transport = DuckyPairingProcess(command: {
            .init(executable: URL(fileURLWithPath: "/bin/sh"), arguments: ["-c", script, "pairing-test", marker.path, pidFile.path],
                  environment: ["PATH": "/usr/bin:/bin", "HOME": root.path])
        })
        try await transport.start(); transport.cancel()
        await settle({ FileManager.default.fileExists(atPath: marker.path) }, attempts: 1200)
        XCTAssertEqual(try String(contentsOf: marker, encoding: .utf8), "cleaned")
        let pid = try XCTUnwrap(Int32(String(contentsOf: pidFile, encoding: .utf8)))
        await settle { kill(pid, 0) == -1 && errno == ESRCH }
    }

    @MainActor private func settle(_ condition: () -> Bool, attempts: Int = 200, file: StaticString = #filePath, line: UInt = #line) async {
        for _ in 0..<attempts {
            if condition() { return }
            try? await Task.sleep(nanoseconds: 5_000_000)
        }
        XCTFail("Timed out awaiting fixture state", file: file, line: line)
    }
}

@MainActor private final class FakePairingTransport: DuckyPairingTransport {
    var onEvent: ((DuckyPairingEvent) -> Void)?
    var commands: [String] = []
    var cancelled = false
    var scopes: [[String]?] = []
    func start() async throws {}
    func send(action: String, deviceID: String?, capabilities: [String]?) throws {
        commands.append("\(action):\(deviceID ?? "")"); scopes.append(capabilities)
    }
    func cancel() { cancelled = true }
}

@MainActor private final class SuspendedPairingAuthentication {
    var continuation: CheckedContinuation<Bool, Never>?
    func wait() async -> Bool { await withCheckedContinuation { continuation = $0 } }
    func finish(_ result: Bool) { continuation?.resume(returning: result); continuation = nil }
}
