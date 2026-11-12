"""MCP stdio server for Europa Universalis IV (tools prefixed ``eu4_``).

Nothing here may write to stdout except MCP frames (the launcher is also used over ssh).  Read tools work from a
pinned "current" save (the newest on disk when first used, then whatever ``eu4_refresh`` last loaded) and
start their output with ``[save file | date]``.
"""
from __future__ import annotations

import functools
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

from mcp.server.mcpserver.exceptions import ToolError

from . import __version__, alerts, digest, journal, paths
from .hookclient import HookClient, HookError, HookUnavailable
from .live import Live, add_days
from .notes import Notes, NotesError
from .save import Save, SaveError, latest_save, open_save

from .front import mcp  # the permanent MCP process; this module is (re)loaded by it and registers its tools there


class State:
    def __init__(self) -> None:
        self.current: Path | None = None
        self.previous: Path | None = None
        self.loaded_snapshots = False
        self.hook: HookClient | None = None
        self.notes: Notes | None = None

    # ---- snapshots (paths persisted so eu4_changes survives a server restart) -----------
    def _snap_file(self) -> Path:
        return paths.state_dir() / "snapshots.json"

    def _load_snapshots(self) -> None:
        if self.loaded_snapshots:
            return
        self.loaded_snapshots = True
        try:
            d = json.loads(self._snap_file().read_text())
            cur, prev = d.get("current"), d.get("previous")
            if cur and Path(cur).exists() and self.current is None:
                self.current = Path(cur)
            if prev and Path(prev).exists():
                self.previous = Path(prev)
        except (OSError, ValueError):
            pass

    def _save_snapshots(self) -> None:
        try:
            f = self._snap_file()
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".tmp")
            tmp.write_text(json.dumps({"current": str(self.current) if self.current else None, "previous": str(self.previous) if self.previous else None}))
            tmp.replace(f)
        except OSError:
            pass

    def set_current(self, path: Path) -> None:
        path = Path(path)
        if self.current is not None and self.current.resolve() != path.resolve():
            self.previous = self.current
        elif self.current is not None and self.previous is None:
            pass
        self.current = path
        self._save_snapshots()

    # ---- accessors ------------------------------------------------------------------------
    def client(self) -> HookClient:
        if self.hook is None:
            self.hook = HookClient()
        return self.hook

    def get_notes(self) -> Notes:
        if self.notes is None:
            self.notes = Notes()
        return self.notes

    def save(self) -> tuple[Save, str]:
        """(Save, header line)."""
        self._load_snapshots()
        newest = latest_save(paths.user_dir())
        if self.current is None or not self.current.exists():
            if newest is None:
                raise ToolError(f"no saves found in {paths.user_dir()} (start a game; eu4_refresh asks the hook to save)")
            self.current = newest
        s = open_save(self.current)
        head = f"[{s.path.name} | {s.date}]"
        if newest is not None and newest.resolve() != self.current.resolve() and newest.stat().st_mtime_ns > s.mtime_ns:
            head += f" (newer on disk: {newest.name}; eu4_refresh to load)"
        return s, head


ST = State()


def tool(fn: Callable) -> Callable:
    """Register ``fn`` as a tool; any expected failure becomes a clean tool error."""

    @functools.wraps(fn)
    def wrapper(*a: Any, **kw: Any) -> str:
        out = err = None
        try:
            out = fn(*a, **kw)
            return out
        except ToolError as e:
            err = str(e)
            raise
        except (ValueError, NotesError, SaveError, HookError, OSError) as e:
            err = str(e)
            raise ToolError(err) from None
        finally:
            journal.tool_call(fn.__name__, kw, out, err, _game_date())

    mcp.tool(structured_output=False)(wrapper)
    return wrapper


def _game_date() -> str | None:
    try:
        return ST.client().request("info", timeout=2).get("date")
    except Exception:
        return None


def _read(fn: Callable[..., str], *args: Any, **kw: Any) -> str:
    s, head = ST.save()
    return head + "\n" + fn(s, *args, **kw)


# ---- read tools ----------------------------------------------------------------------------
@tool
def eu4_status(tag: str = "", detail: int = 0) -> str:
    """Digest of a country (default the player): date, ruler, money, manpower, power, wars, army, diplomacy. detail 0-2 widens."""
    s, head = ST.save()
    out = head + "\n" + digest.status(s, tag=tag or None, detail=detail)
    if not tag or tag.upper() == s.player:
        b = alerts.block(alerts.monthly(_alerts(s), s.date))
        out += ("\n" + b) if b else ""
        from . import revanchism
        r = revanchism.status_line(str(s.date) if s.date else None)
        out += ("\n" + r) if r else ""
    return out


def _live_facts(tag: str) -> dict:
    """Optional live facts for the alert rules (pure reads through the hook); {} without a running game."""
    out: dict = {}
    try:
        lv = _live()
        lv.info()
    except Exception:
        return out
    try:
        out["windows"] = [w for w in lv.open_windows() if w != "CEventWindow"]  # pending events are reported apart
    except Exception:
        pass
    try:
        from .estates import Estates
        out["estates"] = [{k: x[k] for k in ("key", "loyalty", "influence")} for x in Estates(ST.client(), lv)._estates(tag)[1]]
    except Exception:
        pass
    try:
        out["advisors"] = lv.advisor_slots(tag)
    except Exception:
        pass
    return out


def _alerts(s: Save, live: dict | None = None, prev: Path | None | bool = True) -> list:
    """All alerts for the save's player against the previous snapshot (prev=True: ST.previous)."""
    p = ST.previous if prev is True else prev
    old = open_save(p) if p and Path(p).exists() and Path(p).resolve() != s.path.resolve() else None
    if live is None:
        live = _live_facts(s.player or "")
    return alerts.evaluate(s, old, live=live, journal=alerts.journal_tail())


@tool
def eu4_alerts() -> str:
    """Alerts: STOP (act now), INFO (soon)."""
    s, head = ST.save()
    return head + "\n" + (alerts.block(_alerts(s), max_lines=30) or "no alerts")


@tool
def eu4_country(tag: str = "", sections: str = "economy,military,government", detail: int = 0) -> str:
    """Country sections. sections: comma list of economy, military, diplomacy, tech, ideas, ruler, government, estates, subjects, religion_culture."""
    return _read(digest.country, tag or None, sections, detail=detail)


@tool
def eu4_diplomacy(tag: str = "", detail: int = 0, limit: int = 0) -> str:
    """Alliances, marriages, guarantees, subjects, rivals, wars, truces, CBs, opinions of/with neighbours and great powers."""
    return _read(digest.diplomacy, tag or None, detail=detail, limit=limit or None)


@tool
def eu4_wars(tag: str = "", detail: int = 0) -> str:
    """Active wars (all, or those involving tag): sides, goal, scores, recent battles."""
    return _read(digest.wars, tag or None, detail=detail)


@tool
def eu4_armies(tag: str = "", detail: int = 0) -> str:
    """Armies and fleets of a country: unit id, location, regiments by type, morale, leader."""
    return _read(digest.armies, tag or None, detail=detail)


@tool
def eu4_province(id: int, detail: int = 0) -> str:
    """One province: owner, dev, buildings, fort, religion, culture, goods, cores, autonomy, unrest."""
    return _read(digest.province, id, detail=detail)


@tool
def eu4_provinces(tag: str = "", filter: str = "", limit: int = 0) -> str:
    """One line per province of tag. filter terms: dev>8 religion=sunni goods~grain building=temple unrest>0 core=no fort>1."""
    return _read(digest.provinces, tag or None, filter, limit=limit or None)


@tool
def eu4_neighbours(tag: str = "", detail: int = 0) -> str:
    """Bordering countries with dev/army ratios, allies and relation flags (war, truce, CB, rival)."""
    return _read(digest.neighbours, tag or None, detail=detail)


@tool
def eu4_find(text: str, limit: int = 10) -> str:
    """Resolve a country or province name fragment (or tag/id) to tag/id."""
    return _read(digest.find, text, limit=limit)


@tool
def eu4_changes(tag: str = "", detail: int = 0) -> str:
    """What changed for tag between the previous and the current snapshot (wars, provinces, ruler, treasury, alliances, tech)."""
    s, head = ST.save()
    if ST.previous is None or not ST.previous.exists():
        return head + "\nno previous snapshot yet: advance time, then call eu4_refresh and eu4_changes again."
    old = open_save(ST.previous)
    return head + f" vs {old.path.name}\n" + digest.diff(old, s, tag or None, detail=detail)


# ---- game control ---------------------------------------------------------------------------
@tool
def eu4_refresh() -> str:
    """Ask the game (hook) to write a save and load it as the current snapshot; without a hook load the newest save on disk."""
    note = ""
    path: Path | None = None
    try:
        path = _snapshot(Path(ST.client().save()))
    except HookUnavailable:
        note = " (hook not running: loaded newest save on disk)"
    except HookError as e:
        note = f" ({e}; loaded newest save on disk)"
    if path is None:
        path = latest_save(paths.user_dir())
        if path is None:
            raise ToolError(f"no saves found in {paths.user_dir()}" + note)
    ST.set_current(path)
    s = open_save(path)
    prev = f", previous snapshot {ST.previous.name}" if ST.previous else ""
    return f"[{s.path.name} | {s.date}] loaded{prev}{note}"


@tool
def eu4_console(line: str) -> str:
    """Run one raw console command in the game via the hook and return its output."""
    return ST.client().console(line) or "(no output)"


def _pace(tag: str = "") -> tuple[int, str]:
    """Speed limit from the newest snapshot (alerts.pace: 1 in war/rebellion/occupation, 3 in quiet peace); 1 when it cannot be read."""
    try:
        return alerts.pace(ST.save()[0], tag or _live().player())  # ST.save() also restores the snapshot path after a restart/reload
    except Exception:
        return alerts.SPEED_TROUBLE, "state unknown"


@tool
def eu4_time(pause: bool | None = None, speed: int = 0) -> str:
    """Pause/unpause (pause=true/false) and/or set game speed (limit: 1 in war/rebellion/occupation, 3 in quiet peace) via the hook; returns the state."""
    c = ST.client()
    if pause is not None:
        c.pause(pause)
    if speed:
        c.speed(min(speed, _pace()[0]))
    i = c.info()
    return json.dumps(i, separators=(",", ":")) if not isinstance(i, str) else i


# ---- notes ----------------------------------------------------------------------------------
@tool
def eu4_notes_brief() -> str:
    """Plan, commitments, standing rules, last 10 journal lines and intel index. Call first after a context reset."""
    extra = _learned()
    return ST.get_notes().brief() + ("\n" + extra if extra else "")


@tool
def eu4_notes_read(section: str = "", tail: int = 0) -> str:
    """Read a notes section (plan, commitments, standing_rules, journal, intel/TAG); no section lists them with sizes. tail=N last lines."""
    n = ST.get_notes()
    if not section:
        rows = n.list()
        return "\n".join(f"{name} ({size}B)" for name, size in rows) or "(no notes yet)"
    return n.read(section, tail or None) or "(empty)"


@tool
def eu4_notes_write(section: str, text: str) -> str:
    """Replace a notes section (not journal). Empty text on intel/TAG deletes it."""
    n = ST.get_notes()
    if not text.strip() and section.startswith("intel/"):
        return "deleted" if n.delete(section) else "nothing to delete"
    return f"wrote {n.write(section, text)} chars to {section}"


@tool
def eu4_notes_append(section: str, text: str, date: str = "") -> str:
    """Append to a section; journal entries require the in-game date (e.g. 1492.2.6)."""
    return f"{section} now {ST.get_notes().append(section, text, date or None)} chars"


def _snapshot(src: Path) -> Path:
    """Copy a fresh autosave into the snapshot folder (the game overwrites autosave.eu4) and keep the last 4."""
    import shutil

    d = paths.state_dir() / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    dst = d / time.strftime("snap_%Y%m%d_%H%M%S.eu4")
    shutil.copyfile(src, dst)
    for old in sorted(d.glob("snap_*.eu4"))[:-4]:
        old.unlink(missing_ok=True)
    return dst


# ---- live orders ------------------------------------------------------------------------------
def _live() -> Live:
    return Live(ST.client())


def _name(prov: int) -> str:
    try:
        return f"{digest.gamedata().province_name(prov)}({prov})"
    except Exception:
        return str(prov)


@tool
def eu4_army(action: str = "list", tag: str = "", unit: int = 0, to: int = 0, units: str = "", n: int = 0, leader: int = 0, what: str = "") -> str:
    """Live army/navy orders. list: units. move unit=<id> to=<province>: like a right click (paths, boards, attacks). merge units="id,id". split unit=<id> [n=regiments, default half]. disband unit=<id>. assault to=<province>.
    Mercenaries: mercs | hire_merc what=<key> [to=province]; dismiss = disband.
    Leaders: leaders | assign unit leader=<id> | unassign unit.
    Exploration (fleet led by an explorer, army led by a conquistador; never move into unknown land or sea by hand): regions unit (what the fleet may explore) | explore unit what="sea:<region key>" | "coast:<region key>" | circumnavigate | land (keys from regions) | stop unit."""
    lv = _live()
    tag = (tag or lv.player()).upper()
    if action in ("leaders", "assign", "unassign", "regions", "explore", "stop"):
        o = _orders2()
        if action == "leaders":
            rows = [f"#{x['id']} {x['type']} {x['name']} fire {x['fire']} shock {x['shock']} man {x['maneuver']} siege {x['siege']}" +
                    (f" -> unit #{x['unit']} {x['unit_name']}" if x["unit"] else " (free)") for x in o.leaders(tag)]
            return f"{tag} leaders:\n" + ("\n".join(rows) or "none")
        if action == "regions":
            r = o.explore_regions(tag, unit)
            return "\n".join(f"{k}: " + (", ".join(x["key"] for x in v) or "none") for k, v in r.items())
        r = (o.assign_leader(tag, leader, unit) if action == "assign" else o.unassign_leader(tag, unit) if action == "unassign" else
             o.stop_mission(tag, unit) if action == "stop" else o.explore(tag, unit, what.partition(":")[0].strip() or "sea", what.partition(":")[2].strip() or None))
        return f"{r['what']}: ordered"
    if action in ("mercs", "hire_merc"):
        from .mercs import Mercs
        m = Mercs(ST.client(), lv)
        if action == "hire_merc":
            if not what:
                raise ToolError("hire_merc needs what=<company key from mercs>")
            r = m.hire(tag, what, to or None)
            return f"{r['what']}: ordered, cost {r['cost']:.0f}, {r['monthly']:.2f}/month; the army appears there after the recruit time"
        rows = [f"{x['key']} \"{x['name']}\" {x['infantry']}/{x['cavalry']}/{x['artillery']} inf/cav/art, pool {x['manpower']} men, cost {x['cost']:.0f}, {x['monthly']:.2f}/month" +
                (f", home {x['home_name']}({x['home']})" if x.get("home") else "") + ("" if x["can_hire"] else f" - NO: {x['why_not']}") for x in m.available(tag)]
        hired = [f"{x['key']} {x['state']}" + (f" unit #{x['unit']} @{_name(x['location'])}" if x["unit"] else "") + f" men {x['manpower']}/{x['max_manpower']}, {x['monthly']:.2f}/month"
                 for x in m.hired(tag)]
        return f"{tag} mercenary companies for hire:\n" + ("\n".join(rows) or "none") + "\nin service:\n" + ("\n".join(hired) or "none")
    if action == "list":
        us = lv.units(tag)
        rows = [f"#{u['id']} {'fleet' if u['navy'] else 'army'} {u['name']} x{u['n']} @{_name(u['loc'])}" + (f" -> {_name(u['dest'])}" if u.get("dest") else "") for u in us]
        return f"{tag} units (live):\n" + ("\n".join(rows) or "none")
    if action == "move":
        if not unit or not to:
            raise ToolError("move needs unit=<id> and to=<province id>")
        return lv.move(tag, unit, to)
    if action == "merge":
        ids = [int(x) for x in units.replace(" ", "").split(",") if x]
        lv.merge(tag, ids)
        return f"merge ordered: {ids}"
    if action == "split":
        lv.split(tag, unit, n)
        return f"split ordered for #{unit}; eu4_army list shows the new unit"
    if action == "disband":
        lv.disband(tag, unit)
        return f"#{unit} disbanded"
    if action == "assault":
        return f"assault on {_name(to)} ordered ({lv.assault(tag, to)} MIL)"
    raise ToolError(f"unknown action {action!r}: list, move, merge, split, disband, assault, mercs, hire_merc")


def _orders2():
    from .orders2 import Orders2
    return Orders2(ST.client(), _live())


_AB = {"adm": 0, "dip": 1, "mil": 2}


def _ability(what: str) -> int:
    if what.lower() not in _AB:
        raise ToolError("what must be adm, dip or mil")
    return _AB[what.lower()]


@tool
def eu4_economy(action: str, province: int = 0, what: str = "", value: float = 0, tag: str = "", dry: bool = False) -> str:
    """Live internal orders (same commands as the GUI buttons; the game checks legality).
    Monarch power (dry=true only reports the cost): stability | inflation | war_exhaustion | tech what=adm|dip|mil | develop province what=adm|dip|mil (tax/production/manpower) | core province | idea what=<idea key>.
    Money: build province what=<building key, e.g. temple> | recruit province what=<unit key, e.g. western_medieval_infantry> value=<count> | maintenance what=land|naval value=0..1.
    Advisors: advisors (hired + candidates with ids, cost) | hire value=<advisor id> [what=replace] | fire what=ADM|DIP|MIL.
    Trade: merchants (our merchants, nodes, downstream links) | merchant what=<node key> value=0 collect / 1 steer | merchant_recall what=<node> | steer what="<node>><downstream node>".
    Colonies: colonists | colonisable (provinces in range) | recall_colonist province.
    Other: colonise province | idea_group what=<key, e.g. exploration_ideas> | mission what=<mission key> | decision what=<decision key> | leader what=general|admiral|conquistador|explorer."""
    lv = _live()
    tag = (tag or lv.player()).upper()
    a = action.lower()
    spend = {
        "stability": lambda: lv.stability(tag, dry), "inflation": lambda: lv.reduce_inflation(tag, dry),
        "war_exhaustion": lambda: lv.reduce_war_exhaustion(tag, dry), "tech": lambda: lv.tech(tag, _ability(what), dry),
        "develop": lambda: lv.develop(tag, province, _ability(what), dry), "core": lambda: lv.core(tag, province, dry),
        "idea": lambda: lv.idea(tag, what, dry),
    }
    if a in spend:
        cost = spend[a]()
        if dry:
            return f"{a}: costs {cost}, possible now" if cost >= 0 else f"{a}: costs {-cost - 1}, NOT possible now"
        return f"{a} done ({cost} monarch power). now: {_brief(lv, tag)}"
    if dry and a == "build":
        why = lv.build_check(tag, province, what)
        return f"build {what} in {_name(province)}: " + (f"NOT possible now: {why}" if why else "possible now")
    if dry and a in ("recruit", "loan", "maintenance", "idea_group", "mission", "leader"):
        return f"{a}: no dry run exists for this order; NOTHING was ordered. Call again without dry to do it."
    if a == "build":
        return f"{what} started in {_name(province)} ({lv.build(tag, province, what)} ducats). now: {_brief(lv, tag)}"
    if a == "recruit":
        cnt, spent = max(1, int(value or 1)), 0.0
        for k in range(cnt):
            try:
                spent += lv.recruit(tag, province, what)
            except HookError as e:
                if k == 0:
                    raise
                return f"{k} of {cnt} {what} queued in {_name(province)} ({spent:g} ducats), then refused: {e}"
        return f"{cnt} {what} queued in {_name(province)} ({spent:g} ducats). now: {_brief(lv, tag)}"
    if a == "loan":
        return f"loan taken: +{lv.loan(tag)} ducats. now: {_brief(lv, tag)}"
    if a == "maintenance":
        if what not in ("land", "naval", ""):
            raise ToolError("what must be land or naval")
        return json.dumps(lv.maintenance(tag, **({what: value} if what else {})))
    if a in ("advisors", "hire", "fire", "merchants", "merchant", "merchant_recall", "steer", "colonists", "colonisable", "colonise", "recall_colonist"):
        o = _orders2()
        if a == "advisors":
            d = o.advisors(tag)
            f = lambda x: f"#{x['id']} {x['slot']} {x['type']} skill {x['skill']} {x['name']} cost {x['cost']:g} + {x['monthly']:g}/month"
            return "hired:\n" + ("\n".join(f(x) for x in d["hired"]) or "none") + "\ncandidates:\n" + ("\n".join(f(x) for x in d["available"]) or "none")
        if a == "merchants":
            ms = "; ".join(f"{m['name']} {'placed/travelling' if m['travelling_or_placed'] else 'free'}" for m in o.envoys(tag, 1)) or "none"
            ns = [f"{x['key']}: {x['mission']}" + (f", steering to {x['links'][x['steer_to']]}" if x["mission"] == "steer" and 0 <= x["steer_to"] < len(x["links"]) else "") +
                  f"; flows to {', '.join(x['links']) or 'nothing (end node)'}" for x in o.trade_nodes(tag, mine_only=True)]
            return f"merchants: {ms}\n" + ("\n".join(ns) or "no merchant placed")
        if a == "colonists":
            return json.dumps(o.colonists(tag), ensure_ascii=False, default=str)[:1500]
        if a == "colonisable":
            rows = o.colonisable(tag, 40)
            return "\n".join(f"{_name(x['id'])}" + ("" if x.get("can_now", True) else " (not now)") + (" colonist there" if x.get("has_colonist") else "") for x in rows) or "nothing in colonial range"
        if a == "steer":
            node, _, toward = what.partition(">")
            r = o.steer(tag, node.strip(), toward.strip(), dry)
        else:
            r = (o.hire_advisor(tag, int(value), what == "replace", dry) if a == "hire" else o.fire_advisor(tag, what, dry) if a == "fire" else
                 o.send_merchant(tag, what, "steer" if value else "collect", dry) if a == "merchant" else
                 o.recall_merchant(tag, what, dry) if a == "merchant_recall" else
                 o.colonise(tag, province, dry) if a == "colonise" else o.recall_colonist(tag, province, dry))
        return f"{r['what']}: " + (("possible now" if r.get("valid") else "NOT possible now") if dry else f"ordered. now: {_brief(lv, tag)}")
    if a == "idea_group":
        lv.idea_group(tag, what)
        return f"idea group {what} picked"
    if a == "mission":
        lv.mission(tag, what)
        return f"mission {what} completed"
    if a == "decision":
        return f"decision {what}: {lv.decision(tag, what, dry)}"
    if a == "leader":
        lv.leader(tag, what)
        return f"{what} recruited. now: {_brief(lv, tag)}"
    raise ToolError(f"unknown action {action!r} (see the tool description)")


@tool
def eu4_loans(action: str = "list", loan: int = 0, n: int = 1, tag: str = "", dry: bool = False) -> str:
    """Loans: list | take [n] | repay loan=<id> or n=<count> (soonest expiry first; pays the principal). dry=true only checks."""
    lv = _live()
    tag = (tag or lv.player()).upper()
    if action == "list":
        ls = lv.loans(tag)
        rows = [f"#{x['id']} {x['amount']} ducats at {x['interest']:.1f}% until {x['expiry']}" + ("" if x["bank"] else " (from a country)") for x in ls]
        return f"{tag} loans: {len(ls)}, {sum(x['amount'] for x in ls)} ducats owed. now: {_brief(lv, tag)}\n" + "\n".join(rows)
    if action == "take":
        if dry:
            return "take: no dry run exists; NOTHING was ordered."
        got = [lv.loan(tag) for _ in range(max(1, int(n)))]
        return f"{len(got)} loan(s) taken: +{sum(got):g} ducats. now: {_brief(lv, tag)}"
    if action == "repay":
        r = lv.repay(tag, loan, n, dry)
        ids = ", ".join(f"#{x['id']} ({x['amount']})" for x in r["repaid"]) or "none"
        return (f"{'would repay' if dry else 'repaid'}: {ids}; {r['spent']} ducats; loans left {r['left']}" + (f". stopped: {r['why']}" if r["why"] else "") +
                ("" if dry else f". now: {_brief(lv, tag)}"))
    raise ToolError(f"unknown action {action!r}: list, take, repay")


def _brief(lv: Live, tag: str) -> str:
    c = lv.country(tag)
    return f"treasury {c['treasury']:.0f}, manpower {c['manpower']}, ADM/DIP/MIL {c['adm']}/{c['dip']}/{c['mil']}"


@tool
def eu4_events(tag: str = "", detail: int = 0) -> str:
    """Pending events waiting for a decision (live): id, title, options with their effects script. Answer with eu4_answer."""
    from .gamedata import event_def

    lv = _live()
    tag = (tag or lv.player()).upper()
    evs = lv.events(tag)
    if not evs:
        return f"no pending events for {tag}"
    gd = digest.gamedata()
    out = []
    for e in evs:
        d = event_def(e["id"]) or {"options": []}
        title = gd.loc_get(d.get("title") or "", e["id"])
        out.append(f"event {e['pid']}: {title} [{e['id']}]")
        if detail and d.get("desc"):
            out.append("  " + (gd.loc_get(d["desc"], "") or "")[:500])
        for i in range(e["n"]):
            o = d["options"][i] if i < len(d["options"]) else {"name": "", "script": ""}
            ok = "" if e.get("allowed", [True] * e["n"])[i] else " (not available)"
            lim = 600 if detail else 220
            out.append(f"  {i + 1}. {gd.loc_get(o['name'], o['name']) or '?'}{ok}: {o['script'][:lim]}")
    return "\n".join(out)


@tool
def eu4_answer(event: int, option: int, tag: str = "") -> str:
    """Choose an option (1-based, as numbered by eu4_events) of pending event <event id number>."""
    lv = _live()
    tag = (tag or lv.player()).upper()
    from .gamedata import event_def

    eid = lv.answer(tag, event, option)
    d = event_def(eid) or {"options": []}
    gd = digest.gamedata()
    title = gd.loc_get(d.get("title") or "", eid)
    o = d["options"][option - 1] if option - 1 < len(d["options"]) else {"name": "", "script": ""}
    return f'event "{title}" [{eid}]: chose option {option} "{gd.loc_get(o["name"], o["name"]) or "?"}" ({" ".join(o["script"].split())[:300]})'


@tool
def eu4_advance(days: int = 0, until: str = "", speed: int = 0, timeout: int = 120, tag: str = "") -> str:
    """Unpause and run until a date (until=Y.M.D or days=N), then pause, save and report what changed for tag. Stops early when an event waits for your answer or the game pauses itself. timeout = real seconds; call again to continue."""
    lv = _live()
    c = ST.client()
    i = lv.info()
    if not until:
        if days <= 0:
            raise ToolError("give days=N or until=Y.M.D")
        until = add_days(i["date"], days)
    lim, why = _pace(tag)
    c.speed(min(speed or lim, lim))  # every advance sets the speed for the state of the country
    key = lambda d: tuple(int(x) for x in d.split("."))
    player = lv.player()
    deadline = time.time() + max(5, min(timeout, 600))
    pending: list[dict] = []
    quick = ""  # live STOP check at each 10-day step (stability / treasury, from CCountry; the save rules run at the end)
    try:
        c0 = lv.country(player)
    except HookError:
        c0 = None
    try:
        while True:  # 10-day steps: an event for the player stops the run (events do not pause the game by themselves)
            pending = lv.events(player)
            now = lv.info()["date"]
            if pending and now != i["date"]:
                w = {"date": now, "reason": "event"}
                break
            step = min(until, add_days(now, 10), key=key)
            c.pause(False)
            w = c.wait(step, timeout=max(1.0, deadline - time.time()), pause=True)
            if w["reason"] == "reached" and c0 is not None:
                try:
                    cc = lv.country(player)
                except HookError:
                    cc = c0
                if cc["stability"] <= alerts.STABILITY_MIN < c0["stability"]:
                    quick = f"stability {cc['stability']:g}"
                elif cc["treasury"] < alerts.TREASURY_MIN <= c0["treasury"]:
                    quick = f"treasury {cc['treasury']:.0f}"
                if quick:
                    w["reason"] = "alert"
                    pending = lv.events(player)
                    break
            if w["reason"] != "reached" or key(w["date"]) >= key(until) or time.time() >= deadline:
                if w["reason"] == "reached" and key(w["date"]) < key(until):
                    w["reason"] = "timeout"
                pending = lv.events(player)
                break
    finally:
        c.pause(True)  # never leave the game running after a failed wait
    head = f"{i['date']} -> {w['date']} ({w['reason']}" + ("" if w["reason"] == "reached" else f", target {until}") + f") speed {min(speed or lim, lim)} ({why})"
    if pending:
        head += f"\n{len(pending)} event(s) waiting for an answer: " + ", ".join(f"{e['id']} (#{e['pid']})" for e in pending[:8]) + " -> eu4_events"
    live = _live_facts(player)
    if key(w["date"]) == key(i["date"]) and key(until) > key(i["date"]) and not pending:
        live["stuck"] = f"date stayed {w['date']} ({w['reason']})"
    try:
        ST.set_current(_snapshot(Path(c.save())))
    except HookError as e:
        return _stop_line(["game_stuck"] if "stuck" in live else []) + head + f"\nsave failed: {e}"
    cur = open_save(ST.current)
    if cur.date is not None:
        live["save_lag"] = (str(cur.date), w["date"])
    prev = ST.previous if ST.previous is not None and ST.previous.exists() else None
    old = open_save(prev) if prev else None
    al = alerts.evaluate(cur, old, live=live, journal=alerts.journal_tail())
    new = alerts.newly_stopping(al, alerts.evaluate(old, None, tag=player) if old else [])
    names = [a.rule for a in new] + ([f"live {quick}"] if quick else [])
    b = alerts.block(alerts.monthly(al, cur.date))
    body = "snapshot loaded (no earlier snapshot to compare)" if old is None else digest.diff(old, cur, tag or None, detail=0)
    return _stop_line(names) + head + "\n" + body + ("\n" + b if b else "")


def _stop_line(names: list[str]) -> str:
    return f"STOPPED: alert {', '.join(names)}: deal with it before advancing\n" if names else ""


@tool
def eu4_say(text: str, who: str = "strategist") -> str:
    """Post a short comment to the public play log (the watch page): what you decided and why, in 1-3 sentences. who = your role (strategist, clock, steward, general)."""
    text = " ".join(text.split())
    if not text:
        raise ToolError("empty text")
    journal.write("say", who=who[:20], text=text[:1500], date=_game_date())
    return "posted"


@tool
def eu4_playlog(start: str = "", end: str = "", kinds: str = "say,act", page: int = 1) -> str:
    """The play log between two game dates (Y.M.D, inclusive; empty = open end): every order with its result and every comment, one line each, 150 per page. kinds: say,act,read,note."""
    return journal.digest(start, end, kinds, page)


def _nm(tag: str) -> str:
    try:
        return f"{digest.gamedata().country_name(tag) or tag} ({tag})"
    except Exception:
        return tag


def _why(text: str, n: int = 2) -> str:
    return "; ".join(x.strip(" -") for x in str(text).splitlines() if x.strip())[: 160 * n]


def _chance(c: dict) -> str:
    rs = ", ".join(f"{r['value']:+d} {r['text'].rstrip(':')}" for r in c.get("reasons", [])[:12])
    return f"{'would accept' if c.get('accepts') else 'would decline'} (total {c.get('total', c.get('acceptance'))}): {rs}"


@tool
def eu4_diplo(action: str, target: str = "", what: str = "", province: int = 0, money: float = 0, treaties: str = "",
              allies: str = "", index: int = 0, accept: bool = False, cancel: bool = False, dry: bool = False, tag: str = "") -> str:
    """Diplomacy, war and peace (live; same actions as the diplomacy screen, the game checks legality). target = country tag.
    list target: which actions are possible now | chance target what=<action>: will they accept, with reasons |
    send target what=<action, e.g. alliance, royal_marriage, improve_relations, send_gift money=, military_access, guarantee, embargo,
      fabricate_claim province=, insult, call_to_arms> (cancel=true ends/recalls it; dry=true only checks) |
    incoming: proposals waiting for our answer | answer index accept=true|false |
    cbs target: casus belli and war goals | declare_war target what=<cb key> province=<goal province or 0> allies="ARA,POR" (dry=true only checks) |
    wars | peace_terms target (what=offer for concessions we give): numbered terms with war score cost |
    peace target treaties="0,3" or what=white: reports cost and whether they accept; it is SENT only with dry=false AND accept=true |
    rival target | unrival target."""
    from .diplo import Diplo

    lv = _live()
    d = Diplo(ST.client())
    me = (tag or lv.player()).upper()
    t = target.upper()
    a = action.lower()
    if a not in ("incoming", "answer", "wars") and not t:
        raise ToolError(f"{a}: give target=<country tag> (eu4_find turns a name into a tag)")
    if a == "list":
        rows = d.actions(me, t)
        yes = [r["name"] or r["key"] for r in rows if r.get("selectable")]
        no = [f"{r['name'] or r['key']} ({_why(r.get('why', ''), 1)[:90]})" for r in rows if not r.get("selectable")]
        return f"{_nm(me)} -> {_nm(t)}\npossible: {', '.join(yes) or 'none'}\nnot now: " + "; ".join(no[:25])
    if a == "chance":
        return f"{what} to {_nm(t)}: " + _chance(d.chance(me, t, what, **({"money": money} if money else {}), **({"province": province} if province else {})))
    if a == "send":
        opts: dict = {}
        if money:
            opts["money"] = money
        if province:
            opts["province"] = province
        r = d.send(me, t, what, value=not cancel, dry=dry, **opts)
        verb = "would be allowed" if dry else ("sent" if r.get("posted") else "NOT sent")
        return f"{'cancel ' if cancel else ''}{what} -> {_nm(t)}: {verb}. {_why(r.get('why', ''))}"
    if a == "incoming":
        rows = d.incoming(me)
        gd = digest.gamedata()

        def terms(r: dict) -> str:
            if "terms" not in r:
                return f"\n   terms unreadable: {r['terms_error']}" if "terms_error" in r else ""
            ts = [f"{x['type']}" + (f" {gd.province_name(x['province'])}({x['province']})" if x.get("province") else "") + f" -> {x.get('taker', '')}" for x in r["terms"]]
            return "\n   terms: " + ("; ".join(ts) or "none selected (white peace)")
        return "\n".join(f"{r.get('index')}: {r.get('key')} from {_nm(str(r.get('from', '?')))}: {_why(r.get('text', ''))}" + terms(r) for r in rows) or "no proposals waiting"
    if a == "answer":
        r = d.answer(me, index, accept)
        return f"proposal {index} {'accepted' if accept else 'declined'}: {json.dumps(r, default=str)[:300]}"
    if a == "cbs":
        rows = d.war_options(me, t)
        return f"casus belli against {_nm(t)}:\n" + ("\n".join(
            f"  {r['cb']} province={r.get('province', 0)}: {r.get('name', '')}" for r in rows[:40]) or "  none (a war without one costs stability)")
    if a == "declare_war":
        r = d.declare_war(me, t, what, province=province, call_allies=[x.strip().upper() for x in allies.split(",") if x.strip()], dry=dry)
        state = "would be allowed" if dry else ("WAR DECLARED" if r.get("posted") else "NOT declared")
        return f"declare war on {_nm(t)} with {what} ({r.get('goal', '')}): {state}. {_why(r.get('why', ''), 1) if not r.get('selectable', True) or dry else ''}"
    if a == "wars":
        rows = d.wars(me)
        return "\n".join(f"{r['name']}: {r['side']}; attackers {','.join(r['attackers'])}; defenders {','.join(r['defenders'])}; "
                         f"enemy leader {r['enemy_leader']}; war score {r['warscore']}" for r in rows) or "at peace"
    if a == "peace_terms":
        rows = d.peace_options(me, t, demand=what.lower() != "offer", text=False)
        gd = digest.gamedata()
        out = [f"{r['index']}: {r['type']}" + (f" {gd.province_name(r['province'])}({r['province']})" if r.get("province") else "") +
               f" -> {r.get('taker', '')} cost {r.get('cost')}" for r in rows[:120]]
        return f"peace terms {_nm(me)} vs {_nm(t)} ({len(rows)}):\n" + "\n".join(out)
    if a == "peace":
        white = what.lower() == "white"
        ids = [int(x) for x in treaties.replace(" ", "").split(",") if x]
        send = bool(accept) and not dry
        r = d.peace(me, t, demand=what.lower() != "offer", treaties=ids, white=white, send=send)
        head = "white peace" if white else f"terms {ids} (war score cost {r.get('cost')})"
        state = ("SENT" if r.get("posted") else "not sent") if send else "not sent (check only; send with accept=true)"
        return f"peace with {_nm(t)}: {head}: {_chance(r)}. {state}"
    if a == "rival":
        return d.add_rival(me, t)
    if a == "unrival":
        return d.remove_rival(me, t)
    raise ToolError("action: list|chance|send|incoming|answer|cbs|declare_war|wars|peace_terms|peace|rival|unrival")


@tool
def eu4_issue(text: str, tool_name: str = "") -> str:
    """Report a problem with the tools or the guide (an order that failed or did nothing, a wrong or missing fact, something you could not do). It is filed for the maintainer; then carry on playing with what works."""
    p = paths.notes_dir().parent / "issues.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(f"[{time.strftime('%Y-%m-%d %H:%M')}] game {_game_date() or '?'} tool={tool_name or '-'}: {' '.join(text.split())[:1500]}\n")
    return "filed"


@tool
def eu4_estates(action: str = "list", estate: str = "", privilege: str = "", tag: str = "", dry: bool = False) -> str:
    """Estate screen orders (game checks legality; dry=true only checks).
    list (privilege=why adds refusal reasons) | grant estate=<nobles|church|burghers> privilege=<key> | revoke estate privilege |
    sell_titles | seize_land | summon_diet | diet_options | agenda estate."""
    from .estates import Estates
    lv = _live()
    tag = (tag or lv.player()).upper()
    e = Estates(ST.client(), lv)
    a = action.lower()
    if a == "list":
        d = e.list(tag, detail=privilege == "why")
        short = lambda est, ks: ", ".join(k.removeprefix(est + "_") for k in ks) or "none"
        rows = [f"{x['key'].removeprefix('estate_')}: loyalty {x['loyalty']:g}, influence {x['influence']:g}, land {x['land']:g}%\n  granted: {short(x['key'], x['granted'])}\n  grantable: {short(x['key'], x['grantable'])}"
                + ("\n  refused: " + "; ".join(f"{k.removeprefix(x['key'] + '_')}: {v}" for k, v in x.get("not_grantable", {}).items()) if x.get("not_grantable") else "")
                for x in d["estates"]]
        acts = ", ".join(f"{x['key']} {'ok' if x['can_use'] else 'no' + (' (' + x['why_not'][:80] + ')' if x['why_not'] else '')}" for x in d["actions"])
        return "\n".join(rows) + f"\ncrown land {d['crown_land']:g}%; agenda: {json.dumps(d['active_agenda']) if d['active_agenda'] else 'none'}; offered: {json.dumps(d['offered_agendas']) if d['offered_agendas'] else 'none'}\nactions: {acts}"
    if a in ("grant", "revoke"):
        if not estate or not privilege:
            raise ToolError("estate and privilege are required")
        r = (e.grant_privilege if a == "grant" else e.revoke_privilege)(tag, estate, privilege, dry=dry)
    elif a in ("sell_titles", "seize_land", "summon_diet"):
        r = getattr(e, a)(tag, dry=dry)
    elif a == "diet_options":
        return json.dumps(e.diet_options(tag))
    elif a == "agenda":
        if not estate:
            raise ToolError("estate is required")
        r = e.choose_agenda(tag, estate, dry=dry)
    else:
        raise ToolError(f"unknown action {action!r}: list, grant, revoke, sell_titles, seize_land, summon_diet, diet_options, agenda")
    return json.dumps({k: v for k, v in r.items() if k != "ptr"})


LEARN_DIR = paths.notes_dir().parent    # runtime data, kept outside the repo: <data dir>/lessons.txt, mechanics_todo.txt
LEARN_FILES = {"lesson": "lessons.txt", "mechanic": "mechanics_todo.txt"}


@tool
def eu4_learn(text: str, kind: str = "lesson") -> str:
    """Write down something future sessions should know (kept across campaigns, shown by eu4_notes_brief). kind=lesson: what worked or failed in play and why (one or two sentences, general enough to reuse). kind=mechanic: a game mechanic you did not use or could not use with the tools (estates, trade, religion ...), and what you would have wanted to do with it."""
    if kind not in LEARN_FILES:
        raise ToolError("kind must be lesson or mechanic")
    LEARN_DIR.mkdir(parents=True, exist_ok=True)
    with open(LEARN_DIR / LEARN_FILES[kind], "a", encoding="utf-8") as f:
        f.write(f"- [{_game_date() or '?'}] {' '.join(text.split())[:600]}\n")
    return "written"


def _learned(n: int = 12) -> str:
    out = []
    for kind, name in LEARN_FILES.items():
        try:
            rows = [ln for ln in (LEARN_DIR / name).read_text(encoding="utf-8").splitlines() if ln.startswith("- ")]
        except OSError:
            rows = []
        if rows:
            out.append(f"## learned earlier ({kind}s, last {min(n, len(rows))} of {len(rows)}; file docs/game/{name})\n" + "\n".join(rows[-n:]))
    return "\n".join(out)


def _strip_titles(o: Any) -> Any:
    if isinstance(o, dict):
        return {k: _strip_titles(v) for k, v in o.items() if not (k == "title" and isinstance(v, str))}
    if isinstance(o, list):
        return [_strip_titles(v) for v in o]
    return o


def _slim_schemas() -> None:
    """Drop pydantic's auto-generated ``title`` fields: tool definitions are sent to the model every session."""
    for t in mcp._tool_manager._tools.values():
        t.parameters = _strip_titles(t.parameters)


_slim_schemas()


if (paths.state_dir() / "test_tool").exists():     # tests/test_e2e.py: proves that a tool can be added to a running server
    @tool
    def eu4_test_live() -> str:
        """Test tool."""
        return "live tool works"
