#!/usr/bin/env bash
# Prepare an isolated cachedir with a copy of a mod plus the harness and one test file in
# it. Nothing under the user's Zomboid folder is read or written; the game is pointed at the
# cachedir with -cachedir by run_client.sh / run_server.sh. Git Bash on Windows.
#
#   harness/install.sh --mod-dir <dev mods dir> --mod-id ID --cachedir <path> --side client \
#                      --test harness/examples/FoodPreservation/lua/client/ZZ_FPClientTest.lua [--lang KO] [--size 1280x720]
#
# <dev mods dir> is the B42 layout that ships: <dir>/42/mod.info and <dir>/common/media/...
# A mod.info at that root makes the B42 client refuse the folder ("refusing to list"), so
# it is reported and not copied. Re-running replaces the mod copy and the test file; the
# cachedir's saves, options and logs are kept.
set -u
MODDIR=""; MODID=""; CACHEDIR=""; SIDE=""; TEST=""; LANG_=KO; SIZE=1280x720; TOS=0
while [ $# -gt 0 ]; do
  case "$1" in
    --mod-dir) MODDIR="$2"; shift 2 ;;
    --mod-id) MODID="$2"; shift 2 ;;
    --cachedir) CACHEDIR="$2"; shift 2 ;;
    --side) SIDE="$2"; shift 2 ;;
    --test) TEST="$2"; shift 2 ;;
    --lang) LANG_="$2"; shift 2 ;;
    --size) SIZE="$2"; shift 2 ;;
    --accept-tos) TOS=1; shift ;;
    *) echo "unknown arg $1"; exit 2 ;;
  esac
done
[ -n "$MODDIR" ] && [ -n "$MODID" ] && [ -n "$CACHEDIR" ] && { [ "$SIDE" = client ] || [ "$SIDE" = server ]; } \
  || { echo "usage: install.sh --mod-dir <dir> --mod-id ID --cachedir <path> --side client|server [--test file.lua]"; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"
C_U="$(cygpath -u "$CACHEDIR")"; MODDIR="$(cygpath -u "$MODDIR")"
DEST="$C_U/mods/$MODID"

[ -f "$MODDIR/42/mod.info" ] || { echo "$MODDIR/42/mod.info not found: expected the B42 layout (42/mod.info + common/)"; exit 1; }
[ -f "$MODDIR/mod.info" ] && echo "NOTE: $MODDIR/mod.info exists at the root; B42 refuses such a folder, so it is not copied"
mkdir -p "$C_U/mods" && rm -rf "$DEST" && mkdir -p "$DEST"
( cd "$MODDIR" && tar --exclude=.git --exclude=.gitkeep --exclude=./mod.info -cf - . ) | ( cd "$DEST" && tar -xf - )
LUA="$DEST/common/media/lua/$SIDE"
mkdir -p "$LUA/Harness" && cp "$HERE/lua/$SIDE/Harness/"*.lua "$LUA/Harness/"
if [ -n "$TEST" ]; then cp "$TEST" "$LUA/$(basename "$TEST")"; fi

if [ "$SIDE" = client ]; then
  # The game's own words, from the marker it leaves behind: "If this file does not exist,
  # default.txt will be reset to empty (no mods active)." A fresh cachedir has no marker, so
  # the first launch would silently drop the mod we just listed. (Name follows the game
  # version: 42_00 for B42; a later build writes its own and resets once more.)
  [ -f "$C_U/mods/reset-mods-42_00.txt" ] || printf 'If this file does not exist, default.txt will be reset to empty (no mods active).' > "$C_U/mods/reset-mods-42_00.txt"
  printf 'VERSION = 1,\n\nmods\n{\n\tmod = %s,\n}\n\nmaps\n{\n}\n' "$MODID" > "$C_U/mods/default.txt"
  if [ ! -f "$C_U/options.ini" ]; then
    # written before the first launch: a window (not fullscreen) the driver can capture and
    # click; no pause on focus loss (another window taking focus froze a run at tick 480);
    # no survival guide (it pauses the first game); the language the screenshots should use
    W="${SIZE%x*}"; H="${SIZE#*x}"
    printf 'fullScreen=false\nborderless=false\nwidth=%s\nheight=%s\nfocusloss=false\nshowSurvivalGuide=false\nlanguage=%s\nvsync=false\n' "$W" "$H" "$LANG_" > "$C_U/options.ini"
  fi
  # The first launch on a fresh cachedir shows the game's terms-of-service dialog and waits
  # for a click; nothing the runner does gets past it. --accept-tos records the acceptance
  # in this isolated options.ini (the same terms the person running this accepted in their
  # own install). It is a deliberate choice, so it is a flag and not the default.
  if [ "$TOS" = 1 ] && ! grep -q '^termsOfServiceVersion=' "$C_U/options.ini"; then
    echo "termsOfServiceVersion=1" >> "$C_U/options.ini"
  fi
  [ "$TOS" = 1 ] || grep -q '^termsOfServiceVersion=' "$C_U/options.ini" || echo "NOTE: first launch will stop at the terms dialog; click Agree once, or re-run with --accept-tos"
fi
echo "installed $MODID ($SIDE) into $DEST"
echo "  harness: $LUA/Harness/"; [ -n "$TEST" ] && echo "  test:    $LUA/$(basename "$TEST")"
find "$DEST" -type f | wc -l | sed 's/^/  files:   /'
