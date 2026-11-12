"""Watch page: the live game picture (30 fps HLS video from tools/stream.sh; noVNC view-only as fallback) next to the play log (journal.jsonl) and the plan.

Run: python -m eu4mcp.watch [--host 127.0.0.1] [--port 6081] [--vnc-port 6080] (defaults from EU4_BIND, EU4_WATCH_PORT, EU4_VNC_PORT).
Read-only, no login: bind it to a LAN address only on a network you trust.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import digest, journal
from .hookclient import HookClient, HookError
from .live import Live
from .notes import Notes

PAGE = Path(__file__).with_name("watch.html")
# hls.js (Apache-2.0, https://github.com/video-dev/hls.js) is NOT bundled. The page loads the pinned release from a CDN, or from a
# local copy at EU4_HLS_JS (default eu4mcp/vendor/hls.min.js) when you put one there (offline use).
HLS_JS = Path(os.environ.get("EU4_HLS_JS") or Path(__file__).with_name("vendor") / "hls.min.js")
HLS_CDN = "https://cdn.jsdelivr.net/npm/hls.js@1.5.17/dist/hls.min.js"
STREAM_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / "eu4-stream"  # written by tools/stream.sh
_lock = threading.Lock()
_state: dict = {"t": 0.0, "data": {}}
VNC_PORT = int(os.environ.get("EU4_VNC_PORT", "6080"))


def _country_name(tag: str) -> str:
    try:
        return digest.gamedata().country_name(tag)
    except Exception:
        return tag


def state() -> dict:
    """Hook info + the player's live numbers + plan notes, cached for 2 s (every viewer polls)."""
    with _lock:
        if time.time() - _state["t"] < 2:
            return _state["data"]
        d: dict = {"hook": False, "vnc_port": VNC_PORT}
        try:
            h = HookClient(timeout=3)
            info = h.info()
            d.update(hook=True, **info)
            if info.get("ingame"):
                d["name"] = _country_name(info["player"])
                d["country"] = Live(h).country(info["player"])
                from . import revanchism
                u = revanchism.active(date=info.get("date"))
                if u:
                    d["buff"] = revanchism.label(u)
        except (HookError, OSError, KeyError):
            pass
        try:
            n = Notes()
            have = {s for s, _ in n.list()}
            d["notes"] = {s: n.read(s)[-3000:] for s in ("plan", "standing_rules", "commitments") if s in have}
        except Exception:
            d["notes"] = {}
        _state.update(t=time.time(), data=d)
        return d


class Handler(BaseHTTPRequestHandler):
    server_version = "eu4watch"

    def log_message(self, *a) -> None:  # quiet
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        u = urlparse(self.path)
        try:
            if u.path in ("/", "/index.html"):
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif u.path == "/hls.min.js":
                if HLS_JS.is_file():
                    self._send(200, HLS_JS.read_bytes(), "text/javascript")
                else:
                    self.send_response(302)
                    self.send_header("Location", HLS_CDN)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
            elif u.path.startswith("/hls/"):
                name = u.path[5:]
                f = STREAM_DIR / name
                if not re.fullmatch(r"[A-Za-z0-9_]+\.(m3u8|ts)", name) or not f.is_file():
                    self._send(404, b"no stream", "text/plain")
                else:
                    self._send(200, f.read_bytes(), "application/vnd.apple.mpegurl" if name.endswith("m3u8") else "video/mp2t")
            elif u.path == "/api/feed":
                q = parse_qs(u.query)
                after = max(0, int(q.get("after", ["0"])[0]))
                entries, n = journal.read(after)
                self._send(200, json.dumps({"entries": entries, "next": n}).encode(), "application/json")
            elif u.path == "/api/state":
                self._send(200, json.dumps(state()).encode(), "application/json")
            else:
                self._send(404, b"not found", "text/plain")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:  # a viewer must never take the server down
            try:
                self._send(500, str(e).encode(), "text/plain")
            except OSError:
                pass


def main() -> None:
    global VNC_PORT
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("EU4_BIND", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("EU4_WATCH_PORT", "6081")))
    ap.add_argument("--vnc-port", type=int, default=VNC_PORT)
    a = ap.parse_args()
    VNC_PORT = a.vnc_port
    ThreadingHTTPServer((a.host, a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
