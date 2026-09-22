#!/bin/sh
# install.sh — install the FloofyCrew manager from a GitHub release (public edition; tasks 9.2, 10.3).
#
#   curl -fsSL https://raw.githubusercontent.com/<org>/FloofyCrew/main/packaging/public/install.sh | sh
#   sh install.sh [--no-init] [--trigger default|timer|wrapper|none] [--prefix DIR] [--no-color] [--no-banner]
#
# Environment:
#   FLOOFYCREW_REPO     <org>/FloofyCrew (default floofycrew/FloofyCrew — the organisation is
#                       the owner item recorded with the registry repositories; change it there)
#   FLOOFYCREW_VERSION  a release tag (default: the latest release)
#   FLOOFYCREW_PREFIX   install prefix (default ~/.local)
#   FLOOFY_PYTHON       the interpreter to run the manager on (default: see below)
#   FLOOFYCREW_NO_INIT  set to 1 to stop after installing the command
#   FLOOFYCREW_TRIGGER  the re-apply trigger to install: default (hourly user timer + the
#                       kirocrew PATH wrapper), timer, wrapper or none — the answer of menu 4a
#   FLOOFYCREW_NO_BANNER set to 1 to skip the banner (NO_COLOR skips it too)
#   FLOOFYCREW_ASSET_DIR a directory already holding the release assets (SHA256SUMS, floofy.pyz,
#                       the Loader app archive) — used instead of downloading; still verified
#   NO_COLOR            any value: no colour, no banner (Requirement 15.2; also off when
#                       stdout is not a terminal, on TERM=dumb, and with --no-color)
#
# What it does — HTTPS only, nothing executed before its hash is checked — in five numbered steps:
#   1. picks the Python that runs the manager: the KiroCrew host's own venv ~/.kiro/crew-venv
#      (or its newest crew-venv-<ver> sibling) or the pipx venv of kirocrew — else python3.12
#      on PATH; the manager is standard-library only, so any 3.12 works, and the host venv's
#      interpreter lets `floofy` see the host through find_spec("kiro_crew");
#   2. resolves the release tag through the GitHub API and downloads SHA256SUMS, floofy.pyz
#      and the Loader app archive from the release's assets, verifying every download
#      against SHA256SUMS (sha256sum or shasum);
#   3. installs the zipapp as $PREFIX/lib/floofycrew/floofy.pyz, keeps the Loader app
#      archive beside it, and writes the wrapper $PREFIX/bin/floofy, which runs the zipapp
#      on the interpreter of step 1 (re-resolved every run);
#   4. asks its two questions as numbered menus with a default — the re-apply trigger to
#      install and whether to run `floofy init` now; without a terminal the defaults are
#      taken and printed, so a piped or scripted run never blocks;
#   5. runs `floofy init --loader-app <archive>` on your terminal: the one-time warning
#      (a full-screen [ I AGREE ] control, or the typed acknowledgement — nothing is
#      installed or patched before it), the Loader app installed through the host's App
#      Kit, the re-apply trigger. Piped through `sh` the script talks to /dev/tty; without a
#      terminal it prints the command to run instead. It never accepts the warning for you.
#
# FloofyCrew is unofficial and not affiliated with Kiro or KiroCrew. It never modifies
# the host package (design DR-1); `floofy deinit` removes everything it installed.
set -eu

REPO="${FLOOFYCREW_REPO:-floofycrew/FloofyCrew}"
TAG="${FLOOFYCREW_VERSION:-}"
PREFIX="${FLOOFYCREW_PREFIX:-$HOME/.local}"
RUN_INIT=1
[ "${FLOOFYCREW_NO_INIT:-0}" = 1 ] && RUN_INIT=0
TRIGGER="${FLOOFYCREW_TRIGGER:-}"
NO_COLOR_FLAG=0
NO_BANNER="${FLOOFYCREW_NO_BANNER:-0}"
STEPS=5

while [ $# -gt 0 ]; do
  case "$1" in
    --no-init) RUN_INIT=0; shift ;;
    --trigger) TRIGGER="${2:?--trigger needs default|timer|wrapper|none}"; shift 2 ;;
    --prefix) PREFIX="${2:?--prefix needs a directory}"; shift 2 ;;
    --no-color) NO_COLOR_FLAG=1; shift ;;
    --no-banner) NO_BANNER=1; shift ;;
    -h|--help) sed -n '2,43p' "$0"; exit 0 ;;
    *) printf 'install.sh: unknown argument %s\n' "$1" >&2; exit 2 ;;
  esac
done

API="https://api.github.com/repos/$REPO/releases"
DOWNLOAD="https://github.com/$REPO/releases/download"

# --- terminal: palette, glyphs, banner, menus (Requirement 15.2) ----------------------------------------
# Styling is off when stdout is not a terminal, when NO_COLOR is set (any value), on TERM=dumb,
# and with --no-color — the same switches as `floofy` itself (Requirement 15.1).
STYLE=0
if [ -t 1 ] && [ -z "${NO_COLOR+set}" ] && [ "$NO_COLOR_FLAG" = 0 ] && [ "${TERM:-}" != dumb ]; then STYLE=1; fi
# stdin, or /dev/tty when the script is piped into sh on a terminal; empty when there is no terminal to ask on
TTY_IN=""
if [ -t 0 ]; then TTY_IN=-; elif [ -t 1 ] && ( : </dev/tty ) 2>/dev/null; then TTY_IN=/dev/tty; fi
case "${LC_ALL:-${LC_CTYPE:-${LANG:-}}}" in
  *[Uu][Tt][Ff]-8*|*[Uu][Tt][Ff]8*) G_OK='✓'; G_FAIL='✗'; G_TODO='·' ;;
  *) G_OK='+'; G_FAIL='x'; G_TODO='-' ;;
esac

paint() {  # paint TONE TEXT — TONE: heading ok warn danger muted accent bold dim
  if [ "$STYLE" = 1 ]; then
    case "$1" in
      heading) _sgr='1;33' ;; ok) _sgr='32' ;; warn) _sgr='33' ;; danger) _sgr='1;91' ;;
      muted|dim) _sgr='2' ;; accent) _sgr='95' ;; bold) _sgr='1' ;; *) _sgr='0' ;;
    esac
    printf '\033[%sm%s\033[0m' "$_sgr" "$2"
  else
    printf '%s' "$2"
  fi
}
say() { printf '%s\n' "$*"; }
step() { printf '%s %s %s\n' "$(paint accent "[$1/$STEPS]")" "$(paint muted "$G_TODO")" "$2"; }          # a step that starts
done_step() { printf '%s %s %s\n' "$(paint accent "[$1/$STEPS]")" "$(paint ok "$G_OK")" "$2"; }          # a step that finished
fail() { printf '%s %s install.sh: %s\n' "$(paint accent "[$1/$STEPS]")" "$(paint danger "$G_FAIL")" "$2" >&2; exit 1; }
die() { printf 'install.sh: %s\n' "$*" >&2; exit 1; }

# The banner is the FloofyCrew mark (branding/ascii/floofy.ans and .txt, embedded because a piped
# script has no files beside it): 24-bit where COLORTERM says so, else one colour; skipped when
# colours are off, with --no-banner / FLOOFYCREW_NO_BANNER=1, or on a terminal narrower than 42.
# From 110 columns the wide art (floofy-wide.*: the fox with the "FloofyCrew" wordmark beside it,
# as the interactive floofy draws it) is printed instead. The literals below are written by
# scripts/refresh_install_banners.py — never edit them by hand.
banner() {
  [ "$STYLE" = 1 ] || return 0
  [ "$NO_BANNER" = 1 ] && return 0
  _cols="${COLUMNS:-$(tput cols 2>/dev/null || echo 80)}"
  case "$_cols" in ''|*[!0-9]*) _cols=80 ;; esac
  [ "$_cols" -ge 42 ] || return 0
  _ans=$BANNER_ANS; _txt=$BANNER_TXT
  if [ "$_cols" -ge 110 ]; then _ans=$BANNER_WIDE_ANS; _txt=$BANNER_WIDE_TXT; fi
  case "${COLORTERM:-}" in
    truecolor|24bit) printf '%b' "$_ans" | sed 's/^/  /' ;;
    *) printf '\033[95m'; printf '%s\n' "$_txt" | sed 's/^/  /'; printf '\033[0m' ;;
  esac
  say ""
}

# ask_menu VAR DEFAULT PROMPT OPTION... — a numbered menu; an empty answer takes the default, and a
# run without a terminal takes it silently (printed), so automation never blocks (Requirement 15.2).
ask_menu() {
  _var=$1; _default=$2; _prompt=$3; shift 3
  _n=$#; _i=1
  say ""
  say "$(paint bold "$_prompt")"
  for _opt in "$@"; do
    if [ "$_i" = "$_default" ]; then say "  $(paint accent "$_i)") $_opt $(paint muted "(default)")"; else say "  $(paint accent "$_i)") $_opt"; fi
    _i=$((_i + 1))
  done
  _choice=""
  if [ -n "$TTY_IN" ]; then
    while :; do
      printf '%s' "  choice [$_default]: "
      if [ "$TTY_IN" = - ]; then IFS= read -r _choice || _choice=""; else IFS= read -r _choice <"$TTY_IN" || _choice=""; fi
      [ -n "$_choice" ] || _choice=$_default
      case "$_choice" in
        *[!0-9]*) ;;
        *) if [ "$_choice" -ge 1 ] && [ "$_choice" -le "$_n" ]; then break; fi ;;
      esac
      say "  $(paint warn "please answer a number between 1 and $_n")"
    done
  else
    _choice=$_default
    say "  choice: $_default $(paint muted "(default: no terminal to ask on)")"
  fi
  eval "$_var=\$_choice"
}

BANNER_TXT='   .                   ..         
   %%.                .%+         
   %%%.              +%%%         
   %##%+    ....    +%##%   .     
   %###%%%+%%%%%++%%%###%   +@.   
  .%###%%%%%%%%%%%%%%###%   +@@.  
  .%%%%%%%%%%%%%%%%%%%%%%.  @@@@  
   %%%%%%%%%%%%%%%%%%%%%+   @@@@+ 
   +%%%%%%%%%%%%%%%%%%%%.  +@@@@% 
  %%%%%%%%%%%%%%%%%%%%%%%+.%%@@%% 
  %%%%%%%#..%%%#..%%%%%%%%%%%%%%% 
  .%%%%%%#..#%%#..%%%%%%%%%%%%%%. 
  %%%%%%%%%%%%%%%%%%%%%%%%%%%%%.  
  +%%%%%%%%%%%%%%%%%%%%%%%%%%+    
   ..................++++..       '
BANNER_ANS='   \033[38;2;255;228;207m.                   ..         \033[0m
   \033[38;2;255;228;207m%%.                .%+         \033[0m
   \033[38;2;255;228;207m%%%.              +%%%         \033[0m
   \033[38;2;255;228;207m%\033[38;2;255;191;174m##\033[38;2;255;228;207m%+    ....    +%\033[38;2;255;191;174m##\033[38;2;255;228;207m%   \033[38;2;255;255;255m.     \033[0m
   \033[38;2;255;228;207m%\033[38;2;255;191;174m###\033[38;2;255;228;207m%%%+%%%%%++%%%\033[38;2;255;191;174m###\033[38;2;255;228;207m%   \033[38;2;255;255;255m+@.   \033[0m
  \033[38;2;255;228;207m.%\033[38;2;255;191;174m###\033[38;2;255;228;207m%%%%%%%%%%%%%%\033[38;2;255;191;174m###\033[38;2;255;228;207m%   \033[38;2;255;255;255m+@@.  \033[0m
  \033[38;2;255;228;207m.%%%%%%%%%%%%%%%%%%%%%%.  \033[38;2;255;255;255m@@@@  \033[0m
   \033[38;2;255;228;207m%%%%%%%%%%%%%%%%%%%%%+   \033[38;2;255;255;255m@@@@+ \033[0m
   \033[38;2;255;228;207m+%%%%%%%%%%%%%%%%%%%%.  +\033[38;2;255;255;255m@@@@\033[38;2;255;228;207m% \033[0m
  \033[38;2;255;228;207m%%%%%%%%%%%%%%%%%%%%%%%+.%%\033[38;2;255;255;255m@@\033[38;2;255;228;207m%% \033[0m
  \033[38;2;255;228;207m%%%%%%%\033[38;2;255;191;174m#\033[38;2;138;59;76m..\033[38;2;255;228;207m%%%\033[38;2;255;191;174m#\033[38;2;138;59;76m..\033[38;2;255;228;207m%%%%%%%%%%%%%%% \033[0m
  \033[38;2;255;228;207m.%%%%%%\033[38;2;255;191;174m#\033[38;2;138;59;76m..\033[38;2;255;191;174m#\033[38;2;255;228;207m%%\033[38;2;255;191;174m#\033[38;2;138;59;76m..\033[38;2;255;228;207m%%%%%%%%%%%%%%. \033[0m
  \033[38;2;255;228;207m%%%%%%%%%%%%%%%%%%%%%%%%%%%%%.  \033[0m
  \033[38;2;255;228;207m+%%%%%%%%%%%%%%%%%%%%%%%%%%+    \033[0m
   \033[38;2;255;228;207m..................++++..       \033[0m'
BANNER_WIDE_ANS='   \033[38;2;255;228;207m.                   ..                                                                                   \033[0m
   \033[38;2;255;228;207m%%.                .%+                                                                                   \033[0m
   \033[38;2;255;228;207m%%%.              +%%%                                                                                   \033[0m
   \033[38;2;255;228;207m%\033[38;2;255;191;174m##\033[38;2;255;228;207m%+    ....    +%\033[38;2;255;191;174m##\033[38;2;255;228;207m%   \033[38;2;255;255;255m.                                                                               \033[0m
   \033[38;2;255;228;207m%\033[38;2;255;191;174m###\033[38;2;255;228;207m%%%+%%%%%++%%%\033[38;2;255;191;174m###\033[38;2;255;228;207m%   \033[38;2;255;255;255m+@.       \033[38;2;255;228;207m...... ..                    ..          \033[38;2;255;123;109m.\033[38;2;255;130;100m.\033[38;2;255;137;90m.\033[38;2;255;142;81m.\033[38;2;255;148;69m.                        \033[0m
  \033[38;2;255;228;207m.%\033[38;2;255;191;174m###\033[38;2;255;228;207m%%%%%%%%%%%%%%\033[38;2;255;191;174m###\033[38;2;255;228;207m%   \033[38;2;255;255;255m+@@.     \033[38;2;255;228;207m+#++++. ##.                 .##+        \033[38;2;255;112;130m+\033[38;2;255;118;120m#\033[38;2;255;125;111m#\033[38;2;255;130;100m+\033[38;2;255;137;91m+\033[38;2;255;142;80m#\033[38;2;255;148;71m#\033[38;2;255;154;61m.                       \033[0m
  \033[38;2;255;228;207m.%%%%%%%%%%%%%%%%%%%%%%.  \033[38;2;255;255;255m@@@@     \033[38;2;255;228;207m+#+...  ##. .#####. .####+ +###++#. .#+\033[38;2;255;106;140m.\033[38;2;255;112;130m#\033[38;2;255;118;120m#      \033[38;2;255;107;138m.\033[38;2;255;115;126m#\033[38;2;255;125;109m#\033[38;2;255;136;91m#\033[38;2;255;146;75m#\033[38;2;255;154;62m.\033[38;2;255;113;128m+\033[38;2;255;120;117m#\033[38;2;255;129;105m+\033[38;2;255;135;91m+\033[38;2;255;142;80m#\033[38;2;255;150;67m.\033[38;2;255;104;139m.\033[38;2;255;111;133m#\033[38;2;255;115;127m+  \033[38;2;255;129;104m#\033[38;2;255;133;96m#  \033[38;2;255;146;74m+\033[38;2;255;151;67m#\033[0m
   \033[38;2;255;228;207m%%%%%%%%%%%%%%%%%%%%%+   \033[38;2;255;255;255m@@@@+    \033[38;2;255;228;207m+#+...  ##. ##.  ## ##  .#+ +#.  ##.## \033[38;2;255;107;139m.\033[38;2;255;112;130m#\033[38;2;255;118;120m#      \033[38;2;255;106;139m.\033[38;2;255;115;125m#\033[38;2;255;125;109m#  \033[38;2;255;107;139m.\033[38;2;255;114;129m#\033[38;2;255;120;117m#\033[38;2;255;128;104m+\033[38;2;255;135;92m+\033[38;2;255;143;80m#\033[38;2;255;149;67m# \033[38;2;255;110;133m+\033[38;2;255;116;126m#\033[38;2;255;121;119m.\033[38;2;255;124;111m#\033[38;2;255;128;103m#\033[38;2;255;133;96m#\033[38;2;255;138;88m#\033[38;2;255;141;81m.\033[38;2;255;146;74m#\033[38;2;255;150;67m+\033[0m
   \033[38;2;255;228;207m+%%%%%%%%%%%%%%%%%%%%.  +\033[38;2;255;255;255m@@@@\033[38;2;255;228;207m%    +#.     ##+.+##++#+ ##++##. +#.  .###   \033[38;2;255;112;130m+\033[38;2;255;118;120m#\033[38;2;255;124;110m#\033[38;2;255;130;100m+\033[38;2;255;136;90m+\033[38;2;255;143;81m+\033[38;2;255;148;70m#\033[38;2;255;153;62m.\033[38;2;255;108;140m.\033[38;2;255;115;126m#\033[38;2;255;126;109m#   \033[38;2;255;114;129m#\033[38;2;255;121;117m#\033[38;2;255;128;104m+\033[38;2;255;135;92m+\033[38;2;255;142;80m+\033[38;2;255;151;68m+  \033[38;2;255;115;126m#\033[38;2;255;120;118m#\033[38;2;255;124;111m#\033[38;2;255;129;104m.\033[38;2;255;133;97m.\033[38;2;255;137;88m#\033[38;2;255;142;81m#\033[38;2;255;146;74m# \033[0m
  \033[38;2;255;228;207m%%%%%%%%%%%%%%%%%%%%%%%+.%%\033[38;2;255;255;255m@@\033[38;2;255;228;207m%%    ..       ...  ...    ....   ..    ##.     \033[38;2;255;125;110m.\033[38;2;255;130;100m.\033[38;2;255;137;90m.\033[38;2;255;143;81m.   \033[38;2;255;115;125m.\033[38;2;255;126;110m.    \033[38;2;255;120;117m.\033[38;2;255;128;104m.\033[38;2;255;136;91m.\033[38;2;255;142;80m.    \033[38;2;255;120;118m.\033[38;2;255;123;112m.  \033[38;2;255;138;87m.\033[38;2;255;142;82m.  \033[0m
  \033[38;2;255;228;207m%%%%%%%\033[38;2;255;191;174m#\033[38;2;138;59;76m..\033[38;2;255;228;207m%%%\033[38;2;255;191;174m#\033[38;2;138;59;76m..\033[38;2;255;228;207m%%%%%%%%%%%%%%%                                                                           \033[0m
  \033[38;2;255;228;207m.%%%%%%\033[38;2;255;191;174m#\033[38;2;138;59;76m..\033[38;2;255;191;174m#\033[38;2;255;228;207m%%\033[38;2;255;191;174m#\033[38;2;138;59;76m..\033[38;2;255;228;207m%%%%%%%%%%%%%%.                                                                           \033[0m
  \033[38;2;255;228;207m%%%%%%%%%%%%%%%%%%%%%%%%%%%%%.                                                                            \033[0m
  \033[38;2;255;228;207m+%%%%%%%%%%%%%%%%%%%%%%%%%%+                                                                              \033[0m
   \033[38;2;255;228;207m..................++++..                                                                                 \033[0m'
BANNER_WIDE_TXT='   .                   ..                                                                                   
   %%.                .%+                                                                                   
   %%%.              +%%%                                                                                   
   %##%+    ....    +%##%   .                                                                               
   %###%%%+%%%%%++%%%###%   +@.       ...... ..                    ..          .....                        
  .%###%%%%%%%%%%%%%%###%   +@@.     +#++++. ##.                 .##+        +##++##.                       
  .%%%%%%%%%%%%%%%%%%%%%%.  @@@@     +#+...  ##. .#####. .####+ +###++#. .#+.##      .####.+#++#..#+  ##  +#
   %%%%%%%%%%%%%%%%%%%%%+   @@@@+    +#+...  ##. ##.  ## ##  .#+ +#.  ##.## .##      .##  .##++## +#.####.#+
   +%%%%%%%%%%%%%%%%%%%%.  +@@@@%    +#.     ##+.+##++#+ ##++##. +#.  .###   +##+++#..##   ##++++  ###..### 
  %%%%%%%%%%%%%%%%%%%%%%%+.%%@@%%    ..       ...  ...    ....   ..    ##.     ....   ..    ....    ..  ..  
  %%%%%%%#..%%%#..%%%%%%%%%%%%%%%                                                                           
  .%%%%%%#..#%%#..%%%%%%%%%%%%%%.                                                                           
  %%%%%%%%%%%%%%%%%%%%%%%%%%%%%.                                                                            
  +%%%%%%%%%%%%%%%%%%%%%%%%%%+                                                                              
   ..................++++..                                                                                 '

banner
say "$(paint heading "FloofyCrew installer") $(paint muted "— unofficial; not affiliated with Kiro or KiroCrew")"
say ""

case "$REPO" in
  */*) ;;
  *) die "FLOOFYCREW_REPO must be <org>/<repo>, got '$REPO'" ;;
esac
case "$TRIGGER" in
  ""|default|timer|wrapper|none) ;;
  *) die "FLOOFYCREW_TRIGGER/--trigger must be default, timer, wrapper or none (got '$TRIGGER')" ;;
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

# --- [1/5] the interpreter --------------------------------------------------------------------------------
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
step 1 "Python: looking for the KiroCrew venv, then python3.12 on PATH"
PYTHON=$(resolve_python) || fail 1 "no Python 3.12+ found (neither a KiroCrew venv nor python3.12 on PATH); set FLOOFY_PYTHON"
done_step 1 "Python: $PYTHON"

# --- [2/5] the release -------------------------------------------------------------------------------------
WORK=$(mktemp -d "${TMPDIR:-/tmp}/floofycrew-install.XXXXXX")
trap 'rm -rf "$WORK"' EXIT INT TERM

if [ -z "$TAG" ]; then
  step 2 "Release: asking $REPO for its latest release"
  fetch "$API/latest" "$WORK/latest.json" || fail 2 "cannot read the latest release of $REPO (set FLOOFYCREW_VERSION to a tag)"
  TAG=$(sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$WORK/latest.json" | head -n 1)
  [ -n "$TAG" ] || fail 2 "no tag_name in the latest-release answer of $REPO"
fi
step 2 "Release: FloofyCrew $TAG from $REPO — downloading and verifying the assets"

fetch "$DOWNLOAD/$TAG/SHA256SUMS" "$WORK/SHA256SUMS" || fail 2 "release $TAG has no SHA256SUMS asset"
LOADER_ZIP=$(sed -n 's/^[0-9a-f]\{64\}  \(floofycrew-loader-app-[^ ]*\.zip\)$/\1/p' "$WORK/SHA256SUMS" | head -n 1)
[ -n "$LOADER_ZIP" ] || fail 2 "SHA256SUMS names no Loader app archive"

verify() {  # verify FILE against the SHA256SUMS line for its basename
  name=$(basename "$1")
  expected=$(sed -n "s/^\([0-9a-f]\{64\}\)  $name\$/\1/p" "$WORK/SHA256SUMS" | head -n 1)
  [ -n "$expected" ] || fail 2 "SHA256SUMS has no entry for $name"
  actual=$(digest "$1")
  [ "$actual" = "$expected" ] || fail 2 "sha256 mismatch for $name: expected $expected, got $actual"
  say "        $(paint ok "$G_OK") verified $name ($(paint muted "$expected"))"
}

for asset in floofy.pyz "$LOADER_ZIP"; do
  fetch "$DOWNLOAD/$TAG/$asset" "$WORK/$asset" || fail 2 "cannot download $asset from release $TAG"
  verify "$WORK/$asset"
done
done_step 2 "Release: $TAG verified against SHA256SUMS"

# --- [3/5] the command -------------------------------------------------------------------------------------
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
VERSION_LINE=$("$WRAPPER" --version) || fail 3 "$WRAPPER --version failed"
done_step 3 "Command: installed $LIB/floofy.pyz and $WRAPPER (interpreter: $PYTHON)"
say "        $VERSION_LINE"
PATH_NOTE=""
case ":$PATH:" in *":$PREFIX/bin:"*) ;; *) PATH_NOTE="note: $PREFIX/bin is not on PATH"; say "        $(paint warn "$PATH_NOTE")" ;; esac

# --- [4/5] the questions -----------------------------------------------------------------------------------
step 4 "Choices: the re-apply trigger and whether to initialise now"
NO_TTY=0
if [ "$RUN_INIT" != 1 ]; then
  say "  floofy init: skipped $(paint muted "(FLOOFYCREW_NO_INIT=1 / --no-init)") — the command below runs it later"
  [ -n "$TRIGGER" ] && say "  re-apply trigger: $TRIGGER $(paint muted "(from FLOOFYCREW_TRIGGER/--trigger)")"
else
  if [ -z "$TRIGGER" ]; then
    ask_menu TRIGGER_CHOICE 1 "Which re-apply trigger keeps your mods applied after a KiroCrew update?" \
      "hourly user timer (systemd/launchd) and the kirocrew PATH wrapper — the edition's default set" \
      "hourly user timer only" \
      "kirocrew PATH wrapper only" \
      "none (floofy init --no-trigger; run floofy apply yourself after updates)"
    case "$TRIGGER_CHOICE" in 1) TRIGGER=default ;; 2) TRIGGER=timer ;; 3) TRIGGER=wrapper ;; 4) TRIGGER=none ;; esac
  else
    say "  re-apply trigger: $TRIGGER $(paint muted "(from FLOOFYCREW_TRIGGER/--trigger)")"
  fi
  if [ -z "$TTY_IN" ]; then
    RUN_INIT=0; NO_TTY=1
    say ""
    say "  floofy init: later $(paint muted "(no terminal to acknowledge the one-time warning on)")"
  else
    ask_menu INIT_CHOICE 1 "Run floofy init now? It shows the one-time warning, installs the Loader app and the trigger." \
      "yes — initialise now" \
      "later — print the command instead"
    [ "$INIT_CHOICE" = 1 ] || RUN_INIT=0
  fi
fi
INIT_FLAGS=""
case "$TRIGGER" in
  timer) INIT_FLAGS="--trigger user-timer" ;;
  wrapper) INIT_FLAGS="--trigger path-wrapper" ;;
  none) INIT_FLAGS="--no-trigger" ;;
esac
done_step 4 "Choices: trigger=${TRIGGER:-default}, init=$([ "$RUN_INIT" = 1 ] && echo now || echo later)"

# --- [5/5] initialise, then the summary --------------------------------------------------------------------
INIT_CMD="$WRAPPER init --loader-app $LIB/$LOADER_ZIP${INIT_FLAGS:+ $INIT_FLAGS}"
run_init() {
  # shellcheck disable=SC2086  # INIT_FLAGS is a deliberately word-split flag list
  "$WRAPPER" init --loader-app "$LIB/$LOADER_ZIP" $INIT_FLAGS
}
summary() {  # summary NEXT-COMMAND
  say ""
  say "$(paint heading "FloofyCrew $TAG is installed.")"
  say "  command   $WRAPPER"
  say "  zipapp    $LIB/floofy.pyz"
  say "  loader    $LIB/$LOADER_ZIP"
  say "  python    $PYTHON"
  [ -n "$PATH_NOTE" ] && say "  $(paint warn "$PATH_NOTE")"
  say "  next      $(paint accent "$1")"
  say "  $(paint muted "FloofyCrew is unofficial; floofy deinit removes everything it installed.")"
}

if [ "$NO_TTY" = 1 ]; then
  say "$(paint accent "[5/$STEPS]") $(paint warn "$G_TODO") no terminal to acknowledge the one-time warning on; run: $INIT_CMD"
  summary "$INIT_CMD"
  exit 0
fi
if [ "$RUN_INIT" != 1 ]; then
  if [ "${FLOOFYCREW_NO_INIT:-0}" = 1 ]; then
    say "$(paint accent "[5/$STEPS]") $(paint muted "$G_TODO") skipped floofy init (FLOOFYCREW_NO_INIT=1); run: $INIT_CMD"
  else
    say "$(paint accent "[5/$STEPS]") $(paint muted "$G_TODO") skipped floofy init (--no-init, or your choice); run: $INIT_CMD"
  fi
  summary "$INIT_CMD"
  exit 0
fi
step 5 "Initialising FloofyCrew (the one-time warning, the Loader app, the re-apply trigger)…"
say ""
INIT_STATUS=0
if [ "$TTY_IN" = - ]; then
  run_init || INIT_STATUS=$?
else
  run_init </dev/tty >/dev/tty 2>&1 || INIT_STATUS=$?
fi
if [ "$INIT_STATUS" = 0 ]; then
  done_step 5 "Initialised: FloofyCrew is set up"
  summary "floofy doctor   (then floofy install <mod>)"
else
  say "$(paint accent "[5/$STEPS]") $(paint danger "$G_FAIL") floofy init exited with status $INIT_STATUS (no consent, or a step refused); nothing else changed" >&2
  summary "$INIT_CMD"
fi
exit "$INIT_STATUS"
