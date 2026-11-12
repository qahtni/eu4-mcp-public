import socket
import threading

import pytest

from eu4mcp.hookclient import HookClient, HookError, HookTimeout, HookUnavailable
from .fakehook import FakeHook


def test_missing_socket_is_clear(tmp_path):
    c = HookClient(tmp_path / "nope.sock")
    assert not c.available()
    with pytest.raises(HookUnavailable, match="game or hook not running"):
        c.ping()


def test_stale_socket_file_refused(tmp_path):
    p = tmp_path / "stale.sock"
    s = socket.socket(socket.AF_UNIX)
    s.bind(str(p))
    s.close()  # file remains, nobody listens
    with pytest.raises(HookUnavailable, match="not running"):
        HookClient(p).ping()


def test_ops_roundtrip(tmp_path):
    with FakeHook(tmp_path / "h.sock", save_dir=tmp_path) as h:
        c = HookClient(h.path)
        assert c.ping()["frame"] == 42
        assert c.info()["player"] == "CAS"
        assert c.console("add_treasury 100") == "ran: add_treasury 100"
        assert c.pause(True)["paused"] is True and h.paused
        assert c.speed(5)["speed"] == 5
        assert [r["op"] for r in h.log] == ["ping", "info", "console", "pause", "speed"]
        assert h.log[0]["id"] != h.log[1]["id"]
        with pytest.raises(ValueError):
            c.speed(9)


def test_error_reply(tmp_path):
    with FakeHook(tmp_path / "h.sock") as h:
        with pytest.raises(HookError, match="unknown op 'bogus'"):
            HookClient(h.path).request("bogus")


def test_timeout(tmp_path):
    with FakeHook(tmp_path / "h.sock", delay=1.0) as h:
        with pytest.raises(HookTimeout):
            HookClient(h.path, timeout=0.2).ping()


def test_malformed_and_closed_replies(tmp_path):
    p = tmp_path / "bad.sock"
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(p))
    srv.listen(2)
    replies = [b"not json\n", b""]

    def serve():
        for r in replies:
            conn, _ = srv.accept()
            conn.recv(4096)
            if r:
                conn.sendall(r)
            conn.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    with pytest.raises(HookError, match="malformed"):
        HookClient(p, timeout=2).ping()
    with pytest.raises(HookError, match="without answering"):
        HookClient(p, timeout=2).ping()
    t.join(2)
    srv.close()
