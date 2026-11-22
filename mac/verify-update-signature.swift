import CryptoKit
import Foundation

// Public inputs only. Verify the archive against the key actually embedded in
// the app, so publishing with a different Keychain account cannot strand users.
guard CommandLine.arguments.count == 4,
      let keyData = Data(base64Encoded: CommandLine.arguments[1]),
      let signature = Data(base64Encoded: CommandLine.arguments[2]) else { exit(2) }
let key = try Curve25519.Signing.PublicKey(rawRepresentation: keyData)
let data = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[3]), options: .mappedIfSafe)
guard key.isValidSignature(signature, for: data) else { exit(1) }
