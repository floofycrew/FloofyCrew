#!/bin/bash
# cut-release.sh — cut a FloofyCrew release by hand, in the public repository.
#
#   scripts/cut-release.sh <X.Y.Z | next> [--notes FILE] [--remote NAME] [--repo OWNER/NAME] [--check-only]
#
#   X.Y.Z          the version to release. `next` takes the highest vX.Y.Z tag on the remote and adds
#                  one to its patch number — the remote's tags are the truth; the Forge may have
#                  released since your last pull.
#   --notes FILE   the release's intro paragraph(s) for CHANGELOG.md (markdown). They go between the
#                  "## X.Y.Z — <date>" heading and the Supports table; the Unreleased bullets follow.
#                  Optional: without it the section opens with the table (a release that only folds
#                  the Forge's sections and ships the Unreleased bullets has nothing more to say).
#   --remote NAME  the git remote the tag must not exist on yet and the push goes to (default: origin)
#   --repo O/N     the GitHub repository for `gh release view`, when gh cannot infer it from the remote
#   --check-only   run every step up to and including the gates, then put every file back: nothing is
#                  committed or tagged. Exit 0 means a real run would get through.
#
# What it does, in order — aborting, and restoring every file it touched, on the first failure:
#   0. preconditions: run from the clone that holds it, on a branch, with a clean tracked tree
#      (git status --porcelain --untracked-files=no); a git identity set in this clone that is not
#      the Forge's; the remote's tags fetched; vX.Y.Z on neither the remote (git ls-remote --tags)
#      nor this clone; the branch not behind the remote; pyproject.toml not already at X.Y.Z; and
#      something to release — entries under "## Unreleased" in CHANGELOG.md, or Forge releases
#      waiting to be folded (scripts/fold_forge_releases.py --list)
#   1. fold: scripts/fold_forge_releases.py inserts one "## A.B.C — <tag date>" section per Forge
#      release since the changelog's newest heading, with the notes of its GitHub release
#      (gh release view), under "## Unreleased" — the Forge never edits CHANGELOG.md itself
#   2. bumps: pyproject.toml, floofy-core/floofy_core/__init__.py, loader-app/floofy_loader/__init__.py,
#      loader-app/app.json (whatever version each carries), then uv.lock (uv lock)
#   3. CHANGELOG.md: "## X.Y.Z — <today>", the notes and the Supports table (from pyproject's
#      [tool.floofycrew.supports]) go directly under "## Unreleased", so the Unreleased bullets
#      become the release's
#   4. README.md: the "Current release: **X.Y.Z**" line
#   5. gates, quick ones first: the hashed identifier scan over the whole tree
#      (scripts/check_no_internal_identifiers.py --hashed scripts/internal-identifiers.sha256),
#      packaging/public/build.sh --check, then the test suite; a failure keeps its full log
#   6. one commit, "chore(release): Release floofycrew X.Y.Z", and the annotated tag vX.Y.Z
#   7. prints the push command. The push is yours; this script never pushes.
#
# --check-only is the same run with the last two steps left out: it folds (so gh is called when a Forge
# release is waiting), bumps and gates the bumped tree, then puts every file back.
#
# The gate commands can be replaced through the environment — a test harness does this, a release
# should not. Each runs with `bash -c` from the repository root and passes on exit status 0:
#   CUT_RELEASE_PYTEST       default: TMPDIR=/tmp uv run pytest -q -p no:cacheprovider --basetemp=/tmp/fpt-cut-<pid>
#                            (a short --basetemp keeps the tests' unix-socket paths under the OS limit)
#   CUT_RELEASE_BUILD_CHECK  default: packaging/public/build.sh --check
#   CUT_RELEASE_SCAN         default: uv run python scripts/check_no_internal_identifiers.py --paths .
#                              --allowlist scripts/public-export-allow.txt --hashed scripts/internal-identifiers.sha256
#
# Needs bash, git, uv (the project's interpreter and uv.lock) and, when a Forge release has to be
# folded, gh. Exit status: 0 released (or, with --check-only, would be); 1 a precondition, the fold
# or a gate refused; 2 usage.
set -euo pipefail

USAGE="usage: scripts/cut-release.sh <X.Y.Z | next> [--notes FILE] [--remote NAME] [--repo OWNER/NAME] [--check-only]"
say() { printf '%s\n' "$*"; }
usage_error() { say "cut-release.sh: $*" >&2; say "$USAGE" >&2; exit 2; }

VER_ARG=""; NOTES=""; REMOTE=origin; REPO=""; CHECK_ONLY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --notes) [ $# -ge 2 ] || usage_error "--notes needs FILE"; NOTES=$2; shift 2;;
    --remote) [ $# -ge 2 ] || usage_error "--remote needs NAME"; REMOTE=$2; shift 2;;
    --repo) [ $# -ge 2 ] || usage_error "--repo needs OWNER/NAME"; REPO=$2; shift 2;;
    --check-only) CHECK_ONLY=1; shift;;
    -h|--help) sed -n '2,/^set -euo pipefail$/p' "$0" | sed '$d; s/^# \{0,1\}//'; exit 0;;
    -*) usage_error "unknown option: $1";;
    *) [ -z "$VER_ARG" ] || usage_error "one version only (got '$VER_ARG' and '$1')"; VER_ARG=$1; shift;;
  esac
done
[ -n "$VER_ARG" ] || usage_error "the version (X.Y.Z or next) is required"
[ -z "$NOTES" ] || [ -f "$NOTES" ] || usage_error "--notes: no such file: $NOTES"

# Every file a release edits. The clean-tree precondition makes `git checkout HEAD -- <these>` an exact undo.
TOUCHED=(pyproject.toml uv.lock floofy-core/floofy_core/__init__.py loader-app/app.json loader-app/floofy_loader/__init__.py CHANGELOG.md README.md)
RESTORE=0   # 1 while the working tree carries this script's edits and no commit holds them yet
WORK=""     # the gate logs
# shellcheck disable=SC2329  # invoked by the EXIT trap
cleanup() {
  local status=$?
  if [ "$RESTORE" = 1 ]; then
    git checkout -q HEAD -- "${TOUCHED[@]}" 2>/dev/null || true
    say "working tree restored (${#TOUCHED[@]} files back to HEAD)"
  fi
  if [ "$status" -eq 0 ] && [ -n "$WORK" ] && [ -d "$WORK" ]; then rm -rf -- "$WORK"; fi
}
trap cleanup EXIT
fail() { say "FAILED: $*" >&2; exit 1; }
lower() { printf '%s' "$1" | tr '[:upper:]' '[:lower:]'; }
is_version() { [[ $1 =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; }
# The highest X.Y.Z of the versions on stdin (one per line), compared number by number.
highest() {
  awk -F. 'BEGIN { b1 = -1; b2 = -1; b3 = -1 }
    { a1 = $1 + 0; a2 = $2 + 0; a3 = $3 + 0
      if (a1 > b1 || (a1 == b1 && (a2 > b2 || (a2 == b2 && a3 > b3)))) { b1 = a1; b2 = a2; b3 = a3; best = $0 } }
    END { print best }'
}

# --- 0. preconditions --------------------------------------------------------------------------------------------------
ROOT=$(cd "$(dirname "$0")/.." && pwd -P)
cd "$ROOT"
TOP=$(git rev-parse --show-toplevel 2>/dev/null) || fail "$ROOT is not a git work tree"
[ "$TOP" = "$ROOT" ] || fail "run this script from the clone that holds it ($ROOT is inside the work tree $TOP)"
command -v uv >/dev/null 2>&1 || fail "uv is required (the version bump runs 'uv lock'; the gates run under 'uv run')"
BRANCH=$(git branch --show-current)
[ -n "$BRANCH" ] || fail "HEAD is detached; a release is cut on a branch"
DIRTY=$(git status --porcelain --untracked-files=no)
[ -z "$DIRTY" ] || fail "uncommitted tracked changes:"$'\n'"$DIRTY"
# The identity is read from this clone's own .git/config only: the global one may belong to another
# world, and a name guessed from the machine must never reach a public commit (CONTRIBUTING.md has the values).
NAME=$(git config --local user.name || true); EMAIL=$(git config --local user.email || true)
[ -n "$NAME" ] && [ -n "$EMAIL" ] || fail "git user.name and user.email are not set in this clone's .git/config; set both (git config user.name/user.email) before cutting a release"
FORGE_EMAIL=${FORGE_GIT_EMAIL:-forge@floofycrew.invalid}
[ "$(lower "$EMAIL")" != "$(lower "$FORGE_EMAIL")" ] || fail "user.email is the Forge's identity ($FORGE_EMAIL); a hand-cut release is a person's"
say "branch: $BRANCH @ $(git rev-parse --short HEAD); identity: $NAME <$EMAIL>; remote: $REMOTE"

REMOTE_TAGS=$(git ls-remote --tags --refs "$REMOTE" 'refs/tags/v*') || fail "git ls-remote $REMOTE failed; is the remote reachable?"
REMOTE_VERSIONS=$(printf '%s\n' "$REMOTE_TAGS" | sed -n 's|^[0-9a-f]*[[:space:]]*refs/tags/v||p' | grep -E '^[0-9]+\.[0-9]+\.[0-9]+$' || true)
if [ "$VER_ARG" = next ]; then
  HIGHEST=$(printf '%s\n' "$REMOTE_VERSIONS" | highest)
  [ -n "$HIGHEST" ] || fail "no vX.Y.Z tag on $REMOTE to count from; give the version explicitly"
  VER="${HIGHEST%.*}.$(( ${HIGHEST##*.} + 1 ))"
  say "next: the highest tag on $REMOTE is v$HIGHEST, so this release is $VER"
else
  is_version "$VER_ARG" || usage_error "the version must be X.Y.Z (three integers) or next, got '$VER_ARG'"
  VER=$VER_ARG
fi
if grep -qxF "$VER" <<<"$REMOTE_VERSIONS"; then fail "v$VER already exists on $REMOTE"; fi
if git rev-parse -q --verify "refs/tags/v$VER" >/dev/null; then fail "tag v$VER already exists in this clone (not on $REMOTE): delete it or pick another version"; fi
FETCH_ERR=$(git fetch --quiet --tags "$REMOTE" 2>&1) || fail "git fetch --tags $REMOTE failed: $FETCH_ERR"
if git rev-parse -q --verify "refs/remotes/$REMOTE/$BRANCH" >/dev/null; then
  git merge-base --is-ancestor "refs/remotes/$REMOTE/$BRANCH" HEAD || fail "$BRANCH is behind $REMOTE/$BRANCH; pull first"
fi
OLD=$(sed -n 's/^version = "\(.*\)"$/\1/p' pyproject.toml | head -n 1)
[ -n "$OLD" ] || fail "pyproject.toml has no 'version = \"...\"' line"
[ "$OLD" != "$VER" ] || fail "pyproject.toml already says $VER"
grep -qE '^## Unreleased[ \t]*$' CHANGELOG.md || fail "CHANGELOG.md has no '## Unreleased' heading"
UNRELEASED=$(awk '/^## Unreleased[ \t]*$/ { f = 1; next } /^## / { f = 0 } f' CHANGELOG.md | grep -cE '^- ' || true)
PENDING=$(uv run -q python scripts/fold_forge_releases.py --list) || fail "scripts/fold_forge_releases.py --list failed"
PENDING_LIST=$(printf '%s' "$PENDING" | tr '\n' ' ')
if [ "$UNRELEASED" -eq 0 ] && [ -z "$PENDING" ]; then
  fail "nothing to release: CHANGELOG.md's '## Unreleased' has no entries and no Forge release is waiting to be folded"
fi
if [ -n "$PENDING" ]; then
  command -v gh >/dev/null 2>&1 || fail "gh is required to fold the Forge releases ($PENDING_LIST): their notes come from 'gh release view'"
fi
say "releasing $OLD -> $VER: $UNRELEASED unreleased entries; Forge releases to fold: ${PENDING_LIST:-none}"
[ "$CHECK_ONLY" = 0 ] || say "check-only: every edit below is undone at the end; nothing is committed or tagged"

# --- 1. fold the Forge's releases into CHANGELOG.md ---------------------------------------------------------------------
RESTORE=1
if [ -n "$PENDING" ]; then
  FOLD_ARGS=()
  [ -z "$REPO" ] || FOLD_ARGS=(--repo "$REPO")
  uv run -q python scripts/fold_forge_releases.py ${FOLD_ARGS[@]+"${FOLD_ARGS[@]}"} || fail "folding the Forge releases"
  for tag in $PENDING; do
    grep -qE "^## ${tag#v} " CHANGELOG.md || fail "CHANGELOG.md has no '## ${tag#v} —' heading after the fold"
  done
fi

# --- 2.-4. the version files, the changelog heading, the README line --------------------------------------------------
# One Python pass with the project's interpreter: every edit is a single anchored replacement that must
# match exactly once, so a file that lost its shape stops the release instead of shipping a disagreement.
uv run -q python - "$VER" "$NOTES" "$(date +%F)" <<'PY' || fail "editing the version files, CHANGELOG.md and README.md"
import json, re, sys, tomllib
from pathlib import Path

sys.path.insert(0, "scripts")
from fold_forge_releases import supports_table  # the same Supports table the folded sections carry

ver, notes_path, today = sys.argv[1:4]


def edit(path, pattern, replacement, what, flags=re.M):
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    bumped, hits = re.subn(pattern, replacement, text, count=1, flags=flags)
    if hits != 1:
        sys.exit(f"{path}: no {what} line to edit")
    file.write_text(bumped, encoding="utf-8")


edit("pyproject.toml", r'^version = "[^"]*"$', f'version = "{ver}"', 'version = "..."')
for path in ("floofy-core/floofy_core/__init__.py", "loader-app/floofy_loader/__init__.py"):
    edit(path, r'^__version__ = "[^"]*"$', f'__version__ = "{ver}"', '__version__ = "..."')
edit("loader-app/app.json", r'^(\s*"version": )"[^"]*"', rf'\g<1>"{ver}"', '"version": "..."')
if json.loads(Path("loader-app/app.json").read_text(encoding="utf-8")).get("version") != ver:
    sys.exit(f"loader-app/app.json: the top-level version is not {ver} after the bump")

changelog = Path("CHANGELOG.md")
text = changelog.read_text(encoding="utf-8")
head = "## Unreleased\n"
if text.count(head) != 1:
    sys.exit("CHANGELOG.md: expected exactly one '## Unreleased' heading")
if re.search(rf"^## {re.escape(ver)}(\s|$)", text, re.M):
    sys.exit(f"CHANGELOG.md already has a '## {ver}' heading")
supports = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["tool"]["floofycrew"]["supports"]
rows = [(edition, channel, ", ".join(versions)) for edition, channels in supports.items() for channel, versions in channels.items()]
notes = Path(notes_path).read_text(encoding="utf-8").strip() + "\n\n" if notes_path else ""
block = f"{head}\n## {ver} — {today}\n\n{notes}### Supports\n\n" + "\n".join(supports_table(rows)) + "\n"
changelog.write_text(text.replace(head, block, 1), encoding="utf-8")

edit("README.md", r"Current release: \*\*[^*]+\*\*", f"Current release: **{ver}**", "'Current release: **X.Y.Z**'", flags=0)
PY
uv lock -q || fail "uv lock"
LOCKED=$(awk '/^name = "floofycrew"$/ { getline; print; exit }' uv.lock)
[ "$LOCKED" = "version = \"$VER\"" ] || fail "uv.lock did not take the bump to $VER (its floofycrew entry says: ${LOCKED:-nothing})"
say "bumped to $VER: ${TOUCHED[*]}"

# --- 5. gates -----------------------------------------------------------------------------------------------------------
WORK=$(mktemp -d "${TMPDIR:-/tmp}/cut-release.XXXXXX")
PYTEST_CMD=${CUT_RELEASE_PYTEST:-"TMPDIR=/tmp uv run pytest -q -p no:cacheprovider --basetemp=/tmp/fpt-cut-$$"}
BUILD_CMD=${CUT_RELEASE_BUILD_CHECK:-"packaging/public/build.sh --check"}
SCAN_CMD=${CUT_RELEASE_SCAN:-"uv run python scripts/check_no_internal_identifiers.py --paths . --allowlist scripts/public-export-allow.txt --hashed scripts/internal-identifiers.sha256"}
# gate NAME COMMAND: run it from the root with its output in $WORK/NAME.log; its last line is reported.
gate() {
  local name=$1 cmd=$2 log="$WORK/$1.log"
  say "gate $name: $cmd"
  if (cd "$ROOT" && bash -c "$cmd") >"$log" 2>&1; then
    say "  ok: $(tail -n 1 "$log")"
    return 0
  fi
  grep -E '^(FAILED|ERROR)' "$log" | head -n 10 >&2 || true
  tail -n 5 "$log" >&2
  fail "gate $name failed (exit status of: $cmd); full output: $log"
}
gate identifiers "$SCAN_CMD"   # the quick gates first: a leaked name is reported in seconds, not after the test suite
gate packaging "$BUILD_CMD"
gate pytest "$PYTEST_CMD"
PYTEST_SUMMARY=$(tail -n 1 "$WORK/pytest.log")
if [ "$CHECK_ONLY" = 1 ]; then
  say "check-only: floofycrew $VER would get through; nothing committed or tagged"
  exit 0
fi

# --- 6. the release commit and tag ---------------------------------------------------------------------------------------
MSG=(-m "chore(release): Release floofycrew $VER")
[ -z "$NOTES" ] || MSG+=(-m "$(cat "$NOTES")")
[ -z "$PENDING" ] || MSG+=(-m "Folded into CHANGELOG.md from the tags: ${PENDING_LIST% }.")
MSG+=(-m "Gates: pytest: $PYTEST_SUMMARY; packaging: $(tail -n 1 "$WORK/packaging.log"); identifiers: $(tail -n 1 "$WORK/identifiers.log")")
git add -- "${TOUCHED[@]}"
git commit -q "${MSG[@]}" || fail "release commit"
RESTORE=0
git tag -a "v$VER" -m "floofycrew v$VER" || fail "tag v$VER (the release commit $(git rev-parse --short HEAD) is in place: tag it by hand)"

# --- 7. the push is yours -------------------------------------------------------------------------------------------------
say "RELEASED: floofycrew $VER — commit $(git rev-parse --short HEAD), annotated tag v$VER. Push with:"
say "  git push $REMOTE $BRANCH v$VER"
exit 0
