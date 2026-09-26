# Start a session from GitHub

In the desktop app, choose This Mac or a saved remote computer, then Project
source → Clone Git repository → Choose from GitHub. The picker shows the
account and repositories granted to that destination's GitHub connector. Filter
loaded repositories by owner/name; Load more retrieves the next 100, ordered by
recent updates. Private repositories are marked. Selecting one fills its HTTPS
URL and default branch; change the branch if needed, choose a new destination
folder, review, clone, then launch.

GitHub must be enabled in Connectors on the destination. A shared connector is
managed by its administrator. The app does not silently use another account if
the connector is disabled or unavailable. Pasting a URL instead uses the
destination computer's normal Git authorization, and supports other Git hosts.

A selected GitHub repository is fetched using the connector's credential. On a
shared host, the credential stays there: a read-only worker makes a Git bundle,
and the destination downloads and verifies it over the existing mutual-TLS
relay. The same workspace grant, capacity limit, and disable/rotation watcher
apply as for agent connector calls. Tokens are never returned to the dashboard,
written into repository URLs, or stored in repository Git config. Both servers
must run a release supporting the repository protocol.

The clone preserves committed history and the selected branch, sets origin to
the clean GitHub URL, and publishes only into a new directory. Uncommitted source
edits are not copied. Empty repositories are currently rejected. Bundles are
limited to 1 GiB, and existing project compatibility checks still apply. The
progress display is indeterminate during Git cloning; file copies show upload
percentage. GitHub selection does not install a persistent Git credential helper:
later terminal git fetch/push commands use that computer's normal Git credentials;
agents can continue using the GitHub connector for repository and PR operations.

Validation includes synthetic private repository selection, real Git history and
branch cloning through a synthetic authenticated transport, interrupted bundle
handling, mutual-TLS rejection and disablement, and both local and remote launch
forms. Live local checks verified the connected account can list DuckTerm and
clone the public octocat/Hello-World repository. No private repository is copied
onto a VM by these checks.
