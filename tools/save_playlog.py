#!/usr/bin/env python3
"""Archive the play log per session into <data dir>/archive/sessions/ (data dir default ~/.local/share/eu4-mcp).

A session = a run of journal entries with no gap longer than GAP seconds and no backwards jump of the game date by more than 30 days, and no restart of the eu4 game process
(game restarted / save reloaded). Each session is its own file session-<N>-<wall start>-<first game date>.jsonl, rewritten
only when its content changed. Notes, issues and lessons are copied too (only when changed). Never deletes anything.
"""
import json, os, shutil, time
from pathlib import Path

GAP = 1800
SRC = Path(os.environ.get("EU4_MCP_NOTES") or "~/.local/share/eu4-mcp/notes").expanduser().parent   # same rule as eu4mcp.paths.notes_dir().parent
DST = SRC / "archive"
S = DST / "sessions"
S.mkdir(parents=True, exist_ok=True)


def dkey(d):
    try:
        return tuple(int(x) for x in str(d).split("."))
    except ValueError:
        return None


def game_starts():
    """Epoch start times of every eu4 process seen so far (remembered in archive/game_starts.txt): a game restart ends a session."""
    f = DST / "game_starts.txt"
    known = {int(x) for x in f.read_text().split()} if f.exists() else set()
    try:
        for pid in os.listdir("/proc"):
            if pid.isdigit() and Path(f"/proc/{pid}/comm").read_text().strip() == "eu4":
                up = float(Path("/proc/uptime").read_text().split()[0])
                tick = os.sysconf("SC_CLK_TCK")
                st = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19])
                known.add(int(time.time() - up + st / tick))
    except (OSError, ValueError, IndexError):
        pass
    f.write_text("\n".join(str(x) for x in sorted(known)) + "\n")
    return sorted(known)


def sessions(lines, starts=()):
    cur, last_t, last_d = [], None, None
    for ln in lines:
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        t, d = e.get("t"), dkey(e.get("date"))
        if e.get("kind") == "story":  # narrator entries carry stale dates: ignore them for the date check
            d = None
        back = bool(d and last_d and d[0] * 372 + d[1] * 31 + d[2] < last_d[0] * 372 + last_d[1] * 31 + last_d[2] - 30)
        crossed = last_t is not None and t is not None and any(last_t < x <= t + 90 for x in starts)  # game (re)started in between
        if cur and ((last_t is not None and t is not None and t - last_t > GAP) or back or crossed):
            yield cur
            cur = []
        cur.append(ln)
        last_t = t if t is not None else last_t
        if d:
            last_d = d
    if cur:
        yield cur


def write_if_changed(p, data):
    if p.exists() and p.read_bytes() == data:
        return
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, p)


def archive_save():
    """Gzip the newest snapshot of the game save (plain text; summaries read it) every 5 game years, and always keep archive/saves/latest.eu4.gz."""
    snaps = sorted((Path(os.environ.get("EU4_MCP_STATE") or Path.home() / ".config/eu4-mcp") / "snapshots").glob("snap_*.eu4"))
    if not snaps:
        return
    src = snaps[-1]
    with open(src, "rb") as f:
        head = f.read(400).decode("latin-1")
    d = next((ln.split("=", 1)[1] for ln in head.splitlines() if ln.startswith("date=")), None)
    y = dkey(d)[0] if d and dkey(d) else None
    if y is None:
        return
    out = DST / "saves"
    out.mkdir(exist_ok=True)
    import gzip
    last = out / "latest.eu4.gz"
    if not last.exists() or last.stat().st_mtime < src.stat().st_mtime:
        tmp = out / "latest.eu4.gz.tmp"
        with open(src, "rb") as fi, gzip.open(tmp, "wb", 6) as fo:
            shutil.copyfileobj(fi, fo)
        os.replace(tmp, last)
        done = [int(p.name.split("-")[1]) for p in out.glob("save-*-*.eu4.gz")]
        if not done or y >= max(done) + 5:
            shutil.copy2(last, out / f"save-{y}-{d.replace('.', '-')}.eu4.gz")


def main():
    archive_save()
    j = SRC / "journal.jsonl"
    if j.exists():
        lines = j.read_text(encoding="utf-8").splitlines()
        for n, ses in enumerate(sessions(lines, game_starts()), 1):
            first = json.loads(ses[0])
            when = time.strftime("%Y%m%d-%H%M%S", time.localtime(first.get("t", 0)))
            gd = str(first.get("date") or "nodate").replace(".", "-")
            write_if_changed(S / f"session-{n:03d}-{when}-game{gd}.jsonl", ("\n".join(ses) + "\n").encode())
        write_if_changed(DST / "journal.full.jsonl", j.read_bytes())
    for src in [SRC / "issues.txt", SRC / "lessons.txt", SRC / "mechanics_todo.txt", Path(__file__).resolve().parent.parent / "docs" / "lessons.md"]:
        if src.exists():
            write_if_changed(DST / src.name, src.read_bytes())
    nd = SRC / "notes"
    if nd.exists():
        shutil.copytree(nd, DST / "notes", dirs_exist_ok=True)


main()
