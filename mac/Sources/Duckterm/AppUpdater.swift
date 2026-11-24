import AppKit
import Sparkle

/// Sparkle owns the signed download, replacement and relaunch. This adapter
/// presents its lifecycle in the existing Settings card. No HTTP install route.
@MainActor
final class AppUpdater: NSObject, SPUUserDriver, SPUUpdaterDelegate {
    private let bundle: Bundle
    private let defaults: UserDefaults
    private let policy: AppUpdatePolicy?
    private var updater: SPUUpdater?
    private var choose: ((SPUUserUpdateChoice) -> Void)?
    private var cancel: (() -> Void)?
    private var expected: UInt64 = 0
    private var received: UInt64 = 0
    private var installRequested = false
    private var preflighting = false
    private var serverWasStopped = false
    private var phase = "idle"
    private var latest: String?
    private var error: String?
    private var progress: Double?
    private let pendingKey = "DuckTermPendingAppUpdate"
    var onChange: (([String: Any]) -> Void)?
    var preflight: (() async throws -> Void)?
    var prepareRestart: (() async throws -> Void)?
    var recoverServer: (() async -> Void)?

    init(bundle: Bundle = .main, defaults: UserDefaults = .standard) {
        self.bundle = bundle; self.defaults = defaults
        do { policy = try AppUpdatePolicy(info: bundle.infoDictionary ?? [:]) }
        catch { policy = nil; self.error = error.localizedDescription; phase = "unavailable" }
        super.init()
        if policy != nil {
            let updater = SPUUpdater(hostBundle: bundle, applicationBundle: bundle, userDriver: self, delegate: self)
            self.updater = updater
            // Preferences must not override the signed feed or enable a path
            // that bypasses the owner's install action and our preflight.
            updater.clearFeedURLFromUserDefaults()
            updater.automaticallyChecksForUpdates = false
            updater.automaticallyDownloadsUpdates = false
            do { try updater.start() }
            catch { fail("The signed updater could not start. Install the latest app manually.") }
        }
    }

    var state: [String: Any] {
        var result: [String: Any] = ["phase": phase, "installed_version": installed,
                                    "can_cancel": cancel != nil || choose != nil]
        if let latest { result["latest_version"] = latest }
        if let error { result["error"] = error }
        if let progress { result["progress"] = progress }
        return result
    }
    private var installed: String { bundle.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "Unknown" }
    private func publish() { onChange?(state) }
    private func set(_ phase: String) { self.phase = phase; progress = nil; error = nil; publish() }
    private func fail(_ text: String) { phase = "failed"; progress = nil; error = text; publish() }

    func perform(_ operation: String) async throws -> [String: Any] {
        switch operation {
        case "status": break
        case "check":
            guard !preflighting, let updater else { return state }
            if let reply = choose { choose = nil; reply(.dismiss) }
            for _ in 0..<20 {
                if updater.canCheckForUpdates { break }
                try await Task.sleep(nanoseconds: 50_000_000)
            }
            if updater.canCheckForUpdates { latest = nil; set("checking"); updater.checkForUpdates() }
        case "install":
            guard !preflighting, phase == "available", let reply = choose, let preflight else { return state }
            preflighting = true
            defer { preflighting = false }
            do { try await preflight() }
            catch { self.error = error.localizedDescription; publish(); return state }
            // Cancel may have been selected while the asynchronous check ran.
            guard phase == "available", choose != nil else { return state }
            choose = nil; cancel = nil; installRequested = true; set("downloading"); reply(.install)
        case "cancel":
            guard cancel != nil || choose != nil else { return state }
            installRequested = false
            let cancellation = cancel, reply = choose
            cancel = nil; choose = nil; latest = nil; set("idle")
            cancellation?(); reply?(.dismiss)
        default: throw LaunchDestination.Failure.message("Unknown update action")
        }
        return state
    }

    /// Called only after the local dashboard server has started and its profile
    /// has been validated. A download finishing is never proof of an update.
    func verifyRelaunch(backend: String?) {
        guard let target = defaults.string(forKey: pendingKey) else { return }
        latest = target
        if AppUpdatePolicy.verified(target: target, app: installed, backend: backend) {
            defaults.removeObject(forKey: pendingKey); set("succeeded")
        } else {
            fail("Update not confirmed. App \(installed); backend \(backend ?? "unavailable"). Reopen DuckTerm to retry verification.")
        }
    }

    func feedURLString(for updater: SPUUpdater) -> String? { policy?.feed.absoluteString }

    func updater(_ updater: SPUUpdater, shouldProceedWithUpdate item: SUAppcastItem, updateCheck: SPUUpdateCheck) throws {
        guard policy?.accepts(archive: item.fileURL, version: item.versionString,
                              informationOnly: item.isInformationOnlyUpdate) == true else {
            throw LaunchDestination.Failure.message("The offered update does not match this app edition.")
        }
    }
    func show(_ request: SPUUpdatePermissionRequest, reply: @escaping (SUUpdatePermissionResponse) -> Void) {
        reply(SUUpdatePermissionResponse(automaticUpdateChecks: false, sendSystemProfile: false))
    }
    func showUserInitiatedUpdateCheck(cancellation: @escaping () -> Void) { cancel = cancellation; set("checking") }
    func showUpdateFound(with item: SUAppcastItem, state: SPUUserUpdateState, reply: @escaping (SPUUserUpdateChoice) -> Void) {
        latest = item.versionString; choose = reply; cancel = nil; set("available")
    }
    func showUpdateReleaseNotes(with downloadData: SPUDownloadData) {}
    func showUpdateReleaseNotesFailedToDownloadWithError(_ error: Error) {}
    func showUpdateNotFoundWithError(_ error: Error, acknowledgement: @escaping () -> Void) {
        cancel = nil; choose = nil
        let reason = (error as NSError).userInfo[SPUNoUpdateFoundReasonKey] as? Int
        if reason == Int(SPUNoUpdateFoundReason.onLatestVersion.rawValue) || reason == Int(SPUNoUpdateFoundReason.onNewerThanLatestVersion.rawValue) { set("current") }
        else { fail("No compatible update is available for this Mac.") }
        acknowledgement()
    }
    static func recoveryMessage(for error: NSError) -> String {
        if error.domain == SUSparkleErrorDomain && [1003, 1005].contains(error.code) {
            return "Move DuckTerm to Applications to update. Quit DuckTerm, use Finder to move it into Applications, then open it from there and try again."
        }
        return "Update failed (\(error.code)). Your installed app has not been confirmed updated. Try again."
    }

    func showUpdaterError(_ error: Error, acknowledgement: @escaping () -> Void) {
        cancel = nil; choose = nil; installRequested = false
        fail(Self.recoveryMessage(for: error as NSError))
        if serverWasStopped {
            serverWasStopped = false
            Task { await recoverServer?() }
        }
        acknowledgement()
    }
    func showDownloadInitiated(cancellation: @escaping () -> Void) {
        cancel = cancellation; expected = 0; received = 0; set("downloading")
    }
    func showDownloadDidReceiveExpectedContentLength(_ expectedContentLength: UInt64) { expected = expectedContentLength }
    func showDownloadDidReceiveData(ofLength length: UInt64) {
        received += length
        progress = expected > 0 ? min(1, Double(received) / Double(expected)) : nil; publish()
    }
    func showDownloadDidStartExtractingUpdate() { cancel = nil; set("verifying") }
    func showExtractionReceivedProgress(_ progress: Double) { self.progress = progress; publish() }
    func showReady(toInstallAndRelaunch reply: @escaping (SPUUserUpdateChoice) -> Void) {
        guard installRequested, let target = latest, let prepareRestart else { reply(.skip); return }
        Task { @MainActor in
            do {
                // Persist before stopping the backend; the next app checks both
                // versions. No session/DB restore and no tmux termination here.
                defaults.set(target, forKey: pendingKey)
                try await prepareRestart()
                serverWasStopped = true
                set("restarting"); reply(.install)
            } catch {
                defaults.removeObject(forKey: pendingKey)
                installRequested = false; reply(.skip); fail(error.localizedDescription)
            }
        }
    }
    func showInstallingUpdate(withApplicationTerminated terminated: Bool, retryTerminatingApplication: @escaping () -> Void) { set("restarting") }
    func showUpdateInstalledAndRelaunched(_ relaunched: Bool, acknowledgement: @escaping () -> Void) { set("restarting"); acknowledgement() }
    func dismissUpdateInstallation() {
        cancel = nil; choose = nil; installRequested = false
        if ["checking", "available", "downloading", "verifying"].contains(phase) { set("idle") }
    }
}
