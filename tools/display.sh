#!/bin/bash
# Headless X display (EU4_DISPLAY, default :99) for EU4. Starting Xorg needs sudo; see display/README.md. Usage: display.sh start|stop|status|shot [out.png] [width]
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
. "$(dirname "$(readlink -f "$0")")/config.sh"
STATE="$EU4_MCP_STATE"; D="${EU4_DISPLAY#:}"
XCONF="${EU4_XORG_CONF:-$( [ -f "$REPO/display/xorg.conf" ] && echo "$REPO/display/xorg.conf")}"
export DISPLAY="$EU4_DISPLAY" XAUTHORITY="$EU4_XAUTH"
case "${1:-status}" in
start)
    if systemctl is-active -q eu4-xorg; then echo "already running"; exit 0; fi
    mkdir -p "$STATE"; rm -f "$XAUTHORITY"; touch "$XAUTHORITY"; chmod 600 "$XAUTHORITY"
    xauth -q add "$EU4_DISPLAY" . "$(mcookie)"
    sudo systemd-run --collect --unit=eu4-xorg /usr/bin/Xorg "$EU4_DISPLAY" ${XCONF:+-config "$XCONF"} \
        -auth "$XAUTHORITY" -nolisten tcp -noreset -logfile "/var/log/Xorg.$D.log" $EU4_XORG_VT
    # X can take a while on a busy machine: wait up to 60 s, and report failure only when X is really not up
    for i in $(seq 120); do xdpyinfo >/dev/null 2>&1 && break; sleep 0.5; done
    xdpyinfo >/dev/null 2>&1 || { echo "X $EU4_DISPLAY did not come up (sudo journalctl -u eu4-xorg -n 20)"; exit 1; }
    xdpyinfo | grep -E 'dimensions|version number'; glxinfo -B 2>/dev/null | grep -E 'renderer string|direct rendering'
    exit 0
    ;;
stop) sudo systemctl stop eu4-xorg ;;
status) systemctl is-active eu4-xorg; xdpyinfo 2>/dev/null | grep dimensions ;;
shot)
    out="${2:-$STATE/shot.png}"; w="${3:-1280}"
    ffmpeg -loglevel error -y -f x11grab -video_size "${EU4_SCREEN:-1440x1080}" -i "$EU4_DISPLAY" -frames:v 1 -vf "scale=$w:-1" "$out" && echo "$out"
    ;;
*) echo "usage: display.sh start|stop|status|shot [out.png] [width]"; exit 2 ;;
esac
