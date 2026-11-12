"""The MCP process itself: a small, permanent front that loads the tools from ``eu4mcp.server`` and can load them
again while a session is running, so new tools and fixes reach a live Claude session without restarting it.

How: ``tools/deploy.sh`` (tests, then a new ``<state dir>/reload`` stamp). On the next request the front sees the new
stamp, drops every ``eu4mcp.*`` module except itself, imports ``eu4mcp.server`` again (which registers its tools on
``mcp`` below) and, when the tool list is different, sends ``notifications/tools/list_changed`` so the client fetches
the new list. A failed import keeps the old tools and is reported once on the next tool result.
Do NOT put tool code here: this file is the one part that needs a session restart to change.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import traceback
from typing import Any

import anyio
from mcp.server.lowlevel.server import NotificationOptions
from mcp.server.mcpserver import MCPServer
from mcp.server.stdio import stdio_server
from mcp.server.subscriptions import ToolsListChanged
from mcp.types import TextContent

from . import __version__, paths

INSTRUCTIONS = (
    "Tools to play Europa Universalis IV. Read tools return short digests of the newest loaded save; call eu4_refresh "
    "after time has passed. Tags are 3-letter country codes; omit tag for the player. Use eu4_notes_brief first after a reset. "
    "The tool list can change during a session (new tools are announced in a tool result)."
)
KEEP = ("eu4mcp", "eu4mcp.front", "eu4mcp.paths")     # never re-imported


def _stamp() -> int:
    try:
        return (paths.state_dir() / "reload").stat().st_mtime_ns
    except OSError:
        return 0


class Front(MCPServer):
    seen = 0            # stamp of the code that is loaded
    notice = ""         # shown once at the end of the next tool result

    def _names(self) -> dict[str, str]:
        return {t.name: json.dumps(t.parameters, sort_keys=True) + (t.description or "") for t in self._tool_manager.list_tools()}

    def load(self, first: bool = False) -> bool:
        """(Re)import the tool code. Returns True when the tool list changed."""
        before = self._names()
        old_tools = dict(self._tool_manager._tools)
        old_mods = {k: v for k, v in sys.modules.items() if k.startswith("eu4mcp") and k not in KEEP}
        try:
            for k in old_mods:
                del sys.modules[k]
            self._tool_manager._tools.clear()
            importlib.invalidate_caches()
            importlib.import_module("eu4mcp.server")
        except BaseException:
            if first:
                raise
            for k in [k for k in sys.modules if k.startswith("eu4mcp") and k not in KEEP]:
                del sys.modules[k]
            sys.modules.update(old_mods)
            self._tool_manager._tools.clear()
            self._tool_manager._tools.update(old_tools)
            err = traceback.format_exc(limit=3).strip().splitlines()[-1]
            self.notice = f"[eu4-mcp: a tool update failed to load and was NOT applied ({err}); the old tools still work]"
            print(traceback.format_exc(), file=sys.stderr, flush=True)
            return False
        after = self._names()
        if first or after == before:
            return False
        new = sorted(set(after) - set(before))
        gone = sorted(set(before) - set(after))
        changed = sorted(k for k in after if k in before and after[k] != before[k])
        parts = [f"new tools: {', '.join(new)}" if new else "", f"removed: {', '.join(gone)}" if gone else "",
                 f"changed (read their description again): {', '.join(changed)}" if changed else ""]
        self.notice = "[eu4-mcp tools updated live: " + "; ".join(p for p in parts if p) + "]"
        return True

    def _check(self) -> bool:
        s = _stamp()
        if s == self.seen:
            return False
        self.seen = s
        return self.load()

    async def _handle_list_tools(self, ctx: Any, params: Any) -> Any:
        self._check()
        return await super()._handle_list_tools(ctx, params)

    async def _handle_call_tool(self, ctx: Any, params: Any) -> Any:
        if self._check():
            # protocol 2026-07-28+: the change goes out on the client's subscriptions/listen stream (the bus);
            # older clients get the plain notification
            for send in (lambda: self._subscriptions.publish(ToolsListChanged()), ctx.session.send_tool_list_changed):
                try:
                    await send()
                except Exception:
                    print(traceback.format_exc(), file=sys.stderr, flush=True)
        res = await super()._handle_call_tool(ctx, params)
        if self.notice and hasattr(res, "content"):
            res.content.append(TextContent(type="text", text=self.notice))
            self.notice = ""
        return res


mcp = Front("eu4", instructions=INSTRUCTIONS, version=__version__)


def main() -> None:
    mcp.seen = _stamp()
    mcp.load(first=True)
    if "EU4_MCP_STATE" not in os.environ:   # the real setup (tests set it): keep the revanchism service running
        from . import revanchism
        revanchism.ensure_service()

    async def run() -> None:
        low = mcp._lowlevel_server
        async with stdio_server() as (r, w):
            await low.run(r, w, low.create_initialization_options(NotificationOptions(tools_changed=True)))

    anyio.run(run)
