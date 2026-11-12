"""Fake hook: a unix-socket server speaking the protocol in CLAUDE.md / hook/README.md."""
import json
import os
import socketserver
import threading
from pathlib import Path


class FakeHook:
    def __init__(self, path: Path, save_dir: Path | None = None, delay: float = 0.0):
        self.path = Path(path)
        if self.path.exists():
            os.unlink(self.path)
        self.save_dir = save_dir
        self.delay = delay
        self.log: list[dict] = []
        self.paused = False
        self.speed = 3
        outer = self

        class H(socketserver.StreamRequestHandler):
            def handle(self):
                line = self.rfile.readline()
                if not line:
                    return
                if outer.delay:
                    import time

                    time.sleep(outer.delay)
                try:
                    req = json.loads(line)
                except ValueError:
                    self.wfile.write(b"not json\n")
                    return
                outer.log.append(req)
                rep = outer.dispatch(req)
                rep["id"] = req.get("id")
                self.wfile.write(json.dumps(rep).encode() + b"\n")

        self.server = socketserver.ThreadingUnixStreamServer(str(self.path), H)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def dispatch(self, req):
        op = req.get("op")
        if op == "ping":
            return {"ok": True, "result": {"version": "fake", "game_version": "1.37.5", "frame": 42}}
        if op == "info":
            return {"ok": True, "result": {"date": "1444.11.11", "player": "CAS", "speed": self.speed, "paused": self.paused, "ingame": True}}
        if op == "console":
            return {"ok": True, "result": {"output": "ran: " + req["line"]}}
        if op == "pause":
            self.paused = bool(req["on"])
            return {"ok": True, "result": {"paused": self.paused}}
        if op == "speed":
            self.speed = req["value"]
            return {"ok": True, "result": {"speed": self.speed}}
        if op == "save":
            p = (self.save_dir or self.path.parent) / (req["name"] + ".eu4")
            return {"ok": True, "result": {"path": str(p)}}
        if op == "call":
            return {"ok": True, "result": {"called": req["name"], "args": req.get("args")}}
        return {"ok": False, "error": f"unknown op '{op}'"}

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        try:
            os.unlink(self.path)
        except OSError:
            pass
