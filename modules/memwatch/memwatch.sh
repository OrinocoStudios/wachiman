#!/bin/bash
# Memory watch: alerts on Telegram when the Mac is really short of memory, with the top 5
# processes. A plain shell loop, no AI in it.
#
#   memwatch.sh            loop (what `wachiman memwatch watch` and the launchd agent run)
#   memwatch.sh --dry      one sample: "swap_mb free_mb pressure swapouts", then the top 5; sends nothing
#   memwatch.sh --test     send one real test message and exit
#   memwatch.sh --polls N  (test hook) stop after N polls
#
# Why these signals. vm.swapusage "used" is a high-water mark: macOS never shrinks it
# while the machine stays up, so a swap threshold fires once and then screams forever (one
# night in CRIT at 6958 MB while the kernel reported 50% free and vm_stat showed zero
# swapouts). It is reported as context and never sets a level. What does mean something:
#   - `memory_pressure -Q` free percentage: the primary signal.
#   - the swapout RATE (vm_stat Swapouts across two samples, page = 16 KB): pages leaving
#     for disk right now, but only counted when pressure is already below
#     SWAPOUT_GATE_PRESS: a burst of swapouts with plenty of free memory is macOS working.
# Recovery needs hysteresis (PRESSURE_WARN + RECOVER_MARGIN) or the bot flaps at the edge.
#
# Settings: WACHIMAN_MEM_POLL (20 s), _PRESSURE_WARN (20 %), _PRESSURE_CRIT (10 %),
# _SWAPOUT_WARN_MBS (25), _SWAPOUT_GATE_PRESS (35), _RECOVER_MARGIN (5), _COOLDOWN (900 s).

set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/../lib/common.sh"

DIR="$WM_HOME/memwatch"
LOGFILE="$DIR/memwatch.log"
STATE="$DIR/memwatch.state"      # last alert: "epoch LEVEL"
mkdir -p "$DIR"

POLL=$(cfg_num WACHIMAN_MEM_POLL 20)
PRESSURE_WARN=$(cfg_num WACHIMAN_MEM_PRESSURE_WARN 20)
PRESSURE_CRIT=$(cfg_num WACHIMAN_MEM_PRESSURE_CRIT 10)
SWAPOUT_WARN_MBS=$(cfg_num WACHIMAN_MEM_SWAPOUT_WARN_MBS 25)
SWAPOUT_GATE_PRESS=$(cfg_num WACHIMAN_MEM_SWAPOUT_GATE_PRESS 35)
RECOVER_MARGIN=$(cfg_num WACHIMAN_MEM_RECOVER_MARGIN 5)
COOLDOWN=$(cfg_num WACHIMAN_MEM_COOLDOWN 900)

sample() {  # prints: SWAP_MB FREE_MB PRESSURE SWAPOUT_PAGES
  local swap free spec press outs
  swap=$(sysctl -n vm.swapusage | awk '{for(i=1;i<=NF;i++) if($i=="used") {sub(/M/,"",$(i+2)); print int($(i+2))}}')
  read -r free spec outs < <(vm_stat | awk '/Pages free/{f=$3} /Pages speculative/{s=$3} /Swapouts/{o=$NF} END{gsub(/\./,"",f); gsub(/\./,"",s); gsub(/\./,"",o); print f, s, o}')
  free=$(( (${free:-0} + ${spec:-0}) * 16 / 1024 ))
  press=$(memory_pressure -Q 2>/dev/null | awk -F': ' '/free percentage/{gsub(/%/,"",$2); print int($2)}')
  echo "${swap:-0} ${free:-0} ${press:-100} ${outs:-0}"
}

top5() {  # RSS grouped by app (not by helper process)
  ps -axo rss=,comm= | awk '
    { rss=$1; $1=""; c=$0; sub(/^ /,"",c);
      n=c; if (match(c,/\/Applications\/[^\/]+\.app/)) { n=substr(c,RSTART+14,RLENGTH-18) }
      else { k=split(c,a,"/"); n=a[k] }
      m[n]+=rss }
    END { for (k in m) printf "%d\t%s\n", m[k]/1024, k }' | sort -rn | head -5 \
    | awk -F'\t' '{printf "• %s — %d MB\n", $2, $1}'
}

send() { tg_html "$1"; }

MAX_POLLS=0
case "${1:-}" in
  --dry)  sample; top5; exit 0 ;;
  --test)
    read -r SWAP FREE PRESS OUTS < <(sample)
    send "🧪 <b>memwatch $(L test prueba)</b> — $(L pressure presión) ${PRESS}% $(L free libre) · $(L "swap used" "swap acumulado") ${SWAP} MB · $(L free libre) ${FREE} MB
<b>$(L "Top 5 by RSS:" "Top 5 por RSS:")</b>
$(esc "$(top5)")"
    echo "sent"; exit 0 ;;
  --polls) MAX_POLLS="${2:-0}" ;;
esac

last_ts=0; last_level=""
[ -f "$STATE" ] && read -r last_ts last_level < "$STATE"
last_ts=${last_ts:-0}

trap 'wm_log "stopped"; exit 0' INT TERM
wm_log "started pid=$$ poll=${POLL}s press<${PRESSURE_WARN}%/${PRESSURE_CRIT}% swapout>${SWAPOUT_WARN_MBS}MB/s@press<${SWAPOUT_GATE_PRESS}%"

# the rate needs a previous sample, so take one before the loop and sleep at the top of it
read -r _ _ _ prev_outs < <(sample)
prev_t=$(date +%s)
polls=0

while :; do
  sleep "$POLL"
  read -r SWAP FREE PRESS OUTS < <(sample)
  now=$(date +%s)

  dt=$(( now - prev_t )); [ "$dt" -lt 1 ] && dt=1
  d_outs=$(( OUTS - prev_outs )); [ "$d_outs" -lt 0 ] && d_outs=0   # the counter resets
  OUTRATE=$(( d_outs * 16 / 1024 / dt ))
  prev_outs=$OUTS; prev_t=$now

  level=""
  if [ "$PRESS" -le "$PRESSURE_CRIT" ]; then level="CRIT"
  elif [ "$PRESS" -le "$PRESSURE_WARN" ]; then level="WARN"
  elif [ "$OUTRATE" -ge "$SWAPOUT_WARN_MBS" ] && [ "$PRESS" -lt "$SWAPOUT_GATE_PRESS" ]; then level="WARN"
  fi

  if [ -n "$level" ]; then
    # a different level (WARN -> CRIT) always passes; the same level waits for the cooldown
    if [ "$level" != "$last_level" ] || [ $((now - last_ts)) -ge "$COOLDOWN" ]; then
      icon="⚠️"; [ "$level" = "CRIT" ] && icon="🚨"
      msg="$icon <b>$(L "Memory $level on the Mac" "Memoria $level en el Mac")</b>
$(L pressure presión): ${PRESS}% $(L free libre) · swapout: ${OUTRATE} MB/s · $(L free libre): ${FREE} MB
$(L "swap used" "swap acumulado"): ${SWAP} MB <i>($(L "high-water mark, does not go down until reboot" "marca de agua, no baja hasta reiniciar"))</i>
<b>$(L "Top 5 by RSS:" "Top 5 por RSS:")</b>
$(esc "$(top5)")"
      if tg_ready; then send "$msg"; note="alert sent"; else note="alert not sent: no Telegram settings"; fi
      wm_log "$level press=${PRESS}% outrate=${OUTRATE}MB/s free=${FREE}MB swap=${SWAP}MB ($note)"
      last_ts=$now; last_level=$level; echo "$last_ts $last_level" > "$STATE"
    else
      wm_log "$level press=${PRESS}% outrate=${OUTRATE}MB/s free=${FREE}MB (cooldown)"
    fi
  elif [ -n "$last_level" ] && [ "$PRESS" -gt $((PRESSURE_WARN + RECOVER_MARGIN)) ]; then
    send "✅ $(L "Memory recovered" "Memoria recuperada"): $(L pressure presión) ${PRESS}% $(L free libre) · swapout ${OUTRATE} MB/s · $(L free libre) ${FREE} MB"
    wm_log "recovered press=${PRESS}% outrate=${OUTRATE}MB/s free=${FREE}MB"
    last_level=""; echo "$last_ts " > "$STATE"
  fi

  polls=$((polls + 1))
  [ "$MAX_POLLS" -gt 0 ] && [ "$polls" -ge "$MAX_POLLS" ] && exit 0
done
