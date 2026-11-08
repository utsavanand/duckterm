import DuckyCore
import SwiftUI
import UIKit

private enum Sky {
    static let background = Color(red: 0.063, green: 0.071, blue: 0.082)
    static let panel = Color(red: 0.075, green: 0.090, blue: 0.106)
    static let border = Color.white.opacity(0.06)
    static let text = Color(red: 0.831, green: 0.843, blue: 0.863)
    static let muted = Color(red: 0.588, green: 0.616, blue: 0.655)
    static let faint = Color(red: 0.42, green: 0.45, blue: 0.49)
    static let mint = Color(red: 0.510, green: 0.780, blue: 0.675)
    static let amber = Color(red: 0.875, green: 0.702, blue: 0.396)
    static let blue = Color(red: 0.573, green: 0.616, blue: 0.890)
    static let grey = Color(red: 0.424, green: 0.455, blue: 0.490)
    static let rim = Color(red: 0.667, green: 0.784, blue: 0.729)
    static let oracle = Color(red: 0.667, green: 0.714, blue: 0.941)

    static func status(_ s: ConstellationStatus) -> Color {
        switch s { case .busy: return blue; case .waiting: return amber; case .idle: return mint; case .ended, .unknown: return grey }
    }
}

private let labelFont = UIFont.systemFont(ofSize: 11)
/// Stable per-id phase; String.hashValue changes every launch.
private func phase(_ id: String) -> Double { Double(id.unicodeScalars.prefix(256).reduce(7) { ($0 &* 31 &+ Int($1.value)) % 6283 }) / 1000 }
private func measureLabel(_ text: String) -> Double {
    min(110, Double((ConstellationTree.displayLabel(text) as NSString).size(withAttributes: [.font: labelFont]).width.rounded(.up))) + 4
}

/// Optional landscape map of folders and agents. Selection lives in SessionModel;
/// the map only remembers which folder is in focus.
@MainActor
struct ConstellationView<AgentConversation: View, AssistantConversation: View>: View {
    @ObservedObject var model: SessionModel
    var onExit: () -> Void
    var folderQuestionsAvailable: Bool
    /// Shows the Oracle overview (all editions).
    var oracleAvailable: Bool
    /// Allows asking Oracle (Full). Without it Oracle is a read-only overview with no chat column.
    var oracleQuestionsAvailable: Bool
    @Binding var composing: Bool
    private let agentConversation: () -> AgentConversation
    private let assistantConversation: (_ folder: String?) -> AssistantConversation

    init(model: SessionModel, onExit: @escaping () -> Void, folderQuestionsAvailable: Bool, oracleAvailable: Bool,
         oracleQuestionsAvailable: Bool = false, composing: Binding<Bool>,
         @ViewBuilder agentConversation: @escaping () -> AgentConversation,
         @ViewBuilder assistantConversation: @escaping (_ folder: String?) -> AssistantConversation) {
        self.model = model
        self.onExit = onExit
        self.folderQuestionsAvailable = folderQuestionsAvailable
        self.oracleAvailable = oracleAvailable
        self.oracleQuestionsAvailable = oracleQuestionsAvailable
        _composing = composing
        self.agentConversation = agentConversation
        self.assistantConversation = assistantConversation
    }

    @State private var nav = ConstellationNav()
    @State private var projection = ConstellationProjection()
    @State private var showList = false
    @State private var from = ConstellationLayout()
    @State private var to = ConstellationLayout()
    @State private var start = Date.distantPast
    @State private var settled = true
    @State private var pressed = false
    @State private var lastMap = CGRect.zero
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.scenePhase) private var scenePhase

    private var tree: ConstellationTree { projection.tree }
    private var selectedAgent: String? { nav.mode == .map ? model.selectedSessionID : nil }
    private var panelsOpen: Bool { nav.mode == .oracle || nav.asking || selectedAgent != nil }
    private var chatOpen: Bool { nav.mode == .oracle ? oracleQuestionsAvailable : nav.asking || selectedAgent != nil }
    private var focusedFolder: ConstellationNode? { nav.path.last.flatMap { tree.nodes[$0] } }
    private var stale: Bool { model.phase == .stale }

    var body: some View {
        GeometryReader { geo in
            if geo.size.width < geo.size.height {
                portraitGuide
            } else {
                VStack(spacing: 0) {
                    bar
                    GeometryReader { stage in
                        stageView(ConstellationFrame(stage: stage.size, panelsOpen: panelsOpen, composing: composing, chat: chatOpen))
                    }
                }
            }
        }
        .background(Sky.background.ignoresSafeArea())
        .foregroundStyle(Sky.text)
        .tint(Sky.mint)
        .onAppear { updateProjection() }
        .onChange(of: SnapshotContent(model.snapshot)) { _ in updateProjection() }
        .onChange(of: model.selectedSessionID) { _ in reconcile() }
    }

    // MARK: Top bar

    private var bar: some View {
        HStack(spacing: 2) {
            Button(action: back) {
                Image(systemName: "chevron.left").font(.system(size: 17)).frame(width: 44, height: 44).contentShape(Rectangle())
            }
            .keyboardShortcut(.escape, modifiers: [])
            .accessibilityLabel(backLabel)
            .accessibilityIdentifier("constellation-back")
            .foregroundStyle(Sky.muted)
            crumbs
            Spacer(minLength: 6)
            if stale {
                Text("Last known").font(.caption2).foregroundStyle(Sky.amber).padding(.trailing, 6)
            }
            if let folder = focusedFolder, folderQuestionsAvailable, nav.mode == .map, !nav.asking, selectedAgent == nil, folder.total > 0 {
                Button("Ask \(folder.name)") { ask() }
                    .accessibilityIdentifier("constellation-ask")
                    .font(.footnote).lineLimit(1).frame(minHeight: 44).padding(.horizontal, 8)
                    .foregroundStyle(Sky.mint)
            }
            if oracleAvailable { modeSwitch }
            Button { showList.toggle() } label: {
                Image(systemName: showList ? "circle.hexagongrid" : "list.bullet").font(.system(size: 16))
                    .frame(width: 44, height: 44).contentShape(Rectangle())
            }
            .foregroundStyle(to.dense && !showList ? Sky.mint : Sky.muted)
            .disabled(nav.mode == .oracle || tree.requiresListFallback)
            .accessibilityLabel(showList ? "Show as map" : "Show as list")
            .accessibilityIdentifier("constellation-list-toggle")
        }
        .frame(height: 44)
        .padding(.horizontal, 4)
    }

    private var backLabel: String {
        if selectedAgent != nil { return "Close agent" }
        if nav.asking { return "Close folder question" }
        if nav.mode == .oracle { return "Back to map" }
        if nav.path.count > 1, let parent = tree.nodes[nav.path[nav.path.count - 2]] { return "Back to \(parent.name)" }
        if !nav.path.isEmpty { return "Back to all folders" }
        return "Close map"
    }

    private var crumbs: some View {
        let path = nav.mode == .oracle ? [] : nav.path
        // Long paths keep the root, the parent and the current folder.
        let shown: [String?] = path.count > 3 ? [nil] + path.suffix(2).map(Optional.some) : path.map(Optional.some)
        return HStack(spacing: 2) {
            if path.isEmpty && nav.mode == .map {
                Text("All folders").font(.footnote).padding(.horizontal, 5)
            } else {
                Button("All folders") { goTo(depth: 0) }.font(.footnote).foregroundStyle(Sky.muted)
                    .frame(minHeight: 44).padding(.horizontal, 5)
                    .accessibilityIdentifier("constellation-crumb:root")
            }
            ForEach(Array(shown.enumerated()), id: \.offset) { _, id in
                Text("/").font(.caption).foregroundStyle(Sky.faint)
                if let id, let node = tree.nodes[id] {
                    if id == path.last && selectedAgent == nil {
                        Text(node.name).font(.footnote).lineLimit(1).padding(.horizontal, 5)
                            .accessibilityAddTraits(.isHeader)
                            .accessibilityIdentifier("constellation-crumb:" + id)
                    } else {
                        Button(node.name) { goTo(depth: (path.firstIndex(of: id) ?? 0) + 1) }
                            .font(.footnote).lineLimit(1).foregroundStyle(Sky.muted)
                            .frame(minHeight: 44).padding(.horizontal, 5)
                            .accessibilityIdentifier("constellation-crumb:" + id)
                    }
                } else {
                    Text("…").font(.footnote).foregroundStyle(Sky.faint)
                }
            }
        }
    }

    private var modeSwitch: some View {
        HStack(spacing: 0) {
            segment("Map", on: nav.mode == .map) { nav.mode = .map; nav.asking = false }
                .accessibilityIdentifier("constellation-mode-map")
            segment("Oracle", on: nav.mode == .oracle) { openOracle() }
                .accessibilityIdentifier("constellation-mode-oracle")
        }
        .overlay(Rectangle().stroke(Color.white.opacity(0.08), lineWidth: 1).frame(height: 30))
        .padding(.horizontal, 4)
    }

    private func segment(_ title: String, on: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title).font(.footnote).frame(minWidth: 58).frame(height: 30)
                .background(on ? Color.white.opacity(0.05) : .clear)
                .frame(height: 44).contentShape(Rectangle())
        }
        .foregroundStyle(on ? Sky.text : Sky.muted)
        .accessibilityAddTraits(on ? .isSelected : [])
    }

    // MARK: Stage

    private func stageView(_ frame: ConstellationFrame) -> some View {
        ZStack(alignment: .topLeading) {
            if frame.showsMap {
                if (showList || tree.requiresListFallback) && nav.mode == .map {
                    memberList.frame(width: frame.map.width + 16, height: frame.map.height + 6).offset(x: frame.map.minX - 8)
                } else {
                    mapLayer
                }
            }
            if panelsOpen {
                if frame.left > 0 {
                    ScrollView { metadata.padding(.horizontal, 13).padding(.vertical, 10) }
                        .frame(width: frame.left).frame(maxHeight: .infinity)
                        .background(Sky.panel.ignoresSafeArea(edges: [.leading, .bottom]))
                        .overlay(alignment: .trailing) { Rectangle().fill(Sky.border).frame(width: 1) }
                        .transition(.move(edge: .leading).combined(with: .opacity))
                        .accessibilityElement(children: .contain)
                        .accessibilityLabel(nav.mode == .oracle ? "Oracle overview" : nav.asking ? "Folder facts" : "Agent details")
                }
                if frame.right > 0 {
                rightPanel(frame)
                    .frame(width: frame.right).frame(maxHeight: .infinity)
                    .background(Sky.background.ignoresSafeArea(edges: [.trailing, .bottom]))
                    .overlay(alignment: .leading) { Rectangle().fill(Sky.border).frame(width: 1) }
                    .frame(maxWidth: .infinity, alignment: .trailing)
                    .transition(.move(edge: .trailing).combined(with: .opacity))
                }
            }
        }
        .animation(reduceMotion ? nil : .easeOut(duration: 0.3), value: panelsOpen)
        .animation(reduceMotion ? nil : .easeOut(duration: 0.25), value: composing)
        .onAppear { relayout(frame.map) }
        .onChange(of: LayoutKey(tree: tree, nav: nav, selected: selectedAgent, map: frame.map)) { key in relayout(key.map) }
    }

    @ViewBuilder private func rightPanel(_ frame: ConstellationFrame) -> some View {
        VStack(spacing: 0) {
            if frame.left == 0 && !composing, let strip = compactMetadata {
                Text(strip).font(.caption).foregroundStyle(Sky.muted).lineLimit(1)
                    .frame(maxWidth: .infinity, minHeight: 28, alignment: .leading).padding(.horizontal, 13)
                    .overlay(alignment: .bottom) { Rectangle().fill(Sky.border).frame(height: 1) }
            }
            if nav.mode == .oracle {
                assistantConversation(nil)
            } else if nav.asking, let folder = focusedFolder?.folderPath {
                assistantConversation(folder)
            } else if selectedAgent != nil {
                agentConversation()
            }
        }
    }

    private struct LayoutKey: Equatable {
        let tree: ConstellationTree
        let nav: ConstellationNav
        let selected: String?
        let map: CGRect
    }

    private struct SnapshotContent: Equatable {
        let sessions: [AgentStatus]
        let folders: [String]
        init(_ snapshot: HostSnapshot?) {
            sessions = snapshot?.sessions ?? []
            folders = snapshot?.folders ?? []
        }
    }

    private func updateProjection() {
        let content = SnapshotContent(model.snapshot)
        projection.update(sessions: content.sessions, folders: content.folders)
        if tree.requiresListFallback { showList = true }
        reconcile()
    }

    private func relayout(_ map: CGRect) {
        let now = Date()
        let target = ConstellationLayout(tree: tree, nav: nav, selectedAgent: selectedAgent, map: map, measure: measureLabel)
        guard target != to || from.placements.isEmpty else { return }
        // A resize (rotation, keyboard) snaps; navigation animates from wherever the last transition had got to.
        let animate = !reduceMotion && map == lastMap && !to.placements.isEmpty
        from = animate ? from.blended(toward: to, progress: progress(at: now)) : target
        to = target
        start = now
        lastMap = map
        settled = !animate
        if animate {
            let mine = now
            Task { @MainActor in
                try? await Task.sleep(nanoseconds: UInt64(ConstellationLayout.duration * 1_000_000_000) + 30_000_000)
                if start == mine { settled = true }
            }
        }
    }

    private func progress(at date: Date) -> Double {
        settled ? 1 : min(1, date.timeIntervalSince(start) / ConstellationLayout.duration)
    }

    private var ambient: Bool {
        !reduceMotion && scenePhase == .active && !composing && !pressed && !to.placements.isEmpty
    }

    private var mapLayer: some View {
        ZStack(alignment: .topLeading) {
            TimelineView(.animation(minimumInterval: 1 / 30, paused: !ambient && settled)) { context in
                let layout = from.blended(toward: to, progress: progress(at: context.date))
                Canvas { gc, size in draw(gc, size: size, layout: layout, time: ambient ? context.date.timeIntervalSinceReferenceDate : 0) }
                    .accessibilityHidden(true)
            }
            TimelineView(.animation(paused: settled)) { context in
                targets(from.blended(toward: to, progress: progress(at: context.date)))
            }
        }
        .contentShape(Rectangle())
        .onTapGesture { if nav.mode == .map && (selectedAgent != nil || nav.asking || !nav.path.isEmpty) { back() } }
        .simultaneousGesture(DragGesture(minimumDistance: 0).onChanged { _ in pressed = true }.onEnded { _ in pressed = false })
        .opacity(stale ? 0.7 : 1)
    }

    // MARK: Drawing

    private func wobble(_ id: String, _ time: Double, amount: Double) -> CGSize {
        guard time != 0 else { return .zero }
        let ph = phase(id)
        return CGSize(width: amount * sin(time * 0.6 + ph), height: amount * 0.85 * cos(time * 0.8 + ph * 1.3))
    }

    private func draw(_ gc: GraphicsContext, size: CGSize, layout: ConstellationLayout, time: Double) {
        for i in 0..<44 {
            let x = (Double(i) * 137.5 + 19).truncatingRemainder(dividingBy: 1000) / 1000
            let y = (Double(i) * 61.8 + 7).truncatingRemainder(dividingBy: 1000) / 1000
            let twinkle = time == 0 ? 1 : 0.75 + 0.25 * sin(time * 0.5 + x * 30)
            gc.fill(Path(CGRect(x: x * size.width, y: y * size.height, width: 1, height: 1)),
                    with: .color(Color(white: 0.75).opacity((i % 3 == 0 ? 0.13 : 0.06) * twinkle)))
        }
        if let o = layout.placements[ConstellationLayout.oracleID], o.alpha > 0.01 {
            let m = layout.map
            gc.stroke(Path(ellipseIn: m.insetBy(dx: 2, dy: 2)), with: .color(Sky.blue.opacity(0.14 * o.alpha)),
                      style: StrokeStyle(lineWidth: 1, dash: [2, 7]))
        }
        let t = tree
        for id in t.order {
            guard let node = t.nodes[id], let p = layout.placements[id], p.alpha > 0.01, p.r > 0.3 else { continue }
            if node.isFolder { drawFolder(gc, node, p, time: time) } else { drawAgent(gc, node, p, layout: layout, time: time) }
        }
        if let o = layout.placements[ConstellationLayout.oracleID], o.alpha > 0.01 { drawOracle(gc, o, time: time) }
    }

    private func drawFolder(_ gc: GraphicsContext, _ node: ConstellationNode, _ p: ConstellationPlacement, time: Double) {
        let floating = p.role == .top || p.role == .peripheral
        let breathe = floating && time != 0 ? 1 + 0.012 * sin(time * 1.1 + phase(node.id)) : 1
        let drift = floating ? wobble(node.id, time * 0.5, amount: 1.5).height : 0
        let r = p.r * breathe, c = CGPoint(x: p.x, y: p.y + drift)
        let circle = Path(ellipseIn: CGRect(x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r))
        let ancestor = p.role == .ancestor
        if !ancestor && r > 6 {
            var shadow = gc
            shadow.addFilter(.shadow(color: .black.opacity(0.55), radius: min(15, 4 + r * 0.12), x: 0, y: 3 + min(6, r * 0.04)))
            shadow.fill(circle, with: .color(Color(red: 0.067, green: 0.086, blue: 0.102).opacity(0.9 * p.alpha)))
        }
        let k = ancestor ? 0.35 : 1.0
        gc.fill(circle, with: .radialGradient(Gradient(colors: [Sky.mint.opacity(0.17 * p.alpha * k), Sky.mint.opacity(0.06 * p.alpha * k), Sky.mint.opacity(0.015 * p.alpha * k)]),
                                              center: CGPoint(x: c.x - r * 0.32, y: c.y - r * 0.38), startRadius: r * 0.04, endRadius: r))
        if node.waiting > 0 && !ancestor {
            gc.fill(circle, with: .radialGradient(Gradient(colors: [Sky.amber.opacity(0.09 * p.alpha), Sky.amber.opacity(0)]),
                                                  center: CGPoint(x: c.x + r * 0.3, y: c.y + r * 0.35), startRadius: 0, endRadius: r * 0.85))
        }
        gc.stroke(circle, with: .color(Sky.rim.opacity(p.alpha * (p.role == .focus ? 0.34 : ancestor ? 0.16 : 0.24))), lineWidth: 1)
        if nav.asking && p.role == .focus {
            let ring = Path(ellipseIn: CGRect(x: c.x - r - 5, y: c.y - r - 5, width: 2 * r + 10, height: 2 * r + 10))
            gc.stroke(ring, with: .color(Sky.mint.opacity(0.45)), style: StrokeStyle(lineWidth: 1, dash: [2, 5]))
        }
    }

    private func drawAgent(_ gc: GraphicsContext, _ node: ConstellationNode, _ p: ConstellationPlacement, layout: ConstellationLayout, time: Double) {
        guard let agent = node.agent else { return }
        let status = ConstellationStatus(state: agent.state)
        let color = Sky.status(status)
        // Hints inside an unfocused bubble drift a few degrees around it; named dots only wobble in place.
        var c = CGPoint(x: p.x, y: p.y)
        if p.role == .content, time != 0, let parentID = node.parentID, let parent = layout.placements[parentID] {
            let a = 0.06 * sin(time * 0.22 + phase(node.id))
            let dx = c.x - parent.x, dy = c.y - parent.y
            c = CGPoint(x: parent.x + dx * cos(a) - dy * sin(a), y: parent.y + dx * sin(a) + dy * cos(a))
        }
        let w = wobble(node.id, time, amount: p.role == .content ? 1.4 : 1.6)
        c.x += w.width; c.y += w.height
        let ended = status == .ended || status == .unknown
        let r = p.r * (ended || time == 0 ? 1 : 1 + 0.08 * sin(time * 2 + phase(node.id)))
        let glow = r * (p.role == .content ? 3 : 4.2)
        gc.fill(Path(ellipseIn: CGRect(x: c.x - glow, y: c.y - glow, width: 2 * glow, height: 2 * glow)),
                with: .radialGradient(Gradient(colors: [color.opacity((ended ? 0.12 : 0.3) * p.alpha), color.opacity(0)]),
                                      center: c, startRadius: 0, endRadius: glow))
        gc.fill(Path(ellipseIn: CGRect(x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r)), with: .color(color.opacity((ended ? 0.7 : 0.95) * p.alpha)))
        if selectedAgent == agent.id {
            gc.stroke(Path(ellipseIn: CGRect(x: c.x - r - 5, y: c.y - r - 5, width: 2 * r + 10, height: 2 * r + 10)),
                      with: .color(Color(white: 0.92).opacity(0.85)), lineWidth: 1.2)
        }
    }

    private func drawOracle(_ gc: GraphicsContext, _ o: ConstellationPlacement, time: Double) {
        let r = o.r * (time == 0 ? 1 : 1 + 0.06 * sin(time * 1.2)), c = CGPoint(x: o.x, y: o.y)
        gc.fill(Path(ellipseIn: CGRect(x: c.x - r * 4.5, y: c.y - r * 4.5, width: r * 9, height: r * 9)),
                with: .radialGradient(Gradient(colors: [Sky.oracle.opacity(0.28 * o.alpha), Sky.oracle.opacity(0)]), center: c, startRadius: 0, endRadius: r * 4.5))
        gc.fill(Path(ellipseIn: CGRect(x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r)),
                with: .radialGradient(Gradient(colors: [Color(red: 0.91, green: 0.925, blue: 0.99).opacity(0.95 * o.alpha), Sky.blue.opacity(0.85 * o.alpha)]),
                                      center: CGPoint(x: c.x - r * 0.35, y: c.y - r * 0.4), startRadius: 0, endRadius: r))
    }

    // MARK: Targets and labels

    private func targets(_ layout: ConstellationLayout) -> some View {
        let t = tree
        let ids = t.order.filter { layout.placements[$0].map { $0.alpha > 0.05 && $0.role != .hidden && $0.role != .content } ?? false }
        return ZStack(alignment: .topLeading) {
            ForEach(ids, id: \.self) { id in
                if let node = t.nodes[id], let p = layout.placements[id] { target(node, p, layout: layout) }
            }
            if let box = layout.labelFrame(ConstellationLayout.oracleID, name: "Oracle", tree: t, nav: nav, measure: measureLabel) {
                nameLabel("Oracle", sub: "All folders", subColor: Sky.muted, box: box)
                    .accessibilityElement(children: .combine)
                    .accessibilityLabel("Oracle, the assistant for all folders")
                    .accessibilityIdentifier("constellation-node:" + ConstellationLayout.oracleID)
            }
            if let folder = focusedFolder, folder.total == 0, nav.mode == .map, let p = layout.placements[folder.id] {
                Text("No sessions yet").font(.caption).foregroundStyle(Sky.muted).position(x: p.x, y: p.y)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
    }

    @ViewBuilder private func target(_ node: ConstellationNode, _ p: ConstellationPlacement, layout: ConstellationLayout) -> some View {
        switch p.role {
        case .focus:
            rimTitle(node, p, current: true, layout: layout)
                .accessibilityIdentifier("constellation-node:" + node.id)
        case .ancestor:
            EmptyView()
        default:
            let side = p.target
            ZStack(alignment: .topLeading) {
                Button { tap(node) } label: {
                    Color.clear.frame(width: side, height: side)
                        .contentShape(node.isFolder ? AnyShape(Circle()) : AnyShape(Rectangle()))
                }
                .buttonStyle(.plain)
                .position(x: p.x, y: p.y)
                .accessibilityLabel(accessibilityName(node))
                .accessibilityIdentifier("constellation-node:" + node.id)
                .accessibilityAddTraits(selectedAgent != nil && node.agent?.id == selectedAgent ? .isSelected : [])
                if p.labelled { label(node, p, layout: layout).accessibilityHidden(true) }
            }
        }
    }

    private func rimTitle(_ node: ConstellationNode, _ p: ConstellationPlacement, current: Bool, layout: ConstellationLayout) -> some View {
        let box = layout.labelFrame(node.id, name: node.name, tree: tree, nav: nav, measure: measureLabel) ?? .zero
        return HStack(spacing: 6) {
            Text(node.name).font(.footnote).foregroundStyle(current ? Sky.text : Sky.muted).lineLimit(1)
            if current, let sub = folderSub(node) { Text(sub.0).font(.caption).foregroundStyle(sub.1).lineLimit(1) }
        }
        .frame(width: box.width, height: 44)
        .contentShape(Rectangle())
        .position(x: box.midX, y: box.midY)
        .accessibilityAddTraits(current ? .isHeader : [])
    }

    @ViewBuilder private func label(_ node: ConstellationNode, _ p: ConstellationPlacement, layout: ConstellationLayout) -> some View {
        if let box = layout.labelFrame(node.id, name: node.name, tree: tree, nav: nav, measure: measureLabel) {
            if let agent = node.agent {
                nameLabel(node.name, sub: nil, subColor: .clear, box: box, agent: agent)
            } else {
                let sub = box.height > 20 ? folderSub(node) : nil
                nameLabel(node.name, sub: sub?.0, subColor: sub?.1 ?? .clear, box: box, quiet: node.total == 0)
            }
        }
    }

    private func nameLabel(_ name: String, sub: String?, subColor: Color, box: CGRect,
                           agent: AgentStatus? = nil, quiet: Bool = false) -> some View {
        VStack(spacing: 0) {
            Text(name).font(.system(size: 11)).lineLimit(1).truncationMode(.tail)
                .foregroundStyle(quiet ? Sky.muted : agent != nil && selectedAgent != agent?.id ? Color(white: 0.76) : Sky.text)
            if let sub { Text(sub).font(.system(size: 10.5)).foregroundStyle(subColor).lineLimit(1) }
        }
        .frame(width: box.width, height: box.height, alignment: .top)
        .position(x: box.midX, y: box.midY)
        .allowsHitTesting(false)
    }

    private func folderSub(_ node: ConstellationNode) -> (String, Color)? {
        if node.total == 0 { return ("Empty", Sky.muted) }
        if node.waiting > 0 { return ("\(node.waiting) need you", Sky.amber) }
        return ("\(node.total) session\(node.total == 1 ? "" : "s")", Sky.muted)
    }

    private func accessibilityName(_ node: ConstellationNode) -> String {
        if let agent = node.agent {
            let folder = agent.folder.isEmpty ? "Ungrouped" : ConstellationTree.displayLabel(agent.folder)
            return "\(node.name), \(agent.stateLabel), in \(folder)"
        }
        var parts = [node.name, node.total == 0 ? "empty" : "\(node.total) session\(node.total == 1 ? "" : "s")"]
        if node.waiting > 0 { parts.append("\(node.waiting) need you") }
        return parts.joined(separator: ", ")
    }

    // MARK: Panels

    @ViewBuilder private var metadata: some View {
        VStack(alignment: .leading, spacing: 0) {
            if nav.mode == .oracle {
                let roots = tree.roots.compactMap { tree.nodes[$0] }
                title("Oracle", sub: "All folders on this Mac", color: Sky.muted)
                fact("Folders", "\(tree.folderCount)")
                fact("Sessions", "\(roots.reduce(0) { $0 + $1.total })")
                fact("Needs you", "\(roots.reduce(0) { $0 + $1.waiting })", amber: true)
                fact("Busy", "\(roots.reduce(0) { $0 + $1.busy })")
                fact("Ended", "\(roots.reduce(0) { $0 + $1.ended })")
                fact("Empty folders", "\(tree.emptyFolderCount)")
            } else if nav.asking, let folder = focusedFolder {
                let subs = tree.order.filter { tree.ancestors(of: $0).contains(folder.id) && tree.nodes[$0]?.isFolder == true }
                title(folder.name, sub: "Question scope", color: Sky.muted)
                fact("Sessions", "\(folder.total)")
                fact("Subfolders", "\(subs.count)")
                fact("Needs you", "\(folder.waiting)", amber: folder.waiting > 0)
                fact("Busy", "\(folder.busy)")
                fact("Ended", "\(folder.ended)")
            } else if let agent = model.selectedSession {
                let status = ConstellationStatus(state: agent.state)
                title(ConstellationTree.displayLabel(agent.name), sub: agent.stateLabel, color: status == .waiting ? Sky.amber : status == .busy ? Sky.blue : Sky.muted, dot: Sky.status(status))
                detail("Harness", ConstellationTree.displayLabel(agent.runtimeLabel))
                detail("Folder", agent.folder.isEmpty ? "Ungrouped" : ConstellationTree.displayLabel(agent.folder).replacingOccurrences(of: "/", with: " / "))
                detail("Last seen", lastSeen(agent.observedAt) + (stale ? " · last known" : ""))
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var compactMetadata: String? {
        if nav.mode == .oracle {
            let roots = tree.roots.compactMap { tree.nodes[$0] }
            return "\(tree.folderCount) folders · \(roots.reduce(0) { $0 + $1.total }) sessions · \(roots.reduce(0) { $0 + $1.waiting }) need you"
        }
        if nav.asking, let f = focusedFolder { return "\(f.total) sessions · \(f.waiting) need you · includes subfolders" }
        // The chat header already shows harness and status; the strip adds where and when.
        if let a = model.selectedSession { return "\(a.folder.isEmpty ? "Ungrouped" : ConstellationTree.displayLabel(a.folder).replacingOccurrences(of: "/", with: " / ")) · seen \(lastSeen(a.observedAt))" }
        return nil
    }

    private func title(_ text: String, sub: String, color: Color, dot: Color? = nil) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(text).font(.subheadline.weight(.medium)).fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 6) {
                if let dot { Circle().fill(dot).frame(width: 6, height: 6) }
                Text(sub).font(.caption).foregroundStyle(color)
            }
        }
        .padding(.bottom, 8)
        .accessibilityElement(children: .combine)
    }

    private func detail(_ key: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(key).font(.caption2).foregroundStyle(Sky.faint)
            Text(value).font(.caption).fixedSize(horizontal: false, vertical: true)
        }
        .padding(.top, 7)
        .accessibilityElement(children: .combine)
    }

    private func fact(_ key: String, _ value: String, amber: Bool = false) -> some View {
        HStack {
            Text(key).font(.caption)
            Spacer(minLength: 6)
            Text(value).font(.caption.monospacedDigit()).foregroundStyle(amber ? Sky.amber : Sky.text)
        }
        .padding(.vertical, 4)
        .overlay(alignment: .bottom) { Rectangle().fill(Color.white.opacity(0.03)).frame(height: 1) }
        .accessibilityElement(children: .combine)
    }

    private func lastSeen(_ stamp: Int64) -> String {
        // The server reports milliseconds; tolerate seconds too.
        let seconds = stamp > 100_000_000_000 ? Double(stamp) / 1000 : Double(stamp)
        guard seconds > 0 else { return "Unknown" }
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .short
        return formatter.localizedString(for: Date(timeIntervalSince1970: seconds), relativeTo: model.snapshotAt ?? Date())
    }

    private var memberList: some View {
        let current = nav.path.last.flatMap { tree.nodes[$0] }
        let ids = current?.children ?? tree.roots
        return ScrollView {
            LazyVStack(spacing: 0) {
                if ids.isEmpty { Text("No sessions in this folder").font(.footnote).foregroundStyle(Sky.muted).padding(16) }
                ForEach(ids, id: \.self) { id in
                    if let node = tree.nodes[id] { row(node) }
                }
            }
        }
        .background(Sky.background)
    }

    private func row(_ node: ConstellationNode) -> some View {
        Button { tap(node) } label: {
            HStack(spacing: 11) {
                if let agent = node.agent {
                    Circle().fill(Sky.status(ConstellationStatus(state: agent.state))).frame(width: 7, height: 7).frame(width: 16)
                } else {
                    Image(systemName: "folder").font(.system(size: 13)).foregroundStyle(Sky.muted).frame(width: 16)
                }
                Text(node.name).font(.footnote).lineLimit(1)
                Spacer(minLength: 8)
                if let agent = node.agent {
                    Text(agent.stateLabel).font(.caption).foregroundStyle(agent.state == "waiting" ? Sky.amber : Sky.muted)
                } else if let sub = folderSub(node) {
                    Text(sub.0).font(.caption).foregroundStyle(sub.1)
                }
            }
            .padding(.horizontal, 10).frame(minHeight: 46).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .overlay(alignment: .bottom) { Rectangle().fill(Color.white.opacity(0.035)).frame(height: 1) }
        .accessibilityLabel(accessibilityName(node))
        .accessibilityIdentifier("constellation-row:" + node.id)
    }

    private var portraitGuide: some View {
        VStack(spacing: 14) {
            Image(systemName: "rectangle.landscape.rotate").font(.system(size: 30, weight: .light)).foregroundStyle(Sky.mint)
            Text("Turn your phone sideways to see the map").font(.callout)
            Button("Show list", action: onExit).font(.callout).frame(minHeight: 44)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .accessibilityElement(children: .contain)
    }

    // MARK: Navigation

    private func tap(_ node: ConstellationNode) {
        if let agent = node.agent {
            nav.mode = .map
            nav.asking = false
            nav.path = tree.ancestors(of: node.id)
            model.selectSession(selectedAgent == agent.id ? nil : agent.id)
        } else {
            nav.focus(node.id, in: tree)
            model.selectSession(nil)
        }
    }

    private func goTo(depth: Int) {
        nav.mode = .map
        nav.asking = false
        nav.path = Array(nav.path.prefix(depth))
        model.selectSession(nil)
    }

    private func ask() {
        guard folderQuestionsAvailable else { return }
        model.selectSession(nil)
        nav.asking = true
    }

    private func openOracle() {
        model.selectSession(nil)
        nav.asking = false
        nav.mode = .oracle
        showList = false
    }

    /// Undo one step: keyboard, then agent, folder question, Oracle, deepest folder, and finally leave the map.
    private func back() {
        if composing {
            UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
            composing = false
        } else if selectedAgent != nil { model.selectSession(nil) }
        else if nav.asking { nav.asking = false }
        else if nav.mode == .oracle { nav.mode = .map }
        else if !nav.path.isEmpty { nav.path.removeLast() }
        else { onExit() }
    }

    private func reconcile() {
        // Before the first snapshot every folder looks deleted; keep the path until there is data.
        guard model.snapshot != nil else { return }
        let t = tree
        let keep = nav.reconcile(with: t, selectedSession: model.selectedSessionID)
        if model.selectedSessionID != nil, keep == nil { model.selectSession(nil) }
    }
}
