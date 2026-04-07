#!/usr/bin/env bash
# Usage: ./scripts/release.sh <patch|minor|major>
#
# Bumps the version in pyproject.toml,
# generates the changelog with git-cliff, commits, creates a git tag,
# and pushes to origin. GitHub Actions handles PyPI + GitHub Release.

set -euo pipefail

die()  { echo "error: $1" >&2; exit 1; }
info() { echo "==> $1"; }

# ── Args ─────────────────────────────────────────────────────────────
BUMP="${1:-}"
[[ "$BUMP" =~ ^(patch|minor|major)$ ]] || die "usage: $0 <patch|minor|major>"

# ── Prereqs ──────────────────────────────────────────────────────────
command -v git-cliff >/dev/null 2>&1 || die "git-cliff not found. Install: cargo install git-cliff"

# ── Must be on main with a clean tree ────────────────────────────────
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[[ "$BRANCH" == "main" ]] || die "must be on main branch (currently on $BRANCH)"

if ! git diff --quiet || ! git diff --cached --quiet; then
    die "working tree is dirty — commit or stash first"
fi

git pull --ff-only origin main || die "failed to pull latest main"

# ── Read current version ─────────────────────────────────────────────
CURRENT="$(python3 -c "
import tomllib, pathlib
cfg = tomllib.loads(pathlib.Path('pyproject.toml').read_text())
print(cfg['project']['version'])
")"
info "current version: $CURRENT"

IFS='.' read -r MAJOR MINOR PATCH <<< "$CURRENT"

# ── Compute next version ─────────────────────────────────────────────
case "$BUMP" in
    major) MAJOR=$((MAJOR + 1)); MINOR=0; PATCH=0 ;;
    minor) MINOR=$((MINOR + 1)); PATCH=0 ;;
    patch) PATCH=$((PATCH + 1)) ;;
esac

NEXT="${MAJOR}.${MINOR}.${PATCH}"
TAG="v${NEXT}"
info "next version:    $NEXT ($TAG)"

# ── Check tag doesn't already exist ──────────────────────────────────
git fetch --tags
if git rev-parse "$TAG" >/dev/null 2>&1; then
    die "tag $TAG already exists"
fi

# ── Update version strings ───────────────────────────────────────────
sed -i '' "s/^version = \"${CURRENT}\"/version = \"${NEXT}\"/" pyproject.toml

# ── Verify the update worked ─────────────────────────────────────────
VERIFY="$(python3 -c "
import tomllib, pathlib
cfg = tomllib.loads(pathlib.Path('pyproject.toml').read_text())
print(cfg['project']['version'])
")"
[[ "$VERIFY" == "$NEXT" ]] || die "pyproject.toml update failed (got $VERIFY)"

# ── Generate changelog ───────────────────────────────────────────────
info "generating changelog"
git-cliff --tag "$TAG" -o CHANGELOG.md

# ── Commit, tag, push ────────────────────────────────────────────────
info "committing version bump + changelog"
git add pyproject.toml CHANGELOG.md
git commit -m "chore: release ${TAG}"

info "tagging $TAG"
git tag -a "$TAG" -m "Release ${TAG}"

info "pushing to origin"
git push origin main
git push origin "$TAG"

info "done — $TAG pushed. GitHub Actions will handle PyPI + GitHub Release."
