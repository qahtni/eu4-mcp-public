"""Play journal: one JSON line per tool call or comment, read by the watch site (eu4mcp/watch.py)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import paths

READ_TOOLS = {"eu4_status", "eu4_country", "eu4_diplomacy", "eu4_wars", "eu4_armies", "eu4_province", "eu4_provinces",
              "eu4_neighbours", "eu4_find", "eu4_changes", "eu4_refresh", "eu4_notes_brief", "eu4_notes_read", "eu4_events", "eu4_playlog", "eu4_alerts"}


# actions that only look at state: logged as reads so the narrator does not retell them
READ_ACTIONS = {"list", "diet_options", "advisors", "merchants", "colonists", "colonisable", "chance", "cbs", "wars", "peace_terms", "incoming", "help"}


def path() -> Path:
    return paths.notes_dir().parent / "journal.jsonl"


def write(kind: str, **fields: Any) -> None:
    """kind: say (comment), act (order), read (state query), note (notes changed). Never raises."""
    try:
        p = path()
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": round(time.time(), 1), "kind": kind, **fields}, ensure_ascii=False, separators=(",", ":")) + "\n")
    except OSError:
        pass


def tool_call(name: str, args: dict, out: str | None, err: str | None, date: str | None) -> None:
    if name == "eu4_say":
        return
    act = str(args.get("action", "")).lower()
    listing = (name == "eu4_army" and (act or "list") in ("list", "mercs")) or (name == "eu4_estates" and (act or "list") in READ_ACTIONS) \
        or (name == "eu4_economy" and act in READ_ACTIONS) or (name == "eu4_loans" and (act or "list") == "list") or (name == "eu4_diplo" and act in READ_ACTIONS)
    kind = "read" if name in READ_TOOLS or listing or args.get("dry") else \
        "note" if name.startswith("eu4_notes_") else "act"
    a = {k: v for k, v in args.items() if v not in ("", 0, None)}
    limit = 200 if kind == "read" else 1200
    text = err if err is not None else (out or "")
    write(kind, tool=name[4:], args=a, out=text[:limit] + ("…" if len(text) > limit else ""), ok=err is None, date=date)


def read(after: int = 0, limit: int = 400) -> tuple[list[dict], int]:
    """Entries from line index ``after`` on (at most the last ``limit``), and the new line count."""
    try:
        lines = path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return [], 0
    out = []
    for i, ln in enumerate(lines[after:], start=after):
        try:
            out.append({"i": i, **json.loads(ln)})
        except ValueError:
            continue
    return out[-limit:], len(lines)


def _key(date: str | None) -> tuple:
    try:
        return tuple(int(x) for x in (date or "").split("."))
    except ValueError:
        return ()


def digest(start: str = "", end: str = "", kinds: str = "say,act", page: int = 1, per_page: int = 150) -> str:
    """Compact text of the play log between two game dates (inclusive), one line per entry, for a reviewer."""
    want = {k.strip() for k in kinds.split(",") if k.strip()}
    lo, hi = _key(start), _key(end)
    entries, _ = read(0, limit=10**9)
    rows = []
    for e in entries:
        k = _key(e.get("date"))
        if e.get("kind") not in want or (lo and k and k < lo) or (hi and k and k > hi):
            continue
        if e["kind"] == "say":
            body = f"[{e.get('who', 'strategist')}] {e.get('text', '')}"
        else:
            args = " ".join(f"{a}={v}" for a, v in (e.get("args") or {}).items())
            out = " ".join(str(e.get("out", "")).split())
            body = f"{e.get('tool')} {args} -> {'' if e.get('ok', True) else 'FAILED: '}{out[:160]}"
        rows.append(f"{e.get('date') or '?'} {body}")
    pages = max(1, -(-len(rows) // per_page))
    page = min(max(1, page), pages)
    part = rows[(page - 1) * per_page: page * per_page]
    return f"play log: {len(rows)} entries, page {page}/{pages}\n" + "\n".join(part)
