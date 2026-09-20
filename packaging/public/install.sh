#!/bin/sh
# install.sh — install the FloofyCrew manager from a GitHub release (public edition; task 9.2).
#
#   curl -fsSL https://raw.githubusercontent.com/<org>/FloofyCrew/main/packaging/public/install.sh | sh
#
# Environment:
#   FLOOFYCREW_REPO     <org>/FloofyCrew (default floofycrew/FloofyCrew — the organisation is
#                       the owner item recorded with the registry repositories; change it there)
#   FLOOFYCREW_VERSION  a release tag (default: the latest release)
#   FLOOFYCREW_PREFIX   install prefix (default ~/.local)
#   FLOOFY_PYTHON       the interpreter to run the manager on (default: see below)
#   FLOOFYCREW_NO_INIT  set to 1 to stop after installing the command
#   FLOOFYCREW_ASSET_DIR a directory already holding the release assets (SHA256SUMS, floofy.pyz,
#                       the Loader app archive) — used instead of downloading; still verified
#
# What it does — HTTPS only, nothing executed before its hash is checked:
#   1. resolves the release tag through the GitHub API and downloads SHA256SUMS,
#      floofy.pyz and the Loader app archive from the release's assets;
#   2. verifies every download against SHA256SUMS (sha256sum or shasum);
#   3. installs the zipapp as $PREFIX/lib/floofycrew/floofy.pyz, keeps the Loader app
#      archive beside it, and writes the wrapper $PREFIX/bin/floofy, which runs the
#      zipapp on the KiroCrew host's own Python — the managed venv ~/.kiro/crew-venv
#      (or its newest crew-venv-<ver> sibling) or the pipx venv of kirocrew — else
#      python3.12 on PATH; the manager is standard-library only, so any 3.12 works,
#      and the host venv's interpreter lets `floofy` see the host through
#      find_spec("kiro_crew") without extra configuration;
#   4. runs `floofy init --loader-app <archive>` on your terminal: the one-time
#      warning (typed acknowledgement — nothing is installed or patched before it),
#      the Loader app installed through the host's App Kit, the re-apply trigger.
#      Piped through `sh` the script reads the acknowledgement from /dev/tty; without a
#      terminal it prints the command to run instead. It never accepts the warning for you.
#
# FloofyCrew is unofficial and not affiliated with Kiro or KiroCrew. It never modifies
# the host package (design DR-1); `floofy deinit` removes everything it installed.
set -eu

REPO="${FLOOFYCREW_REPO:-floofycrew/FloofyCrew}"
TAG="${FLOOFYCREW_VERSION:-}"
PREFIX="${FLOOFYCREW_PREFIX:-$HOME/.local}"
API="https://api.github.com/repos/$REPO/releases"
DOWNLOAD="https://github.com/$REPO/releases/download"

say() { printf '%s\n' "$*"; }
die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }

case "$REPO" in
  */*) ;;
  *) die "FLOOFYCREW_REPO must be <org>/<repo>, got '$REPO'" ;;
esac

if [ -n "${FLOOFYCREW_ASSET_DIR:-}" ]; then
  [ -d "$FLOOFYCREW_ASSET_DIR" ] || die "FLOOFYCREW_ASSET_DIR=$FLOOFYCREW_ASSET_DIR is not a directory"
  fetch() { cp "$FLOOFYCREW_ASSET_DIR/$(basename "$1")" "$2"; }
  [ -n "$TAG" ] || TAG="local"
elif command -v curl >/dev/null 2>&1; then
  fetch() { curl -fsSL --proto '=https' --tlsv1.2 -o "$2" "$1"; }
elif command -v wget >/dev/null 2>&1; then
  fetch() { wget -q --https-only -O "$2" "$1"; }
else
  die "curl or wget is required"
fi

if command -v sha256sum >/dev/null 2>&1; then
  digest() { sha256sum "$1" | cut -d' ' -f1; }
elif command -v shasum >/dev/null 2>&1; then
  digest() { shasum -a 256 "$1" | cut -d' ' -f1; }
else
  die "sha256sum or shasum is required to verify the download"
fi

# The interpreter rule, shared with the wrapper (one text, evaluated here and written there).
RESOLVE_PYTHON='
resolve_python() {
  if [ -n "${FLOOFY_PYTHON:-}" ]; then printf "%s\n" "$FLOOFY_PYTHON"; return 0; fi
  for cand in "$HOME/.kiro/crew-venv/bin/python3.12" "$HOME/.kiro/crew-venv/bin/python3" \
              "${PIPX_HOME:-$HOME/.local/share/pipx}/venvs/kirocrew/bin/python3" \
              "$HOME/.local/pipx/venvs/kirocrew/bin/python3"; do
    if [ -x "$cand" ] && "$cand" -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" 2>/dev/null; then
      printf "%s\n" "$cand"; return 0
    fi
  done
  newest=""
  for dir in "$HOME"/.kiro/crew-venv-*; do
    [ -x "$dir/bin/python3" ] && newest="$dir/bin/python3"
  done
  if [ -n "$newest" ] && "$newest" -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" 2>/dev/null; then
    printf "%s\n" "$newest"; return 0
  fi
  for name in python3.12 python3.13 python3.14 python3; do
    cand=$(command -v "$name" 2>/dev/null) || continue
    if "$cand" -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" 2>/dev/null; then
      printf "%s\n" "$cand"; return 0
    fi
  done
  return 1
}
'
eval "$RESOLVE_PYTHON"
PYTHON=$(resolve_python) || die "no Python 3.12+ found (neither a KiroCrew venv nor python3.12 on PATH); set FLOOFY_PYTHON"

WORK=$(mktemp -d "${TMPDIR:-/tmp}/floofycrew-install.XXXXXX")
trap 'rm -rf "$WORK"' EXIT INT TERM

if [ -z "$TAG" ]; then
  fetch "$API/latest" "$WORK/latest.json" || die "cannot read the latest release of $REPO (set FLOOFYCREW_VERSION to a tag)"
  TAG=$(sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$WORK/latest.json" | head -n 1)
  [ -n "$TAG" ] || die "no tag_name in the latest-release answer of $REPO"
fi
say "FloofyCrew release $TAG from $REPO"

fetch "$DOWNLOAD/$TAG/SHA256SUMS" "$WORK/SHA256SUMS" || die "release $TAG has no SHA256SUMS asset"
LOADER_ZIP=$(sed -n 's/^[0-9a-f]\{64\}  \(floofycrew-loader-app-[^ ]*\.zip\)$/\1/p' "$WORK/SHA256SUMS" | head -n 1)
[ -n "$LOADER_ZIP" ] || die "SHA256SUMS names no Loader app archive"

verify() {  # verify FILE against the SHA256SUMS line for its basename
  name=$(basename "$1")
  expected=$(sed -n "s/^\([0-9a-f]\{64\}\)  $name\$/\1/p" "$WORK/SHA256SUMS" | head -n 1)
  [ -n "$expected" ] || die "SHA256SUMS has no entry for $name"
  actual=$(digest "$1")
  [ "$actual" = "$expected" ] || die "sha256 mismatch for $name: expected $expected, got $actual"
  say "  verified $name ($expected)"
}

for asset in floofy.pyz "$LOADER_ZIP"; do
  fetch "$DOWNLOAD/$TAG/$asset" "$WORK/$asset" || die "cannot download $asset from release $TAG"
  verify "$WORK/$asset"
done

LIB="$PREFIX/lib/floofycrew"
mkdir -p "$LIB" "$PREFIX/bin"
cp "$WORK/floofy.pyz" "$LIB/floofy.pyz" && chmod 0644 "$LIB/floofy.pyz"
cp "$WORK/$LOADER_ZIP" "$LIB/$LOADER_ZIP" && chmod 0644 "$LIB/$LOADER_ZIP"
cp "$WORK/SHA256SUMS" "$LIB/SHA256SUMS"
WRAPPER="$PREFIX/bin/floofy"
{
  printf '#!/bin/sh\n'
  printf '# floofy — the FloofyCrew mod manager for KiroCrew (unofficial). Installed by packaging/public/install.sh.\n'
  printf '# Runs the zipapp on the KiroCrew host venv interpreter (re-resolved every run), else python3.12.\n'
  printf 'set -u\n'
  printf '%s\n' "$RESOLVE_PYTHON"
  printf 'PYZ="%s"\n' "$LIB/floofy.pyz"
  printf 'PYTHON=$(resolve_python) || { echo "floofy: no Python 3.12+ found (set FLOOFY_PYTHON)" >&2; exit 1; }\n'
  printf 'exec "$PYTHON" "$PYZ" "$@"\n'
} > "$WRAPPER"
chmod 0755 "$WRAPPER"
say "installed $LIB/floofy.pyz and $WRAPPER (interpreter: $PYTHON)"
"$WRAPPER" --version
case ":$PATH:" in *":$PREFIX/bin:"*) ;; *) say "note: $PREFIX/bin is not on PATH" ;; esac

if [ "${FLOOFYCREW_NO_INIT:-0}" = 1 ]; then
  say "skipped floofy init (FLOOFYCREW_NO_INIT=1); run: $WRAPPER init --loader-app $LIB/$LOADER_ZIP"
  exit 0
fi
if [ -t 0 ]; then
  exec "$WRAPPER" init --loader-app "$LIB/$LOADER_ZIP"
elif [ -r /dev/tty ] && [ -w /dev/tty ]; then
  say ""
  say "Initialising FloofyCrew (the one-time warning, the Loader app, the re-apply trigger) on your terminal…"
  exec "$WRAPPER" init --loader-app "$LIB/$LOADER_ZIP" </dev/tty >/dev/tty 2>&1
else
  say "no terminal to acknowledge the one-time warning on; run: $WRAPPER init --loader-app $LIB/$LOADER_ZIP"
fi
