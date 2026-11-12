#!/usr/bin/env python3
"""Send one request to the in-game hook. Usage: hookc.py OP [key=value ...]   e.g. hookc.py console line="speed 3" """
import json, os, socket, sys
state = os.environ.get("EU4_MCP_STATE", os.path.expanduser("~/.config/eu4-mcp"))
req = {"id": 1, "op": sys.argv[1]}
for kv in sys.argv[2:]:
    k, v = kv.split("=", 1)
    try: v = json.loads(v)
    except ValueError: pass
    req[k] = v
s = socket.socket(socket.AF_UNIX); s.settimeout(float(os.environ.get("HOOK_TIMEOUT", "90")))
s.connect(os.path.join(state, "hook.sock")); s.sendall((json.dumps(req) + "\n").encode())
buf = b""
while not buf.endswith(b"\n"):
    d = s.recv(65536)
    if not d: break
    buf += d
print(buf.decode().strip())
