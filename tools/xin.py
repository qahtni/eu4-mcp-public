#!/usr/bin/env python3
"""Input injection on the headless display (EU4_DISPLAY, default :99) via XTEST. Fallback for menus the hook cannot drive.

Usage: xin.py click X Y [button] | move X Y | key KEYSYM [KEYSYM...] | type TEXT
Coordinates are in real screen pixels (1920x1080).
"""
import os
import sys
import time

os.environ.setdefault("DISPLAY", os.environ.get("EU4_DISPLAY", ":99"))
os.environ.setdefault("XAUTHORITY", os.path.join(os.path.expanduser(os.environ.get("EU4_MCP_STATE", "~/.config/eu4-mcp")), "Xauthority"))

from Xlib import X, XK, display  # noqa: E402
from Xlib.ext import xtest  # noqa: E402

d = display.Display()


def move(x, y):
    xtest.fake_input(d, X.MotionNotify, x=int(x), y=int(y))
    d.sync()
    time.sleep(0.15)


def click(x, y, button=1):
    move(x, y)
    xtest.fake_input(d, X.ButtonPress, int(button))
    d.sync()
    time.sleep(0.08)
    xtest.fake_input(d, X.ButtonRelease, int(button))
    d.sync()


def key(name):
    sym = XK.string_to_keysym(name)
    if sym == 0 and len(name) == 1:
        sym = ord(name)
    if sym == 0:
        sys.exit(f"unknown keysym {name}")
    code = d.keysym_to_keycode(sym)
    shift = False
    if code:
        # need shift if the keysym sits in column 1 of the keycode
        shift = d.keycode_to_keysym(code, 0) != sym and d.keycode_to_keysym(code, 1) == sym
    else:
        sys.exit(f"no keycode for {name}")
    sc = d.keysym_to_keycode(XK.XK_Shift_L)
    if shift:
        xtest.fake_input(d, X.KeyPress, sc)
    xtest.fake_input(d, X.KeyPress, code)
    d.sync()
    time.sleep(0.04)
    xtest.fake_input(d, X.KeyRelease, code)
    if shift:
        xtest.fake_input(d, X.KeyRelease, sc)
    d.sync()
    time.sleep(0.04)


SPECIAL = {" ": "space", ".": "period", "_": "underscore", "-": "minus", "/": "slash", "=": "equal", ",": "comma"}


def main():
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    if a[0] == "click":
        click(a[1], a[2], a[3] if len(a) > 3 else 1)
    elif a[0] == "move":
        move(a[1], a[2])
    elif a[0] == "key":
        for k in a[1:]:
            key(k)
    elif a[0] == "type":
        for ch in " ".join(a[1:]):
            key(SPECIAL.get(ch, ch))
    else:
        sys.exit(__doc__)


main()
