#!/usr/bin/env bash
# Boot an isolated Project Zomboid dedicated server on a prepared cachedir (see install.sh),
# wait for the Lua self-test to print "<TAG>] DONE", print its lines and stop the JVM.
# The user's own server and saves are never touched: -cachedir replaces the whole Zomboid
# user folder. Git Bash on Windows.
#
#   harness/run_server.sh --cachedir 'C:\...\fptest\cache' --servername fptest --mod FoodPreservation --tag FPTEST [--port 16271] [--timeout 600]
#
# First boot on a fresh cachedir generates <servername>.ini and then keeps running with
# PauseEmpty=true, under which game time stands still and EveryOneMinute never fires; so a
# fresh cachedir gets a generation boot, the ini is patched, and the real boot follows.
set -u
CACHEDIR=""; NAME=""; MODID=""; TAG=""; PORT=16271; TIMEOUT=600; PZSERVER="/c/PZServer"
while [ $# -gt 0 ]; do
  case "$1" in
    --cachedir) CACHEDIR="$2"; shift 2 ;;
    --servername) NAME="$2"; shift 2 ;;
    --mod) MODID="$2"; shift 2 ;;
    --tag) TAG="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --pzserver) PZSERVER="$2"; shift 2 ;;
    *) echo "unknown arg $1"; exit 2 ;;
  esac
done
[ -n "$CACHEDIR" ] && [ -n "$NAME" ] && [ -n "$MODID" ] && [ -n "$TAG" ] || { echo "usage: run_server.sh --cachedir <path> --servername NAME --mod ID --tag TAG"; exit 2; }
C_W="$(cygpath -w "$CACHEDIR")"; C_U="$(cygpath -u "$CACHEDIR")"
LOG="$C_U/server-console.txt"
INI="$C_U/Server/$NAME.ini"

# only the JVM running on THIS cachedir; other sessions run their own servers
mine() { powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name = 'java.exe'\" | Where-Object { \$_.CommandLine -like '*$(basename "$C_U")*' -and \$_.CommandLine -like '*GameServer*' } | ForEach-Object { \$_.ProcessId }" | tr -d '\r\n '; }
stop() { local p; p="$(mine)"; [ -n "$p" ] && { taskkill //PID "$p" //F >/dev/null 2>&1; sleep 4; }; }
boot() {
  [ -f "$LOG" ] && mv "$LOG" "$C_U/server-console.prev.txt"
  ( cd "$PZSERVER" && "./jre64/bin/java.exe" -Djava.awt.headless=true -Dzomboid.steam=0 -XX:+UseZGC -Xms2g -Xmx6g \
      -Djava.library.path=natives/ -cp "java/;java/projectzomboid.jar" zombie.network.GameServer -statistic 0 \
      "-cachedir=$C_W" -servername "$NAME" -adminusername admin -adminpassword harness \
      -port "$PORT" -udpport "$((PORT + 1))" < /dev/null > "$LOG" 2>&1 & )
}
waitfor() { local pat="$1" lim="$2" t=0; until grep -aq "$pat" "$LOG" 2>/dev/null; do sleep 5; t=$((t + 5)); [ -z "$(mine)" ] && return 1; [ $t -ge "$lim" ] && return 2; done; return 0; }

stop   # RakNet keeps the UDP port for a moment after a kill; a stale JVM fails the bind (Code: 5)
if [ ! -f "$INI" ]; then
  echo "no $NAME.ini yet: generation boot"
  boot; waitfor "SERVER STARTED" 300 || { echo "generation boot did not reach SERVER STARTED"; tail -20 "$LOG"; stop; exit 1; }
  stop
fi
# what a self-test needs: game time running with no players, only this mod, private ports
sed -i "s/^PauseEmpty=.*/PauseEmpty=false/; s/^Mods=.*/Mods=$MODID/; s/^WorkshopItems=.*/WorkshopItems=/; s/^Public=.*/Public=false/; s/^UPnP=.*/UPnP=false/; s/^DefaultPort=.*/DefaultPort=$PORT/; s/^UDPPort=.*/UDPPort=$((PORT + 1))/" "$INI"
[ -f "$C_U/Server/${NAME}_SandboxVars.lua" ] || printf 'SandboxVars = {\n    VERSION = 6,\n    Zombies = 6,\n    DayLength = 1,\n}\n' > "$C_U/Server/${NAME}_SandboxVars.lua"

boot; echo "launched at $(date +%H:%M:%S) on $C_W"
waitfor "SERVER STARTED" 300; rc=$?
[ $rc -ne 0 ] && { echo "server did not start (rc=$rc)"; grep -aiE "error|exception" "$LOG" | grep -v "$TAG" | head -10 | cut -c1-200; stop; exit 1; }
echo "SERVER STARTED at $(date +%H:%M:%S); waiting for $TAG] DONE"
waitfor "$TAG\] DONE" "$TIMEOUT"; rc=$?
[ $rc -eq 1 ] && echo "server died"; [ $rc -eq 2 ] && echo "timeout after ${TIMEOUT}s"

echo "--- harness lines"; grep -a "$TAG\]" "$LOG" | sed 's/^LOG  : Lua *f:\([0-9]*\)[^>]*> /\1 /' | cut -c1-220
echo "--- result"; grep -a "$TAG\] ===== RESULT" "$LOG" | sed -E 's/.*===== (RESULT[^=]*) =====.*/\1/' | tail -1
echo "--- mod-tagged errors after SERVER STARTED"
awk '/SERVER STARTED/{f=1} f' "$LOG" | grep -aE "LuaError|attempted index|tried to call nil" | grep -v "$TAG" | head -10 | cut -c1-200
stop; echo "stopped the server"
grep -aq "$TAG\] ===== RESULT .* 0 failed" "$LOG" 2>/dev/null
