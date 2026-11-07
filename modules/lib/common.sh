# Shared helpers for the Wachiman shell modules. Source it; do not execute it.
#
# Settings follow the same layering as wachiman/paths.py:
#     built-in default  <  $WACHIMAN_HOME/config.env  <  WACHIMAN_* environment
# config.env is parsed as data (grep/cut). It is never sourced or evaluated.

WM_HOME_RAW="${WACHIMAN_HOME:-$HOME/.wachiman}"
case "$WM_HOME_RAW" in
  "~")   WM_HOME="$HOME" ;;
  "~/"*) WM_HOME="$HOME/${WM_HOME_RAW#\~/}" ;;
  *)     WM_HOME="$WM_HOME_RAW" ;;
esac

_cfg_file() {  # KEY -> last value in config.env, quotes and CR stripped
  local f="$WM_HOME/config.env" v
  [ -f "$f" ] || return 0
  v=$(grep -E "^[[:space:]]*(export[[:space:]]+)?$1[[:space:]]*=" "$f" 2>/dev/null | tail -1 | cut -d= -f2- \
      | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
  case "$v" in
    \"?*\") v="${v#\"}"; v="${v%\"}" ;;
    \'?*\') v="${v#\'}"; v="${v%\'}" ;;
  esac
  printf '%s' "$v"
}

cfg() {  # KEY [DEFAULT]; an empty value counts as unset
  local k="$1" v
  v="${!k-}"
  [ -n "$v" ] || v=$(_cfg_file "$k")
  if [ -n "$v" ]; then printf '%s' "$v"; else printf '%s' "${2-}"; fi
}

cfg_num() {  # KEY DEFAULT; non-numbers fall back to the default
  local v
  v=$(cfg "$1" "$2")
  case "$v" in ''|*[!0-9]*) v="$2" ;; esac
  printf '%s' "$v"
}

TOKEN=$(cfg WACHIMAN_TELEGRAM_BOT_TOKEN "")
CHAT=$(cfg WACHIMAN_TELEGRAM_CHAT_ID "")
TG_API=$(cfg WACHIMAN_TELEGRAM_API "https://api.telegram.org")
WM_LANG=$(cfg WACHIMAN_LANG en)
[ "$WM_LANG" = es ] || WM_LANG=en

L() { if [ "$WM_LANG" = es ]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }   # L "english" "español"

esc() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }

wm_log() { [ -n "${LOGFILE:-}" ] && echo "$(date '+%F %T') $*" >> "$LOGFILE"; return 0; }

tg_ready() { [ -n "$TOKEN" ] && [ -n "$CHAT" ]; }

# The URL (which holds the token) goes to curl on stdin, so it never shows in `ps`.
tg_text() {  # TEXT [html]
  tg_ready || return 0
  printf 'url = "%s/bot%s/sendMessage"\n' "$TG_API" "$TOKEN" \
    | curl -s -m 15 -K - -X POST -d "chat_id=$CHAT" --data-urlencode "text=$1" ${2:+-d parse_mode=HTML} \
        >/dev/null 2>&1 || true
}

tg_html() { tg_text "$1" html; }

tg_photo() {  # FILE CAPTION
  tg_ready || return 0
  printf 'url = "%s/bot%s/sendPhoto"\n' "$TG_API" "$TOKEN" \
    | curl -s -m 45 -K - -X POST -F "chat_id=$CHAT" -F "photo=@$1" --form-string "caption=$2" \
        >/dev/null 2>&1 || true
}
