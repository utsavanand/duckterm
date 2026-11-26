# Code explorer and Artifacts

Code explorer browses the selected local session's working directory. Directories
load on expansion, Git ignore rules apply, and symbolic links are not followed.
A missing or remote directory leaves saved Artifacts available independently.
Registered files carry an ART marker and an Open in Artifacts action.

Artifacts combines registrations and saved copies in one searchable list. Each
row identifies whether its original is present, missing, outside the worktree,
or unavailable. Sizes are explicitly saved sizes. A present original opens the
current file, with View saved version available; other rows open saved bytes.
Search matches title, path and the existing kind label. Sort by update time or
name; filter by format and label. Changing a label uses existing metadata.

## Viewing interactive content

Markdown offers Rendered and Source. HTML offers Browser and Source. React apps
must be built as self-contained HTML and registered with a `.react.html` filename.
The React filter uses that suffix, not title guessing. The Source view shows the
registered bundle. Raw JSX/TSX in Code explorer remains source text. DuckTerm does
not install packages, invoke builds or start a development server when opening an
artifact. Producers must bundle scripts, styles and assets before registration;
external modules, CDN dependencies, network APIs and eval are not supported.

Interactive HTML runs in an iframe with `sandbox="allow-scripts"`, without
same-origin, forms, popups, downloads or top-navigation privileges. Its content
security policy allows inline scripts/styles and data images/fonts, while
blocking connections, external scripts, child frames and workers. Markdown still
uses sanitized HTML and only the selection reporter executes. Native navigation
policy also rejects subframe navigation outside about:srcdoc/about:blank and
never opens an external browser on behalf of an artifact. Native message bridges
continue to require the authenticated main dashboard frame.

Selections are untrusted messages. The parent validates the exact iframe window,
opaque origin, random per-preview channel, text bounds and coordinates. Messages
can open the feedback composer; only an explicit owner Send invokes delivery.
Downloads use application/octet-stream blobs so opening saved HTML outside its
frame cannot execute with dashboard privileges.

## Comments and revisions

Artifact feedback uses session + artifact ID + saved content hash. File feedback
uses session + resolved working root + relative path + displayed hash. Before
sending a file comment, the server reuses the bounded, no-follow, ignore-aware
file reader and checks the root and hash. Changed or unavailable content is
rejected and the draft remains visible. Delivery uses a JSON-encoded prompt and
the existing queued terminal submission; failed delivery does not persist a
misleading successful annotation.

Both readers show stored comments for that identity. Different hashes appear
under Earlier versions. Show quote only operates on the current hash and selects
one unique exact match in the active reader mode; missing or repeated text is
reported without guessing. Message comments are not attributed by similar text.
No annotation schema changes are required.
