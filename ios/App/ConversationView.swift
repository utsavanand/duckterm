import DuckyCore
import SwiftUI
import UIKit

enum ChatColor {
    static let background = Color(red: 0.063, green: 0.071, blue: 0.082)
    static let text = Color(red: 0.831, green: 0.843, blue: 0.863)
    static let muted = Color(red: 0.588, green: 0.616, blue: 0.655)
    static let accent = Color(red: 0.510, green: 0.780, blue: 0.675)
    static let input = Color(red: 0.110, green: 0.129, blue: 0.153)
    static let outgoing = Color(red: 0.106, green: 0.125, blue: 0.145)
    static let code = Color(red: 0.090, green: 0.106, blue: 0.125)
    static let warning = Color(red: 1, green: 0.75, blue: 0.16)
}

struct ConversationReadingPosition {
    var messageID: String?
    var anchorY: CGFloat = 0
    var followsLatest = true
}

private struct MessageFrames: PreferenceKey {
    static var defaultValue: [String: CGRect] = [:]
    static func reduce(value: inout [String: CGRect], nextValue: () -> [String: CGRect]) {
        value.merge(nextValue(), uniquingKeysWith: { _, new in new })
    }
}

@MainActor
struct ConversationView: View {
    @ObservedObject var model: SessionModel
    @Binding var position: ConversationReadingPosition
    private let embedded: Bool
    @Binding private var sharedComposing: Bool
    private let onClose: (() -> Void)?
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @FocusState private var composing: Bool
    @State private var restored = false
    @State private var followingLatest = true
    @State private var firstVisibleMessage: String?
    @State private var visibleAnchorY: CGFloat = 0
    @State private var savedOnBack = false
    @State private var measuredViewport = CGSize.zero
    private let bottomID = "conversation-bottom"

    init(model: SessionModel, position: Binding<ConversationReadingPosition>,
         embedded: Bool = false, composing: Binding<Bool> = .constant(false),
         onClose: (() -> Void)? = nil) {
        self.model = model
        _position = position
        self.embedded = embedded
        _sharedComposing = composing
        self.onClose = onClose
    }

    private var live: Bool { model.phase == .live }
    private var readyToSend: Bool { model.canSend && OutgoingMessage.validText(model.draft) }
    // Polls with unchanged content must not move the reader or rebuild Markdown.
    private var contentRevision: [String] {
        var revision: [String] = [model.selectedSessionID ?? "", model.conversation == nil ? "loading" : "loaded"]
        for message in model.conversation?.messages ?? [] { revision.append(contentsOf: [message.id, message.text]) }
        for receipt in model.conversationReceipts {
            revision.append(contentsOf: [receipt.id, receipt.status, model.messageText(for: receipt) ?? ""])
        }
        return revision
    }

    var body: some View {
        VStack(spacing: 0) {
            header.zIndex(1)
            messageList
        }
        .safeAreaInset(edge: .bottom, spacing: 0) {
            if model.selectedSession?.isActive == true && model.snapshot?.allows("session:send") == true {
                composer
            }
        }
        .background {
            if embedded { ChatColor.background }
            else { ChatColor.background.ignoresSafeArea() }
        }
        .foregroundStyle(ChatColor.text)
        .tint(ChatColor.accent)
        .onAppear { composing = sharedComposing }
        .onChange(of: composing) { sharedComposing = $0 }
        .onChange(of: sharedComposing) { composing = $0 }
        .onDisappear {
            if !savedOnBack { savePosition() }
            sharedComposing = false
        }
    }

    private var messageList: some View {
        ScrollViewReader { proxy in
            GeometryReader { viewport in
                ScrollView {
                    VStack(alignment: .leading, spacing: 26) {
                        contents
                        Color.clear.frame(height: 1).id(bottomID)
                            .background(frameReader(bottomID))
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 20).padding(.top, 16).padding(.bottom, 18)
                }
                .coordinateSpace(name: "conversation")
                .accessibilityIdentifier("conversation-scroll")
                .scrollDismissesKeyboard(.interactively)
                .refreshable { await model.retryConnection() }
                .onPreferenceChange(MessageFrames.self) { frames in
                    // Track zero-sized viewports too: keyboard dismissal can return
                    // through the previous size before the panel reaches its final width.
                    // Reflow is not a reader scroll; let the resize callback position it.
                    if measuredViewport != viewport.size {
                        measuredViewport = viewport.size
                        return
                    }
                    guard restored, viewport.size.height > 1 else { return }
                    if let bottom = frames[bottomID] {
                        followingLatest = bottom.minY <= viewport.size.height + 70
                    }
                    let visible = frames.filter {
                        $0.key != bottomID && $0.value.maxY > 0 && $0.value.minY < viewport.size.height
                    }.sorted { $0.value.minY < $1.value.minY }
                    // Prefer a complete row; for a long reply, retain the point within it.
                    if let item = visible.first(where: { $0.value.minY >= 0 }) ?? visible.first {
                        firstVisibleMessage = item.key
                        let distance = viewport.size.height - item.value.height
                        visibleAnchorY = abs(distance) > 1 ? item.value.minY / distance : 0
                    }
                }
                .onChange(of: contentRevision) { _ in
                    if !restored, model.conversation != nil {
                        restorePosition(proxy)
                    } else if followingLatest {
                        scrollToLatest(proxy, animated: false)
                    }
                }
                .onAppear {
                    followingLatest = position.followsLatest
                    if model.conversation != nil { restorePosition(proxy) }
                }
                .onChange(of: model.sendState) { state in
                    if state == .sending { scrollToLatest(proxy, animated: true) }
                }
                .onChange(of: viewport.size) { _ in
                    if followingLatest { scrollToLatest(proxy, animated: false) }
                }
            }
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 0) {
        HStack(spacing: 6) {
            if !embedded {
                Button(action: closeConversation) {
                    Image(systemName: "chevron.left").font(.system(size: 18, weight: .regular))
                        .frame(width: 44, height: 44).contentShape(Rectangle())
                }
                .buttonStyle(.plain).foregroundStyle(ChatColor.muted)
                .accessibilityLabel("Back to sessions").accessibilityIdentifier("conversation-back")
            }
            VStack(alignment: .leading, spacing: 3) {
                Text(model.selectedSession?.name ?? "Session").font(.subheadline.weight(.medium))
                    .lineLimit(1).accessibilityAddTraits(.isHeader)
                HStack(spacing: 5) {
                    Text(model.selectedSession?.runtimeLabel ?? "")
                    Text("·")
                    Text(model.agentActivityLabel)
                        .accessibilityIdentifier("conversation-agent-activity")
                        .foregroundStyle(live ? ChatColor.accent : ChatColor.warning)
                }.font(.caption2).foregroundStyle(ChatColor.muted)
            }
            Spacer(minLength: 4)
            if embedded {
                Button(action: closeConversation) {
                    Image(systemName: "xmark").font(.system(size: 16, weight: .regular))
                        .frame(width: 44, height: 44).contentShape(Rectangle())
                }
                .buttonStyle(.plain).foregroundStyle(ChatColor.muted)
                .accessibilityLabel("Close").accessibilityIdentifier("conversation-close")
            }
        }
        .padding(.horizontal, 8).padding(.vertical, embedded ? 0 : 7)
        connectionDetails
        }
        .background(ChatColor.background)
        .overlay(alignment: .bottom) {
            LinearGradient(colors: [ChatColor.background, ChatColor.background.opacity(0)],
                           startPoint: .top, endPoint: .bottom)
                .frame(height: 14).offset(y: 14)
                .allowsHitTesting(false).accessibilityHidden(true)
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("conversation-header")
    }

    @ViewBuilder private var connectionDetails: some View {
        if !live || model.retrying || model.conversationError != nil || model.receiptError != nil {
            VStack(alignment: .leading, spacing: 3) {
                HStack(alignment: .center, spacing: 8) {
                    Text(connectionSummary)
                        .fixedSize(horizontal: false, vertical: true)
                        .accessibilityIdentifier("conversation-connection")
                    Spacer(minLength: 0)
                    Button("Retry") { Task { await model.retryConnection() } }
                        .frame(minWidth: 44, minHeight: 44).contentShape(Rectangle())
                        .buttonStyle(.plain).foregroundStyle(ChatColor.accent)
                        .disabled(model.retrying)
                        .accessibilityIdentifier("conversation-retry")
                }
                if let detail = !live ? model.error : model.conversationError ?? model.receiptError {
                    Text(detail).fixedSize(horizontal: false, vertical: true)
                        .foregroundStyle(ChatColor.muted)
                        .accessibilityIdentifier("conversation-refresh-error")
                }
                if live, (model.conversationError != nil || model.receiptError != nil), let at = model.conversationAt {
                    Text("Replies updated \(at.formatted(date: .omitted, time: .shortened))")
                        .foregroundStyle(ChatColor.muted)
                }
            }
            .font(.caption2).foregroundStyle(ChatColor.warning)
            .padding(.leading, embedded ? 14 : 58).padding(.trailing, 16).padding(.bottom, 8)
            .accessibilityElement(children: .contain)
        }
    }
    private var connectionSummary: String {
        if !live {
            let state = model.statusRefreshing || model.retrying ? "Reconnecting to Mac" : "Can’t reach your Mac"
            return state + (model.snapshotAt.map { " · updated " + $0.formatted(date: .omitted, time: .shortened) } ?? " · no successful refresh yet")
        }
        if model.retrying { return "Status is current. Refreshing replies…" }
        return model.conversationError != nil ? "Status is current. Couldn’t refresh replies." : "Status is current. Couldn’t refresh delivery."
    }

    private func closeConversation() {
        savePosition(); savedOnBack = true
        composing = false; sharedComposing = false
        if let onClose { onClose() }
        else { model.selectSession(nil) }
    }

    @ViewBuilder private var contents: some View {
        if model.selectedSession == nil {
            note("This session is no longer available.")
        } else if model.snapshot?.supportsConversations != true {
            note("Update DuckTerm on your Mac to view conversations and send messages.")
        } else if model.snapshot?.allows("conversation:read") != true {
            note("Messaging isn’t enabled. On your Mac, choose DuckTerm → Pair iPhone… and pair again with messaging access.")
        } else {
            if let conversation = model.conversation {
                if conversation.truncated {
                    note("Showing recent messages. Open DuckTerm on your Mac for the full conversation.")
                }
                ForEach(conversation.messages) { message in
                    messageView(message).id(message.id).background(frameReader(message.id))
                }
                if conversation.messages.isEmpty && model.conversationReceipts.isEmpty { note("No messages yet") }
            } else if !live || model.conversationError != nil {
                note("Conversation unavailable.")
                    .accessibilityIdentifier("conversation-unavailable")
            } else if model.selectedSession?.canRead != true {
                note(["codex", "claude-code"].contains(model.selectedSession?.runtime ?? "") ?
                     "No conversation yet" : "Conversation unavailable for this session.")
            } else if model.conversationIsLoading {
                ProgressView("Loading conversation…").tint(ChatColor.accent)
                    .accessibilityIdentifier("conversation-loading")
            }
            ForEach(model.conversationReceipts) { receipt in receiptView(receipt) }
            if let session = model.selectedSession, !session.isActive {
                note("Resume this session on your Mac to send a message.")
            }
        }
    }

    private func note(_ text: String) -> some View {
        Text(text).font(.footnote).foregroundStyle(ChatColor.muted)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func messageView(_ message: ConversationMessage) -> some View {
        VStack(alignment: message.role == "assistant" ? .leading : .trailing, spacing: 7) {
            if message.role == "assistant" {
                FormattedReply(text: message.text, messageID: message.id).equatable()
            } else {
                outgoing(message.text)
            }
            if let at = message.at {
                Text(Date(timeIntervalSince1970: (Double(at) / 1000)).formatted(date: .omitted, time: .shortened))
                    .font(.caption2).foregroundStyle(ChatColor.muted)
            }
        }
        .frame(maxWidth: .infinity, alignment: message.role == "assistant" ? .leading : .trailing)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(message.role == "assistant" ? "Agent reply" : "Your message")
        .accessibilityIdentifier("conversation-message:" + message.id)
    }

    private func outgoing(_ text: String) -> some View {
        HStack(spacing: 0) {
            Spacer(minLength: 32)
            Text(text).font(.callout).lineSpacing(5).textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, 13).padding(.vertical, 10)
                .background(ChatColor.outgoing)
        }
    }

    private func receiptView(_ receipt: MessageReceipt) -> some View {
        VStack(alignment: .trailing, spacing: 7) {
            if let text = model.messageText(for: receipt) {
                outgoing(text).accessibilityIdentifier("conversation-receipt-message:" + receipt.id)
            }
            Text(model.receiptLabel(receipt)).font(.caption2)
                .accessibilityIdentifier("conversation-receipt:" + receipt.id)
                .foregroundStyle(receipt.status == "unknown" ? ChatColor.warning : ChatColor.muted)
        }.frame(maxWidth: .infinity, alignment: .trailing)
    }

    private var composer: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack(alignment: .bottom, spacing: 0) {
                TextField("Message agent…", text: Binding(get: { model.draft }, set: { model.setDraft($0) }),
                          prompt: Text("Message agent…").foregroundColor(ChatColor.muted), axis: .vertical)
                    .font(.callout).lineLimit(1...4).focused($composing)
                    .disabled(!model.canEditDraft)
                    .padding(.leading, 13).padding(.vertical, 12)
                    .accessibilityLabel("Message agent").accessibilityIdentifier("conversation-composer")
                Button { Task { await model.sendMessage() } } label: {
                    Group {
                        if model.sendState == .sending { ProgressView().tint(ChatColor.accent) }
                        else { Image(systemName: "arrow.up").font(.system(size: 20, weight: .regular)) }
                    }.frame(width: 44, height: 44).contentShape(Rectangle())
                }
                .buttonStyle(.plain).disabled(!readyToSend)
                .foregroundStyle(readyToSend ? ChatColor.accent : ChatColor.muted.opacity(0.4))
                .accessibilityLabel("Send message").accessibilityIdentifier("conversation-send")
            }
            .background(ChatColor.input)
            .overlay(alignment: .bottom) {
                Rectangle().fill(composing ? ChatColor.accent.opacity(0.32) : .clear).frame(height: 1)
            }
            if let status = composerStatus {
                Text(status).font(.caption).foregroundStyle(ChatColor.muted)
                    .fixedSize(horizontal: false, vertical: true).accessibilityIdentifier("conversation-status")
            }
            if case .acknowledged(let receipt) = model.sendState, receipt.status == "unknown" {
                Button("Start a new message") { model.beginNewMessage() }
                    .font(.footnote).padding(.vertical, 10)
            }
        }
        .padding(.horizontal, 12).padding(.top, 6).padding(.bottom, 7)
        .background(ChatColor.background)
        .animation(reduceMotion ? nil : .easeOut(duration: 0.18), value: composing)
    }

    private var composerStatus: String? {
        if OutgoingMessage.startsWithLocalCommand(model.draft) { return DuckyError.localCommandUnavailable.localizedDescription }
        switch model.sendState {
        case .sending: return "Sending to your Mac…"
        case .unconfirmed: return "Receipt unconfirmed. Checking your Mac; your message won’t be sent again."
        case .rejected(let message): return message
        case .acknowledged(let receipt) where receipt.status == "unknown": return "Delivery unconfirmed. Check your Mac before sending again."
        case .acknowledged(let receipt) where receipt.status == "expired": return live ? "Copy your message above to send it again." : "Reconnect to send another message."
        default:
            if model.draft.utf8.count > 4096 { return "Message is too long (maximum 4,096 bytes)." }
            if !live { return "Reconnect to send another message." }
            if model.sendState == nil, model.conversation?.receipts.last?.status == "expired" { return "Copy your message above to send it again." }
            if model.selectedSession?.runtime == "generic" { return "Messaging isn’t available for shell sessions." }
            return model.canSend ? nil : "Refresh the conversation before sending."
        }
    }

    private func frameReader(_ id: String) -> some View {
        GeometryReader { geometry in
            Color.clear.preference(key: MessageFrames.self, value: [id: geometry.frame(in: .named("conversation"))])
        }
    }
    private func savePosition() {
        position = ConversationReadingPosition(messageID: firstVisibleMessage, anchorY: visibleAnchorY, followsLatest: followingLatest)
    }
    private func restorePosition(_ proxy: ScrollViewProxy) {
        let saved = position
        DispatchQueue.main.async {
            if !saved.followsLatest, let id = saved.messageID,
               model.conversation?.messages.contains(where: { $0.id == id }) == true {
                proxy.scrollTo(id, anchor: UnitPoint(x: 0, y: saved.anchorY))
            } else { proxy.scrollTo(bottomID, anchor: .bottom) }
            restored = true
        }
    }
    private func scrollToLatest(_ proxy: ScrollViewProxy, animated: Bool) {
        followingLatest = true
        DispatchQueue.main.async {
            withAnimation(animated && !reduceMotion ? .easeOut(duration: 0.2) : nil) {
                proxy.scrollTo(bottomID, anchor: .bottom)
            }
        }
    }
}

struct FormattedReply: View, Equatable {
    let text: String
    let messageID: String

    var body: some View {
        let document = MessageMarkdown(text)
        VStack(alignment: .leading, spacing: 14) {
            ForEach(document.blocks) { block in
                HStack(alignment: .top, spacing: 9) {
                    if block.listDepth > 0 {
                        Text(block.isListItem ? block.listOrdinal.map { "\($0)." } ?? "•" : "")
                            .font(.callout).foregroundStyle(ChatColor.muted)
                            .frame(minWidth: 12, alignment: .trailing)
                    }
                    blockView(block).frame(maxWidth: .infinity, alignment: .leading)
                }
                .padding(.leading, min(CGFloat(MessageMarkdown.maximumDisplayDepth) * 16,
                    CGFloat(max(0, block.listDepth - 1)) * 16 + CGFloat(block.quoteDepth) * 12))
                .overlay(alignment: .leading) {
                    if block.quoteDepth > 0 { Rectangle().fill(ChatColor.muted.opacity(0.28)).frame(width: 2) }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        // Defense in depth if SwiftUI introduces a link beyond the projection.
        .environment(\.openURL, OpenURLAction { _ in .discarded })
    }

    @ViewBuilder private func blockView(_ block: MarkdownBlock) -> some View {
        switch block.kind {
        case .code(let language):
            CodeBlock(text: String(block.text.characters), language: language)
                .accessibilityElement(children: .contain)
                .accessibilityIdentifier("message-code:\(messageID):\(block.id)")
        case .thematicBreak:
            Rectangle().fill(ChatColor.muted.opacity(0.2)).frame(height: 1).padding(.vertical, 6)
        case .heading:
            Text(block.text).font(.callout.weight(.semibold)).lineSpacing(5)
                .fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
                .accessibilityAddTraits(.isHeader)
                .accessibilityIdentifier("message-heading:\(messageID):\(block.id)")
        case .paragraph:
            Text(block.text).font(.callout).lineSpacing(6)
                .fixedSize(horizontal: false, vertical: true).textSelection(.enabled)
        }
    }
}

private struct CodeBlock: View {
    let text: String
    let language: String?
    @State private var copied = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                Text(language ?? "Code").font(.caption2).foregroundStyle(ChatColor.muted)
                    .lineLimit(1).padding(.leading, 12)
                Spacer(minLength: 0)
                Button {
                    UIPasteboard.general.string = text; copied = true
                } label: {
                    Image(systemName: copied ? "checkmark" : "doc.on.doc")
                        .font(.system(size: 14)).frame(width: 44, height: 44)
                }.buttonStyle(.plain).foregroundStyle(ChatColor.muted)
                    .accessibilityLabel(copied ? "Copied code" : "Copy code")
            }
            ScrollView(.horizontal) {
                Text(text).font(.system(.footnote, design: .monospaced)).lineSpacing(4)
                    .fixedSize(horizontal: true, vertical: true).textSelection(.enabled)
                    .padding(.horizontal, 12).padding(.bottom, 12)
            }
        }.background(ChatColor.code)
            .task(id: copied) {
                guard copied else { return }
                try? await Task.sleep(nanoseconds: 2_000_000_000)
                if !Task.isCancelled { copied = false }
            }
    }
}
