import DuckyCore
import SwiftUI

private struct AnalysisFrames: PreferenceKey {
    static var defaultValue: [String: CGRect] = [:]
    static func reduce(value: inout [String: CGRect], nextValue: () -> [String: CGRect]) {
        value.merge(nextValue(), uniquingKeysWith: { _, new in new })
    }
}

@MainActor
struct AnalysisConversationView: View {
    @ObservedObject var model: AnalysisModel
    let scope: AnalysisScope
    @Binding var composing: Bool
    @Binding var position: ConversationReadingPosition
    let onClose: () -> Void
    @FocusState private var focused: Bool
    @State private var restored = false
    @State private var savedOnClose = false
    @State private var following = true
    @State private var visibleID: String?
    @State private var anchorY: CGFloat = 0
    private let bottom = "analysis-bottom"
    private var title: String { scope == .oracle ? "Oracle" : "Ask \(scope.title)" }
    private var placeholder: String { scope == .oracle ? "Ask Oracle…" : "Ask about \(scope.title)…" }
    private var current: Bool { model.scope == scope }
    private var denied: Bool { current && (!model.allowed || model.state == .denied) }
    private var count: Int {
        (model.snapshot?.sessions ?? []).filter { session in
            switch scope {
            case .oracle: return true
            case .ungrouped: return session.folder.isEmpty
            case .folder(let path): return session.folder == path || session.folder.hasPrefix(path + "/")
            }
        }.count
    }
    private var subtitle: String {
        if !model.live { return "Last known · \(count) sessions" }
        if count == 0 { return "No sessions yet" }
        switch scope {
        case .oracle: return "All \(count) sessions on this Mac"
        case .ungrouped: return "\(count) sessions not in a folder"
        case .folder: return "\(count) sessions · includes subfolders"
        }
    }
    private var contentKey: [String] {
        guard current else { return [] }
        return model.history.map { $0.requestID + $0.status.rawValue } + [model.receipt?.status.rawValue ?? "", model.submission?.requestID ?? ""]
    }
    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 8) {
                VStack(alignment: .leading, spacing: 3) {
                    Text(title).font(.subheadline.weight(.medium)).lineLimit(1)
                    Text(denied ? "" : subtitle).font(.caption2).foregroundStyle(model.live ? ChatColor.muted : ChatColor.warning).lineLimit(1)
                }
                Spacer(minLength: 0)
                Button {
                    save(); savedOnClose = true; focused = false; composing = false; onClose()
                } label: { Image(systemName: "xmark").font(.system(size: 16)).frame(width: 44, height: 44).contentShape(Rectangle()) }
                    .buttonStyle(.plain).foregroundStyle(ChatColor.muted).accessibilityLabel("Close question")
                    .accessibilityIdentifier("analysis-close")
            }.padding(.leading, 18).padding(.trailing, 4)
                .background(ChatColor.background).zIndex(1)
                .accessibilityElement(children: .contain).accessibilityIdentifier("analysis-header")
            if denied {
                VStack(alignment: .leading, spacing: 12) {
                    Text("Questions are off for this iPhone").font(.callout.weight(.medium))
                    Text("Answers saved on this phone were removed. To turn questions back on, pair again from DuckTerm on your Mac.")
                        .font(.footnote).foregroundStyle(ChatColor.muted)
                    Spacer()
                }.padding(20).frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                    .accessibilityIdentifier("analysis-denied")
            } else { feed }
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if !denied { composer }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(ChatColor.background).foregroundStyle(ChatColor.text).tint(ChatColor.accent)
        .task(id: scope) { await model.select(scope) }
        .onChange(of: focused) { composing = $0 }
        .onChange(of: composing) { focused = $0 }
        .onDisappear { if !savedOnClose { save() }; composing = false }
        .accessibilityElement(children: .contain).accessibilityIdentifier("analysis-panel")
    }
    private var feed: some View {
        ScrollViewReader { proxy in
            GeometryReader { viewport in
                ScrollView {
                    VStack(alignment: .leading, spacing: 24) {
                        if current {
                            if model.history.isEmpty && model.receipt == nil && model.submission == nil {
                                if model.state == .loading { ProgressView("Loading…").font(.footnote) }
                                else {
                                    Text("Ask about the sessions \(scope == .oracle ? "on this Mac" : "in " + scope.title).")
                                        .font(.callout).foregroundStyle(ChatColor.muted)
                                    VStack(alignment: .leading, spacing: 6) {
                                        suggestion("What needs my attention?")
                                        suggestion("What changed today?")
                                    }
                                }
                            }
                            ForEach(model.history) { receipt in
                                exchange(receipt).id(receipt.id).background(frame(receipt.id))
                            }
                            if let receipt = model.receipt { exchange(receipt).id(receipt.id).background(frame(receipt.id)) }
                            else if let submission = model.submission {
                                VStack(alignment: .leading, spacing: 12) {
                                    question(submission.question, at: nil)
                                    Text(model.state == .submitting ? "Sending to your Mac…" : "Receipt unconfirmed. Check your Mac before asking again.")
                                        .font(.footnote).foregroundStyle(ChatColor.muted)
                                }.id(submission.requestID).background(frame(submission.requestID))
                            }
                            if let error = model.error { Text(error).font(.footnote).foregroundStyle(ChatColor.warning) }
                            if model.canRecover {
                                Button("Check again") { Task { await model.recover() } }.font(.footnote)
                                    .accessibilityIdentifier("analysis-recover")
                            }
                        }
                        Color.clear.frame(height: 1).id(bottom).background(frame(bottom))
                    }.padding(.horizontal, 18).padding(.vertical, 16).frame(maxWidth: .infinity, alignment: .leading)
                }
                .clipped()
                .coordinateSpace(name: "analysis-feed").scrollDismissesKeyboard(.interactively)
                .accessibilityIdentifier("analysis-scroll")
                .onPreferenceChange(AnalysisFrames.self) { frames in
                    guard restored, viewport.size.height > 1 else { return }
                    if let end = frames[bottom] { following = end.minY <= viewport.size.height + 60 }
                    let visible = frames.filter { $0.key != bottom && $0.value.maxY > 0 && $0.value.minY < viewport.size.height }.sorted { $0.value.minY < $1.value.minY }
                    if let item = visible.first(where: { $0.value.minY >= 0 }) ?? visible.first {
                        visibleID = item.key
                        let distance = viewport.size.height - item.value.height
                        anchorY = abs(distance) > 1 ? item.value.minY / distance : 0
                    }
                }
                .onAppear {
                    following = position.followsLatest
                    if current && !model.history.isEmpty {
                        let target = !following ? position.messageID ?? bottom : bottom
                        DispatchQueue.main.async { proxy.scrollTo(target, anchor: following ? .bottom : UnitPoint(x: 0, y: position.anchorY)); restored = true }
                    }
                }
                .onChange(of: contentKey) { _ in
                    if !restored {
                        following = position.followsLatest
                        let target = !following ? position.messageID ?? bottom : bottom
                        DispatchQueue.main.async { proxy.scrollTo(target, anchor: following ? .bottom : UnitPoint(x: 0, y: position.anchorY)); restored = true }
                    } else if following { DispatchQueue.main.async { proxy.scrollTo(bottom, anchor: .bottom) } }
                }
                .onChange(of: viewport.size) { _ in
                    if following { DispatchQueue.main.async { proxy.scrollTo(bottom, anchor: .bottom) } }
                }
            }.clipped()
        }
    }
    private func suggestion(_ text: String) -> some View {
        Button(text) { model.setDraft(text); focused = true }
            .font(.footnote).buttonStyle(.plain).padding(.vertical, 8)
            .foregroundStyle(ChatColor.accent).disabled(!model.live || model.busy)
    }
    private func question(_ text: String, at: Int64?) -> some View {
        VStack(alignment: .leading, spacing: 7) {
            Text("You" + (at.map { " · " + time($0) } ?? "")).font(.caption2).foregroundStyle(ChatColor.muted)
            Text(text).font(.callout).lineSpacing(5).textSelection(.enabled)
                .padding(12).frame(maxWidth: .infinity, alignment: .leading).background(ChatColor.outgoing)
        }
    }
    private func exchange(_ receipt: AnalysisReceipt) -> some View {
        VStack(alignment: .leading, spacing: 17) {
            question(receipt.question, at: receipt.createdAt)
            if let answer = receipt.answer {
                VStack(alignment: .leading, spacing: 10) {
                    Text("DuckTerm · \(time(receipt.updatedAt))").font(.caption2).foregroundStyle(ChatColor.muted)
                    FormattedReply(text: answer, messageID: "analysis-" + receipt.id).equatable()
                    if let captured = receipt.capturedAt {
                        Text("From \(receipt.sourceCount) sessions\(receipt.truncated ? " · partial context" : "") · as of \(time(captured))")
                            .font(.caption2).foregroundStyle(ChatColor.muted)
                            .accessibilityIdentifier("analysis-facts")
                    }
                }.opacity(model.live ? 1 : 0.62).accessibilityElement(children: .contain).accessibilityIdentifier("analysis-answer")
            } else if receipt.pending {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text(receipt.status == .queued ? "Waiting for your Mac…" : "Your Mac is answering…")
                }.font(.footnote).foregroundStyle(ChatColor.muted).accessibilityIdentifier("analysis-pending")
            } else {
                Text(failure(receipt)).font(.footnote).foregroundStyle(ChatColor.warning)
                Button("Ask again") { model.setDraft(receipt.question); Task { await model.ask() } }
                    .font(.footnote).disabled(!model.live || model.busy).accessibilityIdentifier("analysis-again")
            }
        }.accessibilityElement(children: .contain).accessibilityIdentifier("analysis-exchange:" + receipt.id)
    }
    private var composer: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .bottom, spacing: 4) {
                TextField("", text: Binding(get: { current ? model.draft : "" }, set: { model.setDraft($0) }),
                          prompt: Text(placeholder).foregroundColor(ChatColor.muted), axis: .vertical)
                    .font(.callout).lineLimit(1...4).padding(.leading, 12).padding(.vertical, 12)
                    .focused($focused).disabled(!model.live || !current || !model.allowed)
                    .accessibilityIdentifier("analysis-composer")
                Button { Task { await model.ask() } } label: {
                    Image(systemName: "arrow.up").font(.system(size: 20)).frame(width: 44, height: 44).contentShape(Rectangle())
                }.buttonStyle(.plain).disabled(!current || !model.canAsk)
                    .accessibilityLabel("Ask").accessibilityIdentifier("analysis-send")
            }.background(ChatColor.input)
            if !model.live { Text("Mac unavailable · reconnect to ask.").font(.caption2).foregroundStyle(ChatColor.warning) }
            else if model.draft.utf8.count > 4096 { Text("Question is too long (maximum 4,096 bytes).").font(.caption2).foregroundStyle(ChatColor.warning) }
        }.padding(.horizontal, 12).padding(.bottom, 10).padding(.top, 6).background(ChatColor.background)
    }
    private func failure(_ receipt: AnalysisReceipt) -> String {
        switch receipt.reason {
        case "server_restarted": return "DuckTerm restarted before answering."
        case "scope_changed": return "DuckTerm changed while answering: a session moved or ended."
        case "work_expired", "queue_expired": return "Your Mac took too long to answer."
        default: return "Your Mac couldn’t answer this question."
        }
    }
    private func time(_ milliseconds: Int64) -> String { Date(timeIntervalSince1970: Double(milliseconds) / 1000).formatted(date: .omitted, time: .shortened) }
    private func frame(_ id: String) -> some View { GeometryReader { geo in Color.clear.preference(key: AnalysisFrames.self, value: [id: geo.frame(in: .named("analysis-feed"))]) } }
    private func save() { position = ConversationReadingPosition(messageID: visibleID, anchorY: anchorY, followsLatest: following) }
}
