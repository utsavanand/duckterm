import AppKit
import XCTest
@testable import Duckterm

@MainActor
private final class MenuPairingPresenter: DuckyPairingPresenting {
    var onClose: (() -> Void)?
    var isKeyWindow: Bool { false }
    private(set) var shows = 0
    private(set) var closes = 0
    // Retain the real preparation contract, but never run its server setup in
    // a menu test. No controller, window, authentication or backend is created.
    let prepare: () async throws -> Void

    init(prepare: @escaping () async throws -> Void) { self.prepare = prepare }
    func show() { shows += 1 }
    func close() { closes += 1; onClose?() }
}

final class DuckyPairingMenuTests: XCTestCase {
    @MainActor private func item(for app: AppDelegate) -> NSMenuItem {
        let item = NSMenuItem(title: "Pair iPhone…", action: #selector(AppDelegate.pairIPhone(_:)), keyEquivalent: "")
        item.target = app
        return item
    }

    @MainActor private func click(_ item: NSMenuItem, on app: AppDelegate) {
        XCTAssertTrue(item.target === app)
        XCTAssertEqual(item.action, #selector(AppDelegate.pairIPhone(_:)))
        // Exercise the menu's actual Objective-C action without NSApplication,
        // an event loop, a window, or any system input event.
        _ = app.perform(item.action!, with: item)
    }

    @MainActor private func allow(_ admission: WorkspaceLaunchAdmission) {
        XCTAssertTrue(admission.decide(notice: nil) { _ in XCTFail("No notice expected"); return false })
    }

    @MainActor func testFirstEditionFetchTimeoutLeavesPairingMenuEnabled() async {
        let admission = WorkspaceLaunchAdmission(); allow(admission)
        var requests = 0, creations = 0
        let app = AppDelegate(launchAdmission: admission, editionRequest: { _ in
            requests += 1
            throw URLError(.timedOut)
        }, makePairing: { _, prepare in
            creations += 1
            return MenuPairingPresenter(prepare: prepare)
        })
        do {
            _ = try await app.refreshEdition()
            XCTFail("Expected the injected first startup fetch to fail")
        } catch { XCTAssertEqual((error as? URLError)?.code, .timedOut) }

        XCTAssertEqual(requests, 1)
        XCTAssertTrue(app.validateMenuItem(item(for: app)), "A missing startup cache must not disable explicit pairing recovery")
        XCTAssertEqual(creations, 0, "Menu validation must not prepare pairing")
    }

    @MainActor func testClickAfterFirstFetchTimeoutCreatesReusesAndReopensPresenter() async {
        let admission = WorkspaceLaunchAdmission(); allow(admission)
        var requests = 0
        var presenters: [MenuPairingPresenter] = []
        let app = AppDelegate(launchAdmission: admission, editionRequest: { _ in
            requests += 1
            throw URLError(.timedOut)
        }, makePairing: { _, prepare in
            let presenter = MenuPairingPresenter(prepare: prepare)
            presenters.append(presenter)
            return presenter
        })
        do {
            _ = try await app.refreshEdition()
            XCTFail("Expected the injected first startup fetch to fail")
        } catch { XCTAssertEqual((error as? URLError)?.code, .timedOut) }

        let menuItem = item(for: app)
        click(menuItem, on: app)
        guard let first = presenters.first else { return XCTFail("The explicit click must create a presenter despite the empty edition cache") }
        XCTAssertEqual(presenters.count, 1); XCTAssertEqual(first.shows, 1)
        XCTAssertNotNil(first.onClose)
        click(menuItem, on: app)
        XCTAssertEqual(presenters.count, 1); XCTAssertEqual(first.shows, 2)

        first.close()
        XCTAssertEqual(first.closes, 1)
        click(menuItem, on: app)
        XCTAssertEqual(presenters.count, 2)
        guard presenters.count == 2 else { return }
        XCTAssertFalse(first === presenters[1]); XCTAssertEqual(presenters[1].shows, 1)
        XCTAssertEqual(first.shows, 2, "A closed presenter must not be reused")
        presenters[1].close()
        XCTAssertEqual(requests, 1, "Menu clicks hand preparation to the presenter; the fake never starts it")
    }

    @MainActor func testPendingPresentingAndCancelledAdmissionBlockMenuAndClick() {
        let admission = WorkspaceLaunchAdmission()
        var requests = 0, creations = 0
        let app = AppDelegate(launchAdmission: admission, editionRequest: { _ in
            requests += 1
            throw URLError(.timedOut)
        }, makePairing: { _, prepare in
            creations += 1
            return MenuPairingPresenter(prepare: prepare)
        })
        let menuItem = item(for: app)
        func assertBlocked(_ state: String) {
            XCTAssertFalse(app.validateMenuItem(menuItem), state)
            click(menuItem, on: app)
            XCTAssertEqual(creations, 0, state)
        }
        assertBlocked("pending")
        XCTAssertFalse(admission.decide(notice: LegacyWorkspaceNotice(edition: "lite")) { _ in
            assertBlocked("presenting")
            return false
        })
        assertBlocked("cancelled")
        XCTAssertFalse(admission.decide(notice: nil) { _ in XCTFail("Cancellation is final"); return true })
        assertBlocked("cancelled after another request")
        XCTAssertEqual(requests, 0, "Blocked actions must not make edition requests")
    }

    @MainActor func testAcceptedAdmissionEnablesMenuAndClickWithoutAnEditionProbe() {
        let admission = WorkspaceLaunchAdmission()
        var requests = 0
        var presenters: [MenuPairingPresenter] = []
        let app = AppDelegate(launchAdmission: admission, editionRequest: { _ in
            requests += 1
            throw URLError(.timedOut)
        }, makePairing: { _, prepare in
            let presenter = MenuPairingPresenter(prepare: prepare)
            presenters.append(presenter)
            return presenter
        })
        let menuItem = item(for: app)
        XCTAssertFalse(app.validateMenuItem(menuItem)); click(menuItem, on: app)
        XCTAssertTrue(presenters.isEmpty)
        allow(admission)
        XCTAssertTrue(app.validateMenuItem(menuItem)); click(menuItem, on: app)
        XCTAssertEqual(presenters.count, 1); XCTAssertEqual(presenters.first?.shows, 1)
        XCTAssertEqual(requests, 0)
        presenters.first?.close()
    }

    @MainActor func testPresenterKeepsLiveVerifiedEditionAcrossRefreshFailureAndRelease() async throws {
        let admission = WorkspaceLaunchAdmission(); allow(admission)
        var response: Any = ["id": "full", "features": ["terminal", "folders", "session_status", "folder_chat", "oracle"]]
        var shouldFail = false
        var verifiedEdition: (() -> DesktopEdition?)?
        var app: AppDelegate? = AppDelegate(launchAdmission: admission, editionRequest: { _ in
            if shouldFail { throw URLError(.timedOut) }
            return response
        }, makePairing: { edition, prepare in
            verifiedEdition = edition
            return MenuPairingPresenter(prepare: prepare)
        })
        weak var released = app
        app?.pairIPhone(nil)
        let current = try XCTUnwrap(verifiedEdition)
        let pairing = DuckyPairingModel(makeTransport: {
            DuckyPairingProcess(command: {
                XCTFail("Availability must not start pairing or a process")
                throw DuckyPairingEvent.Failure.invalid
            })
        }, authenticate: { _ in
            XCTFail("Availability must not request owner authentication")
            return false
        }, verifiedEdition: current)
        XCTAssertNil(current(), "A fresh presenter must not invent an edition before a validated read")
        XCTAssertFalse(pairing.availability.showsQuestions)

        _ = try await app?.refreshEdition()
        XCTAssertEqual(current()?.id, "full")
        XCTAssertTrue(pairing.availability.folder)
        XCTAssertTrue(pairing.availability.oracle)

        response = ["id": "code", "features": ["terminal", "folders", "session_status", "folder_chat"]]
        _ = try await app?.refreshEdition()
        XCTAssertEqual(current()?.id, "code")
        XCTAssertTrue(pairing.availability.folder)
        XCTAssertFalse(pairing.availability.oracle)

        response = ["id": "lite", "features": ["terminal", "folders", "session_status"]]
        _ = try await app?.refreshEdition()
        XCTAssertEqual(current()?.id, "lite")
        XCTAssertFalse(pairing.availability.showsQuestions)

        shouldFail = true
        do { _ = try await app?.refreshEdition(); XCTFail("Expected read failure") }
        catch { XCTAssertEqual((error as? URLError)?.code, .timedOut) }
        XCTAssertNil(current(), "A failed read must not keep granting the previous analysis choices")
        XCTAssertFalse(pairing.availability.showsQuestions)

        app = nil
        XCTAssertNil(released, "The presenter callback must not retain the app delegate")
        XCTAssertNil(current())
        XCTAssertFalse(pairing.availability.showsQuestions)
    }
}
