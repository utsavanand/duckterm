import AuthenticationServices
import DuckyCore
import SwiftUI
import UIKit

/// GitHub sign-in in Apple's web sheet. Only this session receives the ducky://signed-in
/// callback, and its code is useless without the PKCE verifier that never left the phone.
@MainActor
final class GitHubSignIn: NSObject, ASWebAuthenticationPresentationContextProviding {
    private var session: ASWebAuthenticationSession?

    func run(_ account: AccountModel) {
        let start: URL
        do { start = try account.signInURL() } catch { account.cancelSignIn(error); return }
        let next = ASWebAuthenticationSession(url: start, callbackURLScheme: "ducky") { [weak self] callback, error in
            Task { @MainActor in
                self?.session = nil
                guard let callback else {
                    account.cancelSignIn(error.flatMap { ($0 as? ASWebAuthenticationSessionError)?.code == .canceledLogin ? nil : AccountError.github })
                    return
                }
                await account.finishSignIn(callback, name: UIDevice.current.name)
            }
        }
        next.presentationContextProvider = self
        session = next
        if !next.start() { session = nil; account.cancelSignIn(AccountError.github) }
    }

    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        UIApplication.shared.connectedScenes.compactMap { ($0 as? UIWindowScene)?.keyWindow }.first ?? ASPresentationAnchor()
    }
}

/// First launch: sign in, or pair with an invitation link as before.
struct AccountSignInView<Invitation: View>: View {
    @ObservedObject var account: AccountModel
    let signIn: GitHubSignIn
    @ViewBuilder let invitation: () -> Invitation

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Your agents, at a glance").font(.title2.bold())
            Text("Sign in with the GitHub account you use on your Mac.").foregroundStyle(Palette.muted)
            Button { signIn.run(account) } label: {
                HStack {
                    Text(account.phase == .signingIn ? "Signing in…" : "Sign in with GitHub").bold()
                    Spacer()
                    Image(systemName: "person.crop.circle")
                }.padding(14).background(Palette.green).foregroundStyle(Palette.background)
            }.buttonStyle(.plain).disabled(account.phase == .signingIn)
                .accessibilityIdentifier("account-sign-in")
            if let error = account.error { Text(error).font(.footnote).foregroundStyle(Palette.amber) }
            Text("OR PAIR WITH AN INVITATION").font(.caption.bold()).tracking(1).foregroundStyle(Palette.muted).padding(.top, 12)
            invitation()
        }.padding(.top, 32)
    }
}

/// The account's Macs. With exactly one, sign-in asks it straight away and this list is skipped.
struct AccountMacsView<Invitation: View>: View {
    @ObservedObject var account: AccountModel
    @ViewBuilder let invitation: () -> Invitation

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Your Macs").font(.title2.bold())
            Text("Signed in as \(account.session?.login ?? "")").foregroundStyle(Palette.muted)
            if let macs = account.macs, !macs.isEmpty {
                VStack(spacing: 0) {
                    ForEach(macs) { mac in row(mac) }
                }.overlay(Rectangle().stroke(Palette.border, lineWidth: 1))
                Text("Pick the Mac to connect to. You’ll allow this iPhone on that Mac.")
                    .font(.footnote).foregroundStyle(Palette.muted)
            } else if account.macs != nil {
                Text("No Macs on this account yet").font(.headline)
                Text("On your Mac, open Pair iPhone in DuckTerm and choose Sign in with GitHub.")
                    .foregroundStyle(Palette.muted)
            } else if account.loadingMacs {
                ProgressView().tint(Palette.green)
            }
            Button { Task { await account.loadMacs(name: UIDevice.current.name) } } label: {
                Label("Refresh", systemImage: "arrow.clockwise").frame(maxWidth: .infinity).panel()
            }.buttonStyle(.plain).foregroundStyle(Palette.green).disabled(account.loadingMacs)
            if let error = account.error { Text(error).font(.footnote).foregroundStyle(Palette.amber) }
            Text("OR PAIR WITH AN INVITATION").font(.caption.bold()).tracking(1).foregroundStyle(Palette.muted).padding(.top, 12)
            invitation()
        }
        .padding(.top, 24)
        .task { if account.macs == nil { await account.loadMacs(name: UIDevice.current.name) } }
    }

    private func row(_ mac: AccountMac) -> some View {
        Button { Task { await account.ask(mac, name: UIDevice.current.name) } } label: {
            HStack(spacing: 12) {
                Image(systemName: "laptopcomputer").foregroundStyle(Palette.muted)
                VStack(alignment: .leading, spacing: 4) {
                    Text(mac.name).font(.subheadline.bold())
                    Text("Last checked in " + Date(timeIntervalSince1970: TimeInterval(mac.lastSeenAt)).formatted(.relative(presentation: .named)))
                        .font(.caption).foregroundStyle(Palette.muted)
                }
                Spacer(minLength: 8)
                if account.askingHostID == mac.hostID { ProgressView().tint(Palette.green) }
                else { Image(systemName: "chevron.right").font(.caption).foregroundStyle(Palette.muted) }
            }
            .padding(14).frame(maxWidth: .infinity, minHeight: 60, alignment: .leading)
            .background(Palette.panel).contentShape(Rectangle())
            .overlay(alignment: .bottom) { Rectangle().fill(Palette.border).frame(height: 1) }
        }
        .buttonStyle(.plain).disabled(account.askingHostID != nil)
        .accessibilityIdentifier("account-mac:" + mac.hostID)
    }
}

/// Waiting for the owner to allow this iPhone on the Mac, comparing the same four digits.
struct AccountWaitingView: View {
    @ObservedObject var model: SessionModel
    let host: PairedHost

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text(host.macName ?? "Your Mac").font(.caption.bold()).tracking(1).foregroundStyle(Palette.muted)
            Text("Allow this iPhone on your Mac").font(.title2.bold())
            Text("Your Mac shows a request. Check it shows this code, then click Allow.")
                .foregroundStyle(Palette.muted)
            Text(host.matchingCode)
                .font(.system(size: 44, weight: .semibold, design: .monospaced)).tracking(12)
                .frame(maxWidth: .infinity).padding(.vertical, 24).panel()
                .accessibilityLabel("Matching code " + host.matchingCode.map(String.init).joined(separator: " "))
                .accessibilityIdentifier("account-matching-code")
            HStack(spacing: 10) {
                ProgressView().tint(Palette.green)
                Text("Waiting for your Mac…").foregroundStyle(Palette.muted)
            }
            Text("If the codes don’t match, click Deny on your Mac.").font(.footnote).foregroundStyle(Palette.muted)
            if let error = model.error { Text(error).font(.footnote).foregroundStyle(Palette.amber) }
            Button { model.forget() } label: {
                Text("Cancel").frame(maxWidth: .infinity).padding(12)
                    .overlay(Rectangle().stroke(Palette.border, lineWidth: 1))
            }.buttonStyle(.plain).foregroundStyle(Palette.muted)
                .accessibilityIdentifier("account-cancel-waiting")
        }.padding(.top, 24)
    }
}

/// The Mac denied or removed this iPhone. Asking again sends a new request with a new code;
/// an invitation link is only for phones not signed in.
struct AccountDeniedView: View {
    @ObservedObject var model: SessionModel
    @ObservedObject var account: AccountModel
    let host: PairedHost

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text(host.macName ?? "Your Mac").font(.caption.bold()).tracking(1).foregroundStyle(Palette.muted)
            Text("Not allowed on this Mac").font(.title2.bold())
            Text("Your Mac denied this iPhone, or removed it. Ask again to send a new request, then click Allow on your Mac.")
                .foregroundStyle(Palette.muted)
            Button { Task { await account.askAgain(name: UIDevice.current.name) } } label: {
                HStack {
                    Text(account.askingHostID == nil ? "Ask again" : "Asking…").bold()
                    Spacer()
                    if account.askingHostID != nil { ProgressView().tint(Palette.background) }
                }.padding(14).background(Palette.green).foregroundStyle(Palette.background)
            }.buttonStyle(.plain).disabled(account.askingHostID != nil)
                .accessibilityIdentifier("account-ask-again")
            if let error = account.error { Text(error).font(.footnote).foregroundStyle(Palette.amber) }
            Button { model.forget() } label: {
                Text("Choose another Mac").frame(maxWidth: .infinity).padding(12)
                    .overlay(Rectangle().stroke(Palette.border, lineWidth: 1))
            }.buttonStyle(.plain).foregroundStyle(Palette.muted)
                .accessibilityIdentifier("account-choose-mac")
        }.padding(.top, 24)
    }
}

/// Settings: who this iPhone is signed in as, and signing out.
struct AccountSettingsSection: View {
    @ObservedObject var account: AccountModel
    @State private var confirm = false

    var body: some View {
        if let session = account.session {
            Text("ACCOUNT").font(.caption.bold()).tracking(1).foregroundStyle(Palette.muted)
            VStack(alignment: .leading, spacing: 14) {
                Text("Signed in with GitHub as \(session.login)").font(.headline)
                Text("Signing out stops this iPhone finding your Macs. A Mac it already connects to keeps working until you remove the iPhone there.")
                    .font(.subheadline).foregroundStyle(Palette.muted)
                if confirm {
                    HStack {
                        Button("Cancel") { confirm = false }.foregroundStyle(Palette.muted)
                        Spacer()
                        Button("Sign out now") { confirm = false; Task { await account.signOut() } }.foregroundStyle(.red)
                    }
                } else {
                    Button("Sign out") { confirm = true }
                        .foregroundStyle(.red).padding(12).frame(maxWidth: .infinity)
                        .overlay(Rectangle().stroke(Color.red.opacity(0.5), lineWidth: 1))
                        .accessibilityIdentifier("account-sign-out")
                }
                if let error = account.error { Text(error).font(.footnote).foregroundStyle(Palette.amber) }
            }.panel()
        }
    }
}
