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

    private var live: Bool { model.phase == .live }
    private var agents: [AgentStatus] { model.snapshot?.sessions ?? [] }
    private var folders: [String] {
        Array(Set((model.snapshot?.folders ?? []) + agents.map(\.folder))).sorted()
    }
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
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    if settings { settingsView }
                    else { agentView }
                }
                .padding(20)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .refreshable { await model.refresh() }
            .background(Palette.background)
            Rectangle().fill(Palette.border).frame(height: 1)
            HStack(spacing: 0) {
                tab("Agents", systemImage: "circle.inset.filled", selected: !settings) { settings = false }
                tab("Settings", systemImage: "gearshape", selected: settings) { settings = true }
            }
            .background(Palette.panel)
        }
        .background(Palette.background.ignoresSafeArea())
        .foregroundStyle(Color(red: 0.90, green: 0.91, blue: 0.93))
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
                Text("Read-only").font(.caption).foregroundStyle(Palette.muted)
                    .padding(.horizontal, 8).padding(.vertical, 3)
                    .overlay(Rectangle().stroke(Palette.border, lineWidth: 1))
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
                HStack(spacing: 10) {
                    count("Busy", agents.filter { $0.state == "busy" }.count)
                    count("Need you", agents.filter { $0.state == "waiting" }.count)
                    count("Idle", agents.filter { $0.state == "idle" }.count)
                }.opacity(live ? 1 : 0.55)
                if !live { staleBanner }
                if !model.notices.isEmpty && live { noticeBanner }
                if agents.isEmpty {
                    Text("No running agents").font(.title2.bold())
                    Text("Your folders are below. Ducky shows status; start or manage sessions on your Mac.")
                        .foregroundStyle(Palette.muted)
                }
                ForEach(folders, id: \.self) { folder in
                    folderView(folder)
                }
                if snapshot.truncated {
                    Text("Showing the first 1,000 agents and folders. Open DuckTerm on your Mac for the full list.")
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
            Text("For this first pilot, your iPhone and Mac need a private network connection. Ducky cannot control your sessions.")
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

    private func count(_ label: String, _ value: Int) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(String(value)).font(.title2.bold())
            Text(label).font(.subheadline).foregroundStyle(Palette.muted)
        }.panel()
    }

    private func folderView(_ folder: String) -> some View {
        let members = agents.filter { $0.folder == folder }
        return VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text(folder.isEmpty ? "UNGROUPED" : folder.uppercased()).font(.caption.bold()).tracking(1)
                Spacer(); Text(String(members.count)).font(.caption)
            }.foregroundStyle(Palette.muted)
            if members.isEmpty { Text("No running agents").font(.subheadline).foregroundStyle(Palette.muted).panel() }
            ForEach(members) { agent in
                VStack(alignment: .leading, spacing: 6) {
                    HStack(alignment: .top) {
                        Text(agent.name).font(.headline).fixedSize(horizontal: false, vertical: true)
                        Spacer(minLength: 12)
                        Text(stateLabel(agent.state)).font(.subheadline.bold()).foregroundStyle(stateColor(agent.state))
                    }
                    Text("\(runtimeLabel(agent.runtime)) · \(observedLabel(agent.observedAt))")
                        .font(.subheadline).foregroundStyle(Palette.muted)
                    if agent.approvalVisibility == "terminal_only" {
                        Text("Check approval prompts on your Mac").font(.footnote).foregroundStyle(Palette.amber)
                    }
                }.panel().opacity(live ? 1 : 0.55)
            }
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

    private func stateLabel(_ value: String) -> String {
        switch value { case "busy": return "Busy"; case "waiting": return "Needs you"; case "idle": return "Idle"; default: return "Unknown" }
    }
    private func stateColor(_ value: String) -> Color {
        switch value { case "busy": return Palette.blue; case "waiting": return Palette.amber; case "idle": return Palette.green; default: return Palette.muted }
    }
    private func runtimeLabel(_ value: String) -> String {
        switch value { case "claude-code": return "Claude Code"; case "codex": return "Codex"; case "generic": return "Shell"; default: return value }
    }
    private func observedLabel(_ milliseconds: Int64) -> String {
        guard milliseconds > 0 else { return "status age unknown" }
        let date = Date(timeIntervalSince1970: Double(milliseconds) / 1000)
        return "seen \(date.formatted(date: .omitted, time: .shortened))"
    }
}
