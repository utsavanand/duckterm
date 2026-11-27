import Foundation

/// Whether the dashboard's native view may load `destination` in place.
/// Artifact previews are sandboxed `srcdoc` iframes, which WebKit loads as
/// `about:srcdoc`; refusing that left every preview blank in the Mac app while
/// the same page rendered in a browser. `about:` stays confined to subframes so
/// nothing can replace the dashboard itself with a blank page.
func dashboardAllowsNavigation(to destination: URL, dashboard: URL, inMainFrame: Bool) -> Bool {
    if !inMainFrame {
        return ["about:srcdoc", "about:blank"].contains(destination.absoluteString)
    }
    return destination.scheme == dashboard.scheme && destination.host == dashboard.host
        && destination.port == dashboard.port
}

/// An executable artifact must not turn frame navigation into a native browser
/// launch. Only the trusted main dashboard may request an external window.
func dashboardAllowsExternalNavigation(sourceMainFrame: Bool, sourceScheme: String,
                                       sourceHost: String, sourcePort: Int, dashboard: URL) -> Bool {
    sourceMainFrame && sourceScheme == dashboard.scheme && sourceHost == dashboard.host
        && sourcePort == dashboard.port
}
