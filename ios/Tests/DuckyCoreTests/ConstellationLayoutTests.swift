import CoreGraphics
import XCTest
@testable import DuckyCore

private func session(_ id: String, _ folder: String, _ state: String = "idle", name: String? = nil) -> AgentStatus {
    try! JSONDecoder().decode(AgentStatus.self, from: JSONSerialization.data(withJSONObject: [
        "id": id, "name": name ?? id, "folder": folder, "state": state, "runtime": "codex",
        "observed_at": 1, "approval_visibility": "terminal_only"]))
}

/// The design-review sample: 23 live sessions, 7 top-level folders, 3 of them empty.
private func sampleSessions() -> [AgentStatus] {
    let core = ["main-dev", "ui-dev", "perf-dev", "security", "main-qa", "evals-dev"]
    let features = ["duckterm-lite", "connectors-dev", "duckterm-byok", "agent-auth", "checkpoints",
                    "session-memory", "workstreams", "artifacts", "pr-review"]
    let states = ["ui-dev": "busy", "main-qa": "terminated", "connectors-dev": "waiting", "duckterm-byok": "waiting", "checkpoints": "busy"]
    return core.map { session("core-" + $0, "DuckTerm/Core", states[$0] ?? "idle", name: $0) }
        + features.map { session($0, "DuckTerm/Features", states[$0] ?? "idle") }
        + [session("release-dev", "DuckTerm", "waiting"), session("product", "DuckTerm"), session("architect", "DuckTerm"),
           session("job-search", "Bodhbytes", "waiting"), session("bb-ui-dev", "Bodhbytes", name: "ui-dev"), session("main", "Bodhbytes"),
           session("kickoff", "ARK"), session("utsava", "")]
}
private let sampleFolders = ["DuckTerm", "DuckTerm/Core", "DuckTerm/Features", "Bodhbytes", "ARK", "Agent Olympics", "Desktop productivity", "Bodhbit"]
private func f(_ path: String) -> String { ConstellationTree.folderID(path) }
private func a(_ id: String) -> String { ConstellationTree.agentID(id) }

/// Landscape stages after safe areas and the 44pt top bar.
private let stages: [(String, CGSize)] = [
    ("667x375", CGSize(width: 635, height: 323)), ("812x375", CGSize(width: 724, height: 310)),
    ("852x393", CGSize(width: 734, height: 328)), ("932x430", CGSize(width: 814, height: 365)),
]

private struct Scenario { let name: String; let nav: ConstellationNav; let agent: String?; let panels: Bool }
private func scenarios(_ tree: ConstellationTree) -> [Scenario] {
    var focus = ConstellationNav(); focus.focus(f("DuckTerm"), in: tree)
    var nested = ConstellationNav(); nested.focus(f("DuckTerm/Features"), in: tree)
    var ask = focus; ask.asking = true
    var oracle = ConstellationNav(); oracle.mode = .oracle
    return [.init(name: "map", nav: .init(), agent: nil, panels: false), .init(name: "focus", nav: focus, agent: nil, panels: false),
            .init(name: "nested", nav: nested, agent: nil, panels: false), .init(name: "agent", nav: nested, agent: "duckterm-lite", panels: true),
            .init(name: "ask", nav: ask, agent: nil, panels: true), .init(name: "oracle", nav: oracle, agent: nil, panels: true)]
}

private func layout(_ tree: ConstellationTree, _ s: Scenario, _ stage: CGSize) -> ConstellationLayout {
    ConstellationLayout(tree: tree, nav: s.nav, selectedAgent: s.agent,
                        map: ConstellationFrame(stage: stage, panelsOpen: s.panels, composing: false).map)
}

/// Hit areas as the view draws them: folders are circles, agents 44pt squares.
private enum Hit { case circle(CGPoint, Double), square(CGRect) }
private func hits(_ l: ConstellationLayout, _ tree: ConstellationTree, ids: [String]) -> [(String, Hit)] {
    ids.compactMap { id in
        guard let p = l.placements[id], p.interactive, p.alpha > 0 else { return nil }
        let t = p.target
        if tree.nodes[id]?.isFolder == true && p.role != .ancestor { return (id, .circle(CGPoint(x: p.x, y: p.y), t / 2)) }
        return (id, .square(CGRect(x: p.x - t / 2, y: p.y - t / 2, width: t, height: t)))
    }
}
private func overlap(_ a: Hit, _ b: Hit) -> Bool {
    switch (a, b) {
    case let (.circle(p, r), .circle(q, s)): return hypot(p.x - q.x, p.y - q.y) < r + s - 0.5
    case let (.square(x), .square(y)): return x.insetBy(dx: 0.5, dy: 0.5).intersects(y)
    case let (.circle(p, r), .square(x)), let (.square(x), .circle(p, r)):
        let nx = min(max(p.x, x.minX), x.maxX), ny = min(max(p.y, x.minY), x.maxY)
        return hypot(p.x - nx, p.y - ny) < r - 0.5
    }
}

final class ConstellationTreeTests: XCTestCase {
    func testCountsComeFromDescendantsWithoutDoubleCounting() {
        let tree = ConstellationTree(sessions: sampleSessions(), folders: sampleFolders)
        XCTAssertEqual(tree.roots.map { tree.nodes[$0]!.name },
                       ["Ungrouped", "Agent Olympics", "ARK", "Bodhbit", "Bodhbytes", "Desktop productivity", "DuckTerm"])
        let duckterm = tree.nodes[f("DuckTerm")]!
        XCTAssertEqual([duckterm.total, duckterm.waiting, duckterm.busy, duckterm.ended], [18, 3, 2, 1])
        XCTAssertEqual(duckterm.children, [f("DuckTerm/Core"), f("DuckTerm/Features"), a("architect"), a("product"), a("release-dev")])
        XCTAssertEqual(tree.roots.reduce(0) { $0 + tree.nodes[$1]!.total }, 23)
        XCTAssertEqual(tree.nodes[f("Agent Olympics")]!.total, 0)
        XCTAssertEqual(tree.ancestors(of: a("duckterm-lite")), [f("DuckTerm"), f("DuckTerm/Features")])
    }

    func testOverviewCountsRealFoldersAtEveryDepthAndNotUngrouped() {
        let tree = ConstellationTree(sessions: sampleSessions() + [session("deep", "DuckTerm/Core/Perf")],
                                     folders: sampleFolders + ["DuckTerm/Archive", "DuckTerm/Archive/2025"])
        // DuckTerm, Core, Perf, Features, Archive, 2025, Bodhbytes, ARK, Agent Olympics, Desktop productivity, Bodhbit.
        XCTAssertEqual(tree.folderCount, 11)
        XCTAssertNotNil(tree.nodes[f("")], "Ungrouped exists but is not counted as a folder")
        XCTAssertEqual(tree.emptyFolderCount, 5, "Archive, 2025 and the three empty top-level folders")
        XCTAssertEqual(tree.roots.reduce(0) { $0 + tree.nodes[$1]!.total }, 24, "sessions still summed once, from the roots")
    }

    func testArchivedDroppedEndedCountedAndUngroupedOnlyWithSessions() {
        let tree = ConstellationTree(sessions: [session("x", "Work", "archived"), session("y", "Work", "stopped"), session("z", "Deep/A/B")],
                                     folders: ["Empty"])
        XCTAssertNil(tree.nodes[a("x")])
        XCTAssertEqual(tree.nodes[f("Work")]!.ended, 1)
        XCTAssertNil(tree.nodes[f("")], "no Ungrouped without ungrouped sessions")
        XCTAssertEqual(tree.nodes[f("Deep/A")]!.children, [f("Deep/A/B")], "missing intermediate folders are synthesised")
        XCTAssertEqual(tree.nodes[f("Deep")]!.total, 1)
        XCTAssertEqual(tree.roots, [f("Deep"), f("Empty"), f("Work")])
    }
}

final class ConstellationLayoutTests: XCTestCase {
    let tree = ConstellationTree(sessions: sampleSessions(), folders: sampleFolders)

    func testEveryPlacementStaysInsideItsMapAndParentAtEveryPhoneSize() {
        for (size, stage) in stages {
            for s in scenarios(tree) {
                let l = layout(tree, s, stage)
                let map = l.map.insetBy(dx: -1, dy: -1)
                for (id, p) in l.placements where p.alpha > 0 {
                    let tag = "\(size) \(s.name) \(id)"
                    switch p.role {
                    case .top, .peripheral, .focus, .oracle:
                        XCTAssertTrue(map.contains(CGRect(x: p.x - p.r, y: p.y - p.r, width: 2 * p.r, height: 2 * p.r)), tag)
                    case .ancestor:
                        // An ancestor rim may run past the top and bottom, never the sides.
                        XCTAssertTrue(p.x - p.r >= map.minX && p.x + p.r <= map.maxX && map.contains(CGPoint(x: p.x, y: p.y)), tag)
                    case .child, .context, .content:
                        let parent = l.placements[tree.nodes[id]!.parentID!]!
                        XCTAssertLessThanOrEqual(hypot(p.x - parent.x, p.y - parent.y) + p.r, parent.r + 0.5, tag)
                        if p.interactive { XCTAssertTrue(map.contains(CGPoint(x: p.x, y: p.y)), tag + " reachable on screen") }
                    case .hidden: XCTFail(tag + " visible but hidden")
                    }
                }
            }
        }
    }

    func testTargetsAreAtLeast44UnlessPanelsCrowdTheMapAndNeverOverlap() {
        for (size, stage) in stages {
            for s in scenarios(tree) {
                let l = layout(tree, s, stage)
                // With both panels open on a phone narrower than 932, the map is context and may fall back to 30pt targets.
                let roomy = !s.panels || stage.width >= 800
                XCTAssertEqual(l.dense, !roomy && l.dense, "\(size) \(s.name) should fit without crowding")
                var groups: [String: [String]] = [:]
                for (id, p) in l.placements where p.interactive && p.alpha > 0 {
                    XCTAssertGreaterThanOrEqual(p.target, roomy ? 44 : 30, "\(size) \(s.name) \(id)")
                    let key = p.role == .top || p.role == .peripheral ? "top" : tree.nodes[id]?.parentID ?? "top"
                    groups[key, default: []].append(id)
                }
                for (_, ids) in groups {
                    let areas = hits(l, tree, ids: ids.sorted())
                    for i in areas.indices { for j in areas.indices where j > i {
                        XCTAssertFalse(overlap(areas[i].1, areas[j].1), "\(size) \(s.name): \(areas[i].0) overlaps \(areas[j].0)")
                    } }
                }
            }
        }
    }

    func testNoNameOverlapsAnotherNameOrAnyDotOrBubble() {
        for (size, stage) in stages {
            for s in scenarios(tree) {
                let l = layout(tree, s, stage)
                let ids = tree.order + [ConstellationLayout.oracleID]
                let named = ids.compactMap { id in
                    l.labelFrame(id, name: tree.nodes[id]?.name ?? "Oracle", tree: tree, nav: s.nav).map { (id, $0.insetBy(dx: 1, dy: 1)) }
                }
                for i in named.indices { for j in named.indices where j > i {
                    XCTAssertFalse(named[i].1.intersects(named[j].1), "\(size) \(s.name): \(named[i].0) name overlaps \(named[j].0) name")
                } }
                for (id, box) in named {
                    let mine = [id] + tree.ancestors(of: id)
                    for other in ids where !mine.contains(other) {
                        guard let p = l.placements[other], p.alpha > 0.05, p.r > 0.5, p.role != .hidden else { continue }
                        // A folder title may sit inside a bubble that encloses it; only foreign shapes count.
                        if p.role == .focus || p.role == .ancestor, hypot(box.midX - p.x, box.midY - p.y) < p.r { continue }
                        let nx = min(max(p.x, box.minX), box.maxX), ny = min(max(p.y, box.minY), box.maxY)
                        XCTAssertGreaterThanOrEqual(hypot(p.x - nx, p.y - ny), p.r, "\(size) \(s.name): \(id) name overlaps \(other)")
                    }
                }
            }
        }
    }

    func testNineFeatureAgentsAreAllNamedWhenTheFolderHasRoom() {
        var nav = ConstellationNav(); nav.focus(f("DuckTerm/Features"), in: tree)
        for (size, stage) in stages {
            let l = ConstellationLayout(tree: tree, nav: nav, selectedAgent: nil, map: ConstellationFrame(stage: stage, panelsOpen: false, composing: false).map)
            let agents = tree.nodes[f("DuckTerm/Features")]!.children
            XCTAssertEqual(agents.count, 9)
            XCTAssertTrue(agents.allSatisfy { l.placements[$0]!.role == .child && l.placements[$0]!.labelled }, size)
            XCTAssertEqual(l.placements[f("DuckTerm")]!.role, .ancestor)
            XCTAssertEqual(l.placements[f("DuckTerm/Core")]!.role, .context, "the sibling stays reachable on the ancestor rim")
            XCTAssertEqual(l.placements[f("Bodhbytes")]!.role, .peripheral)
        }
    }

    func testSelectedAgentIsAlwaysNamedEvenWhenPanelsCrowdTheMap() {
        let s = scenarios(tree).first { $0.name == "agent" }!
        for (size, stage) in stages {
            let p = layout(tree, s, stage).placements[a("duckterm-lite")]!
            XCTAssertTrue(p.labelled && p.role == .child, size)
        }
    }

    func testFocusKeepsSiblingsOnTheirWholeMapSide() {
        let stage = stages[2].1
        let home = layout(tree, scenarios(tree)[0], stage), focused = layout(tree, scenarios(tree)[1], stage)
        let centre = focused.placements[f("DuckTerm")]!.x
        for id in tree.roots where id != f("DuckTerm") {
            let wasLeft = home.placements[id]!.x < home.placements[f("DuckTerm")]!.x
            XCTAssertEqual(focused.placements[id]!.x < centre, wasLeft, id)
        }
        XCTAssertEqual(focused.placements[a("duckterm-lite")]!.alpha, 1, "descendants still show as dots inside Features")
        XCTAssertEqual(focused.placements[a("duckterm-lite")]!.role, .content)
    }

    func testLayoutIsDeterministicAcrossRefreshOrder() {
        let shuffled = ConstellationTree(sessions: sampleSessions().reversed(), folders: sampleFolders.reversed())
        for s in scenarios(tree) {
            XCTAssertEqual(layout(tree, s, stages[2].1), layout(shuffled, s, stages[2].1), s.name)
        }
    }

    func testFortyLongNamedTopFoldersAllStayReachable() {
        let sessions = (0..<40).flatMap { i in (0..<(i % 6)).map { session("s\(i)-\($0)", "A very long folder name for project number \(i)") } }
        let big = ConstellationTree(sessions: sessions, folders: (0..<40).map { "A very long folder name for project number \($0)" })
        for (size, stage) in stages {
            let map = ConstellationFrame(stage: stage, panelsOpen: false, composing: false).map
            let start = Date()
            let l = ConstellationLayout(tree: big, nav: .init(), selectedAgent: nil, map: map)
            XCTAssertLessThan(Date().timeIntervalSince(start), 1.0, "\(size) layout time")
            XCTAssertEqual(big.roots.filter { l.placements[$0]!.alpha > 0 && l.placements[$0]!.interactive }.count, 40, size)
            for id in big.roots { XCTAssertTrue(map.insetBy(dx: -1, dy: -1).contains(CGPoint(x: l.placements[id]!.x, y: l.placements[id]!.y)), id) }
            var nav = ConstellationNav(); nav.focus(big.roots[5], in: big)
            let focused = ConstellationLayout(tree: big, nav: nav, selectedAgent: nil, map: map)
            XCTAssertEqual(big.roots.filter { focused.placements[$0]!.interactive }.count, 39, "\(size): all siblings tappable at the edges")
        }
    }

    func testFortyAgentsInOneFolderAreAllPlacedInside() {
        let crowd = ConstellationTree(sessions: (0..<40).map { session("agent-\($0)", "Crowd") }, folders: [])
        var nav = ConstellationNav(); nav.focus(f("Crowd"), in: crowd)
        let l = ConstellationLayout(tree: crowd, nav: nav, selectedAgent: "agent-7", map: ConstellationFrame(stage: stages[0].1, panelsOpen: true, composing: false).map)
        let folder = l.placements[f("Crowd")]!
        for id in crowd.nodes[f("Crowd")]!.children {
            let p = l.placements[id]!
            XCTAssertEqual(p.role, .child)
            XCTAssertLessThanOrEqual(hypot(p.x - folder.x, p.y - folder.y) + p.r, folder.r + 0.5, id)
        }
        XCTAssertTrue(l.dense, "the view offers the list when a folder cannot fit 44pt targets")
        XCTAssertTrue(l.placements[a("agent-7")]!.labelled)
    }
}

final class ConstellationNavTests: XCTestCase {
    func testReconcileNeverSwapsInAnotherSession() {
        let tree = ConstellationTree(sessions: sampleSessions(), folders: sampleFolders)
        var nav = ConstellationNav(); nav.focus(f("DuckTerm/Features"), in: tree)
        let gone = ConstellationTree(sessions: sampleSessions().filter { $0.id != "duckterm-lite" }, folders: sampleFolders)
        XCTAssertNil(nav.reconcile(with: gone, selectedSession: "duckterm-lite"))
        XCTAssertEqual(nav.path, [f("DuckTerm"), f("DuckTerm/Features")], "folder focus survives the deleted agent")

        let moved = ConstellationTree(sessions: sampleSessions().map { $0.id == "duckterm-lite" ? session("duckterm-lite", "Bodhbytes") : $0 }, folders: sampleFolders)
        XCTAssertEqual(nav.reconcile(with: moved, selectedSession: "duckterm-lite"), "duckterm-lite")
        XCTAssertEqual(nav.path, [f("Bodhbytes")], "the map follows a moved agent")
    }

    func testReconcileTrimsRemovedOrReparentedFolders() {
        let tree = ConstellationTree(sessions: sampleSessions(), folders: sampleFolders)
        var nav = ConstellationNav(); nav.focus(f("DuckTerm/Features"), in: tree); nav.asking = true
        let noFeatures = ConstellationTree(sessions: sampleSessions().filter { !$0.folder.hasSuffix("Features") },
                                           folders: sampleFolders.filter { !$0.hasSuffix("Features") })
        XCTAssertNil(nav.reconcile(with: noFeatures, selectedSession: nil))
        XCTAssertEqual(nav.path, [f("DuckTerm")])
        XCTAssertTrue(nav.asking, "asking stays on the surviving folder")
        let empty = ConstellationTree(sessions: [], folders: [])
        _ = nav.reconcile(with: empty, selectedSession: nil)
        XCTAssertEqual(nav.path, [])
        XCTAssertFalse(nav.asking)
    }
}

final class ConstellationMotionTests: XCTestCase {
    func testBlendEndsExactlyAtTargetAndGrowsNewNodesFromNothing() {
        let tree = ConstellationTree(sessions: sampleSessions(), folders: sampleFolders)
        let map = ConstellationFrame(stage: stages[2].1, panelsOpen: false, composing: false).map
        let from = ConstellationLayout(tree: tree, nav: .init(), selectedAgent: nil, map: map)
        var nav = ConstellationNav(); nav.focus(f("DuckTerm"), in: tree)
        let to = ConstellationLayout(tree: tree, nav: nav, selectedAgent: nil, map: map)
        XCTAssertEqual(from.blended(toward: to, progress: 1), to, "zero-duration (Reduce Motion) lands on the target")
        XCTAssertEqual(from.blended(toward: to, progress: 0).placements[f("DuckTerm")]!.r, from.placements[f("DuckTerm")]!.r)
        let mid = from.blended(toward: to, progress: 0.5).placements[f("DuckTerm")]!
        XCTAssertGreaterThan(mid.r, from.placements[f("DuckTerm")]!.r)
        XCTAssertLessThan(mid.r, to.placements[f("DuckTerm")]!.r)
        let grown = ConstellationLayout().blended(toward: to, progress: 0.5).placements[f("DuckTerm")]!
        XCTAssertLessThan(grown.r, to.placements[f("DuckTerm")]!.r)
    }

    func testReadOnlyOverviewKeepsFactsAndDropsTheChatColumn() {
        for (size, stage) in stages {
            let frame = ConstellationFrame(stage: stage, panelsOpen: true, composing: false, chat: false)
            XCTAssertEqual(frame.right, 0, size)
            XCTAssertGreaterThanOrEqual(frame.left, 136, "\(size): the overview facts stay even on small phones")
            XCTAssertGreaterThan(frame.map.width, ConstellationFrame(stage: stage, panelsOpen: true, composing: false).map.width, size)
        }
    }

    func testKeyboardGivesTheChatPriority() {
        let typing = ConstellationFrame(stage: CGSize(width: 734, height: 328), panelsOpen: true, composing: true)
        XCTAssertEqual(typing.left, 0)
        XCTAssertTrue(typing.showsMap)
        let short = ConstellationFrame(stage: CGSize(width: 734, height: 150), panelsOpen: true, composing: true)
        XCTAssertEqual(short.right, 734)
        XCTAssertFalse(short.showsMap)
    }
}

final class ConstellationBoundsTests: XCTestCase {
    func testDepthBoundaryKeepsNormalHierarchyAndFlattensBeforeExpansion() {
        let path = Array(repeating: "a", count: 32).joined(separator: "/")
        let normal = ConstellationTree(sessions: [session("normal", path)], folders: [])
        XCTAssertFalse(normal.requiresListFallback)
        XCTAssertEqual(normal.folderCount, 32)
        XCTAssertEqual(normal.ancestors(of: a("normal")).count, 32)

        let deep = Array(repeating: "a", count: 3000).joined(separator: "/")
        let value = session("same-agent", deep, "waiting")
        let bounded = ConstellationTree(sessions: [value], folders: ["Explicit empty", deep])
        XCTAssertTrue(bounded.requiresListFallback)
        XCTAssertEqual(bounded.nodes.count, 3, "Only actual folders plus the original agent survive")
        XCTAssertEqual(bounded.nodes[f(deep)]?.folderPath, deep)
        XCTAssertEqual(bounded.nodes[a("same-agent")]?.agent, value)
        XCTAssertEqual(bounded.ancestors(of: a("same-agent")), [f(deep)])
        XCTAssertEqual(bounded.nodes[f(deep)]?.waiting, 1)
        XCTAssertNil(bounded.nodes[f("a")], "Do not synthesize thousands of prefixes")
    }

    func testExpandedNodeBudgetFallsBackWithoutLosingActualFolders() {
        func path(_ i: Int) -> String { (["root-\(i)"] + Array(repeating: "a", count: 31)).joined(separator: "/") }
        let paths = (0..<129).map(path)
        let boundary = ConstellationTree(sessions: [], folders: Array(paths.prefix(128)))
        XCTAssertFalse(boundary.requiresListFallback)
        XCTAssertEqual(boundary.nodes.count, 4096)
        let bounded = ConstellationTree(sessions: [], folders: paths)
        XCTAssertTrue(bounded.requiresListFallback)
        XCTAssertEqual(Set(bounded.roots), Set(paths.map(f)))
        XCTAssertEqual(bounded.nodes.count, paths.count)
        XCTAssertEqual(bounded.folderCount, paths.count)
    }

    func testFlatFallbackPreservesProtocolMaximumActualSessionsAndFolders() {
        let long = String(repeating: "a/", count: 40)
        let sessions = (0..<1000).map { session("agent-\($0)", long + "session-\($0)", $0 % 2 == 0 ? "busy" : "terminated") }
        let folders = (0..<1000).map { "Empty-\($0)" }
        let tree = ConstellationTree(sessions: sessions, folders: folders)
        XCTAssertTrue(tree.requiresListFallback)
        XCTAssertEqual(tree.nodes.count, 3000)
        XCTAssertEqual(tree.roots.count, 2000)
        XCTAssertEqual(tree.order.count, 3000)
        XCTAssertEqual(tree.roots.reduce(0) { $0 + tree.nodes[$1]!.total }, 1000)
        XCTAssertEqual(tree.roots.reduce(0) { $0 + tree.nodes[$1]!.busy }, 500)
        XCTAssertEqual(tree.roots.reduce(0) { $0 + tree.nodes[$1]!.ended }, 500)
        XCTAssertEqual(Set(tree.nodes.values.compactMap { $0.agent?.id }), Set(sessions.map(\.id)))
        XCTAssertTrue(folders.allSatisfy { tree.nodes[f($0)] != nil })
    }

    func testOversizeHierarchySkipsPackingAndKeepsOracleFacts() {
        let deep = Array(repeating: "a", count: 3000).joined(separator: "/")
        let tree = ConstellationTree(sessions: [session("agent", deep)], folders: ["Empty", deep])
        let map = CGRect(x: 8, y: 2, width: 640, height: 300)
        let forbidden: (String) -> Double = { _ in XCTFail("Fallback must not enter measured packing"); return 0 }
        let layout = ConstellationLayout(tree: tree, nav: .init(), selectedAgent: "agent", map: map, measure: forbidden)
        XCTAssertTrue(layout.dense)
        XCTAssertTrue(layout.placements.isEmpty)
        var oracle = ConstellationNav(); oracle.mode = .oracle
        let overview = ConstellationLayout(tree: tree, nav: oracle, selectedAgent: nil, map: map, measure: forbidden)
        XCTAssertEqual(Set(overview.placements.keys), [ConstellationLayout.oracleID])
        XCTAssertEqual(tree.folderCount, 2)
        XCTAssertEqual(tree.emptyFolderCount, 1)
    }

    func testUTF8LabelAndPathBoundsNeverAlterRoutingIdentity() {
        let path = String(repeating: "p", count: 1024)
        XCTAssertFalse(ConstellationTree(sessions: [], folders: [path]).requiresListFallback)
        XCTAssertTrue(ConstellationTree(sessions: [], folders: [path + "p"]).requiresListFallback)
        let name = "a" + String(repeating: "🦆", count: 10000)
        let original = session("id-" + name, path + "p", name: name)
        let tree = ConstellationTree(sessions: [original], folders: [])
        let projected = tree.nodes[a(original.id)]!
        XCTAssertLessThanOrEqual(projected.name.utf8.count, 256)
        XCTAssertFalse(projected.name.contains("�"))
        XCTAssertEqual(projected.agent, original)
        XCTAssertEqual(tree.nodes[f(original.folder)]?.folderPath, original.folder)
        XCTAssertEqual(ConstellationTree.displayLabel("Regular name"), "Regular name")
    }

    func testProjectionReusesUnchangedContentAndDropsOldHostState() {
        var projection = ConstellationProjection()
        let old = session("same-id", "Old folder", "waiting")
        XCTAssertTrue(projection.update(sessions: [old], folders: ["Empty"]))
        let initial = projection.tree
        for _ in 0..<120 {
            XCTAssertFalse(projection.update(sessions: [old], folders: ["Empty"]))
            XCTAssertEqual(projection.tree, initial)
        }
        let changed = session("same-id", "New folder", "busy")
        XCTAssertTrue(projection.update(sessions: [changed], folders: []))
        XCTAssertNil(projection.tree.nodes[f("Old folder")])
        XCTAssertEqual(projection.tree.nodes[a("same-id")]?.agent, changed)
        XCTAssertTrue(projection.update(sessions: [], folders: []))
        XCTAssertTrue(projection.tree.nodes.isEmpty)
    }

    func testFallbackNavigationKeepsOriginalAgentAndFolderIdentity() {
        let deep = Array(repeating: "a", count: 3000).joined(separator: "/")
        let tree = ConstellationTree(sessions: [session("first", deep), session("second", "Other")], folders: [])
        var nav = ConstellationNav(); nav.focus(f(deep), in: tree)
        XCTAssertEqual(nav.path, [f(deep)])
        XCTAssertEqual(nav.reconcile(with: tree, selectedSession: "first"), "first")
        XCTAssertEqual(nav.path, [f(deep)])
        let gone = ConstellationTree(sessions: [session("second", "Other")], folders: [deep])
        XCTAssertNil(nav.reconcile(with: gone, selectedSession: "first"))
        XCTAssertEqual(nav.path, [f(deep)], "Never replace a deleted selection with another agent")
    }
}
