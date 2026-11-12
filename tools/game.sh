#!/bin/bash
# Start/stop EU4 on the headless display. Usage: game.sh start [--no-hook] [game args...] | stop | status | log [N]
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"
. "$(dirname "$(readlink -f "$0")")/config.sh"
GAME="$EU4_GAME_DIR"
STATE="$EU4_MCP_STATE"
HOOK="$REPO/hook/eu4hook.so"
case "${1:-status}" in
start)
    shift
    if systemctl --user is-active -q eu4-game; then echo "already running"; exit 0; fi
    systemctl is-active -q eu4-xorg || "$REPO/tools/display.sh" start >/dev/null || exit 1
    env=(-E "DISPLAY=$EU4_DISPLAY" -E "XAUTHORITY=$EU4_XAUTH" -E SDL_AUDIODRIVER=dummy -E "EU4_MCP_STATE=$STATE")
    if [ "${1:-}" = "--no-hook" ]; then shift
    elif [ -f "$HOOK" ]; then env+=(-E "LD_PRELOAD=$HOOK" -E "EU4_MCP_FPS=${EU4_MCP_FPS-30}")
    else echo "note: $HOOK not built, starting without the hook"; fi
    systemctl --user reset-failed eu4-game 2>/dev/null
    systemd-run --user --collect --unit=eu4-game -p "WorkingDirectory=$GAME" "${env[@]}" "$GAME/eu4" "$@"
    ;;
stop) systemctl --user stop eu4-game ;;
status) systemctl --user is-active eu4-game ;;
log) journalctl --user -u eu4-game -n "${2:-20}" --no-pager | cut -c40-260 ;;
*) echo "usage: game.sh start [--no-hook] [game args...] | stop | status | log [N]"; exit 2 ;;
esac
