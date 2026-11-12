"""Narrator: turns new play-log orders into plain language for the watch page (journal kind "story").

Every batch is a fresh, tool-less `claude -p` run on Haiku at medium effort with narrator_prompt.txt as its system prompt,
so its context starts empty each time and stays a few thousand tokens (far below 100K).
Run: python -m eu4mcp.narrator [--interval 8] [--quiet 12] [--max-batch 40]
"""
from __future__ import annotations

import argparse
import re
import subprocess
import tempfile
import time
from pathlib import Path

from . import digest, gamedata, journal

PROMPT = Path(__file__).with_name("narrator_prompt.txt")
# bookkeeping calls viewers do not care about: never narrated (they still count as consumed)
NOISE_TOOLS = {"learn", "issue", "notes_write", "notes_append", "refresh", "playlog", "events", "find"}


def _line(e: dict) -> str:
    if e["kind"] == "say":
        return f"{e.get('date') or '?'} [{e.get('who', 'strategist')}] {e.get('text', '')}"
    args = " ".join(f"{a}={v}" for a, v in (e.get("args") or {}).items())
    out = " ".join(str(e.get("out", "")).split())
    return f"{e.get('date') or '?'} {e.get('tool')} {args} -> {'' if e.get('ok', True) else 'REFUSED: '}{out[:500]}"


_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[_.][A-Za-z0-9]+)+|\b[A-Z][A-Z0-9]{2}\b|(?:province|to|loc)=(\d+)")


def glossary(lines: list[str], limit: int = 40) -> list[str]:
    """Names from the game's own text files (localisation/*.yml, events/*.txt) for the keys, tags and ids in the lines."""
    try:
        gd = digest.gamedata()
    except Exception:
        return []
    out: dict[str, str] = {}
    for m in _TOKEN.finditer("\n".join(lines)):
        tok = m.group(0)
        if tok in out or len(out) >= limit:
            continue
        name = None
        if m.group(1):
            name = gd.province_name(int(m.group(1)))
            tok = f"province {m.group(1)}"
        elif len(tok) == 3 and tok.isupper():
            name = gd.country_name(tok)
        elif "." in tok and tok.rsplit(".", 1)[1].isdigit():
            d = gamedata.event_def(tok)
            if d:
                title = gd.loc_get(d.get("title") or "")
                desc = gamedata.clean(gd.loc_get(d.get("desc") or "") or "")[:260]
                opts = "; ".join(f"{i + 1}) {gd.loc_get(o['name'], o['name'])}" for i, o in enumerate(d["options"][:6]))
                name = f'event "{title}": {desc} Options: {opts}' if title else None
        else:
            for k in (tok, tok + "_title", "building_" + tok, tok + "_name", tok.upper()):
                name = gd.loc_get(k)
                if name:
                    break
        if name and name != tok:
            out[tok] = gamedata.clean(name)[:420]
    return [f"{k} = {v}" for k, v in out.items()]


def narrate(prev: list[str], lines: list[str], timeout: int = 120) -> str:
    text = ("Earlier narration:\n" + "\n".join(prev[-3:]) + "\n\n" if prev else "") + "New log lines:\n" + "\n".join(lines)
    g = glossary(lines)
    if g:
        text += "\n\nGlossary (names from the game's text files):\n" + "\n".join(g)
    with tempfile.TemporaryDirectory(prefix="eu4-narrator-") as cwd:  # empty folder: no project files or settings
        r = subprocess.run(
            ["claude", "-p", "--model", "haiku", "--effort", "medium", "--system-prompt-file", str(PROMPT), "--tools", "",
             "--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence"],
            input=text, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[:300])
    return " ".join(r.stdout.split())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=8, help="seconds between checks of the play log")
    ap.add_argument("--quiet", type=float, default=20, help="narrate once the log has been quiet this long ...")
    ap.add_argument("--max-batch", type=int, default=30, help="... or this many orders are waiting ...")
    ap.add_argument("--max-wait", type=float, default=120, help="... or the oldest waiting order is this old (a busy player is never quiet)")
    ap.add_argument("--min-gap", type=float, default=90, help="at most one narration per this many seconds")
    a = ap.parse_args()
    while True:
        try:
            entries, _ = journal.read(0, limit=10**9)
            done = max((e.get("upto", -1) for e in entries if e["kind"] == "story"), default=-1)
            new = [e for e in entries if e["i"] > done and e["kind"] in ("act", "say") and e.get("tool") not in NOISE_TOOLS]
            acts = [e for e in new if e["kind"] == "act"]
            last_story = max((e["t"] for e in entries if e["kind"] == "story"), default=0)
            if acts and time.time() - last_story >= a.min_gap and (len(new) >= a.max_batch or time.time() - new[-1]["t"] >= a.quiet or time.time() - new[0]["t"] >= a.max_wait):
                batch = new[:a.max_batch]
                prev = [e["text"] for e in entries if e["kind"] == "story"]
                text = narrate(prev, [_line(e) for e in batch])
                if " ".join(text.lower().split()) in {" ".join(x.lower().split()) for x in prev[-6:]}:
                    text = "SKIP"  # never repeat an earlier narration word for word
                journal.write("story", text="" if text == "SKIP" else text[:1200], upto=batch[-1]["i"], date=batch[-1].get("date"))
        except Exception as e:  # keep running; the page just shows no new narration
            print(f"narrator: {e}", flush=True)
            time.sleep(30)
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
