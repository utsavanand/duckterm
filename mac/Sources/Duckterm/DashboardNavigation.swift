import Foundation

/// Whether the dashboard's native view may load `destination` in place.
/// Artifact previews are sandboxed `srcdoc` iframes, which WebKit loads as
/// `about:srcdoc`; refusing that left every preview blank in the Mac app while
/// the same page rendered in a browser. `about:` stays confined to subframes so
/// nothing can replace the dashboard itself with a blank page.
func dashboardAllowsNavigation(to destination: URL, dashboard: URL, inMainFrame: Bool) -> Bool {
    if !inMainFrame && ["about:srcdoc", "about:blank"].contains(destination.absoluteString) {
        return true
    }
    return destination.scheme == dashboard.scheme && destination.host == dashboard.host
        && destination.port == dashboard.port
}
