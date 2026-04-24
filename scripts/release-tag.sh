#!/usr/bin/env bash
# Usage: ./scripts/release-tag.sh <tag>
#
# Tags the current main HEAD and pushes the tag.
# Run AFTER the release PR is merged.
# GitHub Actions handles PyPI + GitHub Release from the tag.

set -euo pipefail

die()  { echo "error: $1" >&2; exit 1; }
info() { echo "==> $1"; }

# ── Args ─────────────────────────────────────────────────────────────
TAG="${1:-}"
[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "usage: $0 <tag> (e.g. v0.3.0)"

# ── Must be on main ─────────────────────────────────────────────────
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[[ "$BRANCH" == "main" ]] || die "must be on main branch (currently on $BRANCH)"

git pull --ff-only origin main || die "failed to pull latest main"

# ── Verify tag matches pyproject.toml ────────────────────────────────
EXPECTED="${TAG#v}"
ACTUAL="$(python3 -c "
import tomllib, pathlib
cfg = tomllib.loads(pathlib.Path('pyproject.toml').read_text())
print(cfg['project']['version'])
")"
[[ "$ACTUAL" == "$EXPECTED" ]] || die "pyproject.toml version ($ACTUAL) does not match tag ($EXPECTED). Was the release PR merged?"

LOCK_VERSION="$(python3 -c "
import pathlib, re, sys
text = pathlib.Path('uv.lock').read_text()
match = re.search(r'^\[\[package\]\]\nname = \"kensa\"\nversion = \"([^\"]+)\"\nsource = \{ editable = \".\" \}', text, flags=re.MULTILINE)
if match is None:
    raise SystemExit('missing editable kensa entry in uv.lock')
sys.stdout.write(match.group(1))
")"
[[ "$LOCK_VERSION" == "$EXPECTED" ]] || die "uv.lock version ($LOCK_VERSION) does not match tag ($EXPECTED). Run uv lock and commit the result."

# ── Check tag doesn't already exist ──────────────────────────────────
if git rev-parse "$TAG" >/dev/null 2>&1; then
    die "tag $TAG already exists"
fi

# ── Tag and push ─────────────────────────────────────────────────────
info "tagging $TAG"
git tag -a "$TAG" -m "Release ${TAG}"

info "pushing tag"
git push origin "$TAG"

info "done — $TAG pushed. GitHub Actions will handle PyPI + GitHub Release."
