import XCTest
@testable import Duckterm

final class RemoteHostTests: XCTestCase {
    func testRejectsOptionsAndShellText() {
        for value in ["-oProxyCommand=evil", "host;touch x", "host name", "$(cmd)", "", "host\nother"] {
            XCTAssertThrowsError(try RemoteHost(name: "test", target: value))
        }
        XCTAssertThrowsError(try RemoteHost(name: "test", target: "valid", remotePort: 0))
    }

    func testTunnelVerifiesHostAndHasNoRemoteCommand() throws {
        let host = try RemoteHost(name: "Work", target: "duckterm@server.example")
        let args = host.arguments(port: 14300)
        XCTAssertEqual(args.last, "duckterm@server.example")
        XCTAssertTrue(args.contains("StrictHostKeyChecking=yes"))
        XCTAssertTrue(args.contains("ExitOnForwardFailure=yes"))
        XCTAssertTrue(args.contains("ForwardAgent=no"))
        XCTAssertTrue(args.contains("127.0.0.1:14300:127.0.0.1:4300"))
        XCTAssertTrue(args.contains("-N"))
        XCTAssertTrue(args.contains("BatchMode=yes"))
    }
}
