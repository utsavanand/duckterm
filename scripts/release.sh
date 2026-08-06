#!/usr/bin/env bash
# Cut a RubberTerm release from the current commit and publish it to GitHub
# Releases so you (and testers) can `pipx install` the wheel by URL.
#
#   scripts/release.sh beta     # pre-release (a PEP 440 pre-release version)
#   scripts/release.sh prod     # full release (a final version)
#
# The version comes from src/duckterm/__init__.py (single source of truth).
# BETA requires a pre-release version (e.g. 0.4.0b1); PROD requires a final one
# (e.g. 0.4.0) — the script refuses a mismatch so a beta can't ship as prod.
#
# Flow: fixes land on branches -> main. Cut a beta from main; you + friends
# install and test it. When it's proven, bump to the final version and cut prod.
set -euo pipefail
cd "$(dirname "$0")/.."

CHANNEL="${1:-}"
case "$CHANNEL" in
  beta|prod) ;;
  *) echo "usage: scripts/release.sh {beta|prod}" >&2; exit 2 ;;
esac

PY="${PYTHON:-.venv/bin/python}"
VERSION="$("$PY" -c 'import duckterm; print(duckterm.__version__)')"

# A PEP 440 pre-release has a/b/rc; a final release has none. Match to channel.
if echo "$VERSION" | grep -Eq '(a|b|rc)[0-9]+$'; then IS_PRE=1; else IS_PRE=0; fi
if [ "$CHANNEL" = beta ] && [ "$IS_PRE" = 0 ]; then
  echo "beta needs a pre-release version (e.g. 0.4.0b1); __version__ is $VERSION" >&2
  exit 1
fi
if [ "$CHANNEL" = prod ] && [ "$IS_PRE" = 1 ]; then
  echo "prod needs a final version (e.g. 0.4.0); __version__ is $VERSION" >&2
  exit 1
fi

TAG="v${VERSION}"
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "tag $TAG already exists — bump __version__ first" >&2
  exit 1
fi

echo "==> building wheel for $VERSION ($CHANNEL)"
scripts/build_package.sh

WHEEL="$(ls -1 dist/duckterm-*.whl | head -1)"
echo "==> tagging $TAG"
git tag -a "$TAG" -m "RubberTerm $VERSION ($CHANNEL)"
git push origin "$TAG"

PRERELEASE_FLAG=""
[ "$CHANNEL" = beta ] && PRERELEASE_FLAG="--prerelease"
TITLE="RubberTerm $VERSION"
[ "$CHANNEL" = beta ] && TITLE="$TITLE (beta)"

echo "==> creating GitHub release $TAG"
gh release create "$TAG" "$WHEEL" dist/duckterm-*.tar.gz \
  --title "$TITLE" \
  $PRERELEASE_FLAG \
  --notes "Install:
\`\`\`
pipx install $(basename "$WHEEL")   # from the attached wheel
\`\`\`
Or straight from the release URL (see the wheel asset below).

Run isolated so it never collides with another RubberTerm instance:
\`\`\`
DUCKTERM_INSTANCE=$CHANNEL duckterm serve
DUCKTERM_INSTANCE=$CHANNEL duckterm install-hooks
\`\`\`"

echo "==> done: $(gh release view "$TAG" --json url -q .url)"
