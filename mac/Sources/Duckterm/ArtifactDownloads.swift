import AppKit
import WebKit

/// Saves authenticated dashboard blob downloads after the owner chooses a path.
/// Destination selection is injected so a real WebKit download can be tested
/// without opening a Save panel or changing any production files.
final class ArtifactDownloads: NSObject, WKDownloadDelegate {
    typealias DestinationChooser = (String, @escaping (URL?) -> Void) -> Void
    private let chooseDestination: DestinationChooser
    private let showError: (Error) -> Void
    private var pending: [ObjectIdentifier: (temporary: URL, destination: URL)] = [:]

    init(chooseDestination: @escaping DestinationChooser, showError: @escaping (Error) -> Void) {
        self.chooseDestination = chooseDestination
        self.showError = showError
    }

    func download(
        _ download: WKDownload, decideDestinationUsing response: URLResponse,
        suggestedFilename: String, completionHandler: @escaping (URL?) -> Void
    ) {
        chooseDestination(URL(fileURLWithPath: suggestedFilename).lastPathComponent) { [weak self] destination in
            guard let self, let destination else { completionHandler(nil); return }
            // WebKit requires a nonexisting destination. Stage privately and
            // atomically save afterwards, including user-confirmed replacements.
            let directory = FileManager.default.temporaryDirectory
                .appendingPathComponent("duckterm-artifact-" + UUID().uuidString)
            do {
                try FileManager.default.createDirectory(at: directory,
                    withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
                let temporary = directory.appendingPathComponent("download")
                self.pending[ObjectIdentifier(download)] = (temporary, destination)
                completionHandler(temporary)
            } catch {
                completionHandler(nil)
                self.showError(error)
            }
        }
    }

    func downloadDidFinish(_ download: WKDownload) {
        guard let files = pending.removeValue(forKey: ObjectIdentifier(download)) else { return }
        defer { try? FileManager.default.removeItem(at: files.temporary.deletingLastPathComponent()) }
        do {
            try Data(contentsOf: files.temporary).write(to: files.destination, options: .atomic)
        } catch { showError(error) }
    }

    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        if let files = pending.removeValue(forKey: ObjectIdentifier(download)) {
            try? FileManager.default.removeItem(at: files.temporary.deletingLastPathComponent())
        }
        if (error as NSError).code != NSURLErrorCancelled { showError(error) }
    }
}
