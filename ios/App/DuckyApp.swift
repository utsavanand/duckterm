import DuckyCore
import SwiftUI
import UIKit

@main
@MainActor
struct DuckyApp: App {
    @StateObject private var model = SessionModel()
    @Environment(\.scenePhase) private var scene

    var body: some Scene {
        WindowGroup {
            ContentView(model: model)
                .preferredColorScheme(.dark)
                .task {
                    await model.restore()
                }
                .onOpenURL { url in
                    Task {
                        await model.pair(link: url.absoluteString, name: UIDevice.current.name)
                    }
                }
                .onChange(of: model.host) { _ in
                    // Pairing can finish after the URL-opening scene transition.
                    // Observe the new connection with the current scene value.
                    if scene == .active { model.enterForeground() }
                }
                .onChange(of: scene) { value in
                    if value == .active { model.enterForeground() }
                    else { model.enterBackground() }
                }
        }
    }
}
