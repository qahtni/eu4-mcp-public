"""OPTIONAL revanchism buff (a cheat; OFF by default). Set EU4_MCP_REVANCHISM=1 to enable it, in the environment of the MCP server
and of the service. Disabled, nothing here starts or writes to the game. When the player's country is losing, its revanchism
is held at 100% for 5 game years (no decay), then the game's normal decay applies again.

  python -m eu4mcp.revanchism run             the service (unit eu4-revanchism; the MCP server starts it when missing)
  python -m eu4mcp.revanchism grant [REASON]  a manual grant; the service picks it up
  python -m eu4mcp.revanchism status

Automatic triggers (thresholds below), checked by the service, never by a model; the player has no tool for it:
  - peace:    the game itself raised revanchism (it does so only when provinces are lost in a peace deal);
  - occupied: >= OCC_SHARE of the country's provinces enemy-held in every save for OCC_MONTHS months;
  - bankrupt: a new last_bankrupt date in the save.
Extra: while a grant runs, stability is kept at 0 or higher: whenever it is below 0 it is set
back to 0 (CCountry::SetStability); a stability above 0 is left alone.
There is no cooldown: a trigger after a grant has ended starts a new one. Each grant is written to the play log.
Revanchism = int32 x1000 at CCountry+0xeb8 (CRevanchismTrigger::Evaluate @0x18af29c; save key recovery_motivation).
State: <state dir>/revanchism.json (only the service writes it); a grant request is the file revanchism.request.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from . import journal, paths

OFF, FULL = 0xEB8, 100000
YEARS = 5
OCC_SHARE, OCC_MONTHS = 0.2, 6
PEACE_JUMP = 500          # x1000: a rise of 0.5 points or more that we did not write = the game granted revanchism
SET_STAB = "@_ZN8CCountry12SetStabilityE11CFixedPoint"   # @0x101766a (rdi country, rsi CFixedPoint*): sets +0xbb8, recalculates modifiers
C_STAB = 0xBB8
UNIT = "eu4-revanchism"
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def enabled() -> bool:
    return os.environ.get("EU4_MCP_REVANCHISM", "").strip().lower() in ("1", "true", "yes", "on")


def _file(name: str = "revanchism.json") -> Path:
    return paths.state_dir() / name


def ymd(s: str) -> tuple[int, int, int]:
    y, m, d = (int(x) for x in str(s).split("."))
    return y, m, d


def _fmt(t: tuple) -> str:
    return ".".join(str(x) for x in t)


def _months(a: tuple, b: tuple) -> int:
    return (b[0] - a[0]) * 12 + b[1] - a[1] - (1 if b[2] < a[2] else 0)


def load() -> dict:
    try:
        return json.loads(_file().read_text())
    except (OSError, ValueError):
        return {}


def _store(st: dict) -> None:
    p = _file()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1))
    os.replace(tmp, p)


def active(st: dict | None = None, date: str | None = None) -> str | None:
    """The end date 'Y.M.D' while a grant is running (``date`` = game date now, when known), else None."""
    st = load() if st is None else st
    u = st.get("until")
    if not u or (date and ymd(date) >= ymd(u)):
        return None
    return u


def label(until: str) -> str:
    y, m, _ = ymd(until)
    return f"Revanchism 100% until {MONTHS[m - 1]} {y}"


def status_line(date: str | None = None) -> str:
    u = active(date=date)
    return f"BUFF (optional cheat, enabled by EU4_MCP_REVANCHISM): {label(u)}: +50% tax, +50% manpower recovery, +25% fort defence, -5 unrest, -1 interest." if u else ""


def decide(st: dict, date: tuple, value: int | None, save: dict | None) -> str | None:
    """Pure trigger logic; mutates the tracking fields of ``st``; returns the reason for a new automatic grant.
    ``value`` = live revanchism x1000; ``save`` = {"date", "owned", "occupied", "last_bankrupt"} of a NEW save or None."""
    reason = None
    if value is not None:
        prev = st.get("last_value")
        if prev is not None and value - prev >= PEACE_JUMP:
            reason = "provinces lost in a peace deal"
        st["last_value"] = value
    if save:
        share = save["occupied"] / save["owned"] if save["owned"] else 0
        if share >= OCC_SHARE:
            st.setdefault("occ_since", save["date"])
            if _months(ymd(st["occ_since"]), ymd(save["date"])) >= OCC_MONTHS:
                reason = reason or f"{save['occupied']} of {save['owned']} provinces enemy-held for {OCC_MONTHS} months"
        else:
            st.pop("occ_since", None)
        lb = save.get("last_bankrupt")
        if lb and "bankrupt_seen" in st and lb != st["bankrupt_seen"]:
            reason = reason or "bankruptcy"
        st["bankrupt_seen"] = lb or ""
    if not reason:
        return None
    if st.get("until") and date < ymd(st["until"]):
        return None                       # already running
    return reason


def grant(st: dict, date: tuple, reason: str, tag: str) -> None:
    until = (date[0] + YEARS, date[1], date[2])
    st.update(until=_fmt(until), tag=tag, ended_logged=False)
    st.pop("cooldown_until", None)
    st.setdefault("history", []).append({"date": _fmt(date), "until": _fmt(until), "reason": reason})
    st.pop("occ_since", None)
    journal.write("story", text=f"Revanchism buff granted to {tag} ({reason}): held at 100% until {MONTHS[until[1] - 1]} {until[0]}. "
                                "This is an opt-in cheat.", date=_fmt(date))


def lift_stability(live, c: int) -> float | None:
    """Stability below 0 -> 0 through the game's own setter. Returns the old value when it was changed."""
    v = live.h.vm(f"rs4 {c}+{C_STAB:#x}")[0]
    v &= 0xFFFFFFFF
    v = v - (1 << 32) if v & 0x80000000 else v
    if v >= 0:
        return None
    live.h.vm("buf 8", "wr4 r0 0", f"call {SET_STAB} {c} r0")
    return v / 1000


def _save_facts(tag: str, seen: dict) -> dict | None:
    """Facts from the autosave when it changed since the last look (read-only; never triggers a save)."""
    from .save import open_save, save_dir

    p = save_dir() / "autosave.eu4"
    try:
        m = p.stat().st_mtime_ns
    except OSError:
        return None
    if m == seen.get("m") or time.time() - m / 1e9 < 3:      # unchanged, or possibly still being written
        return None
    seen["m"] = m
    s = open_save(p)
    tab, owned = s.province_table(), list(s.owned_by(tag))
    occ = sum(1 for pid in owned if (tab.get(pid) or {}).get("controller") not in (None, tag, "REB"))
    d = s.date
    return {"date": f"{d.y}.{d.m}.{d.d}", "owned": len(owned), "occupied": occ, "last_bankrupt": str((s.country(tag) or {}).get("last_bankrupt") or "")}


def run() -> None:
    from .hookclient import HookClient
    from .live import Live

    seen: dict = {}
    while True:
        try:
            live = Live(HookClient(timeout=5))
            info = live.info()
            tag, date = info["player"], ymd(info["date"])
            st = load()
            c = live.country_ptr(tag)
            v = live.h.vm(f"rs4 {c}+{OFF:#x}")[0]
            req = _file("revanchism.request")
            reason = None
            if req.exists():
                reason = "a manual grant" + (f": {t}" if (t := req.read_text().strip()) else "")
                req.unlink()
                st["last_value"] = v
            else:
                try:
                    sf = _save_facts(tag, seen)
                except Exception as e:
                    print(f"save not read: {e}", flush=True)
                    sf = None
                held = bool(st.get("until")) and date < ymd(st["until"])
                reason = decide(st, date, None if held else v, sf)
            if reason:
                grant(st, date, reason, tag)
                print(f"{_fmt(date)} {tag} GRANT ({reason}) until {st['until']}", flush=True)
            if st.get("until") and date < ymd(st["until"]):
                if 0 <= v < FULL:
                    live.h.vm(f"wr4 {c}+{OFF:#x} {FULL}")
                st["last_value"] = FULL
                old = lift_stability(live, c)
                if old is not None:
                    journal.write("story", text=f"Stability of {tag} fell to {old:g} and was reset to 0 (revanchism buff running).", date=_fmt(date))
                    print(f"{_fmt(date)} {tag} stability {old:g} -> 0", flush=True)
            elif st.get("until") and not st.get("ended_logged"):
                st["ended_logged"] = True
                journal.write("story", text=f"The revanchism buff of {tag} has ended; it now decays normally.", date=_fmt(date))
                print(f"{_fmt(date)} {tag} buff ended", flush=True)
            if st != load():
                _store(st)
        except Exception as e:  # game closed, loading, main menu
            print(f"waiting: {e}", flush=True)
            time.sleep(10)
        time.sleep(3)


def ensure_service() -> None:
    """Start the service unit when it is not running (called at MCP server start). Never raises. No-op unless enabled()."""
    if not enabled():
        return
    try:
        if subprocess.run(["systemctl", "--user", "is-active", "-q", UNIT], timeout=5).returncode == 0:
            return
        subprocess.run(["systemctl", "--user", "reset-failed", UNIT], capture_output=True, timeout=5)
        subprocess.run(["systemd-run", "--user", "--collect", f"--unit={UNIT}", "-p", "Restart=on-failure", "-p", "RestartSec=10",
                        "-p", f"WorkingDirectory={Path(__file__).resolve().parent.parent}", sys.executable, "-m", "eu4mcp.revanchism", "run"],
                       capture_output=True, timeout=10)
    except Exception:
        pass


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd in ("run", "grant") and not enabled():
        sys.exit("the revanchism buff is a cheat and is disabled; set EU4_MCP_REVANCHISM=1 to enable it")
    if cmd == "run":
        run()
    elif cmd == "grant":
        _file().parent.mkdir(parents=True, exist_ok=True)
        _file("revanchism.request").write_text(" ".join(sys.argv[2:]))
        ensure_service()
        print("grant requested; the service applies it within seconds while a campaign is running")
    else:
        st = load()
        print(json.dumps({k: st.get(k) for k in ("tag", "until", "occ_since", "last_value", "history")}, indent=1))


if __name__ == "__main__":
    main()
