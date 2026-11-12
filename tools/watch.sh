#!/bin/sh
# Watch page (game picture + play log) on http://$EU4_HOST:$EU4_WATCH_PORT/ (EU4_BIND / EU4_WATCH_PORT) ; needs the viewer units eu4-vnc/eu4-novnc for the picture.
. "$(dirname "$(readlink -f "$0")")/config.sh"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
case "$1" in
  start)
    systemctl --user is-active -q eu4-narrator || \
      systemd-run --user --collect --unit=eu4-narrator -p "WorkingDirectory=$REPO" --setenv=PATH="$PATH" "$REPO/.venv/bin/python" -m eu4mcp.narrator
    systemctl --user is-active -q eu4-camera || \
      systemd-run --user --collect --unit=eu4-camera -p "WorkingDirectory=$REPO" "$REPO/.venv/bin/python" -m eu4mcp.camera
    systemctl --user is-active -q eu4-blockwatch || \
      systemd-run --user --collect --unit=eu4-blockwatch -p "WorkingDirectory=$REPO" --setenv=PATH="$PATH" "$REPO/.venv/bin/python" -m eu4mcp.blocker_watch
    systemctl --user is-active -q eu4-watch && { echo "already running"; exit 0; }
    systemd-run --user --collect --unit=eu4-watch -p "WorkingDirectory=$REPO" "$REPO/.venv/bin/python" -m eu4mcp.watch --port "${2:-$EU4_WATCH_PORT}" ;;
  stop) systemctl --user stop eu4-watch eu4-narrator eu4-camera eu4-blockwatch ;;
  status) systemctl --user status eu4-watch eu4-narrator eu4-camera eu4-blockwatch --no-pager -n 5 ;;
  *) echo "usage: $0 start [port] | stop | status"; exit 2 ;;
esac
