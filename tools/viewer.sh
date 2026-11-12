#!/usr/bin/env bash
# Picture of the game for watching only: x11vnc runs with -viewonly, so no viewer can send keys or mouse to the game.
# usage: viewer.sh start [control]|stop|status
set -euo pipefail
. "$(dirname "$(readlink -f "$0")")/config.sh"
XAUTH="$EU4_XAUTH"
case "${1:-status}" in
  start)
    VIEWONLY="-viewonly"; [ "${2:-}" = "control" ] && VIEWONLY=""  # "start control": mouse and keyboard allowed (allow mouse and keyboard)
    systemctl --user stop eu4-vnc eu4-novnc 2>/dev/null || true
    systemd-run --user --collect --unit=eu4-vnc --setenv=XAUTHORITY="$XAUTH" \
      /usr/bin/x11vnc -display "$EU4_DISPLAY" $VIEWONLY -localhost -xrandr -rfbport 5900 -nopw -forever -shared -quiet
    systemd-run --user --collect --unit=eu4-novnc /usr/bin/websockify --web /usr/share/novnc "$EU4_BIND:$EU4_VNC_PORT" 127.0.0.1:5900
    ;;
  stop) systemctl --user stop eu4-vnc eu4-novnc ;;
  status) systemctl --user --no-pager status eu4-vnc eu4-novnc | grep -E "●|Active|x11vnc|websockify" ;;
esac
