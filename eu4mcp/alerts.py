"""Automatic alerts for the player country: ONE tunable rule table (docs/alerts.txt describes it).

Each rule has a level (STOP: stop advancing time and deal with it; INFO: worth doing soon), a check that returns
the things that are true (``(key, text)`` pairs; the key identifies the subject so "newly true" can be decided
against the previous snapshot) and a one-line "what to do".

Inputs: the parsed current save (``eu4mcp.save.Save``), optionally the previous snapshot, optional live facts read
through the hook by the server (``live`` dict, see ``Facts``) and the play journal (refused orders).  Every save field
used is named next to its rule.  Rules whose data the plain-text save does not hold reliably are left out (see
docs/alerts.txt).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import paths
from .clausewitz import Date, aslist
from .save import Save

STOP, INFO = "STOP", "INFO"

# ---- thresholds (tune here) ---------------------------------------------------------------------------------------
TREASURY_MIN = -50          # STOP when treasury < this
LOANS_MAX = 3               # STOP when number of loans >= this
STABILITY_MIN = -2          # STOP when stability <= this
LEGITIMACY_MIN = 70         # STOP when legitimacy < this (monarchies)
DISASTER_PCT = 50           # STOP when a disaster's progress >= this (0-100)
ESTATE_LOYALTY_MIN = 25     # STOP when an estate's loyalty < this
ESTATE_INFLUENCE_MAX = 100  # STOP when an estate's influence >= this (live read only)
NO_HEIR_RULER_AGE = 50      # STOP "no heir" only when the ruler is at least this old
MAJORITY_AGE = 15           # a monarch younger than this rules through a regency council
SAVE_LAG_DAYS = 3           # STOP when the fresh save is this many days behind the game date
WAR_EXHAUSTION_MAX = 6      # INFO when war exhaustion >= this
MANPOWER_LOW = 0.10         # INFO when manpower < this share of max manpower
POWER_MAX = 400             # INFO when a monarch power pool >= this
INFLATION_MAX = 3.0         # INFO when inflation >= this
OVEREXTENSION_MAX = 0.5     # INFO when overextension >= this (save stores a fraction: 0.5 = 50 %)
LIBERTY_MAX = 50            # INFO when a subject's liberty desire >= this
REFUSED_MAX = 3             # INFO when the same order failed this many times ...
REFUSED_WINDOW = 200        # ... within the last N journal entries
MAX_LINES = 8               # lines in an ALERTS block (STOP first)
LINE_CHARS = 170

# ADM tech levels that unlock idea group slots 1..8 (common/technologies/adm.txt, allowed_idea_groups)
IDEA_SLOTS_AT_ADM = (5, 7, 10, 14, 18, 22, 26, 29)


@dataclass
class Alert:
    rule: str
    level: str
    items: list[tuple[Any, str]]
    todo: str

    @property
    def keys(self) -> set:
        return {(self.rule, k) for k, _ in self.items}

    def line(self) -> str:
        what = "; ".join(t for _, t in self.items)
        head = f"{self.level} {self.rule}" + (f" ({len(self.items)})" if len(self.items) > 2 else "") + ": "
        room = LINE_CHARS - len(head) - len(self.todo) - 4
        if len(what) > room:
            what = what[: max(20, room - 1)] + "…"
        return f"{head}{what} -> {self.todo}"


@dataclass
class Facts:
    """What the checks look at.  ``live`` keys (all optional, filled by the server from the hook):
    windows: [class name] of open dialogs; estates: [{key, loyalty, influence}]; advisors: [slot names that are empty];
    stuck: text when the game date did not move during eu4_advance; save_lag: (save date, game date) strings."""
    s: Save
    tag: str
    prev: Save | None = None
    live: dict = field(default_factory=dict)
    journal: list[dict] | None = None

    @property
    def c(self) -> dict:
        return self.s.country(self.tag) or {}

    @property
    def pc(self) -> dict | None:
        return self.prev.country(self.tag) if self.prev is not None else None

    def pname(self, pid: int) -> str:
        n = (self.s.province_table().get(pid) or {}).get("name")
        return f"{n}({pid})" if n else str(pid)


def _f(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _age(p: dict | None, now: Date | None) -> int | None:
    b = (p or {}).get("birth_date")
    if isinstance(b, Date) and now:
        return now.y - b.y - (1 if (now.m, now.d) < (b.m, b.d) else 0)
    return None


def _days(d: str | Date) -> int:
    """EU4 day number (365-day years) of a date or a 'Y.M.D' string (zero padding allowed)."""
    y, m, dd = (d.y, d.m, d.d) if isinstance(d, Date) else (int(x) for x in str(d).split("."))
    ml = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    return y * 365 + sum(ml[: m - 1]) + dd - 1


# ---- STOP checks ----------------------------------------------------------------------------------------------------
def _occupied(f: Facts):
    # provinces.-<id>.owner / controller
    tab = f.s.province_table()
    out = []
    for pid in f.s.owned_by(f.tag):
        ctl = (tab.get(pid) or {}).get("controller")
        if ctl and ctl != f.tag:
            out.append((pid, f"{f.pname(pid)} held by {ctl}"))
    return out


def _rebels(f: Facts):
    # countries.REB.army.{id, location, regiment}
    own = set(f.s.owned_by(f.tag))
    out = []
    for a in aslist((f.s.country("REB") or {}).get("army")):
        if isinstance(a, dict) and a.get("location") in own:
            aid = a["id"].get("id") if isinstance(a.get("id"), dict) else a.get("name")
            out.append((aid, f"{len(aslist(a.get('regiment')))} regs in {f.pname(a['location'])}"))
    return out


SPEED_TROUBLE, SPEED_QUIET = 1, 3  # slow while there is fighting to react to, faster in quiet peace


def pace(s: Save, tag: str) -> tuple[int, str]:
    """(game speed, why) for ``tag`` in this save: 1 at war, with rebels on its land or with provinces held by someone else; else 3."""
    f = Facts(s, tag)
    if any(tag in aslist(w.get("attackers")) or tag in aslist(w.get("defenders")) for w in s.wars()):
        return SPEED_TROUBLE, "at war"
    if _rebels(f):
        return SPEED_TROUBLE, "rebels"
    if _occupied(f):
        return SPEED_TROUBLE, "provinces occupied"
    return SPEED_QUIET, "peace"


def _money(f: Facts):
    # countries.<TAG>.treasury, countries.<TAG>.loan (list of loan blocks)
    c, out = f.c, []
    t = _f(c.get("treasury"))
    if t < TREASURY_MIN:
        out.append(("treasury", f"treasury {t:.0f}"))
    n = len([x for x in aslist(c.get("loan")) if isinstance(x, dict)])
    if n >= LOANS_MAX:
        out.append(("loans", f"{n} loans"))
    return out


def _war_on_us(f: Facts):
    # active_war.{name, defenders, original_attacker}, compared with the previous snapshot by war name
    if f.prev is None:
        return []
    old = {w.get("name") for w in f.prev.wars()}
    return [(w.get("name"), f"{w.get('name')} (attacker {w.get('original_attacker', '?')})")
            for w in f.s.wars() if f.tag in aslist(w.get("defenders")) and w.get("name") not in old]


def _ruler(f: Facts):
    # countries.<TAG>.monarch / heir {id} -> history person {name, birth_date}; government.government
    out = []
    mon, now = f.s.ref(f.tag, "monarch"), f.s.date
    pc = f.pc
    if pc is not None and isinstance(pc.get("monarch"), dict) and isinstance(f.c.get("monarch"), dict) \
            and pc["monarch"].get("id") != f.c["monarch"].get("id"):
        out.append((("new", f.c["monarch"].get("id")), f"new ruler {(mon or {}).get('name', '?')}"))
    age = _age(mon, now)
    if mon and age is not None and age < MAJORITY_AGE:
        out.append(("regency", f"ruler {mon.get('name', '?')} is {age}: regency"))
    gov = f.c.get("government")
    monarchy = isinstance(gov, dict) and gov.get("government") == "monarchy"
    if monarchy and mon and not isinstance(f.c.get("heir"), dict) and (age or 0) >= NO_HEIR_RULER_AGE:
        out.append(("no_heir", f"no heir, ruler aged {age}"))
    return out


def _stability(f: Facts):
    v = f.c.get("stability")  # countries.<TAG>.stability
    return [("stab", f"stability {_f(v):g}")] if v is not None and _f(v) <= STABILITY_MIN else []


def _legitimacy(f: Facts):
    v = f.c.get("legitimacy")  # countries.<TAG>.legitimacy (monarchies only)
    return [("legit", f"legitimacy {_f(v):.0f}")] if v is not None and _f(v) < LEGITIMACY_MIN else []


_DISASTERS: list[str] | None = None


def disaster_names(game: Path | None = None) -> list[str]:
    """Disaster keys in load order (files of common/disasters sorted by name, keys in file order): the index order
    of countries.<TAG>.disaster_progress (55 entries in 1.37.5 = 55 keys; the mapping is inferred, not proven)."""
    global _DISASTERS
    if _DISASTERS is None or game is not None:
        names: list[str] = []
        try:
            for p in sorted(((game or paths.game_dir()) / "common" / "disasters").glob("*.txt")):
                names += re.findall(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\{", p.read_text(encoding="utf-8", errors="replace"), re.M)
        except OSError:
            pass
        _DISASTERS = names
    return _DISASTERS


def _disaster(f: Facts):
    # countries.<TAG>.disaster_progress (list, 0-100 per disaster)
    prog = f.c.get("disaster_progress")
    if not isinstance(prog, list):
        return []
    names = disaster_names()
    out = []
    for i, v in enumerate(prog):
        if _f(v) >= DISASTER_PCT:
            nm = names[i] if len(names) == len(prog) else f"#{i}"
            out.append((i, f"{nm} {_f(v):.0f}%"))
    return out


def _estates(f: Facts):
    # live estates (loyalty + influence) if read through the hook, else countries.<TAG>.estate.{type, loyalty}
    rows = f.live.get("estates")
    if rows is None:
        rows = [{"key": e.get("type"), "loyalty": e.get("loyalty"), "influence": None} for e in aslist(f.c.get("estate")) if isinstance(e, dict)]
    out = []
    for e in rows:
        k = str(e.get("key") or "?").removeprefix("estate_")
        if e.get("loyalty") is not None and _f(e["loyalty"]) < ESTATE_LOYALTY_MIN:
            out.append((("loyalty", k), f"{k} loyalty {_f(e['loyalty']):.0f}"))
        if e.get("influence") is not None and _f(e["influence"]) >= ESTATE_INFLUENCE_MAX:
            out.append((("influence", k), f"{k} influence {_f(e['influence']):.0f}"))
    return out


def _coalition(f: Facts):
    # countries.<TAG>.coalition_against_us (members; their country block has coalition_target=<TAG>)
    return [(t, t) for t in aslist(f.c.get("coalition_against_us")) if isinstance(t, str)]


def _windows(f: Facts):
    return [(w, w) for w in f.live.get("windows") or []]


def _stuck(f: Facts):
    s = f.live.get("stuck")
    return [("stuck", s)] if s else []


def _save_lag(f: Facts):
    lag = f.live.get("save_lag")
    if not lag:
        return []
    sd, gd = lag
    return [("lag", f"save {sd}, game {gd}")] if _days(gd) - _days(sd) >= SAVE_LAG_DAYS else []


# ---- INFO checks ----------------------------------------------------------------------------------------------------
def _war_exhaustion(f: Facts):
    v = _f(f.c.get("war_exhaustion"))  # countries.<TAG>.war_exhaustion
    return [("we", f"war exhaustion {v:.1f}")] if v >= WAR_EXHAUSTION_MAX else []


def _manpower(f: Facts):
    mp, mx = _f(f.c.get("manpower")), _f(f.c.get("max_manpower"))  # thousands
    return [("mp", f"manpower {mp:.1f}k of {mx:.1f}k")] if mx > 0 and mp < MANPOWER_LOW * mx else []


def _power(f: Facts):
    pw = f.c.get("powers")  # countries.<TAG>.powers = [adm, dip, mil]
    if not isinstance(pw, list) or len(pw) != 3:
        return []
    return [(k, f"{k} {int(_f(v))}") for k, v in zip(("ADM", "DIP", "MIL"), pw) if _f(v) >= POWER_MAX]


_BASIC_GROUPS: set[str] | None = None


def basic_idea_groups(game: Path | None = None) -> set[str]:
    """Keys of the pickable idea groups (common/ideas/00_basic_ideas.txt; national ideas live in other files)."""
    global _BASIC_GROUPS
    if _BASIC_GROUPS is None or game is not None:
        try:
            txt = ((game or paths.game_dir()) / "common" / "ideas" / "00_basic_ideas.txt").read_text(encoding="utf-8", errors="replace")
            _BASIC_GROUPS = set(re.findall(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\{", txt, re.M))
        except OSError:
            _BASIC_GROUPS = set()
    return _BASIC_GROUPS


def _idea_slot(f: Facts):
    # technology.adm_tech -> slots; active_idea_groups keys that are basic groups = slots used
    t = f.c.get("technology")
    groups = f.c.get("active_idea_groups")
    if not isinstance(t, dict) or not isinstance(groups, dict):
        return []
    slots = sum(1 for lv in IDEA_SLOTS_AT_ADM if int(_f(t.get("adm_tech"))) >= lv)
    basic = basic_idea_groups()
    used = sum(1 for g in groups if g in basic) if basic else max(0, len(groups) - 1)
    return [("idea", f"{slots - used} free idea group slot(s)")] if slots > used else []


def _advisor(f: Facts):
    return [(s, f"{s} empty") for s in f.live.get("advisors") or []]


def _envoys(f: Facts):
    # countries.<TAG>.{diplomats,merchants,colonists}.envoy; an envoy without ``action`` is idle
    out = []
    for key in ("diplomats", "merchants", "colonists"):
        blk = f.c.get(key)
        lst = [e for e in aslist(blk.get("envoy")) if isinstance(e, dict)] if isinstance(blk, dict) else []
        free = sum(1 for e in lst if "action" not in e)
        if free:
            out.append((key, f"{free} {key}"))
    return out


def _inflation(f: Facts):
    v = _f(f.c.get("inflation"))
    return [("infl", f"inflation {v:.1f}")] if v >= INFLATION_MAX else []


def _overextension(f: Facts):
    v = _f(f.c.get("overextension_percentage"))
    return [("oe", f"overextension {v * 100:.0f}%")] if v >= OVEREXTENSION_MAX else []


def _uncored(f: Facts):
    # owned_provinces minus core_provinces
    core = set(aslist(f.c.get("core_provinces")))
    miss = [p for p in f.s.owned_by(f.tag) if p not in core]
    return [(p, f.pname(p)) for p in miss]


def _liberty(f: Facts):
    # diplomacy.dependency {first=overlord, second=subject}; subject's countries.<SUB>.cached_liberty_desire
    out = []
    for d in f.s.diplomacy().get("dependency", []):
        if isinstance(d, dict) and d.get("first") == f.tag:
            sub = f.s.country(str(d.get("second"))) or {}
            v = sub.get("cached_liberty_desire")
            if v is not None and _f(v) >= LIBERTY_MAX:
                out.append((d.get("second"), f"{d.get('second')} {_f(v):.0f}%"))
    return out


def _refused(f: Facts):
    if not f.journal:
        return []
    counts: dict[str, int] = {}
    for e in f.journal[-REFUSED_WINDOW:]:
        if e.get("kind") == "act" and e.get("ok") is False:
            k = f"{e.get('tool')} " + " ".join(f"{a}={v}" for a, v in sorted((e.get("args") or {}).items()))
            counts[k] = counts.get(k, 0) + 1
    return [(k, f"{k.strip()} x{n}") for k, n in counts.items() if n >= REFUSED_MAX]


@dataclass
class Rule:
    name: str
    level: str
    check: Callable[[Facts], list]
    todo: str


# ---- THE TABLE (order = display order within a level) -------------------------------------------------------------
RULES: list[Rule] = [
    Rule("game_stuck", STOP, _stuck, "date did not move: check for an open window or a crash (eu4_time, display)"),
    Rule("save_stale", STOP, _save_lag, "the save is behind the game: eu4_refresh, check the hook"),
    Rule("window_open", STOP, _windows, "a popup holds the game: close it in the game; events: eu4_events"),
    Rule("occupied", STOP, _occupied, "siege it back (eu4_army move there) or make peace"),
    Rule("rebels", STOP, _rebels, "attack with a bigger army, then raise stability"),
    Rule("war_on_us", STOP, _war_on_us, "eu4_wars; call allies, gather the army"),
    Rule("money", STOP, _money, "cut costs (maintenance, advisors), no new loans, repay when able"),
    Rule("ruler", STOP, _ruler, "eu4_country sections=ruler; plan for heir/regency (royal marriage)"),
    Rule("stability", STOP, _stability, "raise stability (eu4_economy stability)"),
    Rule("legitimacy", STOP, _legitimacy, "raise legitimacy (events, advisors) before it costs stability"),
    Rule("disaster", STOP, _disaster, "lower the disaster's causes now (stability, estates, war)"),
    Rule("estates", STOP, _estates, "eu4_estates list: grant a privilege, agenda or revoke to fix it"),
    Rule("coalition", STOP, _coalition, "stop expanding; improve relations with members; keep allies"),
    Rule("war_exhaustion", INFO, _war_exhaustion, "make peace or reduce it (eu4_economy war_exhaustion)"),
    Rule("manpower", INFO, _manpower, "avoid battles, recruit mercenaries or wait"),
    Rule("power", INFO, _power, "spend it: tech, ideas, develop, stability"),
    Rule("idea_slot", INFO, _idea_slot, "pick an idea group (eu4_economy idea_group)"),
    Rule("advisor", INFO, _advisor, "hire one (eu4_economy advisors, hire)"),
    Rule("idle_envoys", INFO, _envoys, "give them work (merchant, colonise, improve_relations, spy network)"),
    Rule("inflation", INFO, _inflation, "reduce inflation (eu4_economy inflation) or hire a treasurer"),
    Rule("overextension", INFO, _overextension, "core provinces before taking more"),
    Rule("uncored", INFO, _uncored, "core them (eu4_economy core province=)"),
    Rule("liberty", INFO, _liberty, "lower liberty desire (favours, diplomats) or prepare for revolt"),
    Rule("refused", INFO, _refused, "same order failed repeatedly: read the error and change it"),
]


def evaluate(s: Save, prev: Save | None = None, tag: str | None = None, live: dict | None = None,
             journal: list[dict] | None = None, rules: list[Rule] | None = None) -> list[Alert]:
    """The alerts that are true now, STOP rules first.  A failing check is skipped, never raised."""
    tag = tag or s.player
    if not tag:
        return []
    f = Facts(s, tag, prev, dict(live or {}), journal)
    out: list[Alert] = []
    for level in (STOP, INFO):
        for r in rules or RULES:
            if r.level != level:
                continue
            try:
                items = r.check(f)
            except Exception:  # a broken rule must not break eu4_advance
                continue
            if items:
                out.append(Alert(r.name, r.level, items, r.todo))
    return out


def newly_stopping(cur: list[Alert], old: list[Alert]) -> list[Alert]:
    """STOP alerts with a subject that was not true in ``old`` (the previous snapshot's alerts)."""
    seen = set().union(*(a.keys for a in old)) if old else set()
    return [a for a in cur if a.level == STOP and not a.keys <= seen]


def block(alerts: list[Alert], max_lines: int = MAX_LINES) -> str:
    """'ALERTS:' block, at most ``max_lines`` lines after the header; empty string when nothing is true."""
    if not alerts:
        return ""
    lines = [a.line() for a in alerts]
    if len(lines) > max_lines:
        lines = lines[: max_lines - 1] + [f"+{len(lines) - max_lines + 1} more: eu4_alerts"]
    return "ALERTS:\n" + "\n".join(lines)


# ---- INFO at most once per game month ---------------------------------------------------------------------------
def _shown_file() -> Path:
    return paths.state_dir() / "alerts_shown.json"


def monthly(alerts: list[Alert], date: Date | str | None) -> list[Alert]:
    """Keep every STOP alert; keep an INFO alert only if its rule was not shown yet in this game month (records it)."""
    if date is None:
        return alerts
    month = f"{date.y}.{date.m}" if isinstance(date, Date) else ".".join(str(int(x)) for x in str(date).split(".")[:2])
    p = _shown_file()
    try:
        shown = json.loads(p.read_text())
    except (OSError, ValueError):
        shown = {}
    out, changed = [], False
    for a in alerts:
        if a.level == STOP:
            out.append(a)
        elif shown.get(a.rule) != month:
            out.append(a)
            shown[a.rule] = month
            changed = True
    if changed:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(shown))
            tmp.replace(p)
        except OSError:
            pass
    return out


def journal_tail(n: int = REFUSED_WINDOW) -> list[dict]:
    from . import journal

    entries, _ = journal.read(0, limit=n)
    return entries
