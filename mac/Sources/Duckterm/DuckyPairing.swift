import Foundation

struct DuckyPairingDevice: Decodable, Equatable {
    let id: String
    let name: String
    let expiresAt: Int?
    enum CodingKeys: String, CodingKey { case id, name; case expiresAt = "expires_at" }
    var valid: Bool {
        id.range(of: "^[a-f0-9]{32}\\z", options: .regularExpression) != nil &&
            !name.isEmpty && name.count <= 80 && name.unicodeScalars.allSatisfy {
                ![.control, .format, .lineSeparator, .paragraphSeparator].contains($0.properties.generalCategory)
            }
    }
}

struct DuckyPairingInvitation: Equatable {
    let link: String
    let expiresAt: Int
}

enum DuckyPairingEvent: Equatable {
    case invitation(DuckyPairingInvitation), pending(DuckyPairingDevice)
    case approved(DuckyPairingDevice), denied(DuckyPairingDevice)
    case expired(pending: Bool), error(String), cancelled

    static func decode(_ data: Data) throws -> Self {
        struct Frame: Decodable {
            let version: Int
            let state: String
            var link: String?
            var expires_at: Int?
            var device: DuckyPairingDevice?
            var phase: String?
            var code: String?
        }
        guard data.count <= 8192 else { throw Failure.invalid }
        let frame = try JSONDecoder().decode(Frame.self, from: data)
        guard frame.version == 1 else { throw Failure.invalid }
        switch frame.state {
        case "invitation":
            guard let link = frame.link, link.utf8.count <= 4096,
                  let url = URLComponents(string: link), url.scheme == "ducky", url.host == "pair",
                  url.user == nil, url.password == nil, url.port == nil, url.path.isEmpty, url.fragment == nil,
                  url.queryItems?.count == 1, let item = url.queryItems?.first, item.name == "invite",
                  let value = item.value, !value.isEmpty,
                  value.range(of: "^[A-Za-z0-9_-]+\\z", options: .regularExpression) != nil,
                  let expiry = frame.expires_at else { throw Failure.invalid }
            return .invitation(.init(link: link, expiresAt: expiry))
        case "pending", "approved", "denied":
            guard let device = frame.device, device.valid else { throw Failure.invalid }
            if frame.state == "pending" {
                guard device.expiresAt != nil else { throw Failure.invalid }
                return .pending(device)
            }
            return frame.state == "approved" ? .approved(device) : .denied(device)
        case "expired":
            guard ["invitation", "pending"].contains(frame.phase) else { throw Failure.invalid }
            return .expired(pending: frame.phase == "pending")
        case "error":
            guard let code = frame.code,
                  ["setup_unavailable", "listener_unavailable", "pairing_busy", "invalid_command", "approval_unavailable", "pairing_failed"].contains(code) else { throw Failure.invalid }
            return .error(code)
        case "cancelled": return .cancelled
        default: throw Failure.invalid
        }
    }
    enum Failure: Error { case invalid }
}

/// Bounded JSONL decoding also handles partial pipe reads without logging secrets.
struct DuckyPairingFrames {
    private var buffer = Data()
    private var total = 0
    mutating func append(_ bytes: Data) throws -> [DuckyPairingEvent] {
        total += bytes.count
        guard total <= 65536 else { throw DuckyPairingEvent.Failure.invalid }
        buffer.append(bytes)
        var events: [DuckyPairingEvent] = []
        while let newline = buffer.firstIndex(of: 10) {
            events.append(try DuckyPairingEvent.decode(Data(buffer[..<newline])))
            buffer.removeSubrange(...newline)
        }
        guard buffer.count <= 8192 else { throw DuckyPairingEvent.Failure.invalid }
        return events
    }
    var complete: Bool { buffer.isEmpty }
}

@MainActor
protocol DuckyPairingTransport: AnyObject {
    var onEvent: ((DuckyPairingEvent) -> Void)? { get set }
    func start() async throws
    func send(action: String, deviceID: String?) throws
    func cancel()
}

@MainActor
final class DuckyPairingModel {
    enum State: Equatable {
        case opening, invitation(DuckyPairingInvitation), pending(DuckyPairingDevice)
        case approved(DuckyPairingDevice), denied, expired(pending: Bool), error(String), closed
    }
    private(set) var state: State = .opening
    private(set) var busy = false
    var onChange: (() -> Void)?
    private let makeTransport: () -> DuckyPairingTransport
    private let authenticate: (String) async -> Bool
    private let cancelAuthentication: () -> Void
    private let now: () -> Date
    private var transport: DuckyPairingTransport?
    private var authentication: Task<Void, Never>?
    private var generation = 0
    private var invitationExpiry: Int?
    private var decision: String?
    private var startedAt: Date?
    private var decisionSentAt: Date?

    init(makeTransport: @escaping () -> DuckyPairingTransport,
         authenticate: @escaping (String) async -> Bool,
         cancelAuthentication: @escaping () -> Void = {}, now: @escaping () -> Date = Date.init) {
        self.makeTransport = makeTransport; self.authenticate = authenticate
        self.cancelAuthentication = cancelAuthentication; self.now = now
    }

    func start() {
        stopTransport()
        generation += 1
        let current = generation
        state = .opening; busy = false; decision = nil; decisionSentAt = nil; invitationExpiry = nil; startedAt = now()
        let next = makeTransport()
        transport = next
        next.onEvent = { [weak self] event in
            guard let self, self.generation == current else { return }
            self.receive(event)
        }
        onChange?()
        Task { [weak self, weak next] in
            do { try await next?.start() }
            catch {
                guard let self, self.generation == current, self.state != .closed else { return }
                self.fail("pairing_failed")
            }
        }
    }

    func receive(_ event: DuckyPairingEvent) {
        guard state != .closed else { return }
        switch event {
        case .invitation(let invitation):
            guard state == .opening, invitation.expiresAt <= Int(now().timeIntervalSince1970) + 605 else { fail("pairing_failed"); return }
            invitationExpiry = invitation.expiresAt
            state = .invitation(invitation)
        case .pending(let device):
            guard case .invitation = state, let expiry = device.expiresAt,
                  let invitationExpiry, expiry <= invitationExpiry else { fail("pairing_failed"); return }
            state = .pending(device)
        case .approved(let device):
            guard case .pending(let expected) = state, decision == "approve", expected.id == device.id,
                  expected.name == device.name else { fail("approval_unavailable"); return }
            state = .approved(device); busy = false; stopTransport()
        case .denied(let device):
            guard case .pending(let expected) = state, decision == "deny", expected.id == device.id else { fail("approval_unavailable"); return }
            state = .denied; busy = false; stopTransport()
        case .expired(let pending):
            state = .expired(pending: pending); busy = false; stopTransport()
        case .error(let code): fail(code); return
        case .cancelled:
            // A local expiry already selected its final view before cancelling stdin.
            if case .expired = state { return }
            close(); return
        }
        tick()
        onChange?()
    }

    func approve() {
        guard case .pending(let device) = state, !busy, valid(device) else { tick(); return }
        busy = true; onChange?()
        let current = generation
        authentication = Task { [weak self] in
            guard let self else { return }
            let accepted = await self.authenticate("allow \(device.name) to view your DuckTerm agents")
            guard self.generation == current, self.state == .pending(device), self.decision == nil else { return }
            self.authentication = nil
            guard self.valid(device) else { self.tick(); return }
            guard accepted, !Task.isCancelled else { self.busy = false; self.onChange?(); return }
            self.decision = "approve"; self.decisionSentAt = self.now()
            do {
                guard let transport = self.transport else { throw DuckyPairingEvent.Failure.invalid }
                try transport.send(action: "approve", deviceID: device.id)
            }
            catch { self.fail("approval_unavailable") }
        }
    }

    func deny() {
        guard case .pending(let device) = state, decision == nil, valid(device) else { tick(); return }
        cancelAuthentication(); authentication?.cancel(); authentication = nil
        decision = "deny"; decisionSentAt = now(); busy = true; onChange?()
        do {
            guard let transport else { throw DuckyPairingEvent.Failure.invalid }
            try transport.send(action: "deny", deviceID: device.id)
        }
        catch { fail("approval_unavailable") }
    }

    func tick() {
        // Once an explicit decision was sent while valid, await its authoritative
        // result. A committed approval may arrive just after the local countdown.
        if let decisionSentAt, case .pending = state {
            if now().timeIntervalSince(decisionSentAt) > 5 { fail("approval_unavailable") }
            return
        }
        let expiry: Int
        let pending: Bool
        switch state {
        case .opening:
            if let startedAt, now().timeIntervalSince(startedAt) > 45 { fail("listener_unavailable") }
            return
        case .invitation(let invite): expiry = invite.expiresAt; pending = false
        case .pending(let device): expiry = device.expiresAt ?? 0; pending = true
        default: return
        }
        if now().timeIntervalSince1970 >= Double(expiry) {
            state = .expired(pending: pending); busy = false; stopTransport(); onChange?()
        }
    }

    func close() {
        generation += 1; state = .closed; busy = false
        stopTransport(); onChange?()
    }
    private func valid(_ device: DuckyPairingDevice) -> Bool {
        now().timeIntervalSince1970 < Double(device.expiresAt ?? 0)
    }
    private func fail(_ code: String) {
        state = .error(code); busy = false; stopTransport(); onChange?()
    }
    private func stopTransport() {
        cancelAuthentication(); authentication?.cancel(); authentication = nil
        transport?.onEvent = nil; transport?.cancel(); transport = nil
    }
}
