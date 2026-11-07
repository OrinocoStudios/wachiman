#!/bin/bash
# Guard: while the screen is locked, any keyboard or mouse activity sends a Telegram
# alert plus webcam photos. A plain shell loop, no AI in it. Start it with
# `wachiman guard start`; this script is not meant to be run by hand.
#
# Settings (WACHIMAN_*, see config.env.example): GUARD_POLL, GUARD_IDLE_ACTIVE,
# GUARD_IDLE_QUIET, GUARD_BURST_COOLDOWN, GUARD_GRACE, GUARD_PARANOID, GUARD_DEBUG,
# GUARD_PHOTO_GAP, GUARD_SKIP_FRAMES, CAM, CAM_NAME, FFMPEG.
#
# Test hook: --polls N stops after N polls.

set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/../lib/common.sh"

DIR="$WM_HOME/guard"
LOGFILE="$DIR/guard.log"
PIDFILE="$DIR/guard.pid"
STATEFILE="$DIR/last_event.txt"
LOCKSTATE="$DIR/lock.state"
LOCKSINCE="$DIR/lock.since"
mkdir -p "$DIR"

POLL=$(cfg_num WACHIMAN_GUARD_POLL 3)
IDLE_ACTIVE=$(cfg_num WACHIMAN_GUARD_IDLE_ACTIVE 10)     # idle below this = activity
IDLE_QUIET=$(cfg_num WACHIMAN_GUARD_IDLE_QUIET 60)       # it must have been still at least this long
BURST_COOLDOWN=$(cfg_num WACHIMAN_GUARD_BURST_COOLDOWN 60)
GRACE=$(cfg_num WACHIMAN_GUARD_GRACE 30)                 # seconds to unlock after a burst
PHOTO_GAP=$(cfg_num WACHIMAN_GUARD_PHOTO_GAP 1)
SKIP_FRAMES=$(cfg_num WACHIMAN_GUARD_SKIP_FRAMES 12)     # webcams start with black frames
DEBUG=$(cfg WACHIMAN_GUARD_DEBUG 0)

# Does the Mac ask for a password on wake? If not, "unlocked within 30 s" proves nothing
# and deleting the photos would erase an intruder's. Do not trust `sysadminctl
# -screenLock status`: on macOS 27 it says "off" with FileVault on and the password
# really demanded. Default is paranoid (never auto-delete); set GUARD_PARANOID=0 only
# once you have confirmed by hand that the password is required.
PARANOID=$(cfg WACHIMAN_GUARD_PARANOID 1)

MAX_POLLS=0
if [ "${1:-}" = "--polls" ]; then MAX_POLLS="${2:-0}"; fi

FFMPEG=$(cfg WACHIMAN_FFMPEG "")
[ -n "$FFMPEG" ] || FFMPEG=$(command -v ffmpeg 2>/dev/null || true)

# avfoundation indexes are not stable (they reorder as USB devices come and go), so a
# camera NAME is looked up on every start; WACHIMAN_CAM is only the fallback index.
resolve_cam() {
  [ -n "$FFMPEG" ] || return 0
  "$FFMPEG" -hide_banner -f avfoundation -list_devices true -i "" 2>&1 \
    | sed -n '/AVFoundation video devices:/,/AVFoundation audio devices:/p' \
    | grep -F "$1" | head -1 | sed -n 's/.*\[\([0-9][0-9]*\)\].*/\1/p'
}
CAM_NAME=$(cfg WACHIMAN_CAM_NAME "")
CAM_RESOLVED=""
[ -n "$CAM_NAME" ] && CAM_RESOLVED=$(resolve_cam "$CAM_NAME")
if [ -n "$CAM_RESOLVED" ]; then
  CAM="$CAM_RESOLVED"; CAM_SRC="name '$CAM_NAME'"
else
  CAM=$(cfg_num WACHIMAN_CAM 0); CAM_SRC="WACHIMAN_CAM"
fi

idle_secs() { ioreg -c IOHIDSystem 2>/dev/null | awk '/HIDIdleTime/ {print int($NF/1000000000); exit}'; }

is_locked() {
  # 1 = locked, 0 = unlocked. On macOS 27 neither ioreg's CGSSessionScreenIsLocked (stays
  # "Yes" after unlocking) nor the screenLockUIIsShown/Hidden notifications (Hidden fires
  # ~0.1 s before the shield returns) can be trusted. The physical truth is the loginwindow
  # shield window: "shieldWindowShowing:1/0" in the unified log, read incrementally since
  # the previous poll and persisted in lock.state. ioreg is only the fallback until the
  # first event is seen. Each `log show` costs ~1.4 s, fine at a 3 s poll.
  local since now ev
  now=$(date '+%Y-%m-%d %H:%M:%S')
  since=$(cat "$LOCKSINCE" 2>/dev/null)
  [ -z "$since" ] && since=$(date -v-12H '+%Y-%m-%d %H:%M:%S' 2>/dev/null)
  ev=$(log show --start "$since" --end "$now" --style compact \
        --predicate 'process == "loginwindow" AND eventMessage CONTAINS "shieldWindowShowing"' 2>/dev/null \
        | grep -oE "shieldWindowShowing:[01]" | tail -1)
  echo "$now" > "$LOCKSINCE"
  case "$ev" in
    shieldWindowShowing:1) echo 1 > "$LOCKSTATE" ;;
    shieldWindowShowing:0) echo 0 > "$LOCKSTATE" ;;
  esac
  if [ -s "$LOCKSTATE" ]; then cat "$LOCKSTATE"
  else ioreg -n Root -d1 -a 2>/dev/null | grep -A1 CGSSessionScreenIsLocked | grep -c true; fi
}

shoot() {
  # the first frames of a webcam are black: drop them
  [ -n "$FFMPEG" ] || return 1
  "$FFMPEG" -hide_banner -loglevel error -f avfoundation -framerate 30 \
    -video_size 1280x720 -pixel_format uyvy422 -i "$CAM" \
    -vf "select=gte(n\,$SKIP_FRAMES)" -frames:v 1 -y "$1" >/dev/null 2>&1
}

cleanup() {
  wm_log "stopped (signal)"
  tg_text "$(L "🛑 Guard stopped" "🛑 Guard parado") $(date '+%H:%M:%S')"
  rm -f "$PIDFILE"
  exit 0
}
trap cleanup INT TERM
trap 'rm -f "$PIDFILE"' EXIT
echo $$ > "$PIDFILE"

prev_idle=999
prev_locked=$(is_locked)
last_burst=0
pending_ts=0          # 0 = no burst inside the grace window
pending_files=""
polls=0

wm_log "started (pid $$, camera $CAM via $CAM_SRC, paranoid=$PARANOID, ffmpeg=${FFMPEG:-none})"
[ -n "$FFMPEG" ] || wm_log "WARNING: ffmpeg not found (set WACHIMAN_FFMPEG); alerts will have no photos"
if [ "$PARANOID" = "1" ]; then
  tg_text "$(L "👁 Guard active" "👁 Guard activo") $(date '+%H:%M:%S') — $(L "⚠️ paranoid mode: unlocking proves nothing, no photo will ever be deleted" "⚠️ modo paranoico: desbloquear no prueba nada, no se borrará ninguna foto")"
else
  tg_text "$(L "👁 Guard active" "👁 Guard activo") $(date '+%H:%M:%S')"
fi

while true; do
  now=$(date +%s)
  idle=$(idle_secs); [ -z "$idle" ] && idle=0
  locked=$(is_locked)

  # --- lock transitions ---
  if [ "$locked" != "$prev_locked" ]; then
    if [ "$locked" = "1" ]; then
      tg_text "🔒 $(L LOCKED BLOQUEADA) $(date '+%H:%M')"
      wm_log "locked"
    else
      tg_text "🔓 $(L UNLOCKED DESBLOQUEADA) $(date '+%H:%M')"
      wm_log "unlocked"
      # unlocked inside the grace window: it was the owner, the photos are not needed
      if [ "$pending_ts" -ne 0 ] && [ $((now - pending_ts)) -le "$GRACE" ]; then
        if [ "$PARANOID" = "1" ]; then
          tg_text "$(L "⚠️ Unlocked without proof: this does NOT verify identity. Photos kept." "⚠️ Desbloqueo sin prueba: NO verifica identidad. Fotos conservadas.")"
          wm_log "unlock inside grace but paranoid: photos kept ($pending_files)"
        else
          tg_text "$(L "🔓 Unlocked, it was the owner — photos deleted" "🔓 Desbloqueado, era el dueño — fotos borradas")"
          for f in $pending_files; do rm -f "$f"; done
          wm_log "false alarm, photos deleted"
        fi
        pending_ts=0; pending_files=""
      fi
    fi
    prev_locked=$locked
  fi

  # --- grace window expired without an unlock ---
  if [ "$pending_ts" -ne 0 ] && [ $((now - pending_ts)) -gt "$GRACE" ]; then
    tg_text "$(L "📸 Nobody unlocked: photos kept in" "📸 Nadie desbloqueó: fotos guardadas en") $DIR"
    wm_log "burst kept: $pending_files"
    pending_ts=0; pending_files=""
  fi

  # --- activity while locked: an edge from long idle to recent activity ---
  if [ "$locked" = "1" ] && [ "$idle" -lt "$IDLE_ACTIVE" ] && [ "$prev_idle" -ge "$IDLE_QUIET" ]; then
    if [ $((now - last_burst)) -ge "$BURST_COOLDOWN" ]; then
      last_burst=$now
      stamp=$(date '+%Y-%m-%d_%H-%M-%S')
      tg_text "$(L "⚠️ Activity on the Mac" "⚠️ Actividad en el Mac") $(date '+%H:%M:%S') ($(L "screen locked" "pantalla bloqueada"))"
      wm_log "EVENT activity while locked"
      files=""
      for n in 1 2 3; do
        out="$DIR/${stamp}_${n}.jpg"
        if shoot "$out" && [ -s "$out" ]; then
          files="$files $out"
          tg_photo "$out" "$stamp $(L photo foto) $n/3"
        else
          wm_log "capture failed $out"
        fi
        sleep "$PHOTO_GAP"
      done
      pending_files="$files"
      pending_ts=$now
      echo "$(date '+%F %T') activity while locked" > "$STATEFILE"
    else
      wm_log "activity ignored (cooldown)"
    fi
  fi

  [ "$DEBUG" = "1" ] && echo "$(date '+%T') locked=$locked idle=$idle prev_idle=$prev_idle pending=$pending_ts" >> "$DIR/guard.debug"
  prev_idle=$idle
  polls=$((polls + 1))
  [ "$MAX_POLLS" -gt 0 ] && [ "$polls" -ge "$MAX_POLLS" ] && exit 0
  sleep "$POLL"
done
