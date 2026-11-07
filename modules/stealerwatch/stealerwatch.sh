#!/bin/bash
# Stealer watch: looks for the traces macOS infostealers (MacSync, AMOS, SHub, Poseidon)
# leave to survive a reboot and to get data out. A plain shell loop, no AI in it.
#
#   1. new or MODIFIED LaunchAgents / LaunchDaemons   persistence (the #1 method)
#   2. new login items                                alternative persistence
#   3. stealer artefacts in /tmp                      the loot, before it is uploaded
#   4. connections to known command-and-control hosts exfiltration in progress
#   5. unsigned, quarantined executables where droppers land
#
# It does NOT match malware signatures (that is an antivirus, and changes weekly). It
# looks for CHANGES in the places malware has to write to persist: a LaunchAgent that was
# not there yesterday is suspect whatever its name, and no hash would catch it.
# Whatever exists when the baseline is taken is "normal" and never alerts.
#
#   stealerwatch.sh            loop (what `wachiman stealerwatch watch` runs)
#   stealerwatch.sh --dry      one pass on screen, nothing sent or saved   (wachiman stealerwatch scan)
#   stealerwatch.sh --baseline (re)learn what is normal and exit           (wachiman stealerwatch baseline)
#   stealerwatch.sh --once     one pass with alerts, then exit
#   stealerwatch.sh --test     send one real test message and exit
#
# Settings: WACHIMAN_STEALER_POLL (300 s), _COOLDOWN (3600 s, per finding),
# _C2_DOMAINS (space separated), _AGENT_DIRS and _TMP_DIRS (space separated, for tests
# and unusual setups).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/../lib/common.sh"

DIR="$WM_HOME/stealerwatch"
LOGFILE="$DIR/stealerwatch.log"
BASE="$DIR/baseline"             # what is normal on this machine
STATE="$DIR/stealerwatch.state"  # finding -> epoch of the last alert

POLL=$(cfg_num WACHIMAN_STEALER_POLL 300)
COOLDOWN=$(cfg_num WACHIMAN_STEALER_COOLDOWN 3600)
# C2 domains published in infostealer advisories; extend with WACHIMAN_STEALER_C2_DOMAINS.
C2_DOMAINS=$(cfg WACHIMAN_STEALER_C2_DOMAINS "glowmedaesthetics.com")
AGENT_DIRS=$(cfg WACHIMAN_STEALER_AGENT_DIRS "$HOME/Library/LaunchAgents /Library/LaunchAgents /Library/LaunchDaemons")
TMP_DIRS=$(cfg WACHIMAN_STEALER_TMP_DIRS "/tmp /var/tmp")

# --- probes ------------------------------------------------------------------
# Each prints "key<TAB>description" lines. The key identifies the finding for the
# baseline and the cooldown; the description is what the person reads.

filehash() { if command -v md5 >/dev/null 2>&1; then md5 -q "$1" 2>/dev/null; else cksum < "$1" | cut -d' ' -f1; fi; }

probe_agents() {
  # Every startup plist with its hash: catches a new one AND the modification of an
  # existing one (hijacking a legitimate plist is stealthier than adding one).
  local d f
  for d in $AGENT_DIRS; do
    [ -d "$d" ] || continue
    find "$d" -maxdepth 1 -name '*.plist' -type f 2>/dev/null | while IFS= read -r f; do
      printf 'plist:%s:%s\t%s %s\n' "$f" "$(filehash "$f")" "$(L "automatic startup:" "arranque automático:")" "$f"
    done
  done
}

probe_loginitems() {
  osascript -e 'tell application "System Events" to get the name of every login item' 2>/dev/null \
    | tr ',' '\n' | sed 's/^ *//' | while IFS= read -r it; do
      [ -n "$it" ] && printf 'login:%s\t%s %s\n' "$it" "$(L "login item:" "ítem de inicio de sesión:")" "$it"
    done
}

probe_tmp() {
  # Names stealers use to pack the loot before upload; osalogging.zip is MacSync's.
  local d n f
  for d in $TMP_DIRS; do
    for n in osalogging.zip out.zip archive.zip .dat payload; do
      f="$d/$n"
      [ -e "$f" ] && printf 'loot:%s\t%s %s\n' "$f" "$(L "STEALER ARTEFACT:" "ARTEFACTO DE ROBO:")" "$f"
    done
    f="$d/FileGrabber"
    [ -e "$f" ] && printf 'loot:%s\t%s %s\n' "$f" "$(L "COLLECTION FOLDER:" "CARPETA DE RECOLECCIÓN:")" "$f"
  done
  # typical collection folders (FileGrabber = AMOS/Poseidon)
  for f in "$HOME/.config/.dat" "$HOME/FileGrabber"; do
    [ -e "$f" ] && printf 'loot:%s\t%s %s\n' "$f" "$(L "COLLECTION FOLDER:" "CARPETA DE RECOLECCIÓN:")" "$f"
  done
  return 0
}

probe_c2() {
  # Established connections to the addresses known C2 domains resolve to.
  local d ip
  for d in $C2_DOMAINS; do
    for ip in $(dig +short +time=2 +tries=1 "$d" 2>/dev/null | grep -E '^[0-9.]+$'); do
      if lsof -nP -iTCP -sTCP:ESTABLISHED 2>/dev/null | grep -q "$ip"; then
        printf 'c2:%s\t%s (%s -> %s)\n' "$d" "$(L "ACTIVE CONNECTION to a known C2" "CONEXIÓN ACTIVA a un C2 conocido")" "$d" "$ip"
      fi
    done
  done
}

probe_quarantine() {
  # Executables downloaded from the internet (quarantine flag) where a stealer drops
  # itself. Signed is not innocent, but unsigned and downloaded today under ~/Library
  # is exactly the profile.
  local f
  find "$HOME/Library/Application Support" "$HOME/.local/bin" "$HOME/Library/Containers" \
       -maxdepth 3 -type f -perm -u+x -newerct '-1 day' 2>/dev/null | head -40 | while IFS= read -r f; do
    xattr "$f" 2>/dev/null | grep -q 'com.apple.quarantine' || continue
    codesign -dv "$f" >/dev/null 2>&1 && continue
    printf 'unsigned:%s\t%s %s\n' "$f" "$(L "UNSIGNED downloaded executable:" "ejecutable SIN FIRMAR y descargado:")" "$f"
  done
}

scan() { probe_agents; probe_loginitems; probe_tmp; probe_c2; probe_quarantine; }

make_baseline() {
  scan | cut -f1 | sort -u > "$BASE"
  wm_log "baseline created: $(wc -l < "$BASE" | tr -d ' ') known items"
}

send() { tg_html "$1"; }

# --- modes -------------------------------------------------------------------
mkdir -p "$DIR"
ONCE=0
case "${1:-}" in
  --dry)
    echo "== full scan (nothing sent) =="
    scan | awk -F'\t' '{print "  " $2}'
    echo "== keys =="
    scan | cut -f1 | sort -u | wc -l | awk '{print "  " $1 " items"}'
    exit 0 ;;
  --baseline)
    make_baseline; echo "baseline saved in $BASE"; exit 0 ;;
  --test)
    if ! tg_ready; then echo "Telegram token/chat are not set (WACHIMAN_TELEGRAM_BOT_TOKEN, WACHIMAN_TELEGRAM_CHAT_ID)"; exit 1; fi
    send "🛡 stealerwatch: $(L "test alert" "prueba de aviso"). $(date '+%F %T')"
    echo "sent"; exit 0 ;;
  --once) ONCE=1 ;;
esac

tg_ready || wm_log "WARNING: no Telegram settings, findings will only be logged"
[ -f "$BASE" ] || make_baseline
touch "$STATE"

trap 'wm_log "stopped (signal)"; exit 0' INT TERM
wm_log "started (pid $$, poll=${POLL}s, baseline=$(wc -l < "$BASE" | tr -d ' ') items)"

while :; do
  # findings = what is there now and was not in the baseline
  found=$(scan | sort -u | awk -F'\t' -v base="$BASE" '
    BEGIN { while ((getline k < base) > 0) known[k]=1 }
    !($1 in known) { print $1 "\t" $2 }')

  if [ -n "$found" ]; then
    now=$(date +%s)
    notice=""
    while IFS=$'\t' read -r key desc; do
      [ -n "$key" ] || continue
      # cooldown per finding: do not repeat the same one every poll
      last=$(grep -F "$key|" "$STATE" 2>/dev/null | tail -1 | cut -d'|' -f2)
      if [ -n "${last:-}" ] && [ $((now - last)) -lt "$COOLDOWN" ]; then continue; fi
      notice="${notice}• $(esc "$desc")"$'\n'
      grep -vF "$key|" "$STATE" > "$STATE.tmp" 2>/dev/null || true
      mv "$STATE.tmp" "$STATE" 2>/dev/null || true
      echo "$key|$now" >> "$STATE"
      wm_log "FINDING $desc"
    done <<< "$found"

    if [ -n "$notice" ]; then
      send "🛡 <b>$(L "Change in persistence points" "Cambio en los puntos de persistencia")</b>
$(date '+%H:%M:%S')

${notice}
$(L "If you did not just install something, this deserves a look." "Si no acabas de instalar nada, esto merece una mirada.")
$(L "To accept it as normal:" "Para aceptarlo como normal:") <code>wachiman stealerwatch baseline</code>"
    fi
  fi
  [ "$ONCE" = "1" ] && exit 0
  sleep "$POLL"
done
