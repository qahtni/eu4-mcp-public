# Headless display

`tools/display.sh start` starts a second Xorg (default display `:99`) for the game, via `sudo systemd-run` (root is needed to start Xorg).
It uses `EU4_XORG_CONF` if set, otherwise Xorg autodetects. `xorg.conf.example` is the author's config for an NVIDIA card with an
HDMI dummy plug and a custom 1440x1080 mode. Copy it, adapt `BusID`/`Driver`/mode, and point `EU4_XORG_CONF` at the copy.
Alternative without root: run the game on any existing X display and set `EU4_DISPLAY` (for example your desktop's `:0`); the
stream and the blocker watch then capture that display. Untested outside the author's machine.
