#!/bin/bash
# Control the Guard loop: start | start-nolock | stop | status
# (`wachiman guard <action>` calls this.)

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$HERE/../lib/common.sh"

DIR="$WM_HOME/guard"
PIDFILE="$DIR/guard.pid"
LOGFILE="$DIR/guard.log"
SCRIPT="$HERE/guard.sh"
WAIT=$(cfg_num WACHIMAN_GUARD_STARTUP_WAIT 3)
mkdir -p "$DIR"

running_pid() {
  [ -f "$PIDFILE" ] || return 1
  local p; p=$(cat "$PIDFILE" 2>/dev/null)
  [ -n "$p" ] && kill -0 "$p" 2>/dev/null && echo "$p" && return 0
  return 1
}

spawn() {  # starts the loop; prints STARTED / ALREADY_RUNNING; returns non-zero on failure
  local pid
  if pid=$(running_pid); then echo "ALREADY_RUNNING pid=$pid"; return 2; fi
  rm -f "$PIDFILE"
  nohup /bin/bash "$SCRIPT" >> "$LOGFILE" 2>&1 &
  disown 2>/dev/null
  sleep "$WAIT"
  if pid=$(running_pid); then echo "STARTED pid=$pid"; return 0; fi
  echo "START_FAILED - see $LOGFILE"; tail -5 "$LOGFILE" 2>/dev/null
  return 1
}

case "${1:-}" in
  start)
    spawn; rc=$?
    [ "$rc" -eq 0 ] && /bin/bash "$HERE/lock.sh" >/dev/null 2>&1   # lock AFTER the loop is up, or its first message races
    [ "$rc" -eq 2 ] && rc=0
    exit "$rc" ;;

  start-nolock)   # same loop, screen stays unlocked (for testing)
    spawn; rc=$?
    [ "$rc" -eq 2 ] && rc=0
    exit "$rc" ;;

  stop)
    if pid=$(running_pid); then
      kill -TERM "$pid" 2>/dev/null
      for _ in 1 2 3 4 5 6; do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
      kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
      rm -f "$PIDFILE"
      echo "STOPPED pid=$pid"
    else
      rm -f "$PIDFILE"
      echo "NOT_RUNNING"
    fi ;;

  status)
    if pid=$(running_pid); then echo "ACTIVE pid=$pid"; else echo "INACTIVE"; fi
    # the shield state written by the loop is the one to trust; ioreg lies on macOS 27
    if [ -s "$DIR/lock.state" ]; then echo "locked=$(cat "$DIR/lock.state") (loginwindow shield)"
    else echo "locked=$(ioreg -n Root -d1 -a 2>/dev/null | grep -A1 CGSSessionScreenIsLocked | grep -c true) (ioreg, weak)"; fi
    echo "idle=$(ioreg -c IOHIDSystem 2>/dev/null | awk '/HIDIdleTime/ {print int($NF/1000000000); exit}')s"
    echo "photos_kept=$(ls -1 "$DIR"/*_[0-9].jpg 2>/dev/null | wc -l | tr -d ' ')"
    echo "--- last log lines ---"
    tail -8 "$LOGFILE" 2>/dev/null || echo "(no log)" ;;

  *)
    echo "usage: $0 {start|start-nolock|stop|status}"; exit 2 ;;
esac
