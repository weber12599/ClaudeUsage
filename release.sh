#!/bin/bash
# Cut a release: bump the version, commit, and tag. Pushing the tag triggers
# the Release workflow (.github/workflows/release.yml), which builds
# ClaudeUsage.app, zips it, and attaches it to a GitHub Release.
#
#   ./release.sh 0.2.0
#
# claude_usage/__init__.py's __version__ is the single source of truth;
# pyproject.toml reads it and setup.py stamps it into the .app plist.
set -euo pipefail
cd "$(dirname "$0")"

VERSION="${1:-}"
if ! printf '%s' "$VERSION" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$'; then
  echo "usage: ./release.sh X.Y.Z" >&2
  exit 1
fi
TAG="v$VERSION"

[ -z "$(git status --porcelain)" ] || { echo "working tree not clean" >&2; exit 1; }
[ "$(git rev-parse --abbrev-ref HEAD)" = "main" ] || { echo "not on main" >&2; exit 1; }
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "tag $TAG already exists" >&2; exit 1
fi

python3 - "$VERSION" <<'PY'
import pathlib, re, sys
version = sys.argv[1]
path = pathlib.Path("claude_usage/__init__.py")
text = path.read_text(encoding="utf-8")
new, n = re.subn(r'__version__ = "[^"]+"', f'__version__ = "{version}"', text, count=1)
if n == 0:
    sys.exit("could not find __version__ in claude_usage/__init__.py")
if new != text:
    path.write_text(new, encoding="utf-8")
PY

if git diff --quiet -- claude_usage/__init__.py; then
  echo "version already $VERSION — tagging current HEAD"
else
  git add claude_usage/__init__.py
  git commit -m "Release $TAG"
fi
git tag -a "$TAG" -m "$TAG"

echo
echo "tagged $TAG. push to trigger the release build:"
echo "  git push origin main && git push origin $TAG"
