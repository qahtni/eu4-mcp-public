"""eu4mcp.alerts: one test per rule on small synthetic saves, the block/newly/monthly helpers, eu4_advance's STOPPED
line (fake hook objects), and the whole table over a real snapshot when one exists."""
from pathlib import Path

import pytest

from eu4mcp import alerts as al
from eu4mcp.save import Save

BASE_CAS = """\t\tgovernment={
\t\t\tgovernment="monarchy"
\t\t}
\t\ttreasury=100.000
\t\tstability=1.000
\t\tlegitimacy=90.000
\t\tmanpower=20.000
\t\tmax_manpower=25.000
\t\tpowers={
\t\t\t50 60 70
\t\t}
\t\ttechnology={
\t\t\tadm_tech=3
\t\t\tdip_tech=3
\t\t\tmil_tech=3
\t\t}
\t\tactive_idea_groups={
\t\t\tCAS_ideas=0
\t\t}
\t\towned_provinces={
\t\t\t219 4548
\t\t}
\t\tcore_provinces={
\t\t\t219 4548
\t\t}
\t\tmonarch={
\t\t\tid=10
\t\t\ttype=48
\t\t}
\t\their={
\t\t\tid=11
\t\t\ttype=48
\t\t}
\t\thistory={
\t\t\t1418.6.2={
\t\t\t\tmonarch={
\t\t\t\t\tid={
\t\t\t\t\t\tid=10
\t\t\t\t\t\ttype=48
\t\t\t\t\t}
\t\t\t\t\tname="Juan II"
\t\t\t\t\tbirth_date=1405.3.6
\t\t\t\t}
\t\t\t}
\t\t\t1425.1.5={
\t\t\t\their={
\t\t\t\t\tid={
\t\t\t\t\t\tid=11
\t\t\t\t\t\ttype=48
\t\t\t\t\t}
\t\t\t\t\tname="Enrique"
\t\t\t\t\tbirth_date=1425.1.5
\t\t\t\t}
\t\t\t}
\t\t}
"""


def make_save(tmp_path: Path, cas: str = "", extra_countries: str = "", provinces: str = "", top: str = "",
              date: str = "1457.7.22", name: str = "s.eu4", base: str = BASE_CAS) -> Save:
    """A minimal plain-text save: CAS owns Madrid(219) and Huelva(4548); ``cas`` adds/overrides country lines
    (later keys win in the parser)."""
    import re

    def chunks(text):  # key -> lines of a country-level entry (two tabs deep)
        out, cur = {}, None
        for ln in text.splitlines(keepends=True):
            m = re.match(r"\t\t(\w+)=", ln)
            if m and not ln.startswith("\t\t\t"):
                cur = m[1] if m[1] not in out else f"{m[1]}#{len(out)}"
                out[cur] = ""
            out[cur] += ln
        return out

    over = chunks(cas)
    keys = {k.split("#")[0] for k in over}
    base = "".join(v for k, v in chunks(base).items() if k.split("#")[0] not in keys)
    cas = "".join(over.values())
    prov = provinces or ('-219={\n\t\tname="Madrid"\n\t\towner="CAS"\n\t\tcontroller="CAS"\n\t}\n'
                         '-4548={\n\t\tname="Huelva"\n\t\towner="CAS"\n\t\tcontroller="CAS"\n\t}\n')
    txt = (f'EU4txt\ndate={date}\nplayer="CAS"\nprovinces={{\n{prov}}}\n'
           f'countries={{\n\tCAS={{\n{base}{cas}\t}}\n{extra_countries}}}\n{top}')
    p = tmp_path / name
    p.write_text(txt, encoding="utf-8")
    return Save(p)


def rules(alerts):
    return {a.rule: a for a in alerts}


def run(s, prev=None, live=None, journal=None):
    return rules(al.evaluate(s, prev, live=live, journal=journal))


def test_clean_save_has_no_alerts(tmp_path):
    assert run(make_save(tmp_path)) == {}


# ---- STOP rules -----------------------------------------------------------------------------------------------------
def test_occupied(tmp_path):
    s = make_save(tmp_path, provinces='-219={\n\t\tname="Madrid"\n\t\towner="CAS"\n\t\tcontroller="CAS"\n\t}\n'
                                     '-4548={\n\t\tname="Huelva"\n\t\towner="CAS"\n\t\tcontroller="REB"\n\t}\n')
    a = run(s)["occupied"]
    assert a.level == al.STOP and "Huelva(4548) held by REB" in a.line()


def test_pace(tmp_path):
    assert al.pace(make_save(tmp_path), "CAS") == (3, "peace")
    war = 'active_war={\n\tname="W"\n\tattackers={\n\t\t"ARA"\n\t}\n\tdefenders={\n\t\t"CAS"\n\t}\n}\n'
    assert al.pace(make_save(tmp_path, top=war, name="w.eu4"), "CAS") == (1, "at war")
    other = war.replace('"CAS"', '"FRA"')
    assert al.pace(make_save(tmp_path, top=other, name="o.eu4"), "CAS") == (3, "peace")
    occ = '-4548={\n\t\tname="Huelva"\n\t\towner="CAS"\n\t\tcontroller="REB"\n\t}\n'
    assert al.pace(make_save(tmp_path, provinces=occ, name="p.eu4"), "CAS") == (1, "provinces occupied")
    reb = '\tREB={\n\t\tarmy={\n\t\t\tid={\n\t\t\t\tid=7\n\t\t\t\ttype=54\n\t\t\t}\n\t\t\tlocation=219\n\t\t\tregiment={\n\t\t\t\tstrength=1.0\n\t\t\t}\n\t\t}\n\t}\n'
    assert al.pace(make_save(tmp_path, extra_countries=reb, name="r.eu4"), "CAS") == (1, "rebels")


def test_rebels(tmp_path):
    reb = '\tREB={\n\t\tarmy={\n\t\t\tid={\n\t\t\t\tid=7\n\t\t\t\ttype=54\n\t\t\t}\n\t\t\tname="1st Noble Army"\n\t\t\tlocation=219\n' \
          '\t\t\tregiment={\n\t\t\t\tstrength=1.0\n\t\t\t}\n\t\t\tregiment={\n\t\t\t\tstrength=1.0\n\t\t\t}\n\t\t}\n' \
          '\t\tarmy={\n\t\t\tid={\n\t\t\t\tid=8\n\t\t\t\ttype=54\n\t\t\t}\n\t\t\tlocation=1\n\t\t}\n\t}\n'
    a = run(make_save(tmp_path, extra_countries=reb))["rebels"]
    assert a.items == [(7, "2 regs in Madrid(219)")]


def test_money_treasury_and_loans(tmp_path):
    assert "treasury -60" in run(make_save(tmp_path, "\t\ttreasury=-60.000\n"))["money"].line()
    loans = "".join(f"\t\tloan={{\n\t\t\tamount=143\n\t\t\tinterest=4.0\n\t\t}}\n" for _ in range(3))
    assert "3 loans" in run(make_save(tmp_path, loans))["money"].line()
    assert "money" not in run(make_save(tmp_path, loans[: len(loans) * 2 // 3]))


def test_war_on_us_needs_new_war(tmp_path):
    war = 'active_war={\n\tname="Aragonese Conquest of Rioja"\n\tattackers={\n\t\t"ARA"\n\t}\n\tdefenders={\n\t\t"CAS"\n\t}\n\toriginal_attacker="ARA"\n}\n'
    old = make_save(tmp_path, name="old.eu4")
    cur = make_save(tmp_path, top=war)
    assert "war_on_us" not in run(cur)  # no previous snapshot: cannot tell
    a = run(cur, old)["war_on_us"]
    assert "Rioja (attacker ARA)" in a.line()
    assert "war_on_us" not in run(cur, make_save(tmp_path, top=war, name="old2.eu4"))


def test_ruler_new_regency_no_heir(tmp_path):
    old = make_save(tmp_path, name="old.eu4")
    new = make_save(tmp_path, "\t\tmonarch={\n\t\t\tid=11\n\t\t\ttype=48\n\t\t}\n")
    a = run(new, old)["ruler"]
    assert "new ruler Enrique" in a.line()
    young = make_save(tmp_path, "\t\tmonarch={\n\t\t\tid=11\n\t\t\ttype=48\n\t\t}\n", date="1430.1.1", name="y.eu4")
    assert "regency" in run(young)["ruler"].line()
    noheir = make_save(tmp_path, base=BASE_CAS.replace("\t\their={\n\t\t\tid=11\n\t\t\ttype=48\n\t\t}\n", ""), name="n.eu4")
    assert "no heir, ruler aged 52" in run(noheir)["ruler"].line()
    young_noheir = make_save(tmp_path, base=BASE_CAS.replace("\t\their={\n\t\t\tid=11\n\t\t\ttype=48\n\t\t}\n", ""), date="1440.1.1", name="n2.eu4")
    assert "ruler" not in run(young_noheir)  # ruler 34: no heir is not urgent


def test_stability(tmp_path):
    assert "stability" in run(make_save(tmp_path, "\t\tstability=-2.000\n"))
    assert "stability" not in run(make_save(tmp_path, "\t\tstability=-1.000\n"))


def test_legitimacy(tmp_path):
    assert "legitimacy 65" in run(make_save(tmp_path, "\t\tlegitimacy=65.000\n"))["legitimacy"].line()


def test_disaster(tmp_path, monkeypatch):
    monkeypatch.setattr(al, "_DISASTERS", ["a", "peasant_war", "c"])
    a = run(make_save(tmp_path, "\t\tdisaster_progress={\n\t\t\t0.000 55.000 49.000\n\t\t}\n"))["disaster"]
    assert a.items == [(1, "peasant_war 55%")]


def test_estates_save_and_live(tmp_path):
    est = '\t\testate={\n\t\t\ttype="estate_nobles"\n\t\t\tloyalty=20.000\n\t\t}\n\t\testate={\n\t\t\ttype="estate_church"\n\t\t\tloyalty=40.000\n\t\t}\n'
    s = make_save(tmp_path, est)
    assert run(s)["estates"].items == [(("loyalty", "nobles"), "nobles loyalty 20")]
    live = {"estates": [{"key": "estate_nobles", "loyalty": 30, "influence": 100.0}]}
    assert run(s, live=live)["estates"].items == [(("influence", "nobles"), "nobles influence 100")]


def test_coalition(tmp_path):
    a = run(make_save(tmp_path, '\t\tcoalition_against_us={\n\t\t\t"MOR" "GRA"\n\t\t}\n'))["coalition"]
    assert a.level == al.STOP and a.items == [("MOR", "MOR"), ("GRA", "GRA")]


def test_live_window_stuck_save_lag(tmp_path):
    s = make_save(tmp_path)
    r = run(s, live={"windows": ["CRepayBankLoanDialog"], "stuck": "date stayed 1457.07.22 (timeout)",
                     "save_lag": ("1457.7.22", "1457.08.30")})
    assert "CRepayBankLoanDialog" in r["window_open"].line()
    assert "date stayed" in r["game_stuck"].line()
    assert "save 1457.7.22" in r["save_stale"].line()
    assert "save_stale" not in run(s, live={"save_lag": ("1457.7.22", "1457.07.23")})


# ---- INFO rules -----------------------------------------------------------------------------------------------------
def test_war_exhaustion(tmp_path):
    assert run(make_save(tmp_path, "\t\twar_exhaustion=6.500\n"))["war_exhaustion"].level == al.INFO


def test_manpower(tmp_path):
    assert "manpower 2.0k of 25.0k" in run(make_save(tmp_path, "\t\tmanpower=2.000\n"))["manpower"].line()


def test_power(tmp_path):
    a = run(make_save(tmp_path, "\t\tpowers={\n\t\t\t410 20 999\n\t\t}\n"))["power"]
    assert a.items == [("ADM", "ADM 410"), ("MIL", "MIL 999")]


def test_idea_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(al, "_BASIC_GROUPS", {"quality_ideas", "economic_ideas"})
    tech7 = "\t\ttechnology={\n\t\t\tadm_tech=7\n\t\t}\n"
    assert "2 free idea group slot(s)" in run(make_save(tmp_path, tech7))["idea_slot"].line()
    one = "\t\tactive_idea_groups={\n\t\t\tCAS_ideas=1\n\t\t\tquality_ideas=2\n\t\t}\n"
    assert "1 free" in run(make_save(tmp_path, tech7 + one))["idea_slot"].line()
    assert "idea_slot" not in run(make_save(tmp_path))  # adm tech 3: no slot yet


def test_advisor(tmp_path):
    assert run(make_save(tmp_path), live={"advisors": ["DIP"]})["advisor"].items == [("DIP", "DIP empty")]


def test_idle_envoys(tmp_path):
    env = '\t\tdiplomats={\n\t\t\tenvoy={\n\t\t\t\taction=2\n\t\t\t\tname="A"\n\t\t\t}\n\t\t\tenvoy={\n\t\t\t\tname="B"\n\t\t\t}\n\t\t}\n' \
          '\t\tmerchants={\n\t\t\tenvoy={\n\t\t\t\taction=2\n\t\t\t\tname="C"\n\t\t\t}\n\t\t}\n'
    assert run(make_save(tmp_path, env))["idle_envoys"].items == [("diplomats", "1 diplomats")]


def test_inflation(tmp_path):
    assert "inflation 3.5" in run(make_save(tmp_path, "\t\tinflation=3.500\n"))["inflation"].line()


def test_overextension(tmp_path):
    assert "overextension 60%" in run(make_save(tmp_path, "\t\toverextension_percentage=0.600\n"))["overextension"].line()
    assert "overextension" not in run(make_save(tmp_path, "\t\toverextension_percentage=0.344\n"))


def test_uncored(tmp_path):
    a = run(make_save(tmp_path, "\t\tcore_provinces={\n\t\t\t219\n\t\t}\n"))["uncored"]
    assert a.items == [(4548, "Huelva(4548)")]


def test_liberty(tmp_path):
    sub = '\tGAL={\n\t\tcached_liberty_desire=62.000\n\t}\n'
    top = 'diplomacy={\n\tdependency={\n\t\tfirst="CAS"\n\t\tsecond="GAL"\n\t\tsubject_type="vassal"\n\t}\n}\n'
    assert run(make_save(tmp_path, extra_countries=sub, top=top))["liberty"].items == [("GAL", "GAL 62%")]


def test_refused(tmp_path):
    j = [{"kind": "act", "tool": "economy", "args": {"action": "develop", "province": 224}, "ok": False}] * 3 + \
        [{"kind": "act", "tool": "economy", "args": {"action": "build"}, "ok": False}]
    a = run(make_save(tmp_path), journal=j)["refused"]
    assert len(a.items) == 1 and "develop" in a.line() and "x3" in a.line()


# ---- helpers --------------------------------------------------------------------------------------------------------
def test_block_order_and_limit(tmp_path):
    s = make_save(tmp_path, "\t\tstability=-3.000\n\t\tinflation=9.000\n\t\twar_exhaustion=9.000\n")
    out = al.evaluate(s, live={"windows": [f"W{i}" for i in range(2)], "advisors": ["ADM"], "stuck": "x", "save_lag": ("1457.1.1", "1457.7.22")})
    assert [a.level for a in out] == sorted((a.level for a in out), key=lambda v: v != al.STOP)  # STOP first
    b = al.block(out, max_lines=4).splitlines()
    assert b[0] == "ALERTS:" and len(b) == 5 and b[-1].startswith("+") and b[-1].endswith("eu4_alerts")
    assert all(len(x) <= al.LINE_CHARS + 2 for x in b)
    assert al.block([]) == ""


def test_newly_stopping(tmp_path):
    occ = '-219={\n\t\tname="Madrid"\n\t\towner="CAS"\n\t\tcontroller="ARA"\n\t}\n-4548={\n\t\tname="Huelva"\n\t\towner="CAS"\n\t\tcontroller="CAS"\n\t}\n'
    both = occ.replace('controller="CAS"', 'controller="ARA"')
    old = al.evaluate(make_save(tmp_path, provinces=occ, name="o.eu4"))
    same = al.evaluate(make_save(tmp_path, provinces=occ, name="c.eu4"))
    more = al.evaluate(make_save(tmp_path, provinces=both, name="m.eu4"))
    assert al.newly_stopping(same, old) == []
    assert [a.rule for a in al.newly_stopping(more, old)] == ["occupied"]
    assert [a.rule for a in al.newly_stopping(same, [])] == ["occupied"]


def test_monthly_info_throttle(tmp_path, monkeypatch):
    monkeypatch.setenv("EU4_MCP_STATE", str(tmp_path / "st"))
    s = make_save(tmp_path, "\t\tinflation=9.000\n\t\tstability=-3.000\n")
    a = al.evaluate(s)
    assert {x.rule for x in al.monthly(a, s.date)} == {"stability", "inflation"}
    assert {x.rule for x in al.monthly(a, s.date)} == {"stability"}  # INFO already shown this month
    assert {x.rule for x in al.monthly(a, "1457.08.01")} == {"stability", "inflation"}


def test_broken_rule_is_skipped(tmp_path):
    def boom(f):
        raise KeyError("x")
    rs = [al.Rule("boom", al.STOP, boom, "-"), al.Rule("stability", al.STOP, al._stability, "-")]
    assert [a.rule for a in al.evaluate(make_save(tmp_path, "\t\tstability=-3.000\n"), rules=rs)] == ["stability"]


# ---- eu4_advance: STOPPED line ----------------------------------------------------------------------------------------
class FakeClient:
    def __init__(self, save_path, date="1457.07.22"):
        self.save_path, self.date, self.paused = save_path, date, True

    def request(self, op, timeout=None, **kw):
        return {"date": self.date}

    def info(self):
        return {"date": self.date, "player": "CAS", "ingame": True}

    def pause(self, on):
        self.paused = on

    def speed(self, v):
        pass

    def wait(self, until, timeout=0, pause=True, **kw):
        self.date = until
        return {"date": until, "reason": "reached"}

    def save(self):
        return str(self.save_path)


class FakeLive:
    def __init__(self, c):
        self.c = c

    def info(self):
        return self.c.info()

    def player(self):
        return "CAS"

    def events(self, tag=None):
        return []

    def country(self, tag):
        return {"treasury": 100.0, "manpower": 1000, "stability": 1.0, "prestige": 0, "adm": 0, "dip": 0, "mil": 0}

    def open_windows(self):
        return []

    def advisor_slots(self, tag):
        return []


def test_advance_says_stopped_on_new_stop_alert(tmp_path, monkeypatch):
    for k in ("EU4_MCP_STATE", "EU4_MCP_NOTES"):
        monkeypatch.setenv(k, str(tmp_path / k))
    from eu4mcp import server

    old = make_save(tmp_path, name="old.eu4", date="1457.7.22")
    occ = '-219={\n\t\tname="Madrid"\n\t\towner="CAS"\n\t\tcontroller="CAS"\n\t}\n-4548={\n\t\tname="Huelva"\n\t\towner="CAS"\n\t\tcontroller="REB"\n\t}\n'
    new = make_save(tmp_path, provinces=occ, name="new.eu4", date="1457.8.1")
    c = FakeClient(new.path)
    monkeypatch.setattr(server.ST, "hook", c)
    monkeypatch.setattr(server.ST, "current", old.path)
    monkeypatch.setattr(server.ST, "previous", None)
    monkeypatch.setattr(server.ST, "loaded_snapshots", True)
    monkeypatch.setattr(server, "_live", lambda: FakeLive(c))
    out = server.eu4_advance(days=10)
    first = out.splitlines()[0]
    assert first.startswith("STOPPED: alert occupied"), out
    assert "ALERTS:\nSTOP occupied: Huelva(4548) held by REB" in out
    # the same state again: still listed, but not a new STOP (snapshot names have 1 s resolution)
    import time
    time.sleep(1.1)
    c.save_path = make_save(tmp_path, provinces=occ, name="new2.eu4", date="1457.8.11").path
    out2 = server.eu4_advance(days=10)
    assert not out2.startswith("STOPPED") and "STOP occupied" in out2
    st = server.eu4_alerts()
    assert "STOP occupied" in st


# ---- real snapshot ----------------------------------------------------------------------------------------------------
def test_whole_table_on_real_snapshot():
    d = Path("~/.config/eu4-mcp/snapshots").expanduser()
    snaps = sorted(d.glob("snap_*.eu4")) if d.is_dir() else []
    if not snaps:
        pytest.skip("no real snapshot")
    try:
        cur = Save(snaps[-1])
        prev = Save(snaps[-2]) if len(snaps) > 1 else None
    except (OSError, Exception) as e:  # rotated away while reading
        pytest.skip(f"snapshot unreadable: {e}")
    out = al.evaluate(cur, prev)
    for a in out:
        assert a.level in (al.STOP, al.INFO) and a.items and a.todo
    b = al.block(out)
    assert b == "" or (b.startswith("ALERTS:") and len(b.splitlines()) <= al.MAX_LINES + 1)
    print("\n" + (b or "no alerts"))
