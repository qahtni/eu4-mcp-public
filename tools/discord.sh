#!/usr/bin/env bash
# Vesktop (Vencord's Discord client) on its OWN display (EU4_DISCORD_DISPLAY, :98; Xvfb 1440x1080), because the fullscreen EU4 on the game display holds an active
# pointer grab that swallows every click sent to other windows on it. You log in / share through noVNC on EU4_DISCORD_PORT (6082).
# A mirror of the game (ffplay x11grab of :99, as a window on :98) is what "Entire screen" shows, so Vesktop can share the game.
# usage: discord.sh start|stop|status|show|share on|page|off
#   share page    instead of the game mirror: the watch page (EU4_WATCH_PORT) in a Chromium window "Claude plays EU4" (1280x720, 16:9) on :98
#   start   units: eu4-discord-xvfb (:98), -wm (openbox, so the window picker lists real windows and windows can be moved/minimised),
#           eu4-discord (Vesktop), -vnc (x11vnc :5901 control, localhost),
#           -novnc (EU4_BIND:EU4_DISCORD_PORT). Vesktop is a normal window on :98; nothing of it ever appears on the game display :99.
#   share on|off  starts/stops eu4-discord-mirror (ffplay x11grab of :99 as an ordinary 1280x960 window): only while sharing, it costs CPU
#   stop    stops all of it (the game display, game, stream, game viewer are never touched)
# URL: http://$EU4_HOST:$EU4_DISCORD_PORT/  (share "Entire screen" = whole :98, or the "eu4-mirror" window).
# OPTIONAL and untested outside the author's machine. Vesktop is NOT included: install it yourself from github.com/Vencord/Vesktop
# and point VESKTOP_BIN at it. Its login state lives in its own config dir (never read or copied by anything here).
set -euo pipefail
. "$(dirname "$(readlink -f "$0")")/config.sh"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
BIN="${VESKTOP_BIN:-$(command -v vesktop || true)}"
STATE="$EU4_MCP_STATE"
GD="$EU4_DISPLAY"; DD="$EU4_DISCORD_DISPLAY"
export DISPLAY="$DD" XAUTHORITY="$STATE/Xauthority98"   # this file also holds the :99 cookie (copied from Xauthority) for the mirror
UNIT=eu4-discord
UNITS="$UNIT-novnc $UNIT-vnc $UNIT $UNIT-mirror $UNIT-page $UNIT-wm $UNIT-xvfb"
CHROME="${EU4_PAGE_BROWSER:-$(ls -d "$HOME"/.cache/ms-playwright/chromium-*/chrome-linux*/chrome 2>/dev/null | tail -1)}"
SIZE=1440x1080
HOST="$EU4_HOST"; BINDADDR="$EU4_BIND"
WEB="${XDG_DATA_HOME:-$HOME/.local/share}/eu4-mcp/novnc98"
# Chromium switches for a small, quiet client: low-end-device mode (smaller caches, fewer tiles), one renderer, no smooth scrolling,
# no background networking/component updates/crash uploads/spell-check service.
LEAN="${VESKTOP_FLAGS---enable-low-end-device-mode --renderer-process-limit=1 --disable-smooth-scrolling --disable-background-networking --disable-component-update --disable-breakpad --disable-features=SpellcheckService,MediaRouter,Translate}"

mirror_start() {
  # mirror of the game display as a window on :98 (what gets shared). Costs ~35-45 % of one core (x11grab + software drawing), so it only
  # runs while sharing: `discord.sh share on|off`. 15 fps + fast_bilinear was the cheapest of the settings tried.
  systemctl --user is-active -q $UNIT-mirror && return 0
  systemd-run --user --collect --unit=$UNIT-mirror -p Nice=15 -p CPUWeight=20 --setenv=DISPLAY="$DD" --setenv=XAUTHORITY="$XAUTHORITY" \
    --setenv=SDL_RENDER_DRIVER=software --setenv=SDL_VIDEO_X11_NET_WM_BYPASS_COMPOSITOR=0 \
    /usr/bin/ffplay -loglevel error -an -fflags nobuffer -flags low_delay -framedrop -x 1280 -y 960 -left 40 -top 40 -sws_flags fast_bilinear \
    -window_title eu4-mirror -f x11grab -draw_mouse 0 -framerate "${MIRROR_FPS:-15}" -video_size $SIZE -i "$GD" >/dev/null
}

page_start() {
  # the watch page as an ordinary window on :98 (720p: the page is 16:9). Software drawing, own throwaway profile, no login.
  systemctl --user is-active -q $UNIT-page && return 0
  [ -x "$CHROME" ] || { echo "no Chromium found (set EU4_PAGE_BROWSER)"; return 1; }
  systemd-run --user --collect --unit=$UNIT-page -p MemoryHigh=800M -p MemoryMax=1500M -p Nice=10 --setenv=DISPLAY="$DD" --setenv=XAUTHORITY="$XAUTHORITY" \
    "$CHROME" --no-sandbox --disable-gpu --disable-dev-shm-usage --user-data-dir="$HOME/.cache/eu4-mcp/page-profile" --no-first-run \
    --autoplay-policy=no-user-gesture-required --window-size=1280,720 --window-position=40,40 --class=eu4-page --test-type=gpu \
    $LEAN --app="http://$HOST:$EU4_WATCH_PORT/" >/dev/null
}

case "${1:-status}" in
  start)
    [ -x "$BIN" ] || { echo "Vesktop not found (set VESKTOP_BIN)"; exit 1; }
    if systemctl --user is-active -q $UNIT; then echo "already running"; exit 0; fi
    systemctl --user stop $UNITS 2>/dev/null || true
    # own X server :98 with its own cookie file (plus the :99 cookie, read-only use, for the mirror)
    touch "$XAUTHORITY"; chmod 600 "$XAUTHORITY"; xauth -q remove "$DD" 2>/dev/null || true
    xauth -q add "$DD" . "$(mcookie)"
    XAUTHORITY="$STATE/Xauthority" xauth -q extract - "$GD" | xauth -q merge -
    systemd-run --user --collect --unit=$UNIT-xvfb /usr/bin/Xvfb "$DD" -screen 0 ${SIZE}x24 -auth "$XAUTHORITY" -nolisten tcp -noreset >/dev/null
    for i in $(seq 20); do xdpyinfo >/dev/null 2>&1 && break; sleep 0.5; done
    xdpyinfo >/dev/null 2>&1 || { echo "Xvfb :98 did not come up"; exit 1; }
    systemd-run --user --collect --unit=$UNIT-wm --setenv=DISPLAY="$DD" --setenv=XAUTHORITY="$XAUTHORITY" /usr/bin/openbox >/dev/null
    # --no-sandbox: AppArmor (apparmor_restrict_unprivileged_userns=1) blocks the user-namespace sandbox for an unpackaged Electron.
    # --disable-gpu: software rendering (:98 has no GPU), keeps the GPU free for the game.
    systemd-run --user --collect --unit=$UNIT -p MemoryHigh=1G -p MemoryMax=2G -p Nice=5 --setenv=DISPLAY="$DD" --setenv=XAUTHORITY="$XAUTHORITY" \
      "$BIN" --no-sandbox --ozone-platform=x11 --disable-gpu --disable-dev-shm-usage $LEAN >/dev/null
    # noVNC on :6082 (control mode, own x11vnc on :98); root URL redirects to vnc.html with autoconnect
    mkdir -p "$WEB"; for f in /usr/share/novnc/*; do ln -sfn "$f" "$WEB/$(basename "$f")"; done
    printf '<meta http-equiv="refresh" content="0;url=vnc.html?autoconnect=true&resize=scale">\n' > "$WEB/index.html"
    systemd-run --user --collect --unit=$UNIT-vnc /usr/bin/x11vnc -display "$DD" -auth "$XAUTHORITY" -localhost -rfbport 5901 -nopw -forever -shared -nap -quiet >/dev/null
    systemd-run --user --collect --unit=$UNIT-novnc /usr/bin/websockify --web "$WEB" $BINDADDR:$EU4_DISCORD_PORT 127.0.0.1:5901 >/dev/null
    echo "started. Open http://$HOST:$EU4_DISCORD_PORT/ (log in to Discord there). Before sharing run: $0 share on  (and: share off afterwards)"
    ;;
  share)
    systemctl --user is-active -q $UNIT || { echo "Vesktop is not running: $0 start"; exit 1; }
    case "${2:-on}" in
      on) systemctl --user stop $UNIT-page 2>/dev/null || true; mirror_start; echo "game mirror on: share Entire screen or the eu4-mirror window in Discord" ;;
      page) systemctl --user stop $UNIT-mirror 2>/dev/null || true; page_start && echo "watch page on: share the window \"Claude plays EU4\" in Discord" ;;
      off) systemctl --user stop $UNIT-mirror $UNIT-page 2>/dev/null || true; echo "sharing windows off" ;;
      *) echo "usage: discord.sh share on|page|off"; exit 2 ;;
    esac ;;
  show)  # the Discord window was closed to the tray: a second start of the same program makes the running one show it again
    systemctl --user is-active -q $UNIT || { echo "Vesktop is not running: $0 start"; exit 1; }
    timeout 15 "$BIN" --no-sandbox --ozone-platform=x11 --disable-gpu >/dev/null 2>&1 || true; echo "Discord window shown" ;;
  stop) systemctl --user stop $UNITS 2>/dev/null || true; rm -f "$STATE/discord_show"; echo stopped ;;
  status)
    for u in $UNITS; do
      m=$(systemctl --user show $u -p MemoryCurrent --value 2>/dev/null); c=$(systemctl --user show $u -p CPUUsageNSec --value 2>/dev/null)
      case "$m" in ''|*[!0-9]*) m=0 ;; esac; case "$c" in ''|*[!0-9]*) c=0 ;; esac
      printf '%-22s %-9s %5d MB  cpu %ds\n' "$u" "$(systemctl --user is-active $u || true)" $((m / 1048576)) $((c / 1000000000))
    done
    xwininfo -root -tree 2>/dev/null | grep -iE "vesktop|mirror|claude plays" || true
    ;;
  *) echo "usage: discord.sh start|stop|status|show|share on|page|off"; exit 2 ;;
esac
