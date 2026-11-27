import XCTest
@testable import DuckyCore

private let studio = AccountMac(id: "d1", name: "Studio Mac", lastSeenAt: 1_795_000_000,
    hostID: String(repeating: "a", count: 32), certificateSHA256: String(repeating: "c", count: 64),
    tlsName: nil, url: URL(string: "https://192.168.1.24:4430")!)
private let air = AccountMac(id: "d2", name: "MacBook Air", lastSeenAt: 1_795_000_000,
    hostID: String(repeating: "b", count: 32), certificateSHA256: String(repeating: "d", count: 64),
    tlsName: nil, url: URL(string: "https://192.168.1.25:4430")!)
private let signedIn = AccountSession(accountID: String(repeating: "e", count: 32), login: "octo",
    deviceID: String(repeating: "f", count: 32), sessionToken: String(repeating: "s", count: 43))
private let issuedTicket = "eyJob3N0X2lkIjoiYSJ9.c2lnbmF0dXJl"

final class FakeCloud: AccountTransport {
    var macs: Result<[AccountMac], Error> = .success([studio])
    var redeemed: (code: String, verifier: String, name: String)?
    var ticketsFor: [String] = []
    var signOuts = 0
    var signOutError: Error?
    func redeem(code: String, verifier: String, name: String) async throws -> AccountSession {
        redeemed = (code, verifier, name)
        return signedIn
    }
    func macs(token: String) async throws -> [AccountMac] {
        XCTAssertEqual(token, signedIn.sessionToken)
        return try macs.get()
    }
    func ticket(hostID: String, token: String) async throws -> String {
        ticketsFor.append(hostID)
        return issuedTicket
    }
    func signOut(token: String) async throws {
        signOuts += 1
        if let signOutError { throw signOutError }
    }
}

final class FakeAccountStore: AccountStoring {
    var saved: AccountSession?
    init(_ saved: AccountSession? = nil) { self.saved = saved }
    func save(_ session: AccountSession) throws { saved = session }
    func load() throws -> AccountSession? { saved }
    func forget() throws { saved = nil }
}

@MainActor
final class AccountTests: XCTestCase {
    private var transports: [FakeTransport] = []
    private var credentials = FakeStore()
    private var cloud = FakeCloud()
    private var accountStore = FakeAccountStore()

    private func models(claimError: Error? = nil) -> (AccountModel, SessionModel) {
        let devices = SessionModel(credentials: credentials, makeTransport: { [unowned self] host in
            let next = FakeTransport(host: host)
            next.claimError = claimError
            self.transports.append(next)
            return next
        }, onLocalNetwork: { true })
        let account = AccountModel(cloudURL: URL(string: "https://relay.example"), devices: devices,
                                   store: accountStore, makeTransport: { [unowned self] _ in self.cloud })
        account.restore()
        return (account, devices)
    }

    private func signIn(_ account: AccountModel) async throws {
        let start = try account.signInURL()
        let challenge = URLComponents(url: start, resolvingAgainstBaseURL: false)?.queryItems?.first?.value
        XCTAssertEqual(start.path, "/v1/auth/github/start")
        await account.finishSignIn(URL(string: "ducky://signed-in?code=one-time")!, name: "Utsav's iPhone")
        // The service only ever saw the challenge; the redeem proves the verifier behind it.
        XCTAssertEqual(challenge, AccountPairing.PKCE(verifier: cloud.redeemed!.verifier).challenge)
    }

    func testMatchingCodeEqualsTheMacsPythonFunction() {
        // Vectors from duckterm.mobile.account.matching_code.
        XCTAssertEqual(AccountPairing.matchingCode(token: String(repeating: "t", count: 43), certificateSHA256: String(repeating: "a", count: 64)), "0538")
        XCTAssertEqual(AccountPairing.matchingCode(token: String(repeating: "AbC_-", count: 9), certificateSHA256: String(repeating: "0123456789abcdef", count: 4)), "6856")
    }

    func testPKCEUsesS256AndAVerifierTheServiceAccepts() throws {
        // The RFC 7636 appendix B verifier, through duckterm.cloud.store.challenge_for.
        XCTAssertEqual(AccountPairing.PKCE(verifier: "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk").challenge, "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")
        let made = try AccountPairing.PKCE.make()
        XCTAssertNotNil(made.verifier.range(of: "^[A-Za-z0-9._~-]{43,128}$", options: .regularExpression))
        XCTAssertNotEqual(made, try AccountPairing.PKCE.make())
    }

    func testCloudURLMustBeAnHTTPSOrigin() {
        XCTAssertEqual(AccountPairing.cloudURL("https://relay.duckterm.utsava.xyz/")?.absoluteString, "https://relay.duckterm.utsava.xyz")
        for value in ["", "http://relay.example", "$(DUCKY_CLOUD_URL)", "https://relay.example/v1", "https://u:p@relay.example"] {
            XCTAssertNil(AccountPairing.cloudURL(value), value)
        }
        XCTAssertNil(AccountPairing.cloudURL(nil))
        let devices = SessionModel(credentials: FakeStore())
        XCTAssertEqual(AccountModel(cloudURL: nil, devices: devices).phase, .unavailable)
    }

    func testSigningInWithOneMacAsksItAndWaitsWithTheMatchingCode() async throws {
        let (account, devices) = models()
        try await signIn(account)
        XCTAssertEqual(accountStore.saved, signedIn)
        XCTAssertEqual(account.phase, .signedIn)
        XCTAssertEqual(cloud.redeemed?.name, "Utsav's iPhone")
        XCTAssertEqual(cloud.ticketsFor, [studio.hostID])
        XCTAssertEqual(transports.map(\.tickets), [[issuedTicket]])
        XCTAssertEqual(devices.phase, .awaitingApproval)
        let host = try XCTUnwrap(devices.host)
        XCTAssertEqual(host.macName, "Studio Mac")
        XCTAssertEqual(host.accountLogin, "octo")
        XCTAssertEqual(credentials.saved, host)
        XCTAssertEqual(host.matchingCode, AccountPairing.matchingCode(token: host.token, certificateSHA256: studio.certificateSHA256))

        // The owner allows it on the Mac: the next poll sees it active and reads status.
        transports[0].status = "active"
        await devices.refresh()
        XCTAssertEqual(devices.phase, .live)
        XCTAssertEqual(transports[0].snapshotReads, 1)
    }

    func testSeveralMacsWaitForAChoice() async throws {
        cloud.macs = .success([air, studio])
        let (account, devices) = models()
        try await signIn(account)
        XCTAssertEqual(account.macs, [air, studio])
        XCTAssertTrue(cloud.ticketsFor.isEmpty)
        XCTAssertEqual(devices.phase, .unpaired)

        await account.ask(air, name: "Utsav's iPhone")
        XCTAssertEqual(devices.host?.hostID, air.hostID)
        XCTAssertEqual(devices.phase, .awaitingApproval)
    }

    func testAMacOutsideThePrivateNetworkIsNeverListed() async throws {
        let public_ = AccountMac(id: "d3", name: "Cloud Mac", lastSeenAt: 1, hostID: String(repeating: "9", count: 32),
            certificateSHA256: String(repeating: "c", count: 64), tlsName: nil, url: URL(string: "https://8.8.8.8:4430")!)
        cloud.macs = .success([public_])
        let (account, devices) = models()
        try await signIn(account)
        XCTAssertEqual(account.macs, [])
        XCTAssertNil(devices.host)
        XCTAssertTrue(cloud.ticketsFor.isEmpty)
    }

    func testAnUnreachableMacSaysItNeedsTheSameNetworkAndSavesNothing() async throws {
        let (account, devices) = models(claimError: URLError(.cannotConnectToHost))
        try await signIn(account)
        XCTAssertEqual(account.error, "Can’t reach Studio Mac — it needs to be on the same network for now.")
        XCTAssertEqual(devices.phase, .unpaired)
        XCTAssertNil(credentials.saved)
        XCTAssertTrue(transports[0].closed)
    }

    func testAMacWithoutAccountSignInPointsToTheInvitation() async throws {
        let (account, _) = models(claimError: DuckyError.unavailable(404))
        try await signIn(account)
        XCTAssertEqual(account.error, AccountError.macUnsupported("Studio Mac").errorDescription)
    }

    func testCancelledGitHubSignInShowsNoErrorAndSavesNothing() async throws {
        let (account, _) = models()
        _ = try account.signInURL()
        await account.finishSignIn(URL(string: "ducky://signed-in?error=cancelled")!, name: "iPhone")
        XCTAssertEqual(account.phase, .signedOut)
        XCTAssertNil(account.error)
        XCTAssertNil(cloud.redeemed)
        XCTAssertNil(accountStore.saved)
        // A callback with no sign-in in progress is ignored, not redeemed.
        await account.finishSignIn(URL(string: "ducky://signed-in?code=replayed")!, name: "iPhone")
        XCTAssertNil(cloud.redeemed)
        XCTAssertEqual(account.error, AccountError.github.errorDescription)
    }

    func testAnExpiredServiceSessionSignsOutLocally() async {
        accountStore.saved = signedIn
        cloud.macs = .failure(AccountError.signedOut)
        let (account, _) = models()
        XCTAssertEqual(account.phase, .signedIn)
        await account.loadMacs(name: "iPhone")
        XCTAssertEqual(account.phase, .signedOut)
        XCTAssertNil(accountStore.saved)
        XCTAssertEqual(account.error, AccountError.signedOut.errorDescription)
    }

    func testSignOutForgetsTheSessionEvenWhenTheServiceIsUnreachable() async {
        accountStore.saved = signedIn
        cloud.signOutError = AccountError.service
        let (account, _) = models()
        await account.signOut()
        XCTAssertEqual(cloud.signOuts, 1)
        XCTAssertNil(accountStore.saved)
        XCTAssertEqual(account.phase, .signedOut)
        XCTAssertNil(account.error)
    }

    func testCancellingTheWaitForgetsThePendingMacAndShowsTheList() async throws {
        let (account, devices) = models()
        try await signIn(account)
        devices.forget()
        XCTAssertNil(credentials.saved)
        XCTAssertEqual(devices.phase, .unpaired)
        // Back on the list, the only Mac is not asked again without a tap.
        await account.loadMacs(name: "iPhone")
        XCTAssertEqual(cloud.ticketsFor.count, 1)
        XCTAssertEqual(account.macs, [studio])
    }

    func testADeniedPhoneAsksTheSameMacAgainWithANewCredential() async throws {
        let (account, devices) = models()
        try await signIn(account)
        let refused = try XCTUnwrap(devices.host)
        transports[0].statusError = DuckyError.unavailable(403)
        await devices.refresh()
        XCTAssertEqual(devices.phase, .denied)

        await account.askAgain(name: "Utsav's iPhone")
        XCTAssertEqual(cloud.ticketsFor, [studio.hostID, studio.hostID])
        XCTAssertEqual(transports.count, 2)
        XCTAssertEqual(devices.phase, .awaitingApproval)
        let asked = try XCTUnwrap(devices.host)
        XCTAssertEqual(asked.hostID, refused.hostID)
        // The Mac revoked the old token, so reusing it could never be allowed.
        XCTAssertNotEqual(asked.token, refused.token)
        XCTAssertEqual(credentials.saved, asked)
    }
}
