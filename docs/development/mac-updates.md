# Complete Mac app updates

The Settings control updates the installed DuckTerm edition's whole application
bundle: native window, Python backend, app CLI, dashboard, tmux, and helper tools.
It does not upgrade separately installed harnesses or a global pipx installation.
Older releases need one manual installation of an updater-enabled app.

## Runtime

`AppUpdater` adapts pinned Sparkle 2.10.1 to the compact Settings card. Only the
local dashboard's main frame can invoke the native bridge. The page supplies an
action, never a path, download URL, command, feed, or signing key. Remote and
browser dashboards retain a manual download link.

Opening Settings checks the signed feed. Downloading starts only after the owner
selects Update & restart. Progress is measured from received bytes; extraction
and restart are separate states. Closing Settings leaves the operation running;
reopening reads native state. Cancel is available before extraction begins.
The adapter supplies the validated bundle feed directly, clears any saved feed
override, and disables automatic checks/downloads before Sparkle starts. The
bundle disallows automatic installation, so saved preferences cannot bypass the
explicit install action and preflight.

Sparkle verifies the signed feed and archive before extraction and owns the
installation helper, bundle replacement and relaunch. Policy restricts each
production edition to its matching GitHub release archive. The Test app accepts
only an explicitly configured loopback feed, never a production feed.

Before download and again before restart, DuckTerm verifies the backend profile,
blocks an active backup/agent setup, and requires that it owns the backend
process. It never kills an independently launched server. At installation it
stops only its backend child, waits for that process to exit, and lets Sparkle
restart the app. Existing tmux sessions and persistent runtime copies remain.
A pending target version is saved in app preferences. Success requires both the
new app and authenticated backend to report that exact version. A failed
installation asks the old app to restart its backend if it had already stopped.
No automatic database restore or history rollback occurs.

## Release setup (requires the owner's approval)

The release maintainer creates one dedicated Sparkle Ed25519 key using Sparkle's
`generate_keys --account duckterm-updates`. Its private half stays in Keychain;
only the public half is supplied as `DUCKTERM_UPDATE_PUBLIC_KEY` when building
all three editions. Never commit/export the private key or pass it as a command
argument. Back up signing credentials through the maintainer's approved process.
The initial key has not been provisioned by this change.

The signed app contains its public key and one feed URL:

- `https://github.com/utsavanand/duckterm/releases/latest/download/appcast-lite-arm64.xml`
- `https://github.com/utsavanand/duckterm/releases/latest/download/appcast-code-arm64.xml`
- `https://github.com/utsavanand/duckterm/releases/latest/download/appcast-full-arm64.xml`

An unconfigured development bundle reports updating unavailable. It must not be
promoted as an updater-enabled release. `build_update_feeds.py` rejects missing
keys, Test bundles, wrong editions/versions, wrong feeds, and non-notarized apps.

## Publishing a release

1. Build all editions from the tag in a fresh worktree with the approved public
   key. Sign nested code and the enclosing bundle with `mac/sign-bundle.sh`, then
   notarize, staple and archive each complete app as usual.
2. Put the three final ZIPs in one directory. Their names are
   `DuckTerm-Lite-VERSION-macos-arm64.zip`,
   `DuckTerm-Code-VERSION-macos-arm64.zip`, and
   `DuckTerm-Full-VERSION-macos-arm64.zip`.
3. Run `python scripts/build_update_feeds.py --archives /absolute/archives
   --version VERSION --output /absolute/new-feeds --sign-tool
   /absolute/Sparkle/bin/sign_update --account duckterm-updates`.
   It verifies the actual archives, signs archives and feeds, and checks archive
   signatures against the public key embedded in each app. It prints absolute
   feed paths. It does not upload or change a GitHub release.
4. `scripts/release.sh prod` now requires `DUCKTERM_MAC_RELEASE_DIR` and
   `DUCKTERM_SPARKLE_SIGN_TOOL`, verifies/signs the feeds, and includes all six
   Mac assets. For a manually prepared release, upload **all three feeds and all three ZIPs** to the same draft release before
   publishing it as latest. A wheel-only latest release would break these feed
   URLs. Verify the three public feed URLs and signatures after publishing.
5. Test the previous updater-enabled Test candidate upgrading to the new Test
   candidate before distributing production. Use a separate loopback feed and
   disposable key, including tampered archive/feed, cancellation, offline,
   backend mismatch and successful relaunch checks. Preserve session identities.

Production signing, feed publication and a signed candidate upgrade are release
gates. Unit tests and a source build do not establish that installed Macs can
update. Release-dev owns promotion and installation.

## Automated native installer probe

After `swift package --package-path mac resolve`, run
`python scripts/test_native_app_update.py` from a graphical Mac login. It builds
disposable Test bundles with the production updater adapter and a temporary key;
it checks success, tampered archive/feed rejection, and backend-version mismatch.
Each launch seeds a conflicting saved feed and automatic-update preferences;
success also requires the explicit preflight and restart checks to have run.
The backend version is a fixture in this probe, not a real DuckTerm server.
It does not replace testing session reconnection with the complete signed release
candidate. `--case` runs one scenario. Every Test bundle and preference domain
is unique and cleaned up. No production Keychain key is read or created.
