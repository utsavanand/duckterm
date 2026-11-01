import Foundation
import Security
public struct CredentialStore {
    private let service = "com.rubberduckhq.ducky.device"
    public init() {}
    private var query: [String: Any] { [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: "paired-host"] }
    public func save(_ host: PairedHost) throws {
        let data = try JSONEncoder().encode(host)
        let status = SecItemUpdate(query as CFDictionary, [kSecValueData as String: data] as CFDictionary)
        if status == errSecSuccess { return }
        guard status == errSecItemNotFound else { throw DuckyError.credentialStorage(status) }
        var item = query
        item[kSecValueData as String] = data
        item[kSecAttrAccessible as String] = kSecAttrAccessibleWhenUnlockedThisDeviceOnly
        let added = SecItemAdd(item as CFDictionary, nil)
        guard added == errSecSuccess else { throw DuckyError.credentialStorage(added) }
    }
    public func load() throws -> PairedHost? {
        var request = query
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var result: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &result)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = result as? Data else { throw DuckyError.credentialStorage(status) }
        return try JSONDecoder().decode(PairedHost.self, from: data)
    }
    public func forget() throws {
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw DuckyError.credentialStorage(status) }
    }
}
