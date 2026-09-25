import Foundation

@main
struct BugReportDataTests {
    static func main() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("DuckTerm-report-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        func write(_ name: String, _ data: Data) throws -> URL {
            let url = root.appendingPathComponent(name)
            try data.write(to: url)
            return url
        }
        func rejects(_ label: String, _ action: () throws -> Void) {
            do { try action(); fatalError("Expected rejection: \(label)") }
            catch { print("PASS: \(label)") }
        }

        var report = BugReportData()
        let selected = try write("notes.txt", Data("original selected content".utf8))
        try report.add(selected)
        try Data("changed after review".utf8).write(to: selected)
        let exported = root.appendingPathComponent("export")
        let files = try report.write(to: exported, summary: "Bug", details: "Steps", replyTo: "",
                                     screenshot: nil, diagnostics: nil)
        assert(files.map(\.lastPathComponent) == ["report.txt", "attachment-1-notes.txt"])
        let reviewedContent = try String(contentsOf: files[1], encoding: .utf8)
        assert(reviewedContent == "original selected content")
        assert(!FileManager.default.fileExists(atPath: exported.appendingPathComponent("screenshot.png").path))
        assert(!FileManager.default.fileExists(atPath: exported.appendingPathComponent("diagnostics.txt").path))
        for file in files {
            let mode = try FileManager.default.attributesOfItem(atPath: file.path)[.posixPermissions] as! Int
            assert(mode == 0o600)
        }
        let mode = try FileManager.default.attributesOfItem(atPath: exported.path)[.posixPermissions] as! Int
        assert(mode == 0o700)
        print("PASS: reviewed content is immutable; opt-outs omit files; permissions private")

        rejects("blank summary") {
            _ = try report.write(to: root.appendingPathComponent("empty"), summary: " \n", details: "", replyTo: "", screenshot: nil, diagnostics: nil)
        }
        let link = root.appendingPathComponent("link")
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: selected)
        rejects("symlink attachment") { try report.add(link) }
        rejects("directory attachment") { try report.add(root) }
        let large = try write("large.log", Data(repeating: 65, count: BugReportData.maxFileBytes + 1))
        rejects("file over 5 MB") { try report.add(large) }
        var countLimited = BugReportData()
        for _ in 0..<5 { try countLimited.add(selected) }
        rejects("sixth attachment") { try countLimited.add(selected) }
        let chunk = try write("chunk.log", Data(repeating: 66, count: BugReportData.maxFileBytes))
        var totalLimited = BugReportData()
        for _ in 0..<3 { try totalLimited.add(chunk) }
        rejects("total over 15 MB") { try totalLimited.add(selected) }

        var duplicate = BugReportData()
        try duplicate.add(selected); try duplicate.add(selected)
        let full = try duplicate.write(to: root.appendingPathComponent("full"), summary: "Summary", details: "Details", replyTo: "reply@example.invalid",
                                       screenshot: Data([1, 2, 3]), diagnostics: "Fixed diagnostic events only")
        assert(Set(full.map(\.lastPathComponent)).count == 5)
        assert(full.contains { $0.lastPathComponent == "screenshot.png" })
        assert(full.contains { $0.lastPathComponent == "diagnostics.txt" })
        print("PASS: duplicate names preserved; selected screenshot and diagnostics included")
        for i in 0..<110 { AppDiagnostics.shared.record("Fixed event", code: i) }
        let events = AppDiagnostics.shared.text().split(separator: "\n")
        assert(events.count == 100)
        assert(events.first!.hasSuffix("(code 10)"))
        assert(events.last!.hasSuffix("(code 109)"))
        print("PASS: diagnostic ring retains only last 100 events")
        print("All bug-report data tests passed")
    }
}
