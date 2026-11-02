import DuckyCore
import SwiftUI
import UIKit

private enum Palette {
    static let background = Color(red: 0.047, green: 0.059, blue: 0.075)
    static let panel = Color(red: 0.078, green: 0.090, blue: 0.114)
    static let border = Color(red: 0.15, green: 0.17, blue: 0.20)
    static let muted = Color(red: 0.62, green: 0.65, blue: 0.69)
    static let green = Color(red: 0.16, green: 0.78, blue: 0.54)
    static let amber = Color(red: 1, green: 0.75, blue: 0.16)
    static let blue = Color(red: 0.42, green: 0.46, blue: 1)
}

private extension View {
    func panel() -> some View {
        padding(14).frame(maxWidth: .infinity, alignment: .leading)
            .background(Palette.panel)
            .overlay(Rectangle().stroke(Palette.border, lineWidth: 1))
    }
}

@MainActor
struct ContentView: View {
    @ObservedObject var model: SessionModel
    @State private var settings = false
    @State private var pairing = false
    @State private var confirmForget = false
    @State private var pasteError: String?
    @State private var search = ""
    @State private var filter: SessionFilter = .all
    @State private var expanded: Set<String> = []
    private let expansionStore = FolderExpansionStore()

    private var live: Bool { model.phase == .live }
    private var agents: [AgentStatus] { model.snapshot?.sessions ?? [] }
    private var tree: SessionTree { SessionTree(sessions: agents, folders: model.snapshot?.folders ?? []) }
    private var hostLabel: String { model.host?.url.host ?? "your Mac" }
    private var connectionLabel: String {
        switch model.phase {
        case .unpaired: return "Not paired yet"
        case .awaitingApproval: return "Waiting for approval"
        case .live: return "\(hostLabel) · Live"
        case .stale: return "\(hostLabel) · Last known status"
        case .denied: return "Access unavailable"
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            Rectangle().fill(Palette.border).frame(height: 1)
            if model.selectedSessionID != nil && !settings {
                conversationView
            } else {
                if !settings && model.snapshot != nil && (model.phase == .live || model.phase == .stale) {
                    sessionToolbar.padding(.horizontal, 16).padding(.top, 18)
                }
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 20) {
                        if settings { settingsView }
                        else { agentView }
                    }
                    .padding(16)
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .refreshable { await model.refresh() }
                .background(Palette.background)
            }
            Rectangle().fill(Palette.border).frame(height: 1)
            HStack(spacing: 0) {
                tab("Agents", systemImage: "circle.inset.filled", selected: !settings) { settings = false; model.selectSession(nil) }
                tab("Settings", systemImage: "gearshape", selected: settings) { settings = true; model.selectSession(nil) }
            }
            .background(Palette.panel)
        }
        .background(Palette.background.ignoresSafeArea())
        .foregroundStyle(Color(red: 0.90, green: 0.91, blue: 0.93))
        .onAppear { loadExpansion() }
        .onChange(of: model.host?.hostID) { _ in loadExpansion(); search = ""; filter = .all }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                Image("Duckmark").resizable().scaledToFit().frame(width: 34, height: 34)
                Text("Ducky").font(.largeTitle.bold())
            }
            HStack(alignment: .top) {
                Rectangle().fill(live ? Palette.green : Palette.muted).frame(width: 8, height: 8).padding(.top, 5)
                Text(connectionLabel).font(.subheadline).foregroundStyle(Palette.muted)
                Spacer(minLength: 8)
            }
        }.padding(20)
    }

    @ViewBuilder private var agentView: some View {
        if model.phase == .unpaired {
            inviteView
        } else if model.phase == .awaitingApproval {
            pairingView
        } else if model.phase == .denied {
            Text("Check access on your Mac").font(.title2.bold())
            Text(model.error ?? "This iPhone is no longer approved.").foregroundStyle(Palette.muted)
            Text("Create a new Ducky invitation on your Mac to pair again.").foregroundStyle(Palette.muted)
            pasteButton
        } else {
            if let snapshot = model.snapshot {
                if !live { staleBanner }
                if !model.notices.isEmpty && live { noticeBanner }
                if agents.isEmpty { Text("No sessions").font(.title2.bold()) }
                let rows = tree.rows(filter: filter, query: search, expanded: expanded)
                VStack(spacing: 0) {
                    ForEach(rows) { row in
                        if let session = row.session { sessionRow(session, depth: row.depth) }
                        else { folderRow(row) }
                    }
                    if rows.isEmpty && !agents.isEmpty {
                        Text(agents.isEmpty ? "No sessions" : "No matching sessions")
                            .font(.subheadline).foregroundStyle(Palette.muted).padding(20)
                    }
                }.opacity(live ? 1 : 0.55)
                if snapshot.truncated {
                    Text("Showing the first 1,000 sessions and folders. Open DuckTerm on your Mac for the full list.")
                        .font(.footnote).foregroundStyle(Palette.amber)
                }
                if let updated = model.snapshotAt {
                    Text("Updated \(updated.formatted(date: .omitted, time: .shortened)) · Pull to refresh")
                        .font(.footnote).foregroundStyle(Palette.muted)
                }
            } else {
                staleBanner
                Text("No status received yet").font(.title2.bold())
                Text("Keep your Mac awake and connect this iPhone to the same private network.")
                    .foregroundStyle(Palette.muted)
                refreshButton
            }
        }
        if let message = pasteError { Text(message).font(.footnote).foregroundStyle(Palette.amber) }
        if (model.phase == .unpaired || model.phase == .live), let error = model.error {
            Text(error).font(.footnote).foregroundStyle(Palette.amber)
        }
    }

    private var inviteView: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Your agents, at a glance").font(.title2.bold())
            Text("Open your Ducky invitation link on this iPhone, or paste it here.")
                .foregroundStyle(Palette.muted)
            pasteButton
            Text("Your iPhone and Mac need a private network connection.")
                .font(.footnote).foregroundStyle(Palette.muted)
        }.padding(.top, 32)
    }

    private var pairingView: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Approve this iPhone on your Mac").font(.title2.bold())
            Text("Check that this device ID matches, then run this command in a terminal on your Mac.")
                .foregroundStyle(Palette.muted)
            if let id = model.deviceID {
                VStack(alignment: .leading, spacing: 12) {
                    Text("duckterm ducky approve \(id)").font(.system(.callout, design: .monospaced))
                        .textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
                    Button("Copy approval command") { UIPasteboard.general.string = "duckterm ducky approve \(id)" }
                        .foregroundStyle(Palette.green)
                }.panel()
            }
            Text("Approval requests expire after ten minutes. If it expires, create a new invitation on your Mac.")
                .font(.footnote).foregroundStyle(Palette.muted)
            refreshButton
            if let error = model.error { Text(error).font(.footnote).foregroundStyle(Palette.amber) }
        }.padding(.top, 24)
    }

    private var pasteButton: some View {
        Button {
            guard let text = UIPasteboard.general.string, !text.isEmpty else {
                pasteError = "Copy your invitation link, then tap Paste invitation link."; return
            }
            pasteError = nil; pairing = true
            Task {
                await model.pair(link: text.trimmingCharacters(in: .whitespacesAndNewlines), name: UIDevice.current.name)
                pairing = false
            }
        } label: {
            HStack { Text(pairing ? "Connecting…" : "Paste invitation link").bold(); Spacer(); Image(systemName: "link") }
                .padding(14).background(Palette.green).foregroundStyle(Palette.background)
        }.buttonStyle(.plain).disabled(pairing)
    }

    private var refreshButton: some View {
        Button { Task { await model.refresh() } } label: {
            Label("Refresh status", systemImage: "arrow.clockwise").frame(maxWidth: .infinity).panel()
        }.buttonStyle(.plain).foregroundStyle(Palette.green)
    }

    private var staleBanner: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(model.snapshotAt == nil ? "Mac unavailable" : "Showing last known status").bold()
            if let date = model.snapshotAt {
                Text("Last update \(date.formatted(date: .abbreviated, time: .shortened)). These statuses may have changed.")
            } else { Text("Pull to retry. No live status is available yet.") }
            if let error = model.error { Text(error).font(.footnote) }
        }.foregroundStyle(Palette.amber).panel()
    }

    private var noticeBanner: some View {
        HStack(alignment: .top) {
            Text(model.notices.last?.genericText ?? "New agent activity").font(.subheadline)
            Spacer()
            Button { model.clearNotices() } label: { Image(systemName: "xmark") }
                .accessibilityLabel("Dismiss notification")
        }.foregroundStyle(Palette.amber).panel()
    }

    private func loadExpansion() {
        expanded = model.host.map { expansionStore.load(hostID: $0.hostID) } ?? []
    }
    private func saveExpansion() {
        if let hostID = model.host?.hostID { expansionStore.save(expanded, hostID: hostID) }
    }
    private var sessionToolbar: some View {
        VStack(spacing: 12) {
            HStack(spacing: 10) {
                Image(systemName: "magnifyingglass").foregroundStyle(Palette.muted)
                TextField("Search sessions or folders", text: $search)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                    .accessibilityIdentifier("session-search")
            }.padding(12).background(Palette.panel).overlay(Rectangle().stroke(Palette.border))
            HStack(spacing: 0) {
                ForEach(SessionFilter.allCases, id: \.self) { item in
                    Button { filter = item } label: {
                        HStack(spacing: 5) {
                            Text(item.label)
                            Text(String(tree.sessions.filter { item.matches($0) }.count)).monospacedDigit()
                        }.font(.subheadline).frame(maxWidth: .infinity).padding(.vertical, 12)
                            .foregroundStyle(filter == item ? Palette.green : Palette.muted)
                            .overlay(alignment: .bottom) { Rectangle().fill(filter == item ? Palette.green : Palette.border).frame(height: filter == item ? 2 : 1) }
                    }.buttonStyle(.plain).accessibilityAddTraits(filter == item ? .isSelected : [])
                }
            }
            HStack {
                let count = tree.matching(filter: filter, query: search).count
                Text("\(count) \(count == 1 ? "session" : "sessions")")
                Spacer()
                Button("Collapse all") { expanded = []; search = ""; filter = .all; saveExpansion() }
                    .disabled(expanded.isEmpty && search.isEmpty && filter != .waiting)
            }.font(.caption).foregroundStyle(Palette.muted)
        }
    }
    private func folderRow(_ row: SessionTreeRow) -> some View {
        Button {
            if expanded.contains(row.path) { expanded.remove(row.path) } else { expanded.insert(row.path) }
            saveExpansion()
        } label: {
            HStack(spacing: 10) {
                Image(systemName: row.expanded ? "chevron.down" : "chevron.right").font(.caption).frame(width: 12)
                Image(systemName: "folder").font(.subheadline)
                Text(row.label).font(.subheadline.bold()).foregroundStyle(Color.primary).multilineTextAlignment(.leading)
                Spacer(minLength: 6)
                if row.hasAttention { Rectangle().fill(Palette.amber).frame(width: 5, height: 5).accessibilityLabel("Contains sessions needing attention") }
                Text(String(row.count)).font(.caption).monospacedDigit()
            }.foregroundStyle(Palette.muted)
                .padding(.leading, CGFloat(min(row.depth * 15, 45)) + 4).padding(.trailing, 4).padding(.vertical, 14)
                .frame(maxWidth: .infinity, minHeight: 54, alignment: .leading)
                .contentShape(Rectangle())
                .overlay(alignment: .bottom) { Rectangle().fill(Palette.border).frame(height: 1) }
        }.buttonStyle(.plain)
            .accessibilityLabel("\(row.expanded ? "Collapse" : "Expand") \(row.path.isEmpty ? "Ungrouped" : row.path), \(row.count) sessions")
            .accessibilityIdentifier("folder:" + row.path)
    }
    private func sessionRow(_ session: AgentStatus, depth: Int) -> some View {
        Button { model.selectSession(session.id) } label: {
            VStack(alignment: .leading, spacing: 5) {
                HStack(alignment: .firstTextBaseline, spacing: 12) {
                    Text(session.name).font(.subheadline.bold()).multilineTextAlignment(.leading)
                    Spacer(minLength: 4)
                    Text(session.stateLabel).font(.caption).foregroundStyle(stateColor(session.state)).fixedSize()
                }
                Text(session.runtimeLabel).font(.caption).foregroundStyle(Palette.muted)
            }.padding(.leading, CGFloat(min(depth * 15, 45)) + 27).padding(.trailing, 7).padding(.vertical, 12)
                .frame(maxWidth: .infinity, minHeight: 66, alignment: .leading).contentShape(Rectangle())
                .overlay(alignment: .bottom) { Rectangle().fill(Palette.border.opacity(0.6)).frame(height: 1) }
        }.buttonStyle(.plain).accessibilityIdentifier("session:" + session.id)
    }

    private var conversationView: some View {
        VStack(spacing: 0) {
            VStack(alignment: .leading, spacing: 10) {
                Button { model.selectSession(nil) } label: { Label("Sessions", systemImage: "chevron.left") }
                    .font(.subheadline).foregroundStyle(Palette.muted).padding(.vertical, 4)
                if let session = model.selectedSession {
                    Text(session.name).font(.title2.bold()).fixedSize(horizontal: false, vertical: true)
                    HStack {
                        Text(session.runtimeLabel)
                        Text("·")
                        Text(session.stateLabel).foregroundStyle(stateColor(session.state))
                    }.font(.caption).foregroundStyle(Palette.muted)
                }
            }.frame(maxWidth: .infinity, alignment: .leading).padding(20)
                .overlay(alignment: .bottom) { Rectangle().fill(Palette.border).frame(height: 1) }
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    conversationContents
                }.padding(20).frame(maxWidth: .infinity, alignment: .leading)
            }
            .scrollDismissesKeyboard(.interactively)
            .refreshable { await model.refresh(); await model.refreshConversation() }
            if model.selectedSession?.isActive == true && model.snapshot?.allows("session:send") == true {
                composer
            }
        }.background(Palette.background)
    }

    @ViewBuilder private var conversationContents: some View {
        if model.selectedSession == nil {
            Text("This session is no longer available.").foregroundStyle(Palette.muted)
        } else if model.snapshot?.supportsConversations != true {
            Text("Update DuckTerm on your Mac to view conversations and send messages.").foregroundStyle(Palette.muted).panel()
        } else if model.snapshot?.allows("conversation:read") != true {
            VStack(alignment: .leading, spacing: 8) {
                Text("Messaging isn’t enabled").bold()
                Text("On your Mac, choose DuckTerm → Pair iPhone… and pair again with messaging access.")
            }.foregroundStyle(Palette.muted).panel()
        } else {
            if !live { staleBanner }
            if let conversation = model.conversation {
                if conversation.truncated {
                    Text("Showing recent messages. Open DuckTerm on your Mac for the full conversation.").font(.caption).foregroundStyle(Palette.muted)
                }
                ForEach(conversation.messages) { message in
                    if message.role == "assistant" {
                        Text("Agent").font(.caption).foregroundStyle(Palette.muted).padding(.top, 8)
                        Text(message.text).textSelection(.enabled).font(.subheadline)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    } else {
                        Text(message.text).textSelection(.enabled).font(.subheadline).panel()
                    }
                }
                ForEach(conversation.receipts) { receipt in
                    receiptView(receipt)
                }
                if conversation.messages.isEmpty && conversation.receipts.isEmpty {
                    Text("No messages yet").foregroundStyle(Palette.muted)
                }
            } else if model.selectedSession?.canRead != true {
                Text(["codex", "claude-code"].contains(model.selectedSession?.runtime ?? "") ? "No conversation yet" : "Conversation unavailable for this session.").foregroundStyle(Palette.muted)
            } else if model.conversationError == nil {
                ProgressView("Loading conversation…").tint(Palette.green)
            }
            if let error = model.conversationError {
                Text(error).font(.subheadline).foregroundStyle(Palette.amber)
            }
            if case .acknowledged(let receipt) = model.sendState,
               model.conversation?.receipts.contains(where: { $0.id == receipt.id }) != true {
                receiptView(receipt)
            }
            if let session = model.selectedSession, !session.isActive {
                Text("Resume this session on your Mac to send a message.").font(.subheadline).foregroundStyle(Palette.muted).panel()
            }
        }
    }

    private func receiptView(_ receipt: MessageReceipt) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            if let text = model.messageText(for: receipt), receipt.status != "delivered" {
                Text(text).textSelection(.enabled).font(.subheadline).panel()
            }
            Text(receipt.label).font(.caption).foregroundStyle(receipt.status == "unknown" ? Palette.amber : Palette.muted)
        }
    }

    private var composer: some View {
        VStack(alignment: .leading, spacing: 10) {
            TextEditor(text: Binding(get: { model.draft }, set: { model.setDraft($0) }))
                .scrollContentBackground(.hidden).font(.subheadline)
                .frame(height: 74).padding(8).background(Palette.panel)
                .overlay(Rectangle().stroke(Palette.border))
                .overlay(alignment: .topLeading) {
                    if model.draft.isEmpty { Text("Message agent…").font(.subheadline).foregroundStyle(Palette.muted).padding(13).allowsHitTesting(false) }
                }
                .accessibilityLabel("Message agent")
            HStack(alignment: .center, spacing: 12) {
                Text(composerStatus).font(.caption).foregroundStyle(Palette.muted).frame(maxWidth: .infinity, alignment: .leading)
                Button { Task { await model.sendMessage() } } label: {
                    Text(model.sendState == .sending ? "Sending…" : "Send ↗").font(.subheadline.bold())
                        .padding(.horizontal, 18).padding(.vertical, 12)
                        .background(Palette.green).foregroundStyle(Palette.background)
                }.buttonStyle(.plain).disabled(!model.canSend || !OutgoingMessage.validText(model.draft))
                    .opacity(model.canSend && OutgoingMessage.validText(model.draft) ? 1 : 0.45)
            }
            if case .acknowledged(let receipt) = model.sendState, receipt.status == "unknown" {
                Button("Start a new message") { model.beginNewMessage() }.font(.caption).foregroundStyle(Palette.green)
            }
        }.padding(16).overlay(alignment: .top) { Rectangle().fill(Palette.border).frame(height: 1) }
    }
    private var composerStatus: String {
        if OutgoingMessage.startsWithLocalCommand(model.draft) {
            return DuckyError.localCommandUnavailable.localizedDescription
        }
        switch model.sendState {
        case .sending: return "Sending to your Mac…"
        case .unconfirmed: return "Receipt unconfirmed. Checking your Mac; your message won’t be sent again."
        case .rejected(let message): return message
        case .acknowledged(let receipt) where receipt.status == "unknown": return "Delivery unconfirmed. Check your Mac before sending again."
        default:
            if model.draft.utf8.count > 4096 { return "Message is too long (maximum 4,096 bytes)." }
            if !live { return "Reconnect to your Mac to send." }
            if model.selectedSession?.runtime == "generic" { return "Messaging isn’t available for shell sessions." }
            return model.canSend ? "Sends when the agent is ready" : "Refresh the conversation before sending."
        }
    }

    @ViewBuilder private var settingsView: some View {
        Text("PAIRED MAC").font(.caption.bold()).tracking(1).foregroundStyle(Palette.muted)
        VStack(alignment: .leading, spacing: 12) {
            Text(model.host == nil ? "No Mac paired" : hostLabel).font(.headline)
            if let id = model.deviceID { Text("This iPhone: \(id)").font(.system(.footnote, design: .monospaced)).textSelection(.enabled) }
            Text(connectionLabel).font(.subheadline).foregroundStyle(Palette.muted)
        }.panel()
        Text("NOTIFICATIONS").font(.caption.bold()).tracking(1).foregroundStyle(Palette.muted)
        Text("Ducky checks when you open or refresh the app. This pilot cannot alert you in the background yet.")
            .foregroundStyle(Palette.muted).panel()
        if model.host != nil {
            Text("PAIRING").font(.caption.bold()).tracking(1).foregroundStyle(Palette.muted)
            VStack(alignment: .leading, spacing: 14) {
                Text("Forget this Mac").font(.headline)
                Text("Removes this iPhone’s credential. To revoke its approval on your Mac, run:").font(.subheadline).foregroundStyle(Palette.muted)
                Text("duckterm ducky revoke \(model.deviceID ?? "DEVICE_ID")")
                    .font(.system(.footnote, design: .monospaced)).textSelection(.enabled)
                if confirmForget {
                    Text("You’ll need a new invitation to pair again.").font(.footnote)
                    HStack {
                        Button("Cancel") { confirmForget = false }.foregroundStyle(Palette.muted)
                        Spacer()
                        Button("Forget now") { model.forget(); confirmForget = false }.foregroundStyle(.red)
                    }
                } else {
                    Button("Forget this Mac") { confirmForget = true }
                        .foregroundStyle(.red).padding(12).frame(maxWidth: .infinity)
                        .overlay(Rectangle().stroke(Color.red.opacity(0.5), lineWidth: 1))
                }
                if let error = model.error { Text(error).font(.footnote).foregroundStyle(Palette.amber) }
            }.panel()
        }
    }

    private func tab(_ label: String, systemImage: String, selected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            VStack(spacing: 6) { Image(systemName: systemImage); Text(label).font(.caption) }
                .frame(maxWidth: .infinity).padding(.vertical, 16)
                .foregroundStyle(selected ? Palette.green : Palette.muted)
        }.buttonStyle(.plain)
    }

    private func stateColor(_ value: String) -> Color {
        switch value { case "busy": return Palette.blue; case "waiting": return Palette.amber; case "idle": return Palette.green; default: return Palette.muted }
    }
}
