import AppKit
import XCTest
@testable import Duckterm

final class DuckyAccountTests: XCTestCase {
    private let deviceID = String(repeating: "b", count: 32)
    private let now = Date(timeIntervalSince1970: 1000)
    private var claim: DuckyPairingDevice {
        .init(id: deviceID, name: "Utsav’s iPhone", expiresAt: 1500, login: "octo", code: "4821")
    }

    func testAccountFramesAcceptOnlyGitHubsDevicePageAndExactCodes() throws {
        func decode(_ object: [String: Any]) throws -> DuckyPairingEvent {
            try DuckyPairingEvent.decode(JSONSerialization.data(withJSONObject: ["version": 1].merging(object) { $1 }))
        }
        XCTAssertEqual(try decode(["state": "code", "user_code": "WDJB-MJHT", "verification_uri": "https://github.com/login/device"]),
                       .signInCode("WDJB-MJHT", URL(string: "https://github.com/login/device")!))
        XCTAssertEqual(try decode(["state": "signed_in", "login": "octo-cat"]), .signedIn("octo-cat"))
        let device: [String: Any] = ["id": deviceID, "name": "Utsav’s iPhone", "expires_at": 1500, "login": "octo", "code": "4821"]
        XCTAssertEqual(try decode(["state": "claims", "login": "octo", "devices": [device]]), .claims(login: "octo", devices: [claim]))
        XCTAssertEqual(try decode(["state": "claims", "login": NSNull(), "devices": []]), .claims(login: nil, devices: []))
        for bad: [String: Any] in [
            ["state": "code", "user_code": "WDJB-MJHT", "verification_uri": "https://evil.example/login/device"],
            ["state": "code", "user_code": "WDJB-MJHT", "verification_uri": "https://github.com/login/device?next=x"],
            ["state": "code", "user_code": "wdjb mjht", "verification_uri": "https://github.com/login/device"],
            ["state": "signed_in", "login": "-octo"],
            ["state": "signed_in", "login": "octo\nApprove"],
            ["state": "claims", "login": "octo", "devices": [device.merging(["code": "48210"]) { $1 }]],
            ["state": "claims", "login": "octo", "devices": [device.merging(["code": NSNull()]) { $1 }]],
            ["state": "claims", "login": "octo", "devices": Array(repeating: device, count: 5)],
            ["state": "error", "code": "Sign-in service refused the request (secret)"],
        ] {
            XCTAssertThrowsError(try decode(bad), "\(bad)")
        }
    }

    @MainActor func testClaimWindowOffersTheWaitingPhoneWithoutAnInvitation() async {
        let transport = FakeAccountTransport()
        let model = DuckyPairingModel(makeTransport: { transport }, authenticate: { _ in true }, now: { self.now },
                                      expectsInvitation: false)
        model.start(); transport.onEvent?(.pending(claim))
        XCTAssertEqual(model.state, .pending(claim))
        model.approve()
        await settle { transport.commands == ["approve:\(self.deviceID)"] }
        transport.onEvent?(.approved(.init(id: deviceID, name: claim.name, expiresAt: nil, capabilities: ["status:read"])))
        XCTAssertEqual(model.state, .approved(.init(id: deviceID, name: claim.name, expiresAt: nil, capabilities: ["status:read"])))

        for event: DuckyPairingEvent in [
            .invitation(.init(link: "ducky://pair?invite=PREVIEW_ONLY", expiresAt: 1100)),
            .pending(.init(id: deviceID, name: "QR phone", expiresAt: 1500)),
            .pending(.init(id: deviceID, name: "Late phone", expiresAt: 9999, login: "octo", code: "4821")),
        ] {
            let other = FakeAccountTransport()
            let rejecting = DuckyPairingModel(makeTransport: { other }, authenticate: { _ in true }, now: { self.now },
                                              expectsInvitation: false)
            rejecting.start(); other.onEvent?(event)
            XCTAssertEqual(rejecting.state, .error("pairing_failed"), "\(event)")
            XCTAssertTrue(other.cancelled)
        }
    }

    @MainActor func testSignInOpensGitHubOnceAndEndsTheProcessWhenSignedIn() {
        let transport = FakeAccountTransport()
        let model = DuckySignInModel(makeTransport: { transport })
        var opened: [URL] = []
        model.onCode = { opened.append($0) }
        model.start()
        let page = URL(string: "https://github.com/login/device")!
        model.receive(.signInCode("WDJB-MJHT", page))
        XCTAssertEqual(model.state, .code("WDJB-MJHT", page)); XCTAssertEqual(opened, [page])
        // A second code from a confused process is not a second browser tab.
        model.receive(.signInCode("AAAA-BBBB", page))
        XCTAssertEqual(model.state, .error("service_unavailable")); XCTAssertEqual(opened, [page])
        XCTAssertTrue(transport.cancelled)

        let next = FakeAccountTransport()
        let signedIn = DuckySignInModel(makeTransport: { next })
        signedIn.start(); signedIn.receive(.signInCode("WDJB-MJHT", page)); signedIn.receive(.signedIn("octo"))
        XCTAssertEqual(signedIn.state, .signedIn("octo")); XCTAssertTrue(next.cancelled)
        // Signed in without ever showing a code means the process skipped GitHub.
        let skipped = DuckySignInModel(makeTransport: { FakeAccountTransport() })
        skipped.start(); skipped.receive(.signedIn("octo"))
        XCTAssertEqual(skipped.state, .error("service_unavailable"))
    }

    @MainActor func testWatcherReportsTheAccountAndForgetsItWhenTheProcessFails() {
        var transports: [FakeAccountTransport] = []
        let watcher = DuckyAccountWatcher(makeTransport: { let next = FakeAccountTransport(); transports.append(next); return next })
        watcher.start(); watcher.start()
        XCTAssertEqual(transports.count, 1, "One watcher process at a time")
        transports[0].onEvent?(.claims(login: "octo", devices: [claim]))
        XCTAssertEqual(watcher.status, .signedIn("octo")); XCTAssertEqual(watcher.waiting, [claim])
        transports[0].onEvent?(.error("pairing_failed"))
        XCTAssertNil(watcher.status); XCTAssertEqual(watcher.waiting, [])
        XCTAssertTrue(transports[0].cancelled); XCTAssertTrue(watcher.running, "Retries later")
        watcher.stop()
        XCTAssertFalse(watcher.running)
    }

    @MainActor func testPairWindowOffersSignInAndClaimWindowShowsTheCodeNotTheDeviceID() {
        let invitation = DuckyPairingInvitation(link: "ducky://pair?invite=PREVIEW_ONLY", expiresAt: 1100)
        func texts(_ view: NSView) -> [String] { view.subviews.compactMap { ($0 as? NSTextField)?.stringValue } }
        func button(_ view: NSView, _ id: String) -> NSButton? {
            view.subviews.first { $0.identifier?.rawValue == id } as? NSButton
        }
        XCTAssertNil(button(DuckyPairingView(state: .invitation(invitation), icon: nil, now: now), "signIn"),
                     "Without the flag the window is unchanged")
        let signedOut = DuckyPairingView(state: .invitation(invitation), account: .signedOut, icon: nil, now: now)
        var actions: [DuckyPairingView.Action] = []
        signedOut.onAction = { actions.append($0) }
        let signIn = button(signedOut, "signIn")
        XCTAssertEqual(signIn?.title, "Sign in with GitHub")
        if let signIn, let action = signIn.action { signedOut.perform(action, with: signIn) }
        XCTAssertEqual(actions, [.signIn])
        let signedIn = DuckyPairingView(state: .invitation(invitation), account: .signedIn("octo"), icon: nil, now: now)
        XCTAssertNil(button(signedIn, "signIn"))
        XCTAssertTrue(texts(signedIn).contains { $0.hasPrefix("Signed in to GitHub as octo.") })

        let pending = DuckyPairingView(state: .pending(claim), claim: true, icon: nil, now: now)
        XCTAssertTrue(texts(pending).contains("4821")); XCTAssertFalse(texts(pending).contains(deviceID))
        XCTAssertTrue(texts(pending).contains("Signed in to GitHub as octo. View your folders and session status."))
        XCTAssertEqual(button(pending, "deny")?.keyEquivalent, "\r"); XCTAssertEqual(button(pending, "approve")?.keyEquivalent, "")
        let denied = DuckyPairingView(state: .denied, claim: true, icon: nil, now: now)
        XCTAssertNil(button(denied, "restart"), "A claim can't make a new invitation")

        let code = DuckyPairingView(signIn: .code("WDJB-MJHT", URL(string: "https://github.com/login/device")!), icon: nil)
        XCTAssertTrue(texts(code).contains("WDJB-MJHT"))
        XCTAssertEqual(button(code, "copy")?.title, "Copy code")
        for view in [signedOut, signedIn, pending, denied, code] {
            XCTAssertEqual(view.frame.width, 406)
            XCTAssertTrue(view.subviews.allSatisfy { view.bounds.contains($0.frame) })
        }
    }

    @MainActor private func settle(_ condition: () -> Bool, file: StaticString = #filePath, line: UInt = #line) async {
        for _ in 0..<200 {
            if condition() { return }
            try? await Task.sleep(nanoseconds: 5_000_000)
        }
        XCTFail("Timed out awaiting fixture state", file: file, line: line)
    }
}

@MainActor private final class FakeAccountTransport: DuckyPairingTransport {
    var onEvent: ((DuckyPairingEvent) -> Void)?
    var commands: [String] = []
    var cancelled = false
    func start() async throws {}
    func send(action: String, deviceID: String?, capabilities: [String]?) throws {
        commands.append("\(action):\(deviceID ?? "")")
    }
    func cancel() { cancelled = true }
}
