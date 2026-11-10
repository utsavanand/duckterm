import Foundation

/// A folder path is never interpreted by truthiness: Ungrouped and Oracle are distinct.
public enum AnalysisScope: Hashable, Codable {
    case folder(String), ungrouped, oracle
    private enum CodingKeys: String, CodingKey { case kind, path }
    private struct AnyKey: CodingKey {
        let stringValue: String
        let intValue: Int? = nil
        init?(stringValue: String) { self.stringValue = stringValue }
        init?(intValue: Int) { return nil }
    }
    public init(from decoder: Decoder) throws {
        let keys = try decoder.container(keyedBy: AnyKey.self).allKeys.map(\.stringValue)
        guard Set(keys).isSubset(of: ["kind", "path"]) else { throw DuckyError.invalidResponse }
        let c = try decoder.container(keyedBy: CodingKeys.self)
        switch try c.decode(String.self, forKey: .kind) {
        case "folder": self = .folder(try c.decode(String.self, forKey: .path))
        case "ungrouped":
            guard !c.contains(.path) else { throw DuckyError.invalidResponse }; self = .ungrouped
        case "oracle":
            guard !c.contains(.path) else { throw DuckyError.invalidResponse }; self = .oracle
        default: throw DuckyError.invalidResponse
        }
        try validate()
    }
    public func encode(to encoder: Encoder) throws {
        try validate()
        var c = encoder.container(keyedBy: CodingKeys.self)
        switch self {
        case .folder(let path): try c.encode("folder", forKey: .kind); try c.encode(path, forKey: .path)
        case .ungrouped: try c.encode("ungrouped", forKey: .kind)
        case .oracle: try c.encode("oracle", forKey: .kind)
        }
    }
    public func validate() throws {
        if case .folder(let path) = self {
            guard !path.isEmpty, path.utf8.count <= 16384,
                  !path.unicodeScalars.contains(where: { CharacterSet.controlCharacters.contains($0) }) else { throw DuckyError.invalidResponse }
        }
    }
    public var capability: String { self == .oracle ? "analysis:oracle" : "analysis:folder" }
    public var title: String {
        switch self {
        case .folder(let path): return ConstellationTree.displayLabel(path.components(separatedBy: "/").last ?? path)
        case .ungrouped: return "Ungrouped"
        case .oracle: return "Oracle"
        }
    }
}

public struct AnalysisAccess: Decodable, Equatable {
    public let folderQuestions: Bool
    public let oracleQuestions: Bool
    enum CodingKeys: String, CodingKey { case folderQuestions = "folder_questions", oracleQuestions = "oracle_questions" }
}

public struct AnalysisSubmission: Equatable, Encodable {
    public let requestID: String
    public let contextID: String
    public let question: String
    public init(requestID: String, contextID: String, question: String) {
        self.requestID = requestID; self.contextID = contextID; self.question = question
    }
    enum CodingKeys: String, CodingKey { case requestID = "request_id", contextID = "context_id", question }
    public static func validQuestion(_ value: String) -> Bool {
        !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && value.utf8.count <= 4096 &&
        !value.unicodeScalars.contains { CharacterSet.controlCharacters.subtracting(CharacterSet(charactersIn: "\n\t")).contains($0) }
    }
    public static func validID(_ value: String) -> Bool { UUID(uuidString: value)?.uuidString.lowercased() == value }
    public static func validContext(_ value: String) -> Bool {
        !value.isEmpty && value.utf8.count <= 128 && value.unicodeScalars.allSatisfy { (33...126).contains($0.value) }
    }
    public func validate() throws {
        guard Self.validID(requestID), Self.validContext(contextID), Self.validQuestion(question) else { throw DuckyError.invalidResponse }
    }
}

public struct AnalysisReceipt: Decodable, Equatable, Identifiable {
    public enum Status: String, Decodable { case queued, running, complete, failed, cancelled, expired, interrupted }
    public var id: String { requestID }
    public let requestID: String
    public let contextID: String
    public let scope: AnalysisScope
    public let status: Status
    public let reason: String?
    public let question: String
    public let answer: String?
    public let createdAt: Int64
    public let updatedAt: Int64
    public let expiresAt: Int64
    public let capturedAt: Int64?
    public let sourceCount: Int
    public let truncated: Bool
    public var pending: Bool { status == .queued || status == .running }
    enum CodingKeys: String, CodingKey {
        case scope, status, reason, question, answer, truncated
        case requestID = "request_id", contextID = "context_id", createdAt = "created_at", updatedAt = "updated_at"
        case expiresAt = "expires_at", capturedAt = "captured_at", sourceCount = "source_count"
    }
    public func validate(scope expectedScope: AnalysisScope, submission: AnalysisSubmission? = nil) throws {
        try scope.validate()
        guard scope == expectedScope, AnalysisSubmission.validID(requestID), AnalysisSubmission.validContext(contextID),
              AnalysisSubmission.validQuestion(question), createdAt >= 0, updatedAt >= createdAt, expiresAt > createdAt,
              expiresAt - createdAt <= 240_000, (0...40).contains(sourceCount),
              capturedAt.map({ $0 >= createdAt && $0 <= updatedAt }) ?? true,
              reason.map({ $0.utf8.count <= 64 && $0.range(of: "^[a-z][a-z0-9_]*$", options: .regularExpression) != nil }) ?? true,
              status == .complete ? (answer != nil && answer!.utf8.count <= 32768 && reason == nil) : answer == nil else { throw DuckyError.invalidResponse }
        if let submission {
            guard requestID == submission.requestID, contextID == submission.contextID, question == submission.question else { throw DuckyError.invalidResponse }
        }
    }
}

public struct AnalysisContext: Decodable {
    public let version: Int
    public let hostID: String
    public let scope: AnalysisScope
    public let contextID: String
    public let expiresAt: Int64
    public let history: [AnalysisReceipt]
    public let pending: AnalysisReceipt?
    enum CodingKeys: String, CodingKey {
        case version, scope, history, pending
        case hostID = "host_id", contextID = "context_id", expiresAt = "expires_at"
    }
    public func validate(hostID: String, scope: AnalysisScope) throws {
        guard version == 1, self.hostID == hostID, self.scope == scope,
              AnalysisSubmission.validContext(contextID), expiresAt > 0, history.count <= 20,
              Set(history.map(\.requestID)).count == history.count,
              history.reduce(0, { $0 + $1.question.utf8.count + ($1.answer?.utf8.count ?? 0) }) <= 1024 * 1024 else { throw DuckyError.invalidResponse }
        for receipt in history {
            try receipt.validate(scope: scope)
            guard receipt.status == .complete else { throw DuckyError.invalidResponse }
        }
        if let pending {
            try pending.validate(scope: scope)
            guard pending.pending, !history.contains(where: { $0.requestID == pending.requestID }) else { throw DuckyError.invalidResponse }
        }
    }
}

public struct AnalysisResponse: Decodable {
    public let version: Int
    public let hostID: String
    public let receipt: AnalysisReceipt
    enum CodingKeys: String, CodingKey { case version, receipt; case hostID = "host_id" }
    public func validate(hostID: String, scope: AnalysisScope, submission: AnalysisSubmission? = nil, requestID: String? = nil) throws {
        guard version == 1, self.hostID == hostID, requestID.map({ $0 == receipt.requestID }) ?? true else { throw DuckyError.invalidResponse }
        try receipt.validate(scope: scope, submission: submission)
    }
}

public struct AnalysisServiceError: Error {
    public let status: Int
    public let reason: String?
    public static let reasons: Set<String> = ["invalid_scope", "invalid_request", "invalid_request_id", "invalid_context", "invalid_question", "question_too_large", "permission_required", "authorization_changed", "disabled", "configuration_changed", "stale_context", "scope_unavailable", "scope_changed", "request_conflict", "request_not_found", "context_limit", "queue_limit", "rate_limit", "storage_unavailable", "analysis_unavailable"]
    public init(status: Int, reason: String?) { self.status = status; self.reason = reason.flatMap { Self.reasons.contains($0) ? $0 : nil } }
    public var message: String {
        switch reason {
        case "queue_limit", "context_limit": return "Your Mac is answering other questions. Try again in a minute."
        case "rate_limit": return "Too many questions. Try again later."
        case "scope_changed", "stale_context", "scope_unavailable", "configuration_changed", "authorization_changed": return "DuckTerm changed while answering: a session moved or ended."
        default: return "Your Mac couldn’t answer this question."
        }
    }
}
