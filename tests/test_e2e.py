"""End to end: start bin/eu4-mcp as a subprocess and talk MCP over stdio."""
import asyncio
import json
import os
import subprocess
import sys

import pytest

from mcp import Client
from mcp.client.stdio import StdioServerParameters

from .conftest import ROOT, TUT, needs_tutorial
from .fakehook import FakeHook

pytestmark = needs_tutorial
LAUNCHER = ROOT / "bin" / "eu4-mcp"


def _params(tmp_path, **extra):
    env = {**os.environ, "EU4_USER_DIR": str(TUT), "EU4_MCP_STATE": str(tmp_path / "state"), "EU4_MCP_NOTES": str(tmp_path / "notes"), "EU4_MCP_CACHE": str(tmp_path / "cache"), **extra}
    return StdioServerParameters(command=str(LAUNCHER), env=env)


def _text(r):
    return "".join(c.text for c in r.content)


def test_tools_status_notes(tmp_path):
    async def go():
        async with Client(_params(tmp_path)) as c:
            tools = (await c.list_tools()).tools
            names = {t.name for t in tools}
            assert {"eu4_status", "eu4_country", "eu4_diplomacy", "eu4_wars", "eu4_armies", "eu4_province", "eu4_provinces", "eu4_neighbours", "eu4_find", "eu4_changes", "eu4_refresh", "eu4_console", "eu4_time", "eu4_notes_brief", "eu4_notes_read", "eu4_notes_write", "eu4_notes_append"} <= names
            assert {"eu4_diplo", "eu4_army", "eu4_economy", "eu4_advance", "eu4_events", "eu4_answer"} <= names
            size = len(json.dumps([t.model_dump(mode="json", exclude_none=True) for t in tools], separators=(",", ":")))
            print(f"\n[tool list] {len(tools)} tools, {size} chars serialized")
            assert size < 12500  # about 3,450 tokens

            r = await c.call_tool("eu4_status", {})
            t = _text(r)
            assert not r.is_error and t.splitlines()[0].startswith("[eu4_tutorial_chapter") and "treasury" in t
            assert len(t) < 6000

            r = await c.call_tool("eu4_find", {"text": "valladolid"})
            assert "province 215" in _text(r) or "province 215" in _text(r).replace("  ", " ")
            r = await c.call_tool("eu4_province", {"id": 215})
            assert "(215)" in _text(r) and "owner Castile(CAS)" in _text(r)

            r = await c.call_tool("eu4_notes_write", {"section": "plan", "text": "Conquer Granada"})
            assert not r.is_error
            r = await c.call_tool("eu4_notes_append", {"section": "journal", "text": "started", "date": "1444.11.11"})
            assert not r.is_error
            r = await c.call_tool("eu4_notes_append", {"section": "journal", "text": "no date"})
            assert r.is_error
            r = await c.call_tool("eu4_notes_write", {"section": "intel/FRA", "text": "strong"})
            b = _text(await c.call_tool("eu4_notes_brief", {}))
            assert "Conquer Granada" in b and "- 1444.11.11: started" in b and "FRA" in b
            lst = _text(await c.call_tool("eu4_notes_read", {}))
            assert "plan" in lst and "intel/FRA" in lst
            r = await c.call_tool("eu4_notes_write", {"section": "intel/FRA", "text": ""})
            assert "deleted" in _text(r)

            for name in ("eu4_diplo",):
                r = await c.call_tool(name, {"action": "wars"})
                assert r.is_error  # no game behind the test server

            # no hook socket: clear error, refresh falls back to disk
            r = await c.call_tool("eu4_console", {"line": "help"})
            assert r.is_error and "not running" in _text(r)
            r = await c.call_tool("eu4_refresh", {})
            assert not r.is_error and "hook not running" in _text(r)
            r = await c.call_tool("eu4_changes", {})
            assert not r.is_error

    asyncio.run(go())
    assert (tmp_path / "notes" / "default" / "plan.md").read_text().startswith("Conquer Granada")


def test_with_fake_hook(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    chapter = sorted(TUT.glob("*chapter9.eu4"))[0]

    async def go():
        with FakeHook(state / "hook.sock", save_dir=TUT) as h:
            # the fake hook "saves" to <TUT>/<name>.eu4 which does not exist: refresh must fall back, not crash
            async with Client(_params(tmp_path)) as c:
                r = await c.call_tool("eu4_time", {"pause": True, "speed": 4})
                assert not r.is_error and '"paused":true' in _text(r) and '"speed":3' in _text(r)  # 4 is cut to the peace limit of the tutorial save
                r = await c.call_tool("eu4_console", {"line": "add_treasury 5"})
                assert _text(r) == "ran: add_treasury 5"
                r = await c.call_tool("eu4_refresh", {})
                assert not r.is_error and "autosave refused" in _text(r)
            assert [x["op"] for x in h.log if x["op"] != "info"][:2] == ["pause", "speed"]

    asyncio.run(go())


def test_stdout_is_clean_mcp_only(tmp_path):
    """Launching and closing stdin immediately must not print anything but protocol frames."""
    env = {**os.environ, "EU4_USER_DIR": str(TUT), "EU4_MCP_STATE": str(tmp_path), "EU4_MCP_NOTES": str(tmp_path)}
    msg = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}})
    p = subprocess.run([str(LAUNCHER)], input=msg + "\n", capture_output=True, text=True, env=env, timeout=30)
    for line in p.stdout.splitlines():
        assert line.startswith("{"), f"non-JSON on stdout: {line[:80]!r}"
        json.loads(line)


def test_tool_added_to_running_server(tmp_path):
    """tools/deploy.sh in small: a new reload stamp makes the running server load the tool code again."""
    async def go():
        st = tmp_path / "state"
        st.mkdir()
        async with Client(_params(tmp_path)) as c:
            assert "eu4_test_live" not in {t.name for t in (await c.list_tools()).tools}
            (st / "test_tool").write_text("")
            (st / "reload").write_text("1")
            r = await c.call_tool("eu4_notes_brief", {})
            assert "tools updated live: new tools: eu4_test_live" in _text(r)
            assert "eu4_test_live" in {t.name for t in (await c.list_tools()).tools}
            assert "live tool works" in _text(await c.call_tool("eu4_test_live", {}))
            r = await c.call_tool("eu4_notes_brief", {})
            assert "updated live" not in _text(r)      # announced once

    asyncio.run(go())
