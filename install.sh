#!/bin/sh
# Wachiman installer.
#   curl -fsSL https://raw.githubusercontent.com/OrinocoStudios/wachiman/main/install.sh | sh
# or from a checkout:  sh install.sh
#
# Needs macOS with python3 and lsof (both ship with it; python3 comes with the
# Xcode command line tools). Safe to run again: it updates instead.
#
# Optional environment:
#   WACHIMAN_SRC               where the code lives   (default: ~/.wachiman/src)
#   WACHIMAN_REPO_URL          git URL to clone       (default: the public repository)
#   WACHIMAN_NONINTERACTIVE=1  never ask; read WACHIMAN_LANG / _TELEGRAM_* from the environment
#   WACHIMAN_SKIP_GEO=1        do not download the IP owner database now
#   WACHIMAN_SERVICE=1|0       install the background service without asking (1) or never (0)
set -eu

REPO_URL="${WACHIMAN_REPO_URL:-https://github.com/OrinocoStudios/wachiman.git}"
TARBALL_URL="https://github.com/OrinocoStudios/wachiman/archive/refs/heads/main.tar.gz"
HOME_DIR="${WACHIMAN_HOME:-$HOME/.wachiman}"
SRC="${WACHIMAN_SRC:-$HOME_DIR/src}"
BIN_DIR="$HOME/.local/bin"

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# 1. Requirements -----------------------------------------------------------
[ "$(uname -s 2>/dev/null)" = "Darwin" ] || die "Wachiman supports macOS for now."
have python3 || die "python3 not found. Run 'xcode-select --install' and try again."
have lsof || die "lsof not found (it ships with macOS)."
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
  || die "python3 3.9 or newer is needed."

# Can we talk to the person? Under 'curl | sh' stdin is the script, so use the terminal.
INTERACTIVE=0
if [ "${WACHIMAN_NONINTERACTIVE:-0}" != "1" ] && ( : </dev/tty ) 2>/dev/null; then
  INTERACTIVE=1
fi

ask_yes() { # ask_yes "question" -> 0 when the answer is yes
  [ "$INTERACTIVE" = "1" ] || return 1
  printf '%s [y/N] ' "$1" >/dev/tty
  read -r answer </dev/tty || return 1
  case "$answer" in y|Y|yes|YES|s|S|si|SI|sí) return 0 ;; *) return 1 ;; esac
}

# 2. Get the code -----------------------------------------------------------
say "Wachiman: installing into $SRC"
if [ -d "$SRC/.git" ] && have git; then
  git -C "$SRC" pull --ff-only --quiet || say "note: could not update; keeping the version you have."
elif have git && [ ! -e "$SRC" ]; then
  mkdir -p "$(dirname "$SRC")"
  git clone --quiet --depth 1 "$REPO_URL" "$SRC"
else
  have curl || die "need git or curl to download Wachiman."
  mkdir -p "$SRC"
  curl -fsSL "$TARBALL_URL" | tar xz --strip-components=1 -C "$SRC"
fi
[ -f "$SRC/bin/wachiman" ] || die "download incomplete: $SRC/bin/wachiman is missing."
WM="$SRC/bin/wachiman"

# A short command on the PATH, when possible.
mkdir -p "$BIN_DIR"
ln -sf "$WM" "$BIN_DIR/wachiman"

# 3. IP owner database ------------------------------------------------------
if [ "${WACHIMAN_SKIP_GEO:-0}" != "1" ]; then
  say "Downloading the offline database of who owns each IP (about 8 seconds)..."
  "$WM" radar geo-update || say "note: skipped. Run 'wachiman radar geo-update' later when online."
fi

# 4. Setup wizard -----------------------------------------------------------
if [ "$INTERACTIVE" = "1" ]; then
  "$WM" setup </dev/tty
else
  "$WM" setup --non-interactive
fi

# 5. Background service -----------------------------------------------------
SERVICE=no
case "${WACHIMAN_SERVICE:-}" in
  1) SERVICE=yes ;;
  0) ;;
  *) if ask_yes "Run the radar in the background and start it at every login?"; then SERVICE=yes; fi ;;
esac
if [ "$SERVICE" = "yes" ]; then
  "$WM" service install || say "note: could not install the service. Try 'wachiman service install' later."
fi

# 6. Next steps -------------------------------------------------------------
say ""
say "Done. Next steps:"
case ":$PATH:" in
  *":$BIN_DIR:"*) CMD=wachiman ;;
  *) CMD="$BIN_DIR/wachiman"; say "  (to type just 'wachiman', add $BIN_DIR to your PATH)" ;;
esac
[ "$SERVICE" = "yes" ] || say "  $CMD service install   keep the radar running in the background"
say "  $CMD radar scan        take the first sample now (it learns what is normal)"
say "  $CMD web               open the history page at http://127.0.0.1:8765"
say "  $CMD setup             change language or Telegram alerts"
say "  $CMD uninstall         stop the background service and see how to remove everything"
