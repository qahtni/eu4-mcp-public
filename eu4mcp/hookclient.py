"""Client for the in-game hook's unix socket protocol (see CLAUDE.md and hook/README.md).

One connection per request: connect, send ``{"id": n, "op": "...", ...}\\n``, read one JSON line
``{"id": n, "ok": true, "result": ...}`` or ``{"id": n, "ok": false, "error": "..."}``.
"""
from __future__ import annotations

import itertools
import json
import socket
from pathlib import Path
from typing import Any

from . import paths


class HookError(RuntimeError):
    """The hook answered with ok=false (or a malformed reply)."""


class HookUnavailable(HookError):
    """The socket is missing or refuses connections: the game/hook is not running."""


class HookTimeout(HookError):
    pass


_ids = itertools.count(1)
MAX_REPLY = 8 * 1024 * 1024


class HookClient:
    def __init__(self, path: str | Path | None = None, timeout: float = 10.0):
        self.path = Path(path) if path else paths.hook_socket()
        self.timeout = timeout

    def available(self) -> bool:
        return self.path.exists()

    def request(self, op: str, timeout: float | None = None, extra: dict | None = None, **params: Any) -> Any:
        if not self.path.exists():
            raise HookUnavailable(f"game or hook not running (no socket at {self.path})")
        rid = next(_ids)
        payload = json.dumps({"id": rid, "op": op, **params, **(extra or {})}, separators=(",", ":")).encode("utf-8") + b"\n"
        t = self.timeout if timeout is None else timeout
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(t)
        try:
            try:
                s.connect(str(self.path))
            except (FileNotFoundError, ConnectionRefusedError) as e:
                raise HookUnavailable(f"game or hook not running ({self.path}: {e.strerror})") from None
            except socket.timeout:
                raise HookTimeout(f"hook did not accept the connection within {t}s") from None
            s.sendall(payload)
            buf = bytearray()
            while b"\n" not in buf:
                try:
                    chunk = s.recv(65536)
                except socket.timeout:
                    raise HookTimeout(f"hook op '{op}' timed out after {t}s (game paused in a dialog, or frame thread stuck?)") from None
                if not chunk:
                    break
                buf += chunk
                if len(buf) > MAX_REPLY:
                    raise HookError("hook reply too large")
        finally:
            s.close()
        line = bytes(buf).split(b"\n", 1)[0]
        if not line:
            raise HookError(f"hook closed the connection without answering op '{op}'")
        try:
            msg = json.loads(line)
        except ValueError:
            raise HookError(f"malformed hook reply: {line[:120]!r}") from None
        if not isinstance(msg, dict):
            raise HookError(f"malformed hook reply: {line[:120]!r}")
        if msg.get("ok") is not True:
            raise HookError(str(msg.get("error", "hook error (no message)")))
        return msg.get("result")

    # ---- initial ops (hook/README.md) --------------------------------------------------
    def ping(self) -> dict:
        return self.request("ping")

    def info(self) -> dict:
        return self.request("info")

    def console(self, line: str) -> str:
        r = self.request("console", line=line)
        return r.get("output", "") if isinstance(r, dict) else str(r)

    def pause(self, on: bool) -> Any:
        return self.request("pause", on=bool(on))

    def speed(self, value: int) -> Any:
        if not 1 <= int(value) <= 5:
            raise ValueError("speed must be 1-5")
        return self.request("speed", value=int(value))

    def save(self, timeout: float = 60.0) -> str:
        """Trigger the game's autosave and wait until the file is completely rewritten; returns its path."""
        import time

        from .save import save_dir

        p = save_dir() / "autosave.eu4"
        before = p.stat().st_mtime_ns if p.exists() else 0
        out = self.console("autosave")
        if "Autosave command" not in out:
            raise HookError(f"autosave refused: {out or 'no reply'}")
        end = time.monotonic() + timeout
        last = (-1, -1)
        while time.monotonic() < end:
            time.sleep(0.3)
            try:
                st = p.stat()
            except OSError:
                continue
            cur = (st.st_mtime_ns, st.st_size)
            if st.st_mtime_ns != before and cur == last and st.st_size > 1000:
                with open(p, "rb") as f:
                    f.seek(-200, 2)
                    if b"checksum" in f.read():
                        return str(p)
            last = cur
        raise HookTimeout(f"autosave did not finish within {timeout}s")

    def vm(self, *steps: str) -> list:
        """Run steps in one frame (language: hook/eu4hook.cpp). Values come back as ints, text steps as str/list."""
        r = self.request("vm", steps=list(steps))
        return [_dec(x) for x in r]

    def wait(self, until: str, timeout: float = 120.0, pause: bool = True, stop_on_pause: bool = True, watch: list[int] | None = None) -> dict:
        kw: dict[str, Any] = {"until": until, "timeout": timeout, "pause": pause, "stop_on_pause": stop_on_pause}
        if watch:
            kw["watch"] = [hex(a) for a in watch]
        return self.request("wait", timeout=timeout + 15, extra=kw)


def _dec(x: Any) -> Any:
    if isinstance(x, str) and x.startswith("0x"):
        return int(x, 16)
    if isinstance(x, list):
        return [_dec(y) for y in x]
    return x
