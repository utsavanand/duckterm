import Foundation

/// A frozen report is reused across retries and both service hostnames.
struct BugReportSubmission {
    let id: String
    let summary: String
    let body: String
    let archive: Data

    init(id: String = UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased(),
         summary: String, body: String, archive: Data) {
        self.id = id; self.summary = summary; self.body = body; self.archive = archive
    }

    func multipart(boundary: String) throws -> Data {
        guard id.range(of: "^[a-f0-9]{32}$", options: .regularExpression) != nil else {
            throw URLError(.badURL)
        }
        let metadata = try JSONSerialization.data(withJSONObject:
            ["id": id, "summary": summary, "body": body], options: [.sortedKeys])
        var data = Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"report\"\r\n\r\n".utf8)
        data.append(metadata)
        data.append(Data("\r\n--\(boundary)\r\nContent-Disposition: form-data; name=\"bundle\"; filename=\"report.zip\"\r\nContent-Type: application/zip\r\n\r\n".utf8))
        data.append(archive)
        data.append(Data("\r\n--\(boundary)--\r\n".utf8))
        return data
    }
}

enum BugReportDeliveryResult: Equatable {
    case filed(Int)
    case failed(String)
    case unconfirmed

    var message: String {
        switch self {
        case .filed(let number): return "Report #\(number) filed privately. Thank you."
        case .failed(let message): return message
        case .unconfirmed: return "Report delivery could not be confirmed. Retry this report to check, or save the ZIP."
        }
    }
}

private final class ReportNoRedirect: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        // Never forward private report bytes to a location returned by a server.
        completionHandler(nil)
    }
}

struct BugReportDelivery {
    static let urls = [
        URL(string: "https://utsava.xyz/api/duckterm-report")!,
        URL(string: "https://my-digital-card-three.vercel.app/api/duckterm-report")!,
    ]
    static let maxUploadBytes = 20 * 1024 * 1024
    static let maxResponseBytes = 8192
    typealias Transport = (URLRequest) async throws -> (Data, HTTPURLResponse)
    let transport: Transport

    init(transport: @escaping Transport = BugReportDelivery.request) { self.transport = transport }

    static func request(_ request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 30
        configuration.timeoutIntervalForResource = 40
        configuration.httpCookieStorage = nil
        configuration.urlCredentialStorage = nil
        let session = URLSession(configuration: configuration, delegate: ReportNoRedirect(), delegateQueue: nil)
        defer { session.invalidateAndCancel() }
        let (stream, response) = try await session.bytes(for: request)
        guard let response = response as? HTTPURLResponse else { throw URLError(.badServerResponse) }
        var data = Data()
        for try await byte in stream {
            data.append(byte)
            guard data.count <= maxResponseBytes else { throw URLError(.dataLengthExceedsMaximum) }
        }
        return (data, response)
    }

    func send(_ report: BugReportSubmission) async -> BugReportDeliveryResult {
        let boundary = "duckterm-" + UUID().uuidString
        guard let data = try? report.multipart(boundary: boundary) else {
            return .failed("Invalid report. Save the ZIP and use email.")
        }
        guard data.count <= Self.maxUploadBytes else {
            return .failed("Report too large to send. Save the ZIP and use email.")
        }
        var uncertain = false
        for url in Self.urls {
            var request = URLRequest(url: url)
            request.httpMethod = "POST"
            request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
            request.httpBody = data
            do {
                let (body, response) = try await transport(request)
                if response.statusCode == 200 {
                    guard body.count <= Self.maxResponseBytes,
                          let value = try? JSONSerialization.jsonObject(with: body) as? [String: Any],
                          let ok = value["ok"] as? NSNumber,
                          CFGetTypeID(ok) == CFBooleanGetTypeID(), ok.boolValue,
                          let number = value["id"] as? NSNumber,
                          CFGetTypeID(number) != CFBooleanGetTypeID(),
                          number.doubleValue == Double(number.intValue), number.intValue > 0 else {
                        return .unconfirmed
                    }
                    return .filed(number.intValue)
                }
                // Refusals, including rate limiting, never try the alternate host.
                if uncertain || response.statusCode == 409 || response.statusCode >= 500 && response.statusCode != 503 {
                    return .unconfirmed
                }
                switch response.statusCode {
                case 413: return .failed("Report too large to send. Save the ZIP and use email.")
                case 429: return .failed("Too many reports. Try again in an hour, or use email.")
                case 503: return .failed("Private reporting is not configured yet. Use email or save the ZIP.")
                default: return .failed("Could not file the report. Use email or save the ZIP.")
                }
            } catch is CancellationError { return .unconfirmed }
            catch let error as URLError where error.code == .cancelled { return .unconfirmed }
            catch {
                // The first host might have saved before its connection failed.
                // The exact same report ID and bytes make fallback a safe retry.
                uncertain = true
            }
        }
        return .unconfirmed
    }
}
