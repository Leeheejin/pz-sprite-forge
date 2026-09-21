#!/usr/bin/env bash
# Launch an isolated Project Zomboid client on a prepared cachedir (see install.sh), get it
# past everything that waits for a human, wait for the Lua harness to print "<TAG>] DONE",
# then collect its lines and screenshots and stop the client. Git Bash on Windows.
#
#   harness/run_client.sh --cachedir 'C:\...\fpclient\cache' --tag FPCLIENT --out ./shots [--park] [--timeout 900]
#
# The client is identified by its cachedir in the command line, never by process name:
# other sessions run their own clients. The cachedir's last path component is the tag
# that pzwin.ps1 matches, so make it distinctive (fpclient, not cache).
set -u
CACHEDIR=""; TAG=""; OUT=""; PARK=0; TIMEOUT=900
EXE='C:\Program Files (x86)\Steam\steamapps\common\ProjectZomboid\ProjectZomboid64.exe'
while [ $# -gt 0 ]; do
  case "$1" in
    --cachedir) CACHEDIR="$2"; shift 2 ;;
    --tag) TAG="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --exe) EXE="$2"; shift 2 ;;
    --park) PARK=1; shift ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    *) echo "unknown arg $1"; exit 2 ;;
  esac
done
[ -n "$CACHEDIR" ] && [ -n "$TAG" ] || { echo "usage: run_client.sh --cachedir <win path> --tag TAG [--out dir] [--park]"; exit 2; }

HERE="$(cd "$(dirname "$0")" && pwd)"
PZ="$HERE/pzwin.ps1"
C_W="$CACHEDIR"                                   # Windows form for the game
C_U="$(cygpath -u "$CACHEDIR")"                   # POSIX form for us
WTAG="$(basename "$C_U")"                          # what pzwin matches in the command line
[ "$WTAG" = "cache" ] && WTAG="$(basename "$(dirname "$C_U")")"
LOG="$C_U/console.txt"
W=$(grep -oE '^width=[0-9]+' "$C_U/options.ini" 2>/dev/null | cut -d= -f2); H=$(grep -oE '^height=[0-9]+' "$C_U/options.ini" 2>/dev/null | cut -d= -f2)
W=${W:-1280}; H=${H:-720}

mine() { powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name = 'ProjectZomboid64.exe'\" | Where-Object { \$_.CommandLine -like '*$WTAG*' } | ForEach-Object { \$_.ProcessId }" | tr -d '\r\n '; }
win() { powershell -NoProfile -ExecutionPolicy Bypass -File "$PZ" -tag "$WTAG" "$@"; }

[ -n "$(mine)" ] && { echo "a client on this cachedir is already running (pid $(mine)); stop it first"; exit 1; }
[ -f "$LOG" ] && mv "$LOG" "$C_U/console.prev.txt"
mkdir -p "$C_U/Screenshots"; rm -f "$C_U/Screenshots/"*.png

# detached: a client started as our child dies with the shell, and Start-Process -PassThru's
# PID is the launcher's, which re-execs, so no PID is kept
powershell -NoProfile -Command "Start-Process -FilePath '$EXE' -ArgumentList @('-cachedir=$C_W','-nosteam') -WorkingDirectory (Split-Path '$EXE') | Out-Null"
echo "launched at $(date +%H:%M:%S) on $C_W"
until [ -f "$LOG" ]; do sleep 2; done
t=0
until grep -aq "game loading took" "$LOG" 2>/dev/null; do
  sleep 3; t=$((t + 3))
  [ -z "$(mine)" ] && { echo "client died during load"; exit 1; }
  # a fresh cachedir stops at the terms dialog, which no click of ours should answer
  if [ $t -eq 90 ] && ! grep -aq "exit zombie.gameStates.TermsOfServiceState" "$LOG"; then
    echo "WARNING: still on the terms-of-service dialog after 90s; click Agree in the window, or re-run install.sh with --accept-tos"
  fi
done
echo "world loaded at $(date +%H:%M:%S)"

# the "click to continue" gate after loading (no -debug): a click in the middle of the window
sleep 12; win click -x $((W / 2)) -y $((H / 2)) >/dev/null
for _ in 1 2 3 4; do sleep 8; grep -aq "harness engaged" "$LOG" && break; win click -x $((W / 2)) -y $((H / 2)) >/dev/null; done
grep -aq "harness engaged" "$LOG" && echo "harness engaged at $(date +%H:%M:%S)" || echo "WARNING: harness never engaged"
# the gate click leaves the mouse over the game; parked in a corner it cannot hover a tooltip
# into a picture
[ "$PARK" = 1 ] && { sleep 2; win move -x $((W - 10)) -y $((H - 8)) >/dev/null; echo "mouse parked"; }

t=0
until grep -aq "$TAG\] DONE" "$LOG" 2>/dev/null; do
  sleep 5; t=$((t + 5))
  [ -z "$(mine)" ] && { echo "client died at $(date +%H:%M:%S)"; break; }
  [ $t -ge "$TIMEOUT" ] && { echo "timeout after ${TIMEOUT}s"; break; }
done

echo "--- harness lines"
grep -a "$TAG\]" "$LOG" | sed 's/^LOG  : Lua *f:\([0-9]*\)[^>]*> /\1 /' | cut -c1-220
echo "--- result"; grep -a "$TAG\] ===== RESULT" "$LOG" | sed -E 's/.*===== (RESULT[^=]*) =====.*/\1/' | tail -1
echo "--- lua errors (excluding harness lines)"
grep -aE "LuaError|attempted index|tried to call nil" "$LOG" | grep -v "$TAG" | head -10 | cut -c1-200
if [ -n "$OUT" ]; then mkdir -p "$OUT"; cp "$C_U/Screenshots/"*.png "$OUT/" 2>/dev/null; echo "--- screenshots in $OUT"; ls "$OUT"; fi
[ -n "$(mine)" ] && { taskkill //PID "$(mine)" //F >/dev/null 2>&1; echo "stopped the client"; }
cp "$LOG" "$C_U/console.$TAG.txt" 2>/dev/null
# exit status: a test run passes only with "0 failed"; a shot run (no RESULT line) passes on DONE
if grep -aq "$TAG\] ===== RESULT" "$LOG" 2>/dev/null; then grep -aq "$TAG\] ===== RESULT .* 0 failed" "$LOG"; else grep -aq "$TAG\] DONE" "$LOG" 2>/dev/null; fi
