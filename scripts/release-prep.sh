#!/usr/bin/env bash
# Usage: ./scripts/release-prep.sh <patch|minor|major>
#
# Creates a release branch with version bump + changelog,
# then opens a PR against main. Merge the PR, then run release-tag.sh.

set -euo pipefail

die()  { echo "error: $1" >&2; exit 1; }
info() { echo "==> $1"; }

# ── Args ─────────────────────────────────────────────────────────────
BUMP="${1:-}"
[[ "$BUMP" =~ ^(patch|minor|major)$ ]] || die "usage: $0 <patch|minor|major>"

# ── Prereqs ──────────────────────────────────────────────────────────
command -v git-cliff >/dev/null 2>&1 || die "git-cliff not found. Install: cargo install git-cliff"
command -v gh >/dev/null 2>&1 || die "gh CLI not found. Install: https://cli.github.com"
command -v uv >/dev/null 2>&1 || die "uv not found. Install: https://docs.astral.sh/uv/"

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

# ── Create release branch ────────────────────────────────────────────
RELEASE_BRANCH="chore/release-${TAG}"
git checkout -b "$RELEASE_BRANCH"

# ── Update version strings ───────────────────────────────────────────
sed -i '' "s/^version = \"${CURRENT}\"/version = \"${NEXT}\"/" pyproject.toml
sed -i '' "s/^version = \"${CURRENT}\"/version = \"${NEXT}\"/" packages/kensa-mcp/pyproject.toml
sed -i '' "s/kensa\[mcp\]==${CURRENT}/kensa[mcp]==${NEXT}/" packages/kensa-mcp/pyproject.toml

# ── Verify the updates worked ────────────────────────────────────────
VERIFY="$(python3 -c "
import tomllib, pathlib
cfg = tomllib.loads(pathlib.Path('pyproject.toml').read_text())
print(cfg['project']['version'])
")"
[[ "$VERIFY" == "$NEXT" ]] || die "pyproject.toml update failed (got $VERIFY)"

SHIM_VERIFY="$(python3 -c "
import tomllib, pathlib
cfg = tomllib.loads(pathlib.Path('packages/kensa-mcp/pyproject.toml').read_text())
print(cfg['project']['version'])
")"
[[ "$SHIM_VERIFY" == "$NEXT" ]] || die "shim pyproject.toml update failed (got $SHIM_VERIFY)"

SHIM_PIN="$(grep -oE 'kensa\[mcp\]==[0-9][^"]*' packages/kensa-mcp/pyproject.toml | sed 's/.*==//')"
[[ "$SHIM_PIN" == "$NEXT" ]] || die "shim dep pin update failed (got $SHIM_PIN)"

# ── Refresh lockfile so editable package version stays in sync ──────
info "refreshing uv.lock"
uv lock || die "uv lock failed"

LOCK_VERIFY="$(python3 -c "
import pathlib, re, sys
text = pathlib.Path('uv.lock').read_text()
match = re.search(r'^\[\[package\]\]\nname = \"kensa\"\nversion = \"([^\"]+)\"\nsource = \{ editable = \".\" \}', text, flags=re.MULTILINE)
if match is None:
    raise SystemExit('missing editable kensa entry in uv.lock')
sys.stdout.write(match.group(1))
")"
[[ "$LOCK_VERIFY" == "$NEXT" ]] || die "uv.lock update failed (got $LOCK_VERIFY)"

# ── Generate changelog ───────────────────────────────────────────────
info "generating changelog"
git-cliff --tag "$TAG" -o CHANGELOG.md

# ── Commit and open PR ───────────────────────────────────────────────
git add pyproject.toml packages/kensa-mcp/pyproject.toml uv.lock CHANGELOG.md
git commit -m "chore: release ${TAG}"
git push -u origin "$RELEASE_BRANCH"

gh pr create \
    --title "chore: release ${TAG}" \
    --body "Bump version to ${NEXT} and update changelog.

- [ ] Review and polish \`CHANGELOG.md\` before merging
- After merge, run \`./scripts/release-tag.sh ${TAG}\`"

info "PR created. Merge it, then run: ./scripts/release-tag.sh ${TAG}"
