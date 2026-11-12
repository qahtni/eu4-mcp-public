#!/usr/bin/env bash
# 30 fps video of the game screen (X display -> NVENC H.264 -> HLS) for the watch page. One-way: video carries no input.
# usage: stream.sh start [fps] | stop | status
set -euo pipefail
. "$(dirname "$(readlink -f "$0")")/config.sh"
export DISPLAY="$EU4_DISPLAY" XAUTHORITY="$EU4_XAUTH"
DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/eu4-stream"
case "${1:-status}" in
  start)
    systemctl --user stop eu4-stream 2>/dev/null || true
    FPS="${2:-30}"
    # the game window if there is one (borderless, may be smaller than the screen), else the active mode
    GEO="$(xwininfo -name 'Europa Universalis IV' 2>/dev/null | awk '/Absolute upper-left X/ {x=$4} /Absolute upper-left Y/ {y=$4} /Width:/ {w=$2} /Height:/ {h=$2} END {if (w) print w"x"h" "x","y}')"
    if [ -n "$GEO" ]; then SIZE="${GEO% *}"; POS="${GEO#* }"; else SIZE="$(xrandr | sed -n 's/.* connected[^0-9]*\([0-9]*x[0-9]*\)+.*/\1/p' | head -1)"; POS="0,0"; fi
    mkdir -p "$DIR"; rm -f "$DIR"/*
    systemd-run --user --collect --unit=eu4-stream --setenv=XAUTHORITY="$XAUTHORITY" -p Restart=on-failure -p RestartSec=3 \
      /usr/bin/ffmpeg -hide_banner -loglevel warning -f x11grab -draw_mouse 0 -framerate "$FPS" -video_size "$SIZE" -i "$EU4_DISPLAY+$POS" \
      ${EU4_ENCODER:--c:v h264_nvenc -preset p4 -tune ll -rc cbr} -b:v 2M -maxrate 2M -bufsize 1M -fps_mode cfr -r "$FPS" -g "$FPS" -bf 0 -pix_fmt yuv420p \
      -f hls -hls_time 1 -hls_list_size 6 -hls_flags delete_segments+independent_segments+omit_endlist \
      -hls_segment_filename "$DIR/s%05d.ts" "$DIR/live.m3u8"
    ;;
  stop) systemctl --user stop eu4-stream ;;
  status) systemctl --user --no-pager status eu4-stream -n 3 | head -12; ls "$DIR" 2>/dev/null | tail -3 ;;
esac
