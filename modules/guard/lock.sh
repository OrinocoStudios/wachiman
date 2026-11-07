#!/bin/bash
# Lock the screen for real on macOS 27.
#
# The only method that works is SACLockScreenImmediate() from login.framework, called
# through python ctypes; it needs no permission. The obvious alternatives are dead:
#   - osascript keystroke needs Accessibility ("not allowed to send keystrokes (1002)")
#   - CGSession no longer exists
#   - pmset displaysleepnow only blanks the display (kept as a last resort, reported as NOT_LOCKED)
# The final verdict reads ioreg, which is unreliable on macOS 27: the guard loop's shield
# state (guard.debug) is the figure to trust when it is running.

WAIT="${WACHIMAN_LOCK_WAIT:-3}"

locked() { ioreg -n Root -d1 -a 2>/dev/null | grep -A1 CGSSessionScreenIsLocked | grep -c true; }

python3 - <<'PY' 2>/dev/null
import ctypes
p = "/System/Library/PrivateFrameworks/login.framework/Versions/Current/login"
try:
    ctypes.CDLL(p).SACLockScreenImmediate()   # SACLockScreenImmediate
    print("SAC_OK")
except Exception as e:
    print("SAC_FAIL", e)
PY

sleep "$WAIT"
if [ "$(locked)" = "1" ]; then
  echo "LOCKED"
  exit 0
fi

echo "SAC did not lock, falling back to pmset (display only)"
pmset displaysleepnow
sleep "$WAIT"
[ "$(locked)" = "1" ] && echo "LOCKED (pmset)" || echo "NOT_LOCKED"
