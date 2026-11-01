import Darwin
import Foundation
import Network
import dnssd

/// Resolve only the invited local name; HTTP always receives an already filtered literal.
enum LocalEndpoint {
    typealias Resolver = (String) async throws -> [String]

    static func hostname(_ url: URL) -> String {
        (url.host ?? "").trimmingCharacters(in: CharacterSet(charactersIn: "[]")).lowercased()
    }

    static func isLocalName(_ value: String) -> Bool {
        value.utf8.allSatisfy { $0 < 128 } &&
            value.lowercased().range(of: "^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\\.local\\z", options: .regularExpression) != nil
    }

    static func privateLiteral(_ value: String) -> String? {
        // No scoped/link-local, abbreviated/octal IPv4 or IPv4-mapped IPv6 aliases.
        if let address = IPv4Address(value) {
            let components = value.split(separator: ".", omittingEmptySubsequences: false)
            guard components.count == 4, components.allSatisfy({ Int($0).map(String.init) == String($0) }) else { return nil }
            let bytes = Array(address.rawValue)
            guard bytes[0] == 10 || bytes[0] == 127 ||
                    (bytes[0] == 192 && bytes[1] == 168) ||
                    (bytes[0] == 172 && (16...31).contains(bytes[1])) else { return nil }
            return address.debugDescription
        }
        if !value.contains("%"), let address = IPv6Address(value) {
            guard address == IPv6Address.loopback || (address.rawValue.first! & 0xfe) == 0xfc else { return nil }
            return address.debugDescription
        }
        return nil
    }

    static func validTLSName(_ name: String?, for url: URL) -> Bool {
        guard let name else { return true }
        return privateLiteral(name) != nil || (isLocalName(name) && name.lowercased() == hostname(url))
    }

    static func addresses(for url: URL, resolver: Resolver) async throws -> [String] {
        let name = hostname(url)
        if let literal = privateLiteral(name) { return [literal] }
        guard isLocalName(name) else { throw DuckyError.invalidInvitation }
        let answers = try await resolver(name)
        // Filter again even for an injected resolver. Mixed IPv4/link-local Bonjour answers
        // are common; forbidden records never become connection targets.
        let addresses = Set(answers.compactMap(privateLiteral)).sorted {
            let leftV6 = $0.contains(":"), rightV6 = $1.contains(":")
            return leftV6 == rightV6 ? $0 < $1 : !leftV6
        }
        guard !addresses.isEmpty else { throw URLError(.cannotFindHost) }
        return Array(addresses.prefix(3))
    }

    static func resolve(_ hostname: String) async throws -> [String] {
        guard isLocalName(hostname) else { throw DuckyError.invalidInvitation }
        return try await LocalNameLookup().resolve(hostname)
    }
}

/// DNS-SD uses the OS resolver and can actually cancel; a detached getaddrinfo
/// would leave an unbounded blocking worker behind after a timeout.
private final class LocalNameLookup: @unchecked Sendable {
    private let queue = DispatchQueue(label: "app.duckterm.ducky.local-name")
    private var service: DNSServiceRef?
    private var continuation: CheckedContinuation<[String], Error>?
    private var answers = Set<String>()
    private var cancelled = false
    private var finishing = false

    func resolve(_ hostname: String) async throws -> [String] {
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                queue.async { self.start(hostname, continuation: continuation) }
            }
        } onCancel: {
            self.queue.async {
                self.cancelled = true
                self.finish(.failure(CancellationError()))
            }
        }
    }

    private func start(_ hostname: String, continuation: CheckedContinuation<[String], Error>) {
        self.continuation = continuation
        guard !cancelled else { finish(.failure(CancellationError())); return }
        let result = DNSServiceGetAddrInfo(&service, 0, 0,
            DNSServiceProtocol(kDNSServiceProtocol_IPv4 | kDNSServiceProtocol_IPv6),
            hostname, { _, flags, _, error, _, address, _, context in
                guard let context else { return }
                let lookup = Unmanaged<LocalNameLookup>.fromOpaque(context).takeUnretainedValue()
                lookup.receive(flags: flags, error: error, address: address)
            }, Unmanaged.passUnretained(self).toOpaque())
        guard result == kDNSServiceErr_NoError, let service,
              DNSServiceSetDispatchQueue(service, queue) == kDNSServiceErr_NoError else {
            finish(.failure(URLError(.cannotFindHost))); return
        }
        queue.asyncAfter(deadline: .now() + 3) { [weak self] in
            guard let self, self.continuation != nil else { return }
            self.finish(self.answers.isEmpty ? .failure(URLError(.cannotFindHost)) : .success(Array(self.answers)))
        }
    }

    private func receive(flags: DNSServiceFlags, error: DNSServiceErrorType, address: UnsafePointer<sockaddr>?) {
        guard continuation != nil, error == kDNSServiceErr_NoError, let address else { return }
        var buffer = [CChar](repeating: 0, count: Int(NI_MAXHOST))
        guard getnameinfo(address, socklen_t(address.pointee.sa_len), &buffer,
                          socklen_t(buffer.count), nil, 0, NI_NUMERICHOST) == 0 else { return }
        let literal = String(cString: buffer)
        if flags & DNSServiceFlags(kDNSServiceFlagsAdd) != 0 { answers.insert(literal) }
        else { answers.remove(literal) }
        // Allow a brief batch for both address families, then stop this OS query.
        if !finishing, answers.contains(where: { LocalEndpoint.privateLiteral($0) != nil }),
           flags & DNSServiceFlags(kDNSServiceFlagsMoreComing) == 0 {
            finishing = true
            queue.asyncAfter(deadline: .now() + 0.15) { [weak self] in
                guard let self, self.continuation != nil else { return }
                self.finish(.success(Array(self.answers)))
            }
        }
    }

    private func finish(_ result: Result<[String], Error>) {
        if let service { DNSServiceRefDeallocate(service); self.service = nil }
        guard let continuation else { return }
        self.continuation = nil
        continuation.resume(with: result)
    }
}
