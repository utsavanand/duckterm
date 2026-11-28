import Foundation
import Security

/// One JSON value in this device's Keychain, never synced or backed up to another device.
struct KeychainItem<Value: Codable> {
    private let service = "com.rubberduckhq.ducky.device"
    let account: String
    private var query: [String: Any] { [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account] }
    func save(_ value: Value) throws {
        let data = try JSONEncoder().encode(value)
        let status = SecItemUpdate(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if status == errSecSuccess { return }
        guard status == errSecItemNotFound else { throw DuckyError.credentialStorage(status) }
        var item = query
        item[kSecValueData as String] = data
        item[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        let added = SecItemAdd(item as CFDictionary, nil)
        guard added == errSecSuccess else { throw DuckyError.credentialStorage(added) }
    }
    func load() throws -> Value? {
        var request = query
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data else { throw DuckyError.credentialStorage(status) }
        return try JSONDecoder().decode(Value.self, from: data)
    }
    func forget() throws {
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw DuckyError.credentialStorage(status) }
    }
}

public struct CredentialStore {
    private let item = KeychainItem<PairedHost>(account: "paired-host")
    public init() {}
    public func save(_ host: PairedHost) throws { try item.save(host) }
    public func load() throws -> PairedHost? { try item.load() }
    public func forget() throws { try item.forget() }
}

public struct AccountStore {
    private let item = KeychainItem<AccountSession>(account: "account-session")
    public init() {}
    public func save(_ session: AccountSession) throws { try item.save(session) }
    public func load() throws -> AccountSession? { try item.load() }
    public func forget() throws { try item.forget() }
}
