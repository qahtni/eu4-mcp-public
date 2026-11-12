#!/usr/bin/env python3
"""Camera probe. Usage: cam.py PROVINCE_ID [HEIGHT]   (centres the map on the province's city position)"""
import re, struct, subprocess, sys, os
pid, h = int(sys.argv[1]), float(sys.argv[2]) if len(sys.argv) > 2 else None
txt = open(os.path.join(os.path.expanduser(os.environ.get("EU4_GAME_DIR", "~/games/eu4")), "map", "positions.txt"), encoding="latin-1").read()
m = re.search(r"^%d=\{\s*position=\{\s*([\d.]+) ([\d.]+)" % pid, txt, re.M)
x, y = round(float(m.group(1))), round(float(m.group(2)))
steps = ["idler", "rd8 r0+0x11f8"]
if h is not None:
    steps.append("wr4 r1+0x698 %d" % struct.unpack("<I", struct.pack("<f", h))[0])
steps.append("call @_ZN12CInGameIdler16CenterOnPositionEii r0 %d %d" % (x, y))
print(x, y); subprocess.run([sys.executable, os.path.join(os.path.dirname(__file__), "vm.py"), *steps])
