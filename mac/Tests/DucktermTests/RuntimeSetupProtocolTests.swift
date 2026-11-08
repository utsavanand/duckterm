import Foundation
import XCTest
@testable import Duckterm

final class RuntimeSetupProtocolTests: XCTestCase {
    private let ready = #"{"protocol":1,"type":"ready","runtime":"codex","action":"login"}"#
    private let exited = #"{"protocol":1,"type":"exited","exit_code":0,"cancelled":false}"#
    private let cancelled = #"{"protocol":1,"type":"error","reason":"cancelled"}"#
    private let status = #"{"protocol":1,"type":"status","runtime":"codex","installed":true,"version":"0.155.1","probe":"ok","authentication":"signed_out","credential_method":"unknown","environment_api_key_configured":false}"#

    private func decode(_ text: String) throws -> RuntimeSetupEvent { try .decode(Data(text.utf8)) }
    private func line(_ text: String) -> Data { Data((text + "\n").utf8) }
    private func rejected(_ text: String, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertThrowsError(try decode(text), file: file, line: line) { error in
            XCTAssertTrue(error is RuntimeSetupProtocolError, file: file, line: line)
            XCTAssertFalse(String(describing: error).contains("SECRET"), file: file, line: line)
        }
    }

    func testStatusKeepsInstalledAuthenticationAndEnvironmentCredentialsSeparate() throws {
        guard case let .status(value) = try decode(status) else { return XCTFail("Expected status") }
        XCTAssertTrue(value.installed)
        XCTAssertEqual(value.version, "0.155.1")
        XCTAssertEqual(value.authentication, .signedOut)
        XCTAssertEqual(value.credentialMethod, .unknown)
        XCTAssertFalse(value.environmentAPIKeyConfigured)
        let withKey = status.replacingOccurrences(of: #""environment_api_key_configured":false"#, with: #""environment_api_key_configured":true"#)
        guard case let .status(keyValue) = try decode(withKey) else { return XCTFail("Expected status") }
        XCTAssertTrue(keyValue.environmentAPIKeyConfigured)
        XCTAssertEqual(keyValue.authentication, .signedOut, "A configured key is not proof of account sign-in")
    }

    func testAllSafeAuthMethodsAndUnknownProbeStatusRemainTyped() throws {
        for auth in ["not_checked", "signed_in", "signed_out", "unknown"] {
            for method in ["unknown", "account", "api_key"] {
                let text = status.replacingOccurrences(of: "signed_out", with: auth)
                    .replacingOccurrences(of: #""credential_method":"unknown""#, with: #""credential_method":"\#(method)""#)
                guard case let .status(value) = try decode(text) else { return XCTFail("Expected status") }
                XCTAssertEqual(value.authentication.rawValue, auth)
                XCTAssertEqual(value.credentialMethod.rawValue, method)
            }
        }
        for probe in ["missing", "unrecognized", "timeout", "failed"] {
            let text = status.replacingOccurrences(of: #""version":"0.155.1""#, with: #""version":null"#)
                .replacingOccurrences(of: #""probe":"ok""#, with: #""probe":"\#(probe)""#)
                .replacingOccurrences(of: "signed_out", with: "unknown")
                .dropLast() + #","reason":"auth_status_unavailable"}"#
            guard case let .status(value) = try decode(String(text)) else { return XCTFail("Expected status") }
            XCTAssertNil(value.version)
            XCTAssertEqual(value.reason, .authStatusUnavailable)
        }
    }

    func testOnlySanitizedVersionTokensAreAccepted() throws {
        for version in ["1.2.3", "0.155.1-beta", "9999.9999.9999-alpha.9999", "1.2.3-rc.1"] {
            XCTAssertNoThrow(try decode(status.replacingOccurrences(of: "0.155.1", with: version)))
        }
        for version in ["SECRET", "1.2", "v1.2.3", "1.2.3+SECRET", "1.2.3\\nSECRET", "10000.1.2", "1.2.3-SECRET"] {
            rejected(status.replacingOccurrences(of: "0.155.1", with: version))
        }
    }

    func testExactKeysTypesAndEnumsRejectUnexpectedProviderInformation() {
        for text in [status.dropLast() + #","email":"SECRET"}"#,
                     status.dropLast() + #","reason":null}"#,
                     status.replacingOccurrences(of: "signed_out", with: "logged_in"),
                     status.replacingOccurrences(of: #""installed":true"#, with: #""installed":1"#),
                     status.replacingOccurrences(of: #""version":"0.155.1""#, with: #""version":155"#),
                     status.replacingOccurrences(of: #""environment_api_key_configured":false"#, with: #""environment_api_key_configured":"false""#),
                     ready.replacingOccurrences(of: "codex", with: "custom"),
                     ready.replacingOccurrences(of: "login", with: "status"),
                     ready.replacingOccurrences(of: #""protocol":1"#, with: #""protocol":true"#),
                     ready.replacingOccurrences(of: #""protocol":1"#, with: #""protocol":1.0"#),
                     ready.replacingOccurrences(of: #""protocol":1"#, with: #""protocol":1e0"#),
                     ready.replacingOccurrences(of: #""protocol":1,"#, with: ""),
                     #"{"protocol":2,"type":"error","reason":"cancelled"}"#,
                     #"{"protocol":1,"type":"error","reason":"SECRET"}"#,
                     #"{"protocol":1,"type":"error","reason":{"SECRET":true}}"#,
                     #"{"protocol":1,"type":"error","reason":["cancelled"]}"#] {
            rejected(String(text))
        }
    }

    func testDuplicateKeysIncludingEscapedSpellingsAreRejected() {
        rejected(#"{"protocol":2,"protocol":1,"type":"error","reason":"cancelled"}"#)
        rejected(#"{"protocol":1,"type":"output","data":"","\u0064ata":"U0VDUkVU"}"#)
        rejected(#"{"protocol":1,"type":"error","reason":"SECRET","reason":"cancelled"}"#)
    }

    func testMalformedAndNestedJSONCannotEscapeFixedErrors() {
        for text in ["", "{}", "[]", "null", "true", ready + "{}", ready + "SECRET", ready.dropLast() + ",}",
                     #"{"protocol":1,"type":"error","reason":"cancelled\q"}"#,
                     #"{"protocol":1,"type":"error","reason":"cancelled\uD800"}"#,
                     String(repeating: "[", count: 20_000)] { rejected(String(text)) }
        var invalidUTF8 = Data(#"{"protocol":1,"type":"error","reason":""#.utf8)
        invalidUTF8.append(0xff); invalidUTF8.append(contentsOf: Data(#""}"#.utf8))
        XCTAssertThrowsError(try RuntimeSetupEvent.decode(invalidUTF8))
    }

    func testBinaryTerminalOutputRemainsDataAndRequiresCanonicalBase64() throws {
        let bytes = Data([0, 255, 27, 10, 13, 0xc3, 0xa9])
        XCTAssertEqual(try decode(#"{"protocol":1,"type":"output","data":"\#(bytes.base64EncodedString())"}"#), .output(bytes))
        for encoded in ["?", "Zg", "Zh==", "Zg==\\n", "Zg== SECRET", "===="] {
            rejected(#"{"protocol":1,"type":"output","data":"\#(encoded)"}"#)
        }
        let excessive = Data(repeating: 0, count: 32_769).base64EncodedString()
        rejected(#"{"protocol":1,"type":"output","data":"\#(excessive)"}"#)
    }

    func testPartialAndCoalescedReadsPreserveEventOrder() throws {
        var frames = RuntimeSetupFrames(runtime: .codex, action: .login)
        let wire = line(ready) + line(#"{"protocol":1,"type":"output","data":"aGk="}"#) + line(exited)
        var received: [RuntimeSetupEvent] = []
        for byte in wire { received += try frames.append(Data([byte])) }
        XCTAssertEqual(received, [.ready(runtime: .codex, action: .login), .output(Data("hi".utf8)), .exited(code: 0, cancelled: false)])
        XCTAssertTrue(frames.complete)
        XCTAssertNoThrow(try frames.finish())
        var coalesced = RuntimeSetupFrames(runtime: .codex, action: .login)
        XCTAssertEqual(try coalesced.append(wire), received)
    }

    func testStreamingDoesNotAccumulateAWholeTerminalTranscript() throws {
        var frames = RuntimeSetupFrames(runtime: .codex, action: .install)
        _ = try frames.append(line(ready.replacingOccurrences(of: "login", with: "install")))
        let chunk = Data(repeating: 65, count: 32_768)
        let wire = line(#"{"protocol":1,"type":"output","data":"\#(chunk.base64EncodedString())"}"#)
        for _ in 0..<12 { XCTAssertEqual(try frames.append(wire), [.output(chunk)]) }
        XCTAssertFalse(frames.complete)
        _ = try frames.append(line(exited)); try frames.finish()
    }

    func testLineAndReadBoundsIncludeNewlineAndCannotRecoverAfterFailure() throws {
        let padded = cancelled + String(repeating: " ", count: 65_535 - cancelled.utf8.count)
        var exact = RuntimeSetupFrames(runtime: .codex, action: .login)
        XCTAssertEqual(try exact.append(line(padded)), [.error(.cancelled)])
        XCTAssertThrowsError(try decode(padded + " "))
        var partial = RuntimeSetupFrames(runtime: .codex, action: .login)
        XCTAssertEqual(try partial.append(Data(repeating: 32, count: 65_535)), [])
        XCTAssertThrowsError(try partial.append(Data([32])))
        XCTAssertThrowsError(try partial.append(line(cancelled)))
        var excessive = RuntimeSetupFrames(runtime: .codex, action: .login)
        XCTAssertThrowsError(try excessive.append(Data(repeating: 32, count: 65_537)))
    }

    func testExpectedRuntimeActionAndProtocolOrderAreEnforced() throws {
        for wire in [line(ready.replacingOccurrences(of: "codex", with: "claude-code")),
                     line(ready.replacingOccurrences(of: "login", with: "install")), line(status), line(exited),
                     line(#"{"protocol":1,"type":"output","data":""}"#), line(ready) + line(ready)] {
            var frames = RuntimeSetupFrames(runtime: .codex, action: .login)
            XCTAssertThrowsError(try frames.append(wire))
        }
        var statusFrames = RuntimeSetupFrames(runtime: .codex, action: .status)
        XCTAssertEqual(try statusFrames.append(line(status)), [try decode(status)])
        XCTAssertTrue(statusFrames.complete); try statusFrames.finish()
        XCTAssertThrowsError(try statusFrames.append(line(cancelled)))
        var wrongStatus = RuntimeSetupFrames(runtime: .claudeCode, action: .status)
        XCTAssertThrowsError(try wrongStatus.append(line(status)))
    }

    func testPrematureEOFAndTrailingTerminalBytesAreNotSuccess() throws {
        for wire in [Data(), Data(ready.utf8), line(ready), line(ready) + Data(exited.utf8)] {
            var frames = RuntimeSetupFrames(runtime: .codex, action: .login)
            _ = try frames.append(wire)
            XCTAssertFalse(frames.complete)
            XCTAssertThrowsError(try frames.finish())
        }
        for wire in [line(cancelled) + Data([32]), line(status) + line(status), Data([10])] {
            var frames = RuntimeSetupFrames(runtime: .codex, action: .status)
            XCTAssertThrowsError(try frames.append(wire))
        }
    }

    func testFixedErrorsCanTerminateBeforeReadyOrAfterOutput() throws {
        for reason in ["invalid_request", "invalid_executable", "configuration_unavailable", "busy", "not_installed",
                       "invalid_command", "cancelled", "timeout", "output_limit", "cleanup_failed", "transport_unavailable", "setup_unavailable"] {
            let wire = line(#"{"protocol":1,"type":"error","reason":"\#(reason)"}"#)
            var frames = RuntimeSetupFrames(runtime: .codex, action: .login)
            XCTAssertEqual(try frames.append(wire), [.error(RuntimeSetupFailureReason(rawValue: reason)!)])
            try frames.finish()
        }
        var frames = RuntimeSetupFrames(runtime: .codex, action: .login)
        _ = try frames.append(line(ready)); _ = try frames.append(line(cancelled))
        XCTAssertTrue(frames.complete)
    }

    func testExitStatusIsAnIntegerAndCancellationUsesItsOwnErrorFrame() throws {
        XCTAssertEqual(try decode(exited.replacingOccurrences(of: #""exit_code":0"#, with: #""exit_code":-9"#)), .exited(code: -9, cancelled: false))
        for code in ["false", "0.0", "2147483648", "\"0\""] { rejected(exited.replacingOccurrences(of: #""exit_code":0"#, with: #""exit_code":\#(code)"#)) }
        rejected(exited.replacingOccurrences(of: "false", with: "true"))
    }

    func testInputEncoderPreservesBinaryBytesWithoutAddingControlFields() throws {
        let bytes = Data((0...255).map(UInt8.init))
        let data = try RuntimeSetupCommand.input(bytes).encode()
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: String])
        XCTAssertEqual(object, ["type": "input", "data": bytes.base64EncodedString()])
        XCTAssertEqual(data.last, 10)
        XCTAssertEqual(data.filter { $0 == 10 }.count, 1)
        XCTAssertNoThrow(try RuntimeSetupCommand.input(Data(repeating: 0, count: 32_768)).encode())
        XCTAssertThrowsError(try RuntimeSetupCommand.input(Data(repeating: 0, count: 32_769)).encode())
    }

    func testResizeAndCancelEncoderHaveOnlyFixedFieldsAndBoundedGeometry() throws {
        XCTAssertEqual(String(decoding: try RuntimeSetupCommand.cancel.encode(), as: UTF8.self), "{\"type\":\"cancel\"}\n")
        for (columns, rows) in [(1, 1), (80, 24), (500, 300)] {
            let data = try RuntimeSetupCommand.resize(columns: columns, rows: rows).encode()
            let object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
            XCTAssertEqual(Set(object.keys), ["type", "cols", "rows"])
            XCTAssertEqual(object["cols"] as? Int, columns)
            XCTAssertEqual(object["rows"] as? Int, rows)
        }
        for (columns, rows) in [(0, 24), (501, 24), (80, 0), (80, 301), (Int.max, 1)] {
            XCTAssertThrowsError(try RuntimeSetupCommand.resize(columns: columns, rows: rows).encode())
        }
    }
}
