"""Short plain-text digests of a loaded ``Save``.

Every function returns a string under ~6,000 characters (about 1,500 tokens) by default; ``detail`` (0, 1, 2)
doubles the budget per step and ``limit`` widens list lengths.  Lists that are cut end with
``(+N more, raise limit)``.  Empty/default fields are omitted.  Each field notes the save key it comes from
(``# key:`` comments); a field is left out when the save does not contain it rather than guessed.

Conventions: countries print as ``Name(TAG)``, provinces as ``Name(id)``; money in ducats, manpower in men
(the save stores thousands), dates as ``y.m.d``.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from .clausewitz import Date, aslist
from .gamedata import GameData
from .save import Save

BASE_BUDGET = 6000
TRUCE_YEARS = 5  # common/defines.lua TRUCE_YEARS: minimum truce length after a war

_GD: GameData | None = None


def gamedata() -> GameData:
    global _GD
    if _GD is None:
        _GD = GameData.load()
    return _GD


def set_gamedata(gd: GameData | None) -> None:
    global _GD
    _GD = gd


# ---- formatting helpers ----------------------------------------------------------------------
def num(x: Any, digits: int = 1) -> str:
    """Round for display: 12345.6 -> '12346', 12.34 -> '12.3', 3.0 -> '3'."""
    if isinstance(x, bool) or x is None:
        return str(x)
    if isinstance(x, int):
        return str(x)
    if abs(x) >= 100:
        return f"{x:.0f}"
    s = f"{x:.{digits}f}"
    return s.rstrip("0").rstrip(".") if "." in s else s


def men(thousands: float) -> str:
    """Manpower/sailors are stored in thousands (manpower=6.988 -> 6988 men) -> '7.0k'."""
    return f"{thousands:.1f}k"


class Out:
    """Line collector with a hard character budget."""

    def __init__(self, detail: int = 0, limit: int | None = None):
        self.detail = max(0, int(detail or 0))
        self.limit_arg = limit
        self.max = BASE_BUDGET * (2**self.detail)
        self.lines: list[str] = []

    def lim(self, default: int) -> int:
        if self.limit_arg:
            return max(1, int(self.limit_arg))
        return default * (1 + 2 * self.detail)

    def add(self, line: str = "") -> None:
        if line:
            self.lines.append(line)

    def items(self, label: str, items: Iterable[str], default: int, sep: str = ", ") -> None:
        """``label: a, b, c (+N more, raise limit)`` as one line."""
        items = list(items)
        if not items:
            return
        n = self.lim(default)
        shown = items[:n]
        tail = f" (+{len(items) - n} more, raise limit)" if len(items) > n else ""
        self.lines.append(f"{label}: {sep.join(shown)}{tail}")

    def rows(self, items: list[str], default: int, header: str | None = None) -> None:
        """One item per line, cut with a 'more' marker."""
        if not items:
            return
        n = self.lim(default)
        if header:
            self.lines.append(header)
        self.lines.extend(items[:n])
        if len(items) > n:
            self.lines.append(f"(+{len(items) - n} more, raise limit)")

    def text(self) -> str:
        out, total = [], 0
        for ln in self.lines:
            total += len(ln) + 1
            if total > self.max:
                out.append("(output truncated at budget, raise detail)")
                break
            out.append(ln)
        return "\n".join(out)


class Ctx:
    """A save plus names."""

    def __init__(self, save: Save, gd: GameData | None = None):
        self.s = save
        self.gd = gd or gamedata()

    def name(self, tag: str) -> str:
        n = self.gd.country_name(tag)
        if n is None:
            n = self.s.country_names().get(tag)  # key: countries.<TAG>.name (formed/colonial tags)
            if n is not None:
                n = self.gd.loc_get(n, n)
        return n or tag

    def c(self, tag: str) -> str:
        n = self.name(tag)
        return f"{n}({tag})" if n != tag else tag

    def p(self, pid: int) -> str:
        n = None
        t = self.s.province_table().get(pid)  # key: provinces.-<id>.name
        if t:
            n = t.get("name")
        n = n or self.gd.province_name(pid)
        return f"{n}({pid})" if n else str(pid)

    def l(self, key: str) -> str:
        return self.gd.l(key)

    def country(self, tag: str) -> dict | None:
        return self.s.country(tag)

    def player(self) -> str | None:
        return self.s.player

    def resolve_tag(self, tag: str | None) -> str:
        t = (tag or self.s.player or "").strip()
        if not tag and t:
            pc = self.s.country(t) or {}
            if not pc.get("capital") and not pc.get("owned_provinces"):
                raise ValueError(f"the save's player '{t}' is an observer without a country; pass a tag (e.g. a great power from eu4_find)")
        if t.upper() in self.s.countries():
            return t.upper()
        if t in self.s.countries():
            return t
        hits = self.gd.find_countries(t, self.s.country_names()) if t else []
        hits = [h for h in hits if h[0] in self.s.countries()]
        if hits:
            return hits[0][0]
        raise ValueError(f"unknown country '{t}' (use eu4_find)" if t else "no player in this save; pass a tag")


def _ctx(save: Save, gd: GameData | None = None) -> Ctx:
    return Ctx(save, gd)


def _person(x: Ctx, tag: str, key: str) -> dict | None:
    return x.s.ref(tag, key)  # key: countries.<TAG>.{monarch,heir,queen} -> history.<date>.{monarch,heir,queen} by id


def _age(person: dict, now: Date | None) -> int | None:
    b = person.get("birth_date")
    if isinstance(b, Date) and now:
        return now.y - b.y - (1 if (now.m, now.d) < (b.m, b.d) else 0)
    return None


def _person_line(x: Ctx, p: dict, now: Date | None) -> str:
    # keys: history person {name, ADM, DIP, MIL, birth_date, personalities, dynasty, claim, female}
    bits = [f"{p.get('monarch_name') or p.get('name', '?')}"]
    if all(k in p for k in ("ADM", "DIP", "MIL")):
        bits.append(f"{p['ADM']}/{p['DIP']}/{p['MIL']}")
    a = _age(p, now)
    if a is not None and a >= 0:
        bits.append(f"age {a}")
    pers = p.get("personalities")
    if isinstance(pers, dict) and pers:
        bits.append(", ".join(x.l(k) for k in pers))
    if p.get("claim") is not None:
        bits.append(f"claim {num(p['claim'])}")
    return " ".join(bits)


def _army_summary(x: Ctx, c: dict) -> tuple[int, dict[str, int], int, dict[str, int]]:
    """(#army units, regiments by class, #fleets, ships by class) from countries.<TAG>.{army,navy}.{regiment,ship}.type."""
    regs: dict[str, int] = {}
    n_armies = 0
    for a in aslist(c.get("army")):
        if isinstance(a, dict):
            n_armies += 1
            for r in aslist(a.get("regiment")):
                if isinstance(r, dict):
                    k = x.gd.unit_class(str(r.get("type"))) or "other"
                    regs[k] = regs.get(k, 0) + 1
    ships: dict[str, int] = {}
    n_fleets = 0
    for a in aslist(c.get("navy")):
        if isinstance(a, dict):
            n_fleets += 1
            for r in aslist(a.get("ship")):
                if isinstance(r, dict):
                    k = x.gd.unit_class(str(r.get("type"))) or "other"
                    ships[k] = ships.get(k, 0) + 1
    return n_armies, regs, n_fleets, ships


_SHORT = {"infantry": "inf", "cavalry": "cav", "artillery": "art", "galley": "gal", "light_ship": "light", "heavy_ship": "heavy", "transport": "trans"}


def _units(d: dict[str, int]) -> str:
    return " ".join(f"{_SHORT.get(k, k)} {v}" for k, v in sorted(d.items(), key=lambda kv: -kv[1]))


def _envoys(c: dict, key: str) -> tuple[int, int] | None:
    """(free, total) from countries.<TAG>.<key>.envoy; an envoy with an ``action`` key is busy (assumption)."""
    blk = c.get(key)
    if not isinstance(blk, dict):
        return None
    lst = [e for e in aslist(blk.get("envoy")) if isinstance(e, dict)]
    if not lst:
        return None
    return sum(1 for e in lst if "action" not in e), len(lst)


def _wars_of(s: Save, tag: str) -> list[dict]:
    # keys: active_war.attackers / defenders (lists of tags)
    return [w for w in s.wars() if tag in aslist(w.get("attackers")) or tag in aslist(w.get("defenders"))]


def _loans(c: dict) -> tuple[int, float] | None:
    # key: countries.<TAG>.loan (blocks with ``amount``); absent in the tutorial saves, so field names are unverified
    ls = [l for l in aslist(c.get("loan")) if isinstance(l, dict)]
    if not ls:
        return None
    return len(ls), sum(float(l.get("amount", 0) or 0) for l in ls)


# ======================================================================================
# status
# ======================================================================================
def status(save: Save, gd: GameData | None = None, detail: int = 0, limit: int | None = None, tag: str | None = None) -> str:
    x = _ctx(save, gd)
    out = Out(detail, limit)
    tag = x.resolve_tag(tag)
    c = x.country(tag)
    if c is None:
        raise ValueError(f"country {tag} not in save")
    now = save.date
    who = "player" if tag == save.player else "observing"
    out.add(f"{now} {x.c(tag)} [{who}]")
    # government / ruler
    gov = c.get("government")
    gname = None
    if isinstance(gov, dict):
        gname = gov.get("government")  # countries.<TAG>.government.government
    mon = _person(x, tag, "monarch")
    line = []
    if gname:
        line.append(x.l(str(gname)))
    if mon:
        line.append(f"ruler {_person_line(x, mon, now)}")
    if line:
        out.add(" | ".join(line))
    heir = _person(x, tag, "heir")
    if heir:
        out.add(f"heir {_person_line(x, heir, now)}")
    # economy
    eco = []
    if "treasury" in c:
        eco.append(f"treasury {num(c['treasury'])}")  # countries.<TAG>.treasury
    if "estimated_monthly_income" in c:
        eco.append(f"income ~{num(c['estimated_monthly_income'])}/mo")  # countries.<TAG>.estimated_monthly_income
    led = c.get("ledger")
    if isinstance(led, dict) and "lastmonthincome" in led:  # countries.<TAG>.ledger.lastmonthincome/lastmonthexpense
        inc, exp = led.get("lastmonthincome", 0), led.get("lastmonthexpense", 0)
        eco.append(f"last month +{num(inc)} -{num(exp)} (net {num(inc - exp)})")
    ln = _loans(c)
    if ln:
        eco.append(f"loans {ln[0]} ({num(ln[1])})")
    if c.get("inflation"):
        eco.append(f"inflation {num(c['inflation'], 2)}")  # countries.<TAG>.inflation (raw value)
    out.add(" | ".join(eco))
    # internal
    st = []
    for k, label in (("stability", "stability"), ("prestige", "prestige"), ("legitimacy", "legitimacy"), ("republican_tradition", "republican tradition"), ("devotion", "devotion"), ("horde_unity", "horde unity"), ("meritocracy", "meritocracy"), ("corruption", "corruption")):
        v = c.get(k)
        if v is not None and (v or k in ("stability", "prestige")):
            st.append(f"{label} {num(v)}")
    if "war_exhaustion" in c:  # key not present in the tutorial saves; shown only when the save has it
        st.append(f"war exhaustion {num(c['war_exhaustion'])}")
    if "overextension_percentage" in c:  # raw value as stored (0.21), unit not verified
        st.append(f"overextension {num(c['overextension_percentage'], 2)}")
    out.add(" | ".join(st))
    pw = c.get("powers")  # countries.<TAG>.powers = [adm, dip, mil] (order assumed, matches game convention)
    tech = c.get("technology")
    row = []
    if isinstance(pw, list) and len(pw) == 3:
        row.append(f"monarch power A/D/M {'/'.join(num(v, 0) for v in pw)}")
    if isinstance(tech, dict):
        row.append(f"tech {tech.get('adm_tech')}/{tech.get('dip_tech')}/{tech.get('mil_tech')}")  # technology.{adm,dip,mil}_tech
    if c.get("technology_group"):
        row.append(x.l(str(c["technology_group"])))
    out.add(" | ".join(row))
    # military
    mil = []
    if "manpower" in c:
        mil.append(f"manpower {men(c['manpower'])}/{men(c.get('max_manpower', 0))}")  # manpower / max_manpower
    if "sailors" in c:
        mil.append(f"sailors {num(c['sailors'], 0)}/{num(c.get('max_sailors', 0), 0)}")
    na, regs, nf, ships = _army_summary(x, c)
    if regs:
        mil.append(f"army {sum(regs.values())} regs in {na} stacks ({_units(regs)})")
    if ships:
        mil.append(f"navy {sum(ships.values())} ships in {nf} fleets ({_units(ships)})")
    out.add(" | ".join(mil))
    # wars
    ws = _wars_of(save, tag)
    cur = [t for t in aslist(c.get("current_at_war_with"))]
    if ws:
        parts = []
        for w in ws[: out.lim(4)]:
            side = "att" if tag in aslist(w.get("attackers")) else "def"
            foes = aslist(w.get("defenders" if side == "att" else "attackers"))
            parts.append(f"{w.get('name', '?')} ({side}) vs {', '.join(foes[:4])}{'+' if len(foes) > 4 else ''}")
        out.add("at war: " + "; ".join(parts))
    elif cur:
        out.add("at war with " + ", ".join(cur))
    # territory
    terr = []
    owned = c.get("owned_provinces")
    if isinstance(owned, list):
        terr.append(f"{len(owned)} provinces")
    dev = c.get("raw_development", c.get("development"))
    if dev is not None:
        terr.append(f"dev {num(dev, 0)}")  # raw_development
    if c.get("capital"):
        terr.append(f"capital {x.p(c['capital'])}")
    if c.get("religion"):
        terr.append(x.l(str(c["religion"])))
    if c.get("primary_culture"):
        terr.append(x.l(str(c["primary_culture"])))
    out.add(" | ".join(terr))
    # diplomacy snapshot
    d = []
    rel = relations(save, tag)
    for label, key in (("allies", "allies"), ("subjects", "subjects"), ("guaranteeing", "guarantees"), ("royal marriages", "rm")):
        if rel.get(key):
            d.append(f"{label} {len(rel[key])}")
    if rel.get("overlord"):
        d.append(f"overlord {rel['overlord']}")
    if rel.get("rivals"):
        d.append("rivals " + ",".join(rel["rivals"]))
    out.add(" | ".join(d))
    # envoys
    env = []
    for key, label in (("diplomats", "diplomats"), ("merchants", "merchants"), ("colonists", "colonists"), ("missionaries", "missionaries")):
        e = _envoys(c, key)
        if e:
            env.append(f"{label} {e[0]}/{e[1]} free")
    out.add(" | ".join(env))
    # great power rank
    gp = [g for g in aslist((save.top("great_powers") or {}).get("original")) if isinstance(g, dict) and g.get("country") == tag]
    if gp:
        out.add(f"great power #{gp[0].get('rank')}")  # great_powers.original.{rank,country}
    return out.text()


# ======================================================================================
# relations (shared by country/diplomacy/neighbours/diff)
# ======================================================================================
def relations(s: Save, tag: str) -> dict[str, Any]:
    """Relations of ``tag`` from the ``diplomacy`` block and the country block.

    keys: diplomacy.alliance/royal_marriage/guarantee/dependency/military_access/support_heir/warning/... {first, second, start_date,
    subject_type}; countries.<TAG>.rival {country, date}; countries.<TAG>.active_relations.<X>.truce/last_war.
    """
    k = ("rel", tag)
    if k in s._cache:
        return s._cache[k]
    dip = s.diplomacy()
    r: dict[str, Any] = {"allies": [], "rm": [], "guarantees": [], "guarantors": [], "subjects": [], "overlord": None, "overlord_type": None, "access": [], "rivals": [], "truces": [], "cb": [], "cb_against": []}

    def other(rec: dict) -> str | None:
        if rec.get("first") == tag:
            return rec.get("second")
        if rec.get("second") == tag:
            return rec.get("first")
        return None

    for rec in dip.get("alliance", []):
        o = other(rec)
        if o:
            r["allies"].append((o, rec.get("start_date")))
    for rec in dip.get("royal_marriage", []):
        o = other(rec)
        if o:
            r["rm"].append((o, rec.get("start_date")))
    for rec in dip.get("guarantee", []):
        if rec.get("first") == tag:
            r["guarantees"].append((rec.get("second"), rec.get("start_date")))
        elif rec.get("second") == tag:
            r["guarantors"].append((rec.get("first"), rec.get("start_date")))
    for rec in dip.get("dependency", []):
        if rec.get("first") == tag:
            r["subjects"].append((rec.get("second"), rec.get("subject_type")))
        elif rec.get("second") == tag:
            r["overlord"] = rec.get("first")
            r["overlord_type"] = rec.get("subject_type")
    for rec in dip.get("military_access", []):
        o = other(rec)
        if o:
            r["access"].append((o, "gives" if rec.get("first") == tag else "has"))
    for rec in dip.get("casus_belli", []):
        if rec.get("first") == tag:
            r["cb"].append((rec.get("type"), rec.get("second"), rec.get("start_date")))
        elif rec.get("second") == tag:
            r["cb_against"].append((rec.get("type"), rec.get("first"), rec.get("start_date")))
    c = s.country(tag) or {}
    for rv in aslist(c.get("rival")):
        if isinstance(rv, dict) and rv.get("country"):
            r["rivals"].append(rv["country"])  # countries.<TAG>.rival.country
    ar = c.get("active_relations")
    if isinstance(ar, dict):
        for o, v in ar.items():
            if isinstance(v, dict) and v.get("truce") is True:
                r["truces"].append((o, v.get("last_war")))
    s._cache[k] = r
    return r


def _mod_names(v: Any) -> list[str]:
    """Names from ``modifier={modifier="x" date=...}`` blocks (the inner key is also forced to a list by the parser)."""
    out: list[str] = []
    for m in aslist(v):
        if isinstance(m, dict):
            out.extend(str(n) for n in aslist(m.get("modifier")))
    return out


def _tags(items: list) -> list[str]:
    return [i[0] if isinstance(i, tuple) else i for i in items]


# ======================================================================================
# country sections
# ======================================================================================
SECTIONS = ("economy", "military", "diplomacy", "tech", "ideas", "ruler", "government", "estates", "subjects", "religion_culture")


def country(save: Save, tag: str | None = None, sections: Iterable[str] | str | None = None, gd: GameData | None = None, detail: int = 0, limit: int | None = None) -> str:
    x = _ctx(save, gd)
    out = Out(detail, limit)
    tag = x.resolve_tag(tag)
    c = x.country(tag)
    if c is None:
        raise ValueError(f"country {tag} not in save")
    if isinstance(sections, str):
        sections = [t for t in re.split(r"[,\s]+", sections) if t]
    secs = list(sections) if sections else ["economy", "military", "government"]
    bad = [t for t in secs if t not in SECTIONS]
    if bad:
        raise ValueError(f"unknown section(s) {bad}; choose from {', '.join(SECTIONS)}")
    out.add(f"{x.c(tag)} {save.date}")
    for sec in secs:
        out.add(f"-- {sec}")
        globals()[f"_sec_{sec}"](x, out, tag, c)
    return out.text()


def _sec_economy(x: Ctx, out: Out, tag: str, c: dict) -> None:
    s = x.s
    for k, label in (("treasury", "treasury"), ("estimated_monthly_income", "income est/mo"), ("inflation", "inflation"), ("mercantilism", "mercantilism"), ("corruption", "corruption"), ("splendor", "splendor"), ("average_autonomy", "avg autonomy"), ("average_unrest", "avg unrest")):
        pass
    bits = [f"{label} {num(c[k], 2 if k in ('inflation', 'corruption') else 1)}" for k, label in (("treasury", "treasury"), ("estimated_monthly_income", "income est/mo"), ("inflation", "inflation"), ("mercantilism", "mercantilism"), ("corruption", "corruption"), ("splendor", "splendor")) if c.get(k)]
    out.add(" | ".join(bits))
    led = c.get("ledger")
    if isinstance(led, dict) and "lastmonthincome" in led:
        out.add(f"last month: income {num(led['lastmonthincome'])}, expense {num(led.get('lastmonthexpense', 0))}")
    ln = _loans(c)
    if ln:
        out.add(f"loans: {ln[0]} totalling {num(ln[1])}")
    if c.get("raw_development") is not None:
        out.add(f"dev {num(c['raw_development'], 0)} in {len(c.get('owned_provinces', []))} provinces; avg autonomy {num(c.get('average_autonomy', 0))}, avg unrest {num(c.get('average_unrest', 0))}")
    ports = c.get("num_of_ports")
    if ports is not None:
        out.add(f"cities {c.get('num_of_cities')}, ports {ports}, overseas {c.get('num_of_overseas', 0)}, non-core {c.get('num_of_non_cores', 0)}")
    if c.get("overextension_percentage"):
        out.add(f"overextension {num(c['overextension_percentage'], 2)} (raw)")
    env = []
    for key in ("merchants", "colonists"):
        e = _envoys(c, key)
        if e:
            env.append(f"{key} {e[0]}/{e[1]} free")
    out.add(" | ".join(env))


def _sec_military(x: Ctx, out: Out, tag: str, c: dict) -> None:
    s = x.s
    bits = []
    if "manpower" in c:
        bits.append(f"manpower {men(c['manpower'])}/{men(c.get('max_manpower', 0))}")
    if "sailors" in c:
        bits.append(f"sailors {num(c['sailors'], 0)}/{num(c.get('max_sailors', 0), 0)}")
    for k, label in (("army_tradition", "army tradition"), ("navy_tradition", "navy tradition"), ("army_professionalism", "professionalism")):
        if c.get(k):
            bits.append(f"{label} {num(c[k])}")
    out.add(" | ".join(bits))
    na, regs, nf, ships = _army_summary(x, c)
    if regs:
        out.add(f"army: {sum(regs.values())} regiments in {na} stacks ({_units(regs)})")
    if ships:
        out.add(f"navy: {sum(ships.values())} ships in {nf} fleets ({_units(ships)})")
    mercs = [m for m in aslist(c.get("mercenary_company")) if isinstance(m, dict) and m.get("manpower")]
    if mercs:
        out.add(f"mercenary companies with manpower: {len(mercs)}")
    ppl = s.people(tag)
    leaders = []
    for ref in aslist(c.get("leader")):
        p = ppl.get(ref.get("id")) if isinstance(ref, dict) else None
        if p:
            leaders.append(_leader_line(p))
    out.items("leaders", leaders, 8, "; ")
    ws = _wars_of(s, tag)
    if ws:
        out.add("at war: " + "; ".join(w.get("name", "?") for w in ws))
    out.add("see eu4_armies for stacks")


def _leader_line(p: dict) -> str:
    # keys: history leader {name, type, fire, shock, manuever (sic), siege}
    return f"{p.get('name', '?')} {p.get('type', '')[:3]} F{p.get('fire', 0)}/S{p.get('shock', 0)}/M{p.get('manuever', 0)}/Si{p.get('siege', 0)}"


def _sec_diplomacy(x: Ctx, out: Out, tag: str, c: dict) -> None:
    r = relations(x.s, tag)
    out.items("allies", [x.c(t) for t in _tags(r["allies"])], 8)
    out.items("royal marriages", [x.c(t) for t in _tags(r["rm"])], 8)
    out.items("guarantees", [x.c(t) for t in _tags(r["guarantees"])], 8)
    out.items("guaranteed by", [x.c(t) for t in _tags(r["guarantors"])], 8)
    out.items("rivals", [x.c(t) for t in r["rivals"]], 6)
    out.items("truces", [x.c(t) for t in _tags(r["truces"])], 8)
    if r["overlord"]:
        out.add(f"overlord {x.c(r['overlord'])} ({x.l(str(r['overlord_type']))})")
    out.add("details: eu4_diplomacy")


def _sec_tech(x: Ctx, out: Out, tag: str, c: dict) -> None:
    t = c.get("technology")
    if isinstance(t, dict):
        out.add(f"adm {t.get('adm_tech')} dip {t.get('dip_tech')} mil {t.get('mil_tech')}")
    if c.get("technology_group"):
        out.add(f"group {x.l(str(c['technology_group']))}, unit set {c.get('unit_type')}")
    inst = c.get("institutions")
    if isinstance(inst, list):
        out.add(f"institutions embraced: {sum(1 for v in inst if v)} of {len(inst)}")  # countries.<TAG>.institutions (0/1 per institution)
    if "technology_cost" in c:
        out.add(f"tech cost modifier {num(c['technology_cost'], 2)}")
    pw = c.get("powers")
    if isinstance(pw, list) and len(pw) == 3:
        out.add(f"monarch power A/D/M {'/'.join(num(v, 0) for v in pw)}")


def _sec_ideas(x: Ctx, out: Out, tag: str, c: dict) -> None:
    g = c.get("active_idea_groups")  # countries.<TAG>.active_idea_groups = {group: ideas unlocked}
    if isinstance(g, dict) and g:
        out.add("; ".join(f"{x.l(k)} {v}/7" for k, v in g.items()))
    else:
        out.add("no idea groups")
    for k in ("completed_missions",):
        v = c.get(k)
        if isinstance(v, list) and v:
            out.add(f"completed missions: {len(v)}")


def _sec_ruler(x: Ctx, out: Out, tag: str, c: dict) -> None:
    now = x.s.date
    for key, label in (("monarch", "ruler"), ("queen", "consort"), ("heir", "heir")):
        p = _person(x, tag, key)
        if p:
            out.add(f"{label}: {_person_line(x, p, now)}")
    if c.get("inauguration"):
        out.add(f"reign since {c['inauguration']}")
    adv = x.s.hired_advisors(tag)  # top-level active_advisors.<TAG>.advisor ids -> provinces.*.history.advisor {name,type,skill}
    if adv:
        out.items("advisors listed in save", [f"{x.l(str(a.get('type')))} {a.get('skill')} {a.get('name')}" for a in adv], 6, "; ")
    ppl = x.s.people(tag)
    leaders = [_leader_line(ppl[r["id"]]) for r in aslist(c.get("leader")) if isinstance(r, dict) and r.get("id") in ppl]
    out.items("leaders", leaders, 6, "; ")
    n = len(aslist(c.get("previous_monarch")))
    if n:
        out.add(f"previous monarchs on record: {n}")


def _sec_government(x: Ctx, out: Out, tag: str, c: dict) -> None:
    gov = c.get("government")
    if isinstance(gov, dict):
        rs = gov.get("reform_stack")
        reforms = aslist(rs.get("reforms")) if isinstance(rs, dict) else []
        out.add(f"{x.l(str(gov.get('government')))}; rank {c.get('government_rank')}; reforms: {', '.join(x.l(str(r)) for r in reforms)}")
    bits = []
    for k, label in (("legitimacy", "legitimacy"), ("republican_tradition", "republican tradition"), ("devotion", "devotion"), ("horde_unity", "horde unity"), ("meritocracy", "meritocracy"), ("stability", "stability"), ("prestige", "prestige"), ("corruption", "corruption"), ("government_reform_progress", "reform progress"), ("authority", "authority")):
        if c.get(k) is not None:
            bits.append(f"{label} {num(c[k], 2 if k == 'corruption' else 1)}")
    out.add(" | ".join(bits))
    if c.get("national_focus"):
        out.add(f"national focus {c['national_focus']}")
    if c.get("capital"):
        out.add(f"capital {x.p(c['capital'])}")
    out.items("modifiers", [x.l(m) for m in _mod_names(c.get("modifier"))], 10)
    fl = c.get("flags")
    if isinstance(fl, dict):
        out.items("flags", [str(k) for k in fl], 10)


def _sec_estates(x: Ctx, out: Out, tag: str, c: dict) -> None:
    es = [e for e in aslist(c.get("estate")) if isinstance(e, dict)]
    rows = [f"{x.l(str(e.get('type')))}: loyalty {num(e.get('loyalty', 0), 0)}, land {num(e.get('territory', 0), 0)}%" for e in es]  # estate.{type,loyalty,territory}
    out.rows(rows, 10)
    if not rows:
        out.add("no estates")


def _sec_subjects(x: Ctx, out: Out, tag: str, c: dict) -> None:
    r = relations(x.s, tag)
    if r["overlord"]:
        out.add(f"overlord {x.c(r['overlord'])} ({x.l(str(r['overlord_type']))})")
    rows = []
    for t, typ in r["subjects"]:
        sc = x.s.country(t) or {}
        rows.append(f"{x.c(t)} {x.l(str(typ))} dev {num(sc.get('raw_development', 0), 0)}")
    out.rows(rows, 12)
    if not rows and not r["overlord"]:
        out.add("no subjects, no overlord")


def _sec_religion_culture(x: Ctx, out: Out, tag: str, c: dict) -> None:
    bits = []
    if c.get("religion"):
        bits.append(x.l(str(c["religion"])))
    if c.get("secondary_religion"):
        bits.append(f"secondary {x.l(str(c['secondary_religion']))}")
    if c.get("primary_culture"):
        bits.append(f"culture {x.l(str(c['primary_culture']))}")
    acc = [x.l(str(a)) for a in aslist(c.get("accepted_culture"))]
    if acc:
        bits.append("accepted: " + ", ".join(acc))
    out.add(" | ".join(bits))
    b2 = []
    for k, label in (("religious_unity", "religious unity"), ("piety", "piety"), ("fervor", "fervor"), ("papal_influence", "papal influence"), ("num_of_heathen_provs", "heathen provinces"), ("num_of_heretic_provs", "heretic provinces")):
        if c.get(k):
            b2.append(f"{label} {num(c[k], 2)}")
    out.add(" | ".join(b2))


# ======================================================================================
# diplomacy
# ======================================================================================
def _opinion(c: dict | None, other: str) -> tuple[float, str | None, list[str]] | None:
    """(sum of listed modifiers, attitude, modifier names) of ``c``'s view of ``other`` from
    countries.<c>.active_relations.<other>.{opinion, cached_sum, attitude}."""
    if not c:
        return None
    ar = c.get("active_relations")
    v = ar.get(other) if isinstance(ar, dict) else None
    if not isinstance(v, dict):
        return None
    mods = [m.get("modifier") for m in aslist(v.get("opinion")) if isinstance(m, dict)]
    cs = v.get("cached_sum")
    if cs is None and not mods:
        return None
    return float(cs or 0), v.get("attitude"), [m for m in mods if m]


def _interesting(s: Save, tag: str) -> list[str]:
    """Neighbours (countries.<TAG>.neighbours) plus great powers (great_powers.original.country)."""
    c = s.country(tag) or {}
    out = list(aslist(c.get("neighbours")))
    for g in aslist((s.top("great_powers") or {}).get("original")):
        if isinstance(g, dict) and g.get("country") not in out and g.get("country") != tag:
            out.append(g["country"])
    return out


def diplomacy(save: Save, tag: str | None = None, gd: GameData | None = None, detail: int = 0, limit: int | None = None) -> str:
    x = _ctx(save, gd)
    out = Out(detail, limit)
    tag = x.resolve_tag(tag)
    c = x.country(tag) or {}
    r = relations(save, tag)
    out.add(f"Diplomacy of {x.c(tag)} {save.date}")
    out.items("allies", [f"{x.c(t)} since {d}" if d else x.c(t) for t, d in r["allies"]], 10)
    out.items("royal marriages", [x.c(t) for t, _ in r["rm"]], 10)
    if r["overlord"]:
        out.add(f"overlord: {x.c(r['overlord'])} ({x.l(str(r['overlord_type']))})")
    out.items("subjects", [f"{x.c(t)} ({x.l(str(typ))})" for t, typ in r["subjects"]], 10)
    out.items("guarantees", [x.c(t) for t, _ in r["guarantees"]], 10)
    out.items("guaranteed by", [x.c(t) for t, _ in r["guarantors"]], 10)
    out.items("military access", [f"{x.c(t)} ({how})" for t, how in r["access"]], 8)
    out.items("rivals", [x.c(t) for t in r["rivals"]], 6)
    ws = _wars_of(save, tag)
    foes = []
    for w in ws:
        side = "attackers" if tag in aslist(w.get("attackers")) else "defenders"
        other = "defenders" if side == "attackers" else "attackers"
        foes.append(f"{w.get('name', '?')}: vs {', '.join(x.c(t) for t in aslist(w.get(other))[:6])}")
    out.items("at war", foes, 4, "; ")
    # truces: countries.<TAG>.active_relations.<X>.truce=yes with .last_war; expiry not stored, minimum is last_war + TRUCE_YEARS
    tr = []
    for t, lw in sorted(r["truces"], key=lambda e: str(e[1]), reverse=True):
        if isinstance(lw, Date):
            tr.append(f"{x.c(t)} (war ended {lw}, truce until at least {lw.y + TRUCE_YEARS}.{lw.m}.{lw.d})")
        else:
            tr.append(x.c(t))
    out.items("truces", tr, 8, "; ")
    # casus belli held / held against us: diplomacy.casus_belli {type, first(holder), second(target), start_date}
    out.items("CBs we hold", [f"{x.l(str(t))} on {x.c(o)}" for t, o, _ in r["cb"]], 8)
    out.items("CBs against us", [f"{x.l(str(t))} by {x.c(o)}" for t, o, _ in r["cb_against"]], 8)
    # opinions
    rows = []
    for o in _interesting(save, tag):
        mine = _opinion(c, o)
        theirs = _opinion(save.country(o), tag)
        if not mine and not theirs:
            continue
        mag = max(abs(mine[0]) if mine else 0, abs(theirs[0]) if theirs else 0)
        txt = f"{x.c(o)}"
        if mine and (mine[0] or mine[2]):
            txt += f" we->{num(mine[0], 0)}"
        if theirs and (theirs[0] or theirs[2] or theirs[1]):
            att = (theirs[1] or "").replace("attitude_", "")
            txt += f" they->{num(theirs[0], 0)}" + (f" [{att}]" if att and att != "neutral" else "")
        rows.append((mag, txt))
    rows.sort(key=lambda e: -e[0])
    out.rows([t for _, t in rows], 12, "opinion modifier sums (modifiers listed in save only; base opinion not stored):")
    out.add("coalitions: not present in the save format seen")
    return out.text()


# ======================================================================================
# wars
# ======================================================================================
def _war_goal(w: dict) -> str | None:
    h = w.get("history")
    g = h.get("war_goal") if isinstance(h, dict) else None  # active_war.history.war_goal.{type, province, casus_belli}
    if isinstance(g, dict):
        return g.get("type"), g.get("province"), g.get("casus_belli")
    return None


def wars(save: Save, tag: str | None = None, gd: GameData | None = None, detail: int = 0, limit: int | None = None) -> str:
    x = _ctx(save, gd)
    out = Out(detail, limit)
    ws = save.wars()
    if tag:
        tag = x.resolve_tag(tag)
        ws = _wars_of(save, tag)
    if not ws:
        return f"No active wars{' involving ' + x.c(tag) if tag else ''} ({save.date})."
    out.add(f"{len(ws)} active war(s) {save.date}")
    n = out.lim(6)
    # rank by size
    ws = sorted(ws, key=lambda w: -(len(aslist(w.get("attackers"))) + len(aslist(w.get("defenders")))))
    for w in ws[:n]:
        h = w.get("history") if isinstance(w.get("history"), dict) else {}
        start = min((k for k in h if isinstance(k, Date)), default=None)
        out.add(f"* {w.get('name', '?')} (since {start})")
        goal = _war_goal(w)
        if goal:
            t, prov, cb = goal
            out.add(f"  goal: {x.l(str(cb)) if cb else t}" + (f" at {x.p(prov)}" if prov else ""))
        parts = {str(p.get("tag")): p for p in aslist(w.get("participants")) if isinstance(p, dict)}  # participants.{tag,value,war_score}

        def side(label: str, key: str) -> None:
            tags = aslist(w.get(key))
            bits = []
            for t in tags[: out.lim(8)]:
                p = parts.get(t, {})
                ws_ = p.get("war_score")
                bits.append(x.c(t) + (f" ws{num(ws_)}" if ws_ else ""))
            more = f" (+{len(tags) - len(bits)} more)" if len(tags) > len(bits) else ""
            out.add(f"  {label}: {', '.join(bits)}{more}")

        side("attackers", "attackers")
        side("defenders", "defenders")
        sc = []
        if "attacker_score" in w:
            sc.append(f"attacker {num(w['attacker_score'])}")  # active_war.attacker_score / defender_score (raw)
        if "defender_score" in w:
            sc.append(f"defender {num(w['defender_score'])}")
        if sc:
            out.add("  score: " + ", ".join(sc))
        battles = []
        for dk, ev in h.items():
            if isinstance(dk, Date):
                for e in aslist(ev):
                    if isinstance(e, dict):
                        for b in aslist(e.get("battle")):
                            if isinstance(b, dict):
                                battles.append((dk, b))
        for dk, b in battles[-3:]:
            a, d = b.get("attacker") or {}, b.get("defender") or {}
            won = "attacker won" if b.get("result") is True else "defender won"  # battle.result yes = attacker victory
            out.add(f"  battle {dk} {b.get('name', '')} @{x.p(b['location']) if b.get('location') else '?'}: {a.get('country')} vs {d.get('country')}, {won}")
    if len(ws) > n:
        out.add(f"(+{len(ws) - n} more, raise limit)")
    return out.text()


# ======================================================================================
# armies
# ======================================================================================
def armies(save: Save, tag: str | None = None, gd: GameData | None = None, detail: int = 0, limit: int | None = None) -> str:
    x = _ctx(save, gd)
    out = Out(detail, limit)
    tag = x.resolve_tag(tag)
    c = x.country(tag) or {}
    ppl = save.people(tag)

    def leader(a: dict) -> str:
        r = a.get("leader")  # army.leader {id,type} -> country history leader
        if isinstance(r, dict) and r.get("id") in ppl:
            return " led by " + _leader_line(ppl[r["id"]])
        return ""

    def uid(a: dict) -> str:
        i = a.get("id")
        return f"#{i.get('id')}/{i.get('type')}" if isinstance(i, dict) else "#?"

    rows = []
    tot = {}
    for a in aslist(c.get("army")):
        if not isinstance(a, dict):
            continue
        regs = [r for r in aslist(a.get("regiment")) if isinstance(r, dict)]
        cls: dict[str, int] = {}
        for r in regs:
            k = x.gd.unit_class(str(r.get("type"))) or "other"
            cls[k] = cls.get(k, 0) + 1
            tot[k] = tot.get(k, 0) + 1
        mor = [r["morale"] for r in regs if "morale" in r]
        strs = [r["strength"] for r in regs if "strength" in r]  # regiment.strength only present when damaged
        bits = [f"{uid(a)} {a.get('name', '?')} @{x.p(a['location']) if a.get('location') else '?'}", f"{len(regs)} regs ({_units(cls)})"]
        if strs:
            bits.append(f"str {num(100 * sum(strs) / len(regs), 0)}%")
        if mor:
            bits.append(f"morale {num(sum(mor) / len(mor))}")
        rows.append((len(regs), " | ".join(bits) + leader(a)))
    rows.sort(key=lambda e: -e[0])
    out.add(f"Armies of {x.c(tag)} {save.date}: {sum(tot.values())} regiments in {len(rows)} stacks ({_units(tot)})")
    out.rows([r for _, r in rows], 20)
    nrows, ntot = [], {}
    for a in aslist(c.get("navy")):
        if not isinstance(a, dict):
            continue
        ships = [r for r in aslist(a.get("ship")) if isinstance(r, dict)]
        cls = {}
        for r in ships:
            k = x.gd.unit_class(str(r.get("type"))) or "other"
            cls[k] = cls.get(k, 0) + 1
            ntot[k] = ntot.get(k, 0) + 1
        mor = [r["morale"] for r in ships if "morale" in r]
        strs = [r["strength"] for r in ships if "strength" in r]
        bits = [f"{uid(a)} {a.get('name', '?')} @{x.p(a['location']) if a.get('location') else '?'}", f"{len(ships)} ships ({_units(cls)})"]
        if strs:
            bits.append(f"str {num(100 * sum(strs) / len(ships), 0)}%")
        if mor:
            bits.append(f"morale {num(sum(mor) / len(mor))}")
        nrows.append((len(ships), " | ".join(bits) + leader(a)))
    nrows.sort(key=lambda e: -e[0])
    if nrows:
        out.add(f"Fleets: {sum(ntot.values())} ships in {len(nrows)} fleets ({_units(ntot)})")
        out.rows([r for _, r in nrows], 12)
    if not rows and not nrows:
        out.add("no armies or fleets")
    return out.text()


# ======================================================================================
# provinces
# ======================================================================================
def _dev(p: dict) -> tuple[float, float, float]:
    return p.get("base_tax", 0), p.get("base_production", 0), p.get("base_manpower", 0)  # provinces.<id>.base_{tax,production,manpower}


def province(save: Save, pid: int, gd: GameData | None = None, detail: int = 0, limit: int | None = None) -> str:
    x = _ctx(save, gd)
    out = Out(detail, limit)
    pid = abs(int(pid))
    p = save.province(pid)
    if p is None:
        raise ValueError(f"province {pid} not in save (use eu4_find)")
    t, pr, m = _dev(p)
    head = f"{x.p(pid)} {save.date}"
    own = p.get("owner")
    if own:
        head += f" owner {x.c(own)}"
        if p.get("controller") and p["controller"] != own:
            head += f", controlled by {x.c(p['controller'])}"
    else:
        head += " unowned"
        if p.get("native_size"):
            head += f" (natives size {num(p['native_size'])})"
    out.add(head)
    if "base_tax" in p:
        bits = [f"dev {num(t + pr + m, 0)} ({num(t, 0)}/{num(pr, 0)}/{num(m, 0)} tax/prod/man)"]
        if p.get("trade_goods"):
            bits.append(x.l(str(p["trade_goods"])))
        if p.get("trade"):
            bits.append(f"node {x.l(str(p['trade']))}")
        out.add(" | ".join(bits))
    b = p.get("buildings")
    if isinstance(b, dict) and b:
        names = []
        fort = 0
        for k in b:
            lvl = x.gd.forts.get(str(k))
            if lvl:
                fort = max(fort, lvl)
            names.append(x.l(str(k)))
        out.add(f"buildings: {', '.join(names)}" + (f" | fort level {fort}" if fort else ""))
    bits = []
    if p.get("religion"):
        bits.append(x.l(str(p["religion"])))
    if p.get("culture"):
        bits.append(x.l(str(p["culture"])))
    if p.get("local_autonomy"):
        bits.append(f"autonomy {num(p['local_autonomy'], 0)}")
    if p.get("unrest"):
        bits.append(f"unrest {num(p['unrest'])}")
    if p.get("devastation"):
        bits.append(f"devastation {num(p['devastation'])}")
    if p.get("hre") is True:
        bits.append("in HRE")
    if p.get("center_of_trade"):
        bits.append(f"center of trade {p['center_of_trade']}")
    if p.get("garrison"):
        bits.append(f"garrison {num(p['garrison'], 0)}")
    out.add(" | ".join(bits))
    cores = aslist(p.get("cores"))
    if cores:
        out.items("cores", [c for c in cores], 8)
    cl = aslist(p.get("claims"))
    if cl:
        out.items("claims", cl, 8)
    out.items("modifiers", [x.l(m) for m in _mod_names(p.get("modifier"))], 6)
    units = [u for u in aslist(p.get("unit")) if isinstance(u, dict)]
    if units:
        out.add(f"units present: {len(units)}")
    return out.text()


_FILTER = re.compile(r"^(\w+)\s*(>=|<=|=|>|<|~)\s*(.+)$")


def _matches(x: Ctx, pid: int, p: dict, terms: list[tuple[str, str, str]]) -> bool:
    t, pr, m = _dev(p)
    vals = {"dev": t + pr + m, "tax": t, "prod": pr, "man": m, "autonomy": p.get("local_autonomy", 0), "unrest": p.get("unrest", 0), "devastation": p.get("devastation", 0), "garrison": p.get("garrison", 0)}
    text = {"religion": p.get("religion"), "culture": p.get("culture"), "goods": p.get("trade_goods"), "node": p.get("trade"), "controller": p.get("controller"), "owner": p.get("owner")}
    for key, op, val in terms:
        if key in vals:
            try:
                v, w = float(vals[key]), float(val)
            except ValueError:
                return False
            if not {">": v > w, "<": v < w, ">=": v >= w, "<=": v <= w, "=": v == w, "~": v == w}[op]:
                return False
        elif key in text:
            a = str(text[key] or "").lower()
            if (op == "~" and val.lower() not in a) or (op != "~" and a != val.lower()):
                return False
        elif key == "building":
            b = p.get("buildings")
            if not (isinstance(b, dict) and any(val.lower() in str(k).lower() for k in b)):
                return False
        elif key == "core":
            if (val.lower() in ("yes", "true", "1")) != bool(p.get("owner") and p["owner"] in aslist(p.get("cores"))):
                return False
        elif key == "hre":
            if (val.lower() in ("yes", "true", "1")) != (p.get("hre") is True):
                return False
        elif key == "fort":
            lvl = max((x.gd.forts.get(str(k), 0) for k in (p.get("buildings") or {})), default=0)
            if lvl < float(val):
                return False
        elif key == "name":
            if val.lower() not in str(p.get("name", "")).lower():
                return False
        else:
            raise ValueError(f"unknown filter key '{key}'; use dev tax prod man autonomy unrest devastation garrison religion culture goods node building core hre fort name")
    return True


def provinces(save: Save, tag: str | None = None, filter: str = "", gd: GameData | None = None, detail: int = 0, limit: int | None = None) -> str:
    """One line per province owned by ``tag``. ``filter``: space-separated terms like ``dev>10 religion=sunni goods~grain building=temple``."""
    x = _ctx(save, gd)
    out = Out(detail, limit)
    tag = x.resolve_tag(tag)
    terms = []
    for tok in re.split(r"[ ,]+", (filter or "").strip()):
        if not tok:
            continue
        m = _FILTER.match(tok)
        if not m:
            raise ValueError(f"bad filter term '{tok}' (expected key<op>value, op in = > < >= <= ~)")
        terms.append((m[1].lower(), m[2], m[3]))
    rows = []
    for pid in save.owned_by(tag):
        p = save.province(pid)
        if not p or not _matches(x, pid, p, terms):
            continue
        t, pr, m = _dev(p)
        b = p.get("buildings")
        fort = max((x.gd.forts.get(str(k), 0) for k in (b or {})), default=0)
        bits = [f"{pid} {p.get('name', '?')}", f"{num(t)}/{num(pr)}/{num(m)}", f"{x.l(str(p.get('religion', '')))[:12]}", f"{x.l(str(p.get('culture', '')))[:12]}", x.l(str(p.get("trade_goods", "")))[:12]]
        if fort:
            bits.append(f"fort{fort}")
        if p.get("local_autonomy"):
            bits.append(f"aut{num(p['local_autonomy'], 0)}")
        if p.get("unrest"):
            bits.append(f"unrest{num(p['unrest'])}")
        if p.get("controller") and p["controller"] != tag:
            bits.append(f"held by {p['controller']}")
        if p.get("hre") is True:
            bits.append("HRE")
        rows.append((t + pr + m, " ".join(bits)))
    rows.sort(key=lambda e: -e[0])
    out.add(f"{len(rows)} province(s) of {x.c(tag)}{' matching ' + filter if filter else ''} {save.date}; id name tax/prod/man religion culture goods")
    out.rows([r for _, r in rows], 25)
    return out.text()


# ======================================================================================
# neighbours
# ======================================================================================
def neighbours(save: Save, tag: str | None = None, gd: GameData | None = None, detail: int = 0, limit: int | None = None) -> str:
    x = _ctx(save, gd)
    out = Out(detail, limit)
    tag = x.resolve_tag(tag)
    me = save.country(tag) or {}
    nb = aslist(me.get("neighbours"))  # countries.<TAG>.neighbours: bordering tags, precomputed by the game
    if not nb:
        return f"{x.c(tag)}: the save lists no neighbours for this country ({save.date})."
    my_dev = me.get("raw_development", 0) or 1
    _, my_regs, _, _ = _army_summary(x, me)
    my_n = sum(my_regs.values()) or 1
    myrel = relations(save, tag)
    rows = []
    for o in nb:
        oc = save.country(o)
        if not oc:
            continue
        _, regs, _, ships = _army_summary(x, oc)
        r = relations(save, o)
        flags = []
        if o in _tags(myrel["allies"]):
            flags.append("ally")
        if o in _tags(myrel["rm"]):
            flags.append("RM")
        if o in _tags(myrel["guarantees"]):
            flags.append("we guarantee")
        if o in _tags(myrel["guarantors"]):
            flags.append("guarantees us")
        if o in [t for t, _ in myrel["subjects"]]:
            flags.append("our subject")
        if myrel["overlord"] == o:
            flags.append("our overlord")
        if o in aslist(me.get("current_at_war_with")):
            flags.append("AT WAR")
        if o in _tags(myrel["truces"]):
            flags.append("truce")
        if o in myrel["rivals"]:
            flags.append("our rival")
        if tag in r["rivals"]:
            flags.append("rivals us")
        if any(t == o for _, t, _ in myrel["cb"]):
            flags.append("we have CB")
        if any(t == o for _, t, _ in myrel["cb_against"]):
            flags.append("has CB on us")
        dev = oc.get("raw_development", 0)
        nreg = sum(regs.values())
        al = [x.c(t) for t in _tags(r["allies"]) if t != tag][:3]
        line = f"{x.c(o)}: dev {num(dev, 0)} ({num(dev / my_dev, 1)}x), army {nreg} regs ({num(nreg / my_n, 1)}x)"
        if ships:
            line += f", navy {sum(ships.values())}"
        if r["overlord"]:
            line += f", subject of {r['overlord']}"
        if al:
            line += f", allies {', '.join(al)}"
        if flags:
            line += f" [{', '.join(flags)}]"
        rows.append((dev, line))
    rows.sort(key=lambda e: -e[0])
    out.add(f"Neighbours of {x.c(tag)} {save.date} (ours: dev {num(my_dev, 0)}, army {my_n if my_regs else 0} regs; ratios are theirs/ours; from save key 'neighbours', no image adjacency)")
    out.rows([r for _, r in rows], 14)
    return out.text()


# ======================================================================================
# find
# ======================================================================================
def find(src: Save | GameData, text: str, limit: int = 10) -> str:
    """Resolve a country or province by name fragment (tag or id also accepted)."""
    if isinstance(src, Save):
        gd, extra, s = gamedata(), src.country_names(), src
    else:
        gd, extra, s = src, None, None
    text = text.strip()
    lines = []
    t = text.upper()
    cs = gd.find_countries(text, {k: gd.l(v) for k, v in extra.items()} if extra else None)
    if s is not None:
        cs = [c for c in cs if c[0] in s.countries()]
    for tag, name in cs[:limit]:
        lines.append(f"country {tag} {name}")
    if len(cs) > limit:
        lines.append(f"(+{len(cs) - limit} more countries, raise limit)")
    pid = int(text) if text.isdigit() else None
    if pid is not None:
        n = gd.province_name(pid)
        if n:
            own = s.province_table().get(pid, {}).get("owner") if s else None
            lines.append(f"province {pid} {n}" + (f" owner {own}" if own else ""))
    ps = gd.find_provinces(text) if pid is None else []
    for p, name in ps[:limit]:
        own = s.province_table().get(p, {}).get("owner") if s else None
        lines.append(f"province {p} {name}" + (f" owner {own}" if own else ""))
    if len(ps) > limit:
        lines.append(f"(+{len(ps) - limit} more provinces, raise limit)")
    return "\n".join(lines) if lines else f"no country or province matches '{text}'"


# ======================================================================================
# diff
# ======================================================================================
def _ruler_name(s: Save, tag: str, key: str) -> str | None:
    p = s.ref(tag, key)
    return (p.get("monarch_name") or p.get("name")) if p else None


def diff(old: Save, new: Save, tag: str | None = None, gd: GameData | None = None, detail: int = 0, limit: int | None = None) -> str:
    x = _ctx(new, gd)
    xo = _ctx(old, gd)
    out = Out(detail, limit)
    tag = x.resolve_tag(tag)
    co, cn = old.country(tag) or {}, new.country(tag) or {}
    out.add(f"Changes for {x.c(tag)}: {old.date} -> {new.date}")
    if old.date and new.date and new.date < old.date:
        out.add("WARNING: the second save is older than the first")
    # scalar deltas: treasury, manpower (thousands), sailors, prestige, stability, dev, provinces, inflation, power
    d = []
    for k, label, dig in (("treasury", "treasury", 0), ("manpower", "manpower(k)", 1), ("sailors", "sailors", 0), ("prestige", "prestige", 1), ("stability", "stability", 1), ("raw_development", "dev", 0), ("inflation", "inflation", 2), ("legitimacy", "legitimacy", 0)):
        a, b = co.get(k), cn.get(k)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and round(a, dig) != round(b, dig):
            d.append(f"{label} {num(a, dig)}->{num(b, dig)}")
    po, pn = len(co.get("owned_provinces", [])), len(cn.get("owned_provinces", []))
    if po != pn:
        d.append(f"provinces {po}->{pn}")
    out.add("; ".join(d))
    # tech / ideas
    to, tn = co.get("technology") or {}, cn.get("technology") or {}
    ups = [f"{k[:3]} {to.get(k)}->{v}" for k, v in tn.items() if to.get(k) != v]
    out.items("tech", ups, 6)
    io, inn = co.get("active_idea_groups") or {}, cn.get("active_idea_groups") or {}
    out.items("ideas", [f"{x.l(k)} {io.get(k, 0)}->{v}" for k, v in inn.items() if io.get(k, 0) != v], 6)
    # ruler / heir
    for key, label in (("monarch", "ruler"), ("heir", "heir"), ("queen", "consort")):
        a, b = _ruler_name(old, tag, key), _ruler_name(new, tag, key)
        if a != b:
            out.add(f"{label}: {a} -> {b}")
    # wars: active_war.name sets
    wo = {w.get("name"): w for w in old.wars()}
    wn = {w.get("name"): w for w in new.wars()}
    started = [n for n in wn if n not in wo]
    ended = [n for n in wo if n not in wn]
    near = {tag} | set(aslist(cn.get("neighbours"))) | set(aslist(co.get("neighbours")))

    def involved(w: dict) -> bool:
        return tag in aslist(w.get("attackers")) or tag in aslist(w.get("defenders"))

    def relevant(w: dict) -> bool:
        return bool(near & (set(aslist(w.get("attackers"))) | set(aslist(w.get("defenders")))))

    def wlabel(w: dict, verb: str) -> str:
        return w.get("name", "?") + (f" ({verb})" if involved(w) else "")

    r_start = [wlabel(wn[n], "we are in it") for n in started if relevant(wn[n])]
    r_end = [wlabel(wo[n], "we were in it") for n in ended if relevant(wo[n])]
    out.items("wars started (us/neighbours)", r_start, 6, "; ")
    out.items("wars ended (us/neighbours)", r_end, 6, "; ")
    other = (len(started) - len(r_start), len(ended) - len(r_end))
    if any(other):
        out.add(f"other wars elsewhere: {other[0]} started, {other[1]} ended")
    # relations
    ro, rn = relations(old, tag), relations(new, tag)

    def delta(label: str, a: list, b: list) -> None:
        sa, sb = set(_tags(a)), set(_tags(b))
        if sb - sa:
            out.items(f"new {label}", [x.c(t) for t in sorted(sb - sa)], 8)
        if sa - sb:
            out.items(f"ended {label}", [x.c(t) for t in sorted(sa - sb)], 8)

    delta("alliances", ro["allies"], rn["allies"])
    delta("royal marriages", ro["rm"], rn["rm"])
    delta("guarantees given", ro["guarantees"], rn["guarantees"])
    delta("guarantees received", ro["guarantors"], rn["guarantors"])
    delta("subjects", ro["subjects"], rn["subjects"])
    delta("truces", ro["truces"], rn["truces"])
    delta("rivals", ro["rivals"], rn["rivals"])
    if ro["overlord"] != rn["overlord"]:
        out.add(f"overlord: {ro['overlord']} -> {rn['overlord']}")
    # province ownership: involving us or our neighbours (countries.<TAG>.neighbours in either save)
    watch = {tag} | set(aslist(cn.get("neighbours"))) | set(aslist(co.get("neighbours")))
    tab_o, tab_n = old.province_table(), new.province_table()
    ch = []
    for pid, v in tab_n.items():
        a = tab_o.get(pid, {}).get("owner")
        b = v.get("owner")
        if a != b and (a in watch or b in watch):
            ch.append((0 if tag in (a, b) else 1, pid, a, b))
    ch.sort()
    out.rows([f"{x.p(pid)}: {a or 'none'} -> {b or 'none'}" for _, pid, a, b in ch], 12, f"province ownership changes ({len(ch)}):")
    # armies
    _, ro_, _, so_ = _army_summary(x, co)
    _, rn_, _, sn_ = _army_summary(x, cn)
    if sum(ro_.values()) != sum(rn_.values()) or sum(so_.values()) != sum(sn_.values()):
        out.add(f"army {sum(ro_.values())}->{sum(rn_.values())} regs, navy {sum(so_.values())}->{sum(sn_.values())} ships")
    if len(out.lines) == 1:
        out.add("no notable changes")
    return out.text()
