"""Blocker watch: every N minutes grab a small screenshot of the game display (EU4_DISPLAY), ask a tool-light Haiku run whether a modal pop-up blocks
the game (message box, "Completed Estate Agenda", event window, error dialog ...) and, if so, click its OK/close button with XTEST.

No game state is read or changed except that one click. Each run is a fresh `claude -p` (no session) with one small JPEG, so token use is tiny.
Run: python -m eu4mcp.blocker_watch [--every 900]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from . import journal, paths

W, H = 1440, 1080          # game display
SW, SH = 720, 540          # screenshot sent to the model (half size)
ENV = {**os.environ, "DISPLAY": paths.display(), "XAUTHORITY": str(paths.xauthority())}
PROMPT = (
    "Read the image file shot.jpg (a 720x540 screenshot of the strategy game Europa Universalis IV). Decide whether a MODAL pop-up window is open in the "
    "middle of the screen that blocks play: a message box with an Ok/Close button (e.g. 'Completed Estate Agenda', 'Error', 'Game over', 'Save failed'), "
    "or an event window with only a single dismiss button. Ordinary UI (top bar, map, side panels, tooltips, the small message list at the right edge, "
    "a country or province window the player opened) is NOT a blocker. An event window with several options is NOT a blocker either (the player answers those). "
    'Answer with ONE line of JSON only, no other text: {"blocker": false} or {"blocker": true, "what": "<short title>", "x": <int>, "y": <int>} where x,y '
    "are the pixel coordinates in the 720x540 image of the centre of the single Ok/Close/Continue button to click."
)


def shot(path: Path) -> None:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "x11grab", "-video_size", f"{W}x{H}", "-i", ENV["DISPLAY"], "-frames:v", "1",
                    "-vf", f"scale={SW}:{SH}", "-q:v", "6", str(path)], env=ENV, check=True, timeout=30)


def ask(cwd: str) -> dict:
    r = subprocess.run(["claude", "-p", "--model", "haiku", "--effort", "low", "--tools", "Read", "--allowedTools", "Read", "--strict-mcp-config",
                        "--disable-slash-commands", "--no-session-persistence", "--system-prompt",
                        "You inspect screenshots and answer exactly as asked.", "--setting-sources", "", PROMPT], capture_output=True, text=True, timeout=180, cwd=cwd)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:300])
    m = re.search(r"\{.*\}", r.stdout, re.S)
    return json.loads(m.group(0)) if m else {"blocker": False}


def click(x: int, y: int) -> None:
    from Xlib import X, display
    from Xlib.ext import xtest
    d = display.Display(ENV["DISPLAY"])
    xtest.fake_input(d, X.MotionNotify, x=x, y=y)
    d.sync(); time.sleep(0.2)
    xtest.fake_input(d, X.ButtonPress, 1); d.sync(); time.sleep(0.08)
    xtest.fake_input(d, X.ButtonRelease, 1); d.sync()


PARK = (1300, 985)   # inside the minimap frame (bottom right, 1440x1080 screen): no province tooltip, far from the screen edges (edge scroll)


def park_mouse() -> None:
    """Move the pointer to PARK so it does not hover a province (tooltips on the stream)."""
    from Xlib import X, display
    from Xlib.ext import xtest
    d = display.Display(ENV["DISPLAY"])
    xtest.fake_input(d, X.MotionNotify, x=PARK[0], y=PARK[1])
    d.sync()


def check_once() -> str:
    os.environ.update({"DISPLAY": ENV["DISPLAY"], "XAUTHORITY": ENV["XAUTHORITY"]})
    with tempfile.TemporaryDirectory(prefix="eu4-blocker-") as cwd:
        shot(Path(cwd) / "shot.jpg")
        res = ask(cwd)
        if not res.get("blocker"):
            park_mouse()
            return "clear"
        x, y = int(res["x"]), int(res["y"])
        if not (0 <= x < SW and 0 <= y < SH):
            return f"blocker '{res.get('what')}' but bad coordinates {x},{y}: not clicked"
        click(x * W // SW, y * H // SH)
        time.sleep(2)
        shot(Path(cwd) / "shot.jpg")
        again = ask(cwd)
        park_mouse()
        return f"dismissed '{res.get('what')}' by clicking {x * W // SW},{y * H // SH}" + (" (STILL BLOCKED)" if again.get("blocker") else "")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=float, default=900, help="seconds between checks")
    a = ap.parse_args()
    while True:
        try:
            msg = check_once()
            if msg != "clear":
                journal.write("note", tool="blocker_watch", text=msg, date=None)
                print(time.strftime("%F %T"), msg, flush=True)
        except Exception as e:
            print(f"blocker_watch: {e}", flush=True)
        time.sleep(a.every)


if __name__ == "__main__":
    main()
