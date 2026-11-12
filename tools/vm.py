#!/usr/bin/env python3
"""Run hook vm steps. Usage: vm.py 'step' 'step' ...   (see hook/eu4hook.cpp for the step language)"""
import json, os, socket, sys
state = os.environ.get("EU4_MCP_STATE", os.path.expanduser("~/.config/eu4-mcp"))
s = socket.socket(socket.AF_UNIX); s.settimeout(30)
s.connect(os.path.join(state, "hook.sock")); s.sendall((json.dumps({"id": 1, "op": "vm", "steps": sys.argv[1:]}) + "\n").encode())
buf = b""
while not buf.endswith(b"\n"):
    d = s.recv(65536)
    if not d: break
    buf += d
r = json.loads(buf)
if not r["ok"]: sys.exit("ERR " + r["error"])
for i, (st, v) in enumerate(zip(sys.argv[1:], r["result"])):
    print(f"r{i} {st!r:50} = {v if not isinstance(v, str) or not v.startswith('0x') else v + ' (' + str(int(v, 16)) + ')'}")
