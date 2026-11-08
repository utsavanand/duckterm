import Darwin
import XCTest
@testable import Duckterm

final class RuntimeSetupDeliveryTests: XCTestCase {
    @MainActor func testNormalDeliveryWaitsForAcknowledgmentAndPreservesOrder() async {
        var acknowledge: (@MainActor () -> Void)?
        var order: [String] = []
        let delivery = Task {
            await RuntimeSetupDelivery.deliver {
                order.append("enqueued")
                acknowledge = $0
            }
            order.append("delivered")
        }
        await settle { acknowledge != nil }
        XCTAssertEqual(order, ["enqueued"])
        acknowledge?()
        await delivery.value
        XCTAssertEqual(order, ["enqueued", "delivered"])
    }

    @MainActor func testMissingCallbackReturnsWithinTheBoundAndLateCallbacksAreHarmless() async {
        var acknowledge: (@MainActor () -> Void)?
        var finishes = 0
        let started = ContinuousClock.now
        await RuntimeSetupDelivery.deliver { acknowledge = $0 }
        finishes += 1
        let elapsed = started.duration(to: .now)
        XCTAssertGreaterThanOrEqual(elapsed, .milliseconds(900))
        XCTAssertLessThan(elapsed, .seconds(3), "A missing browser callback must not hold helper cleanup")
        acknowledge?()
        acknowledge?()
        await Task.yield()
        XCTAssertEqual(finishes, 1)
    }

    @MainActor func testImmediateAndDuplicateCallbacksCompleteExactlyOnce() async {
        var order: [String] = []
        await RuntimeSetupDelivery.deliver { acknowledge in
            order.append("enqueued")
            acknowledge()
            acknowledge()
            order.append("callback returned")
        }
        order.append("delivered")
        XCTAssertEqual(order, ["enqueued", "callback returned", "delivered"])
    }

    @MainActor func testCancelledCallerStillReleasesMissingBrowserCallback() async {
        var enqueued = false
        let delivery = Task {
            await RuntimeSetupDelivery.deliver { _ in enqueued = true }
        }
        await settle { enqueued }
        delivery.cancel()
        let started = ContinuousClock.now
        await delivery.value
        XCTAssertLessThan(started.duration(to: .now), .seconds(3))
    }

    @MainActor func testMissingBrowserCallbackCannotStrandCancelledHelperCompletion() async throws {
        let root = URL(fileURLWithPath: "/private/tmp").appendingPathComponent("runtime-setup-delivery-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: false,
                                               attributes: [.posixPermissions: 0o700])
        defer { try? FileManager.default.removeItem(at: root) }
        let script = #"""
        printf '%s' "$$" > "$1/child.pid"
        printf '%s\n' '{"protocol":1,"type":"ready","runtime":"codex","action":"login"}'
        printf '%s\n' '{"protocol":1,"type":"output","data":"eA=="}'
        /bin/cat > /dev/null
        : > "$1/cleaned"
        printf '%s\n' '{"protocol":1,"type":"error","reason":"cancelled"}'
        """#
        let process = RuntimeSetupProcess(runtime: .codex, action: .login) {
            .init(executable: URL(fileURLWithPath: "/bin/sh"), arguments: ["-c", script, "delivery-fixture", root.path],
                  environment: ["HOME": root.path, "TMPDIR": root.path, "PATH": "/usr/bin:/bin"])
        }
        defer { process.cancel() }
        var consuming = false, ended = false
        var events: [RuntimeSetupEvent] = []
        process.onEvent = { events.append($0) }
        process.onOutput = { _ in
            consuming = true
            await RuntimeSetupDelivery.deliver { _ in /* Browser never acknowledges. */ }
        }
        process.onEnd = { ended = true }
        try await process.start()
        await settle { consuming }
        process.cancel()
        for _ in 0..<600 {
            if ended { break }
            try? await Task.sleep(nanoseconds: 5_000_000)
        }
        XCTAssertTrue(ended, "Cleanup completion must not depend on a browser response")
        XCTAssertEqual(events.last, .error(.cancelled))
        XCTAssertTrue(FileManager.default.fileExists(atPath: root.appendingPathComponent("cleaned").path))
        let pid = try XCTUnwrap(Int32(String(contentsOf: root.appendingPathComponent("child.pid"), encoding: .utf8)))
        XCTAssertEqual(kill(pid, 0), -1)
        XCTAssertEqual(errno, ESRCH)
    }

    @MainActor private func settle(_ condition: () -> Bool, file: StaticString = #filePath, line: UInt = #line) async {
        for _ in 0..<200 {
            if condition() { return }
            try? await Task.sleep(nanoseconds: 5_000_000)
        }
        XCTFail("Private delivery fixture did not reach its callback", file: file, line: line)
    }
}
