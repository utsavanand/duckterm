import CoreGraphics
import Foundation

/// Status colour bucket. Ended covers every finished state; unknown states never borrow a live colour.
public enum ConstellationStatus: Equatable {
    case busy, waiting, idle, ended, unknown
    public init(state: String) {
        switch state {
        case "busy": self = .busy
        case "waiting": self = .waiting
        case "idle": self = .idle
        case "terminated", "stopped", "interrupted": self = .ended
        default: self = .unknown
        }
    }
}

public struct ConstellationNode: Identifiable, Equatable {
    public enum Kind: Equatable { case folder(path: String), agent(AgentStatus) }
    public let id: String
    public let name: String
    public let kind: Kind
    public let parentID: String?
    public let depth: Int
    public internal(set) var children: [String] = []
    public internal(set) var total = 0
    public internal(set) var waiting = 0
    public internal(set) var busy = 0
    public internal(set) var ended = 0
    public var isFolder: Bool { if case .folder = kind { return true } else { return false } }
    public var agent: AgentStatus? { if case .agent(let a) = kind { return a } else { return nil } }
    public var folderPath: String? { if case .folder(let p) = kind { return p } else { return nil } }
}

/// Folder hierarchy for the map. Follows SessionTree's policy: archived and merged
/// sessions are dropped, ended ones count, explicit empty folders stay, and
/// Ungrouped exists only while it holds sessions.
public struct ConstellationTree: Equatable {
    public static let maximumHierarchyDepth = 32
    public static let maximumExpandedNodes = 4096
    public static let maximumPathBytes = 1024
    public static let maximumLabelBytes = 256
    public static func folderID(_ path: String) -> String { "folder:" + path }
    public static func agentID(_ id: String) -> String { "session:" + id }
    /// Only the presentation is shortened. IDs, folder paths and agents remain unchanged.
    public static func displayLabel(_ value: String) -> String {
        var bytes = Array(value.utf8.prefix(maximumLabelBytes))
        while !bytes.isEmpty {
            if let text = String(bytes: bytes, encoding: .utf8) { return text }
            bytes.removeLast() // At most three incomplete trailing UTF-8 bytes.
        }
        return ""
    }
    public let nodes: [String: ConstellationNode]
    public let roots: [String]
    /// Parents before children, siblings in display order.
    public let order: [String]
    /// Oversize hierarchies retain actual folders/sessions in the existing list,
    /// without synthesizing ancestors or running the map's packing search.
    public let requiresListFallback: Bool

    public init(sessions: [AgentStatus], folders: [String]) {
        let kept = sessions.filter { !["archived", "merged"].contains($0.state) }
        var paths = Set(folders.filter { !$0.isEmpty })
        for s in kept where !s.folder.isEmpty { paths.insert(s.folder) }
        let actualPaths = paths
        let hasUngrouped = kept.contains { $0.folder.isEmpty }
        let remaining = Self.maximumExpandedNodes - kept.count - (hasUngrouped ? 1 : 0)
        var flat = paths.count > remaining || paths.contains { path in
            // Check before split/prefix expansion: even a small response can
            // contain thousands of slash-separated components.
            path.utf8.count > Self.maximumPathBytes ||
                path.utf8.lazy.filter { $0 == 47 }.prefix(Self.maximumHierarchyDepth).count >= Self.maximumHierarchyDepth
        }
        if !flat {
            expansion: for path in actualPaths {
                let pieces = path.split(separator: "/", omittingEmptySubsequences: false)
                if pieces.count > 1 {
                    for i in 1..<pieces.count {
                        paths.insert(pieces.prefix(i).joined(separator: "/"))
                        if paths.count > remaining { flat = true; break expansion }
                    }
                }
            }
        }
        if flat { paths = actualPaths }
        requiresListFallback = flat
        func parentPath(_ path: String) -> String? { path.lastIndex(of: "/").map { String(path[..<$0]) } }
        func byName(_ a: String, _ b: String) -> Bool {
            let c = a.localizedCaseInsensitiveCompare(b)
            return c == .orderedSame ? a < b : c == .orderedAscending
        }
        var nodes: [String: ConstellationNode] = [:]
        let allPaths = paths.union(hasUngrouped ? [""] : [])
        for path in allPaths {
            let depth = flat || path.isEmpty ? 1 : path.split(separator: "/", omittingEmptySubsequences: false).count
            let name = path.isEmpty ? "Ungrouped" : flat ? path : String(path.split(separator: "/", omittingEmptySubsequences: false).last ?? "")
            nodes[Self.folderID(path)] = .init(id: Self.folderID(path), name: Self.displayLabel(name), kind: .folder(path: path),
                                               parentID: flat ? nil : parentPath(path).map(Self.folderID), depth: depth)
        }
        for s in kept {
            let folder = Self.folderID(s.folder)
            nodes[Self.agentID(s.id)] = .init(id: Self.agentID(s.id), name: Self.displayLabel(s.name), kind: .agent(s),
                                              parentID: folder, depth: (nodes[folder]?.depth ?? 0) + 1)
            var cursor: String? = folder
            let status = ConstellationStatus(state: s.state)
            while let id = cursor, var node = nodes[id] {
                node.total += 1
                if status == .waiting { node.waiting += 1 }
                if status == .busy { node.busy += 1 }
                if status == .ended { node.ended += 1 }
                nodes[id] = node
                cursor = node.parentID
            }
        }
        var childFolders: [String: [String]] = [:], childAgents: [String: [AgentStatus]] = [:]
        for path in allPaths where !flat && !path.isEmpty {
            if let parent = parentPath(path) { childFolders[parent, default: []].append(path) }
        }
        for s in kept { childAgents[s.folder, default: []].append(s) }
        for path in allPaths {
            let folders = (childFolders[path] ?? []).sorted(by: byName).map(Self.folderID)
            let agents = (childAgents[path] ?? []).sorted {
                let c = $0.name.localizedCaseInsensitiveCompare($1.name)
                return c == .orderedSame ? $0.id < $1.id : c == .orderedAscending
            }.map { Self.agentID($0.id) }
            nodes[Self.folderID(path)]?.children = folders + agents
        }
        let tops = allPaths.filter { !$0.isEmpty && (flat || !$0.contains("/")) }.sorted(by: byName)
        roots = (hasUngrouped ? [Self.folderID("")] : []) + tops.map(Self.folderID)
        var order: [String] = [], queue = roots
        while !queue.isEmpty {
            let id = queue.removeFirst()
            order.append(id)
            queue += nodes[id]?.children ?? []
        }
        self.nodes = nodes
        self.order = order
    }

    /// Real folders at every depth; Ungrouped is a bucket, not a folder.
    public var folderCount: Int { nodes.values.filter { ($0.folderPath ?? "").isEmpty == false }.count }
    public var emptyFolderCount: Int { nodes.values.filter { ($0.folderPath ?? "").isEmpty == false && $0.total == 0 }.count }

    /// Folder ids from the top level down to the node's parent.
    public func ancestors(of id: String) -> [String] {
        var out: [String] = [], cursor = nodes[id]?.parentID
        while let c = cursor, let node = nodes[c] { out.insert(c, at: 0); cursor = node.parentID }
        return out
    }
}

/// Rebuild only when snapshot content changes, never from an animation frame.
public struct ConstellationProjection {
    private var sessions: [AgentStatus] = []
    private var folders: [String] = []
    public private(set) var tree = ConstellationTree(sessions: [], folders: [])
    public init() {}
    @discardableResult public mutating func update(sessions: [AgentStatus], folders: [String]) -> Bool {
        guard sessions != self.sessions || folders != self.folders else { return false }
        self.sessions = sessions; self.folders = folders
        tree = ConstellationTree(sessions: sessions, folders: folders)
        return true
    }
}

/// Map navigation that survives refreshes. Agent selection itself lives in SessionModel.
public struct ConstellationNav: Equatable {
    public enum Mode: Equatable { case map, oracle }
    public var mode: Mode = .map
    /// Folder ids, outermost first. The last one is the current focus.
    public var path: [String] = []
    public var asking = false
    public init() {}

    public mutating func focus(_ folderID: String, in tree: ConstellationTree) {
        mode = .map; asking = false
        path = tree.ancestors(of: folderID) + [folderID]
    }

    /// Repairs state after a refresh. Returns the agent id still safe to keep selected:
    /// a deleted agent is dropped (never swapped for another), a moved one pulls the path along.
    public mutating func reconcile(with tree: ConstellationTree, selectedSession: String?) -> String? {
        // Keep the longest prefix that is still a real parent chain from the top level.
        var kept: [String] = []
        for id in path {
            guard let node = tree.nodes[id], node.isFolder, node.parentID == kept.last else { break }
            kept.append(id)
        }
        path = kept
        if asking && path.isEmpty { asking = false }
        guard let session = selectedSession else { return nil }
        let id = ConstellationTree.agentID(session)
        guard tree.nodes[id] != nil else { return nil }
        let wanted = tree.ancestors(of: id)
        if path != wanted && mode == .map { path = wanted }
        return session
    }
}

/// Splits the stage between the side panels and the map.
public struct ConstellationFrame: Equatable {
    public let left: CGFloat
    public let right: CGFloat
    public let map: CGRect
    public var showsMap: Bool { map.width >= 120 && map.height >= 120 }

    /// `chat` false is a read-only overview: the facts panel stays, no conversation column.
    public init(stage: CGSize, panelsOpen: Bool, composing: Bool, chat: Bool = true) {
        let w = stage.width, h = stage.height
        // Below 700pt the details fold into a strip above the chat so the map keeps a usable width.
        var l: CGFloat = w < 700 ? 0 : w < 860 ? 162 : 170
        var r: CGFloat = w < 700 ? 280 : w < 860 ? 292 : 320
        if composing { l = 0; r = (w * (w < 700 ? 0.6 : 0.55)).rounded() }
        // A landscape keyboard can leave too little height for a map; the chat takes everything.
        if composing && h < 170 { r = w }
        if !chat { l = max(l, 136); r = 0 }
        if !panelsOpen { l = 0; r = 0 }
        left = l; right = r
        map = CGRect(x: l + 8, y: 2, width: max(0, w - l - r - 16), height: max(0, h - 6))
    }
}

public struct ConstellationPlacement: Equatable {
    public enum Role: Equatable { case top, peripheral, focus, ancestor, child, content, hidden, oracle }
    public var x: Double
    public var y: Double
    public var r: Double
    public var alpha: Double
    public var role: Role
    /// Whether the name is drawn without selection or focus.
    public var labelled: Bool
    /// Side of the hit target. 44pt unless the folder is too crowded, and never overlapping a sibling's.
    public var hit: Double = 44
    public var interactive: Bool {
        switch role { case .hidden, .content, .focus, .oracle: return false; default: return true }
    }
    public var target: Double { role == .ancestor || role == .focus ? 44 : max(hit, r * 2) }
}

public struct ConstellationLayout: Equatable {
    public static let oracleID = "oracle"
    public static let duration = 0.56
    public var placements: [String: ConstellationPlacement] = [:]
    public var map: CGRect = .zero
    /// True when something could not be placed without crowding; the view offers the list.
    public var dense = false

    /// Rough width of an 11pt name; the app passes a measured one.
    public static func approxLabelWidth(_ text: String) -> Double { min(110, Double(ConstellationTree.displayLabel(text).count) * 5.9) + 4 }

    /// Folder names carry a count line ("12 sessions", "3 need you") that can be wider than the name.
    public static func folderLabelWidth(_ name: String, measure: (String) -> Double) -> Double { max(measure(name), measure("00 need you")) }

    public init() {}

    public init(tree: ConstellationTree, nav: ConstellationNav, selectedAgent: String?, map: CGRect,
                measure: @escaping (String) -> Double = ConstellationLayout.approxLabelWidth) {
        if tree.requiresListFallback {
            self.map = map
            dense = true
            if nav.mode == .oracle {
                placements[Self.oracleID] = .init(x: Double(map.midX), y: Double(map.midY), r: 15,
                                                 alpha: 1, role: .oracle, labelled: true)
            }
            return
        }
        var b = Builder(tree: tree, nav: nav, map: map, measure: measure)
        b.build()
        placements = b.out
        self.map = map
        dense = b.dense
        if let id = selectedAgent.map(ConstellationTree.agentID), var p = placements[id], p.role != .hidden {
            p.labelled = true; placements[id] = p
        }
    }

    /// Where a node's visible name sits, in the same coordinates as the placements, or nil when it has none.
    /// The focused folder's title sits inside the top of its rim. Ancestors have no map title: their
    /// crescent is too thin to hold one cleanly and the breadcrumb already names them as buttons.
    /// Other names go below their node, or above or beside it when that would touch another node.
    public func labelFrame(_ id: String, name: String, tree: ConstellationTree, nav: ConstellationNav,
                           measure: (String) -> Double = ConstellationLayout.approxLabelWidth) -> CGRect? {
        guard let p = placements[id], p.alpha > 0.05 else { return nil }
        let isFolder = tree.nodes[id]?.isFolder ?? false
        var width: Double, height: Double, cx: Double, top: Double
        switch p.role {
        case .focus:
            // Sized from the ring, not the label measure: the title uses a larger font than node labels.
            width = min(Double(map.width) - 16, 2 * p.r + 40); height = 18; cx = p.x; top = p.y - p.r + 11
        case .ancestor:
            return nil
        case .oracle:
            width = measure("All folders") + 6; height = 28; cx = p.x; top = p.y + p.r + 3
        default:
            guard p.labelled, p.role != .hidden, p.role != .content else { return nil }
            let counted = isFolder && (p.role == .top || p.role == .child)
            width = min(110, counted ? Self.folderLabelWidth(name, measure: measure) : measure(name)) + 6
            height = counted ? 28 : 15
            cx = p.x; top = p.y + p.r + (isFolder ? 3 : 4)
        }
        func clamped(_ cx: Double, _ top: Double) -> CGRect {
            let x = min(max(cx, Double(map.minX) + width / 2), Double(map.maxX) - width / 2)
            return CGRect(x: x - width / 2, y: max(Double(map.minY) + 2, top), width: width, height: height)
        }
        guard p.role != .focus, p.role != .oracle else { return clamped(cx, top) }
        let enclosing = Set(tree.ancestors(of: id))
        func clear(_ box: CGRect) -> Bool {
            placements.allSatisfy { other, q in
                guard other != id, !enclosing.contains(other), q.alpha > 0.05, q.r > 0.5, q.role != .hidden else { return true }
                let nx = min(max(q.x, Double(box.minX)), Double(box.maxX)), ny = min(max(q.y, Double(box.minY)), Double(box.maxY))
                return hypot(q.x - nx, q.y - ny) >= q.r + 1
            }
        }
        let gap = p.r + 4
        let options = [clamped(cx, top), clamped(cx, p.y - gap - height),
                       clamped(p.x + gap + width / 2, p.y - height / 2), clamped(p.x - gap - width / 2, p.y - height / 2)]
        return options.first(where: clear) ?? options[0]
    }

    /// Eased blend toward `target`. Shared by the canvas and the hit targets so they never disagree.
    public func blended(toward target: ConstellationLayout, progress: Double) -> ConstellationLayout {
        let t = progress >= 1 ? 1 : 1 - pow(1 - max(0, progress), 3)
        if t >= 1 { return target }
        var result = target
        for (id, to) in target.placements {
            let from = placements[id] ?? ConstellationPlacement(x: to.x, y: to.y, r: 0, alpha: 0, role: .hidden, labelled: false)
            var p = to
            p.x = from.x + (to.x - from.x) * t; p.y = from.y + (to.y - from.y) * t
            p.r = from.r + (to.r - from.r) * t; p.alpha = from.alpha + (to.alpha - from.alpha) * t
            result.placements[id] = p
        }
        for (id, from) in placements where target.placements[id] == nil {
            var p = from; p.alpha = from.alpha * (1 - t); p.role = .hidden
            result.placements[id] = p
        }
        return result
    }
}

// MARK: - Geometry

/// Collision outline in absolute coordinates: true circles for bubbles and dots, rectangles for labels and square targets.
private enum Shape {
    case circle(x: Double, y: Double, r: Double)
    case rect(minX: Double, minY: Double, maxX: Double, maxY: Double)

    var minY: Double { switch self { case .circle(_, let y, let r): return y - r; case .rect(_, let y, _, _): return y } }
    var bounds: (Double, Double, Double, Double) {
        switch self {
        case let .circle(x, y, r): return (x - r, y - r, x + r, y + r)
        case let .rect(a, b, c, d): return (a, b, c, d)
        }
    }
    func overlaps(_ o: Shape, gap: Double) -> Bool {
        switch (self, o) {
        case let (.circle(x1, y1, r1), .circle(x2, y2, r2)):
            return hypot(x1 - x2, y1 - y2) < r1 + r2 + gap
        case let (.rect(a, b, c, d), .rect(e, f, g, h)):
            return a < g + gap && e < c + gap && b < h + gap && f < d + gap
        case let (.circle(x, y, r), .rect(a, b, c, d)), let (.rect(a, b, c, d), .circle(x, y, r)):
            let nx = min(max(x, a), c), ny = min(max(y, b), d)
            return hypot(x - nx, y - ny) < r + gap
        }
    }
    func inside(circleX cx: Double, _ cy: Double, _ limit: Double) -> Bool {
        switch self {
        case let .circle(x, y, r): return hypot(x - cx, y - cy) + r <= limit
        case let .rect(a, b, c, d): return [(a, b), (c, b), (a, d), (c, d)].allSatisfy { hypot($0.0 - cx, $0.1 - cy) <= limit }
        }
    }
}

private struct Item {
    let id: String
    var r: Double
    /// Outline relative to the item's centre.
    var parts: [Shape]
    func shapes(_ x: Double, _ y: Double) -> [Shape] {
        parts.map {
            switch $0 {
            case let .circle(dx, dy, r): return .circle(x: x + dx, y: y + dy, r: r)
            case let .rect(a, b, c, d): return .rect(minX: x + a, minY: y + b, maxX: x + c, maxY: y + d)
            }
        }
    }
    /// A bubble, its hit circle, and (optionally) the name underneath.
    static func folder(_ id: String, r: Double, minTarget: Double, label: Double?) -> Item {
        var parts: [Shape] = [.circle(x: 0, y: 0, r: max(r, minTarget / 2))]
        if let label { parts.append(.rect(minX: -label / 2, minY: r + 1, maxX: label / 2, maxY: r + 31)) }
        return Item(id: id, r: r, parts: parts)
    }
    /// A dot with a square hit target and (optionally) the name underneath.
    static func agent(_ id: String, r: Double, minTarget: Double, label: Double?) -> Item {
        let half = max(minTarget / 2, r + 1.5)
        var parts: [Shape] = [minTarget > 0 ? .rect(minX: -half, minY: -half, maxX: half, maxY: half) : .circle(x: 0, y: 0, r: half)]
        if let label { parts.append(.rect(minX: -label / 2, minY: r + 3, maxX: label / 2, maxY: r + 20)) }
        return Item(id: id, r: r, parts: parts)
    }
}

private struct Builder {
    let tree: ConstellationTree
    let nav: ConstellationNav
    let map: CGRect
    let measure: (String) -> Double
    var out: [String: ConstellationPlacement] = [:]
    var dense = false

    init(tree: ConstellationTree, nav: ConstellationNav, map: CGRect, measure: @escaping (String) -> Double) {
        self.tree = tree; self.nav = nav; self.map = map; self.measure = measure
    }

    static let nestRatio = 0.86
    private func node(_ id: String) -> ConstellationNode? { tree.nodes[id] }

    func topRadius(_ n: ConstellationNode) -> Double {
        n.total > 0 ? min(14 + 17 * Double(n.total).squareRoot(), Double(map.height) * 0.29) : 15
    }

    mutating func set(_ id: String, _ x: Double, _ y: Double, _ r: Double, _ role: ConstellationPlacement.Role, labelled: Bool, hit: Double = 44) {
        let empty = node(id).map { $0.isFolder && $0.total == 0 } ?? false
        out[id] = .init(x: x, y: y, r: r, alpha: empty ? 0.5 : 1, role: role, labelled: labelled, hit: hit)
    }

    mutating func build() {
        guard map.width > 0, map.height > 0 else { return hideRest() }
        if nav.mode == .oracle {
            let cx = Double(map.midX), cy = Double(map.midY)
            out[ConstellationLayout.oracleID] = .init(x: cx, y: cy, r: 15, alpha: 1, role: .oracle, labelled: true)
            let obstacle = Item.folder(ConstellationLayout.oracleID, r: 15, minTarget: 44, label: 90)
            let scale = max(0.5, min(1, Double(map.width) / 700))
            packTop(scale: scale, obstacle: (obstacle, cx, cy))
        } else if nav.path.isEmpty {
            packTop(scale: 1, obstacle: nil)
        } else {
            focusTop()
        }
        hideRest()
    }

    /// Whole-map placement of the top-level folders: biggest first, each as close as
    /// it can get to a stable golden-spiral anchor derived from its name order.
    mutating func packTop(scale: Double, obstacle: (Item, Double, Double)?) {
        let roots = tree.roots
        let w = Double(map.width), h = Double(map.height), x0 = Double(map.minX), y0 = Double(map.minY)
        // Biggest first, nearest the centre; ties keep name order so a refresh does not reshuffle.
        let sequence = roots.enumerated().sorted { (topRadius(node($0.1)!), -$0.0) > (topRadius(node($1.1)!), -$1.0) }.map(\.1)
        let anchors: [String: (Double, Double)] = Dictionary(uniqueKeysWithValues: sequence.enumerated().map { i, id in
            let a = Double(i) * 2.399963 + 0.6, d = (Double(i) + 0.5) / Double(max(1, roots.count))
            return (id, (x0 + w / 2 + cos(a) * d.squareRoot() * w * 0.4, y0 + h / 2 + sin(a) * d.squareRoot() * h * 0.36))
        })
        // Start from a shrink that roughly fits by area, so crowded maps do not search from full size every time.
        let need = sequence.reduce(0.0) { sum, id in
            let r = topRadius(node(id)!) * scale
            return sum + (2 * r + 8) * (2 * r + 34) + 40 * 30
        }
        var shrink = min(1, (0.6 * w * h / max(1, need)).squareRoot()), labels = true
        for attempt in 0..<12 {
            var items: [Item] = []
            for id in sequence {
                let n = node(id)!, r = max(7, topRadius(n) * scale * shrink)
                items.append(.folder(id, r: r, minTarget: 44, label: labels ? min(110, ConstellationLayout.folderLabelWidth(n.name, measure: measure)) + 8 : nil))
            }
            var placed: [(Item, Double, Double)] = obstacle.map { [$0] } ?? []
            var ok = true
            for item in items {
                if let p = bestSpot(item, anchor: anchors[item.id]!, placed: placed, step: max(4, min(w, h) / 48), reach: max(w, h), fits: { shape in
                    let b = shape.bounds
                    return b.0 >= x0 && b.1 >= y0 && b.2 <= x0 + w && b.3 <= y0 + h
                }) { placed.append((item, p.0, p.1)) } else { ok = false; break }
            }
            if ok || attempt == 11 {
                if !ok {
                    dense = true
                    for item in items where !placed.contains(where: { $0.0.id == item.id }) {
                        let a = anchors[item.id]!
                        placed.append((item, min(max(a.0, x0 + item.r), x0 + w - item.r), min(max(a.1, y0 + item.r), y0 + h - item.r)))
                    }
                }
                for (item, x, y) in placed where item.id != ConstellationLayout.oracleID {
                    set(item.id, x, y, item.r, .top, labelled: labels)
                    if item.r >= 12 { fill(item.id, x, y, item.r) }
                }
                return
            }
            if shrink > 0.45 { shrink *= 0.88 } else if labels { labels = false; dense = true } else { shrink *= 0.88 }
        }
    }

    /// The focused top-level folder takes the centre; its siblings keep their
    /// whole-map side and vertical order in two edge columns.
    mutating func focusTop() {
        guard let focusID = nav.path.first, node(focusID) != nil else { return }
        var home = Builder(tree: tree, nav: ConstellationNav(), map: map, measure: measure)
        home.build()
        let w = Double(map.width), h = Double(map.height)
        let narrow = w < 520
        // The deepest focus gets the full height; each ancestor is a wider rim around it and may run past the map's top and bottom.
        let focusRadius = max(40, min(h * 0.47, w / 2 - (narrow ? 26 : 110)))
        let radius = min(focusRadius / pow(Self.nestRatio, Double(nav.path.count - 1)), w / 2 - (narrow ? 4 : 60))
        let cx = Double(map.midX), cy = Double(map.midY) + 4
        set(focusID, cx, cy, radius, nav.path.count == 1 ? .focus : .ancestor, labelled: true)
        fill(focusID, cx, cy, radius)
        let fx = home.out[focusID]?.x ?? cx
        let siblings = tree.roots.filter { $0 != focusID }
        var left = siblings.filter { (home.out[$0]?.x ?? 0) < fx }, right = siblings.filter { (home.out[$0]?.x ?? 0) >= fx }
        func byX(_ a: String, _ b: String) -> Bool { (home.out[a]?.x ?? 0, a) < (home.out[b]?.x ?? 0, b) }
        while left.count > right.count + 2 { left.sort(by: byX); right.append(left.removeLast()) }
        while right.count > left.count + 2 { right.sort(by: byX); left.append(right.removeFirst()) }
        for (column, x) in [(left, Double(map.minX) + (narrow ? 12 : 44)), (right, Double(map.maxX) - (narrow ? 12 : 44))] {
            let sorted = column.sorted { (home.out[$0]?.y ?? 0, $0) < (home.out[$1]?.y ?? 0, $1) }
            let spacing = h / Double(max(1, sorted.count))
            // Unnamed edge circles read as clutter; without room for names the breadcrumb is the way back.
            guard !narrow && spacing >= 48 else { continue }
            for (i, id) in sorted.enumerated() {
                let r = node(id)!.total > 0 ? 12.0 : 8.0
                set(id, x, Double(map.minY) + spacing * (Double(i) + 0.5) - 6, min(r, spacing / 3), .peripheral, labelled: true)
            }
        }
    }

    mutating func fill(_ id: String, _ x: Double, _ y: Double, _ rad: Double) {
        guard let n = node(id), !n.children.isEmpty else { return }
        let index = nav.path.firstIndex(of: id)
        let next = index.flatMap { $0 + 1 < nav.path.count ? nav.path[$0 + 1] : nil }
        let deep = index != nil && index == nav.path.count - 1
        if let next, n.children.contains(next) {
            let natural = pack(n, x, y, rad, labelled: false, tappable: false).spots
            let dx = (natural[next]?.x ?? x) - x
            // The child keeps its natural side, leaving a wider crescent opposite for its siblings.
            let side: Double = dx >= 0 ? 1 : -1, cr = Self.nestRatio * rad
            let cx = x + side * (1 - Self.nestRatio) * 0.45 * rad
            set(next, cx, y, cr, next == nav.path.last ? .focus : .ancestor, labelled: true)
            fill(next, cx, y, cr)
            // The crescent is too thin to name the parent's other children, so they stay hidden; the breadcrumb reaches them.
            return
        }
        let (spots, crowded) = pack(n, x, y, rad, labelled: deep, tappable: deep)
        if crowded && deep { dense = true }
        for child in n.children {
            guard let spot = spots[child] else { continue }
            let (cx, cy, r) = (spot.x, spot.y, spot.r)
            set(child, cx, cy, r, deep ? .child : .content, labelled: spot.labelled, hit: spot.hit)
            if node(child)?.isFolder == true && r >= 12 { fill(child, cx, cy, r) }
        }
    }

    /// Children of a folder packed inside its circle. Tries readable labels first,
    /// then bare 44pt targets, then bare dots, and finally flags the folder as dense.
    typealias Spot = (x: Double, y: Double, r: Double, labelled: Bool, hit: Double)
    func pack(_ n: ConstellationNode, _ x: Double, _ y: Double, _ rad: Double, labelled wantLabels: Bool, tappable: Bool)
        -> (spots: [String: Spot], crowded: Bool) {
        let kids = n.children.compactMap { node($0) }
        let folders = kids.filter(\.isFolder), agents = kids.filter { !$0.isFolder }
        let maxCount = Double(max(1, folders.map(\.total).max() ?? 1))
        let crowd = min(1, 1.6 / Double(max(1, folders.count)).squareRoot())
        func folderR(_ f: ConstellationNode) -> Double {
            f.total > 0 ? rad * min(0.42, max(0.14, 0.36 * (Double(f.total) / maxCount).squareRoot() * crowd)) : rad * 0.14 * crowd
        }
        let dotR = tappable ? 4.6 : max(1.5, min(3, rad * 0.04))
        let topBand = tappable ? 30.0 : 0
        let sequence = folders.sorted { (folderR($0), $1.id) > (folderR($1), $0.id) } + agents
        let ordered = folders + agents
        let anchors = Dictionary(uniqueKeysWithValues: ordered.enumerated().map { i, k -> (String, (Double, Double)) in
            if ordered.count == 1 { return (k.id, (x, y + topBand / 3)) }
            let a = Double(i) * 2.399963 - .pi / 2, d = ((Double(i) + 0.5) / Double(ordered.count)).squareRoot() * 0.62 * rad
            return (k.id, (x + cos(a) * d, y + topBand / 3 + sin(a) * d * 0.9))
        })
        // Every name first; then only subfolder names (agents stay named on selection); then smaller
        // targets, only when 44pt ones cannot fit; the view then offers the list too.
        var tiers: [(labels: Bool, folderLabels: Bool, minTarget: Double)] = []
        if wantLabels { tiers += [(true, true, 44), (false, true, 44)] }
        if tappable { tiers += [(false, false, 44), (false, false, 36), (false, false, 30)] } else { tiers.append((false, false, 0)) }
        let limit = rad * 0.94
        if wantLabels && folders.isEmpty, let rows = rows(agents, x, y, limit, top: y - rad + topBand, dotR: dotR) { return (rows, false) }
        for setup in tiers {
            var placed: [(Item, Double, Double)] = []
            var result: [String: Spot] = [:]
            var ok = true
            for k in sequence {
                let r = k.isFolder ? folderR(k) : dotR
                let named = setup.labels || (setup.folderLabels && k.isFolder)
                let label = named ? min(110, k.isFolder ? ConstellationLayout.folderLabelWidth(k.name, measure: measure) : measure(k.name)) + 8 : nil
                let item: Item = k.isFolder ? .folder(k.id, r: r, minTarget: setup.minTarget, label: label)
                    : .agent(k.id, r: r, minTarget: setup.minTarget, label: label)
                let fits: (Shape) -> Bool = { $0.minY >= y - rad + topBand && $0.inside(circleX: x, y, limit) }
                guard let spot = bestSpot(item, anchor: anchors[k.id]!, placed: placed, step: max(2.5, rad / 16), reach: 2 * rad, fits: fits) else { ok = false; break }
                placed.append((item, spot.0, spot.1))
                result[k.id] = (spot.0, spot.1, r, named, setup.minTarget)
            }
            if ok { return (result, tappable && setup.minTarget < 44) }
        }
        // Too many children for this circle: keep every one reachable on a sunflower and let the view offer the list.
        var result: [String: Spot] = [:]
        var points: [(Double, Double)] = []
        for i in ordered.indices {
            let a = Double(i) * 2.399963, d = ((Double(i) + 0.5) / Double(ordered.count)).squareRoot() * 0.8 * rad
            points.append((x + cos(a) * d, y + sin(a) * d))
        }
        // Shrink every target to the closest pair's spacing so none overlap, however small.
        var gap = Double.infinity
        for i in points.indices { for j in points.indices where j > i {
            gap = min(gap, max(abs(points[i].0 - points[j].0), abs(points[i].1 - points[j].1)))
        } }
        let hit = min(44, gap.isFinite ? gap - 1 : 44)
        for (i, k) in ordered.enumerated() {
            let r = k.isFolder ? min(hit / 2, max(4, rad * 0.06)) : max(1.5, min(dotR, rad * 0.04))
            result[k.id] = (points[i].0, points[i].1, r, false, hit)
        }
        return (result, true)
    }

    /// Agents in centred rows, each as wide as its measured name (at least 44pt) and 46pt apart.
    /// Denser than free packing, so more names stay readable.
    func rows(_ agents: [ConstellationNode], _ x: Double, _ y: Double, _ limit: Double, top: Double, dotR: Double) -> [String: Spot]? {
        let pitch = 46.0, widths = agents.map { max(44, measure($0.name) + 8) }
        let usable = (y + limit) - top
        let maxRows = Int(usable / pitch)
        guard maxRows >= 1 else { return nil }
        for count in 1...maxRows {
            let block = Double(count) * pitch
            let first = top + (usable - block) / 2 + 22
            var rowsOut: [[Int]] = Array(repeating: [], count: count), i = 0
            for row in 0..<count where i < agents.count {
                let cy = first + Double(row) * pitch
                // The row must fit at both the target's top and its label's bottom edge.
                let edge = max(abs(cy - 22 - y), abs(cy + 24 - y))
                guard edge < limit else { continue }
                let room = 2 * (limit * limit - edge * edge).squareRoot()
                var used = 0.0
                while i < agents.count && used + widths[i] <= room { used += widths[i]; rowsOut[row].append(i); i += 1 }
            }
            guard i == agents.count else { continue }
            var out: [String: Spot] = [:]
            for (row, members) in rowsOut.enumerated() {
                let cy = first + Double(row) * pitch
                var cx = x - members.reduce(0) { $0 + widths[$1] } / 2
                for m in members { out[agents[m].id] = (cx + widths[m] / 2, cy, dotR, true, 44); cx += widths[m] }
            }
            return out
        }
        return nil
    }

    func bestSpot(_ item: Item, anchor: (Double, Double), placed: [(Item, Double, Double)], step: Double,
                  reach: Double, fits: (Shape) -> Bool) -> (Double, Double)? {
        let others = placed.flatMap { $0.0.shapes($0.1, $0.2) }
        // Rings of candidates around the anchor; the first ring with a valid spot wins.
        let maxRing = Int(reach / step) + 1
        for ring in 0...maxRing {
            let count = max(1, ring * 8)
            for i in 0..<count {
                let a = Double(i) / Double(count) * 2 * .pi
                let px = anchor.0 + cos(a) * Double(ring) * step, py = anchor.1 + sin(a) * Double(ring) * step
                let mine = item.shapes(px, py)
                if mine.allSatisfy(fits) && !mine.contains(where: { m in others.contains { $0.overlaps(m, gap: 4) } }) { return (px, py) }
            }
        }
        return nil
    }

    /// Anything not placed folds into its nearest placed ancestor, invisible, so it can grow out of it later.
    mutating func hideRest() {
        for id in tree.order where out[id] == nil {
            var cursor = node(id)?.parentID, anchor: ConstellationPlacement?
            while let c = cursor, anchor == nil { anchor = out[c]; cursor = node(c)?.parentID }
            let a = anchor ?? .init(x: Double(map.midX), y: Double(map.midY), r: 0, alpha: 0, role: .hidden, labelled: false)
            out[id] = .init(x: a.x, y: a.y, r: 0, alpha: 0, role: .hidden, labelled: false)
        }
        if out[ConstellationLayout.oracleID] == nil {
            out[ConstellationLayout.oracleID] = .init(x: Double(map.maxX) - 30, y: Double(map.minY), r: 0, alpha: 0, role: .hidden, labelled: false)
        }
    }
}
