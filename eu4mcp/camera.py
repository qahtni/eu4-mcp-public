"""Camera helper: keeps the map on the action for viewers. No model, no key presses: it calls the game's own
CInGameIdler::CenterOnPosition through the hook. Run: python -m eu4mcp.camera  (tools/watch.sh starts it).

What it shows, first match:
  1. the province of a fresh order in the journal (move target, build, recruit, develop, colonise ...): medium zoom
  2. the player's largest marching army, followed province by province: medium zoom
  3. otherwise it leaves the camera alone (so a person on the viewer can pan freely).
Each shot is held for HOLD seconds before a lower-priority one may replace it.
"""
from __future__ import annotations

import re
import struct
import time
from pathlib import Path

from . import journal, paths
from .hookclient import HookClient, HookError
from .live import Live

CAM = 0x11F8          # CInGameIdler -> CEU3Camera*  (CInGameIdler::GetCamera)
CAM_HEIGHT = 0x698    # target height, read by CenterOnPosition (CEU3Camera::ZoomToHeight writes it)
CENTER = "_ZN12CInGameIdler16CenterOnPositionEii"
CLOSE, MEDIUM, WIDE = 180.0, 300.0, 700.0   # game limits: 50..3000
HOLD = 8.0


def positions() -> dict[int, tuple[int, int]]:
    """Province id -> city position in map pixels (map/positions.txt, first pair)."""
    txt = (paths.game_dir() / "map" / "positions.txt").read_text(encoding="latin-1")
    return {int(m.group(1)): (round(float(m.group(2))), round(float(m.group(3))))
            for m in re.finditer(r"^(\d+)=\{\s*position=\{\s*([\d.]+) ([\d.]+)", txt, re.M)}


class Camera:
    def __init__(self, hook: HookClient):
        self.h = hook
        self.pos = positions()

    def look(self, province: int, height: float | None = MEDIUM) -> bool:
        xy = self.pos.get(int(province))
        if not xy:
            return False
        steps = ["idler", f"rd8 r0+{CAM:#x}"]
        if height is not None:
            h = min(3000.0, max(50.0, float(height)))
            steps.append(f"wr4 r1+{CAM_HEIGHT:#x} {struct.unpack('<I', struct.pack('<f', h))[0]}")
        steps.append(f"call @{CENTER} r0 {xy[0]} {xy[1]}")
        self.h.vm(*steps)
        return True


def order_province(e: dict) -> int:
    """Province a journal order points at, 0 if none."""
    if e.get("kind") != "act" or not e.get("ok", True):
        return 0
    a = e.get("args") or {}
    for k in ("to", "province"):
        try:
            if int(a.get(k) or 0) > 0:
                return int(a[k])
        except (TypeError, ValueError):
            pass
    return 0


def main() -> None:
    cam: Camera | None = None
    _, seen = journal.read(0)      # only orders given from now on
    shown, prio, since = 0, 0, 0.0
    while True:
        time.sleep(2)
        try:
            h = HookClient(timeout=5)
            info = h.info()
            if not info.get("ingame"):
                continue
            cam = cam or Camera(h)
            cam.h = h
            target, p = 0, 0
            entries, seen2 = journal.read(seen)
            if seen2 < seen:
                seen2, entries = journal.read(0)[1], []
            seen = seen2
            for e in entries:
                if order_province(e):
                    target, p = order_province(e), 2
            if not target:
                units = [u for u in Live(h).units(info["player"]) if u["moving"] and not u["navy"] and u["loc"]]
                if units:
                    target, p = max(units, key=lambda u: u["n"])["loc"], 1
            now = time.time()
            if not target or target == shown or (p < prio and now - since < HOLD):
                continue
            if cam.look(target):
                shown, prio, since = target, p, now
        except (HookError, OSError, KeyError, ValueError):
            continue


if __name__ == "__main__":
    main()
