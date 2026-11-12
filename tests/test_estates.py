"""Offline checks of eu4mcp.estates against a scripted stand-in for HookClient.vm (no game, no socket)."""
import pytest

from eu4mcp import estates as es
from eu4mcp.hookclient import HookError

COUNTRY, EST, T0, T1, PRIVS0, P1, P2, P3 = 0x200000, 0x300000, 0x400000, 0x401000, 0x410000, 0x500000, 0x501000, 0x502000
MODS, CLDB, CLDATA, AGENDA, ACTIVE, OFFER, OFFERS = 0x700000, 0x600000, 0x610000, 0x800000, 0x810000, 0x820000, 0x830000
TAGV = int.from_bytes(b"POR\0" + (5).to_bytes(2, "little") + b"\0\x01", "little")


class StubHook:
    """Evaluates vm steps against a fake memory; ``calls`` maps a symbol to f(*args) -> value; records requests."""

    def __init__(self, mem=None, strs=None, calls=None, valid=1):
        self.mem, self.strs, self.calls = dict(mem or {}), dict(strs or {}), dict(calls or {})
        self.valid, self.reqs, self.next_new, self.next_buf = valid, [], 0xB000, 0xD000

    def _op(self, tok, regs):
        cut = next((i for i in range(1, len(tok)) if tok[i] in "+-"), None)
        base, off = (tok, 0) if cut is None else (tok[:cut], int(tok[cut:], 0))
        if base.startswith("r") and base[1:].isdigit():
            v = regs[int(base[1:])]
        elif base.startswith("@"):
            v = 0x9990000
        else:
            v = int(base, 0)
        return v + off

    def vm(self, *steps):
        self.reqs.append(list(steps))
        out, regs = [], []
        for s in steps:
            t = s.split()
            op, v, res = t[0], 0, None
            if op in ("mkstr", "buf"):
                self.next_buf += 0x40
                v = self.next_buf
            elif op == "new":
                self.next_new += 0x100
                v = self.next_new
            elif op == "call":
                f = self.calls.get(t[1])
                v = f(*[self._op(x, regs) for x in t[2:]]) if f else 0
            elif op == "vcall":
                v = self.valid if t[2] == "0x48" else 0
            elif op in ("rd1", "rd4", "rd8", "rs4"):
                v = self.mem.get(self._op(t[1], regs), 0)
            elif op in ("wr4", "wr8"):
                self.mem[self._op(t[1], regs)] = self._op(t[2], regs)
            elif op == "str":
                res = self.strs.get(self._op(t[1], regs), "")
            elif op == "post":
                v = 1
            out.append(v if res is None else res)
            regs.append(v)
        return out


class FakeLive:
    def __init__(self, events=()):
        self._events, self.closed = list(events), []

    def country_ptr(self, tag):
        return COUNTRY

    def events(self, tag=None):
        return list(self._events)

    def close_event_windows(self, pid=None):
        self.closed.append(pid)
        return 1


def _world(**kw):
    e0, e1 = EST, EST + es.ESTATE_SIZE
    mem = {COUNTRY + 0x1C: TAGV, COUNTRY + es.C_ESTATES: EST, COUNTRY + es.C_ESTATES + 8: EST + 2 * es.ESTATE_SIZE,
           e0 + es.E_TYPE: T0, e0 + es.E_LOYALTY: 45000, e0 + es.E_LAND: 30000,
           e1 + es.E_TYPE: T1, e1 + es.E_LOYALTY: (-5000) & 0xFFFFFFFF, e1 + es.E_LAND: 20000,
           T0 + es.T_INDEX: 5, T0 + es.T_PRIVS: PRIVS0, T0 + es.T_NPRIVS: 3, T1 + es.T_INDEX: 6,
           PRIVS0: P1, PRIVS0 + 8: P2, PRIVS0 + 16: P3, P1 + es.PR_INDEX: 10, P2 + es.PR_INDEX: 11, P3 + es.PR_INDEX: 12,
           CLDB + es.CL_DATA: CLDATA, CLDB + es.CL_COUNT: 3, COUNTRY + es.C_ACTIVE_AGENDA: 0,
           COUNTRY + es.C_OFFERED: OFFERS, COUNTRY + es.C_NOFFERED: 1, OFFERS: OFFER, OFFER: AGENDA, OFFER + es.AA_ESTATE: T0}
    strs = {T0 + es.T_KEY: "estate_nobles", T1 + es.T_KEY: "estate_burghers", P1 + es.PR_KEY: "estate_nobles_land_rights",
            P2 + es.PR_KEY: "estate_nobles_officer_corp", P3 + es.PR_KEY: "estate_nobles_nobility_primacy",
            AGENDA + es.AGENDA_KEY: "estate_nobles_build_fort"}
    for i, k in enumerate(("sale_of_titles", "summon_the_diet", "seize_land")):
        mem[CLDATA + i * es.CL_SIZE + es.CL_INDEX] = i
        strs[CLDATA + i * es.CL_SIZE + es.CL_KEY] = k
    h = StubHook(mem, strs, **kw)

    def sret(val):
        def f(res, *a):
            h.mem[res] = val
            return res
        return f

    h.calls.update({
        es.GET_MODIFIERS: lambda cd: MODS if cd == COUNTRY + es.C_CACHED else 0,
        es.INFLUENCE: lambda res, est, mod: (h.mem.__setitem__(res, 60000 if est == EST else 150000), res)[1]
        if mod == MODS + 8 else pytest.fail("wrong modifier"),
        es.CROWN_SHARE: sret(50000),
        es.HAS_PRIV: lambda est, p: int(est == EST and p == P1),
        es.PRIV_VALID: lambda p, scope, why: 1, es.SCOPE_CTOR: lambda *a: 0, es.SCOPE_SETTAG: lambda *a: 0,
        es.CAN_GRANT: lambda est, tagp, p, why: int(h.mem.get(tagp) == TAGV and p == P2),
        es.CAN_REVOKE: lambda est, tv, p, b, why: int(tv == TAGV and b == 0),
        es.CL_DB: lambda: CLDB,
        es.CL_CAN: lambda c, el, why: int(el != CLDATA + 2 * es.CL_SIZE),
    })
    return h


def test_list_reads_estates_privileges_crown_land_and_agendas():
    h = _world()
    st = es.Estates(h, FakeLive()).list("POR")
    n, b = st["estates"]
    assert (n["key"], n["loyalty"], n["influence"], n["land"]) == ("estate_nobles", 45.0, 60.0, 30.0)
    assert (b["key"], b["loyalty"], b["influence"]) == ("estate_burghers", -5.0, 100.0)   # clamped like the GUI
    assert n["granted"] == ["estate_nobles_land_rights"] and n["grantable"] == ["estate_nobles_officer_corp"]
    assert b["granted"] == [] and b["grantable"] == []
    assert st["crown_land"] == 50.0 and st["active_agenda"] is None
    assert st["offered_agendas"] == [{"agenda": "estate_nobles_build_fort", "estate": "estate_nobles", "date": 0}]
    assert [(a["key"], a["can_use"]) for a in st["actions"]] == [("sale_of_titles", True), ("summon_the_diet", True), ("seize_land", False)]


def _cmd_req(h):
    return [r for r in h.reqs if any(s.startswith("new") for s in r)][-1]


def test_grant_dry_builds_the_inlined_command_and_deletes_it():
    h = _world()
    res = es.Estates(h, FakeLive()).grant_privilege("POR", "nobles", "officer_corp", dry=True)
    assert res["valid"] and not res["posted"] and res["can_grant"]
    r = _cmd_req(h)
    o = "r0"
    assert r[:5] == [f"new {es.CMD_SIZE:#x}", f"wr4 {o}+0x8 0x165", f"call {es.CSTRING_CTOR} {o}+0x10",
                     f"wr8 {o}+0x30 0xffffffff", f"wr8 {o}+0x3c 0xffff0000397a"]
    assert r[5:8] == [f"wr4 {o}+0x48 11", f"wr4 {o}+0x4c 5", f"wr8 {o}+0x50 {TAGV:#x}"]
    assert r[8:] == [f"wr8 {o} {es.GRANT_VT}+0x10", f"vcall {o} 0x48"]
    assert h.reqs[-1][0].startswith("del ")


def test_grant_refuses_before_building_when_not_grantable():
    h = _world()
    e = es.Estates(h, FakeLive())
    with pytest.raises(HookError, match="cannot grant"):
        e.grant_privilege("POR", "estate_nobles", "estate_nobles_nobility_primacy")
    with pytest.raises(HookError, match="already granted"):
        e.grant_privilege("POR", "nobles", "land_rights")
    with pytest.raises(HookError, match="no estate"):
        e.grant_privilege("POR", "church", "x")
    assert not any(s.startswith("new") for r in h.reqs for s in r)


def test_revoke_posts_with_revoke_vtable_and_id():
    h = _world()
    res = es.Estates(h, FakeLive()).revoke_privilege("POR", "nobles", "land_rights")
    assert res["posted"]
    r = _cmd_req(h)
    assert "wr8 r0+0x3c 0xffff0000397c" in r and "wr4 r0+0x48 10" in r and r[-2] == f"wr8 r0 {es.REVOKE_VT}+0x10"
    assert h.reqs[-1][0].startswith("post ")


def test_refused_by_isvalid_is_deleted():
    h = _world(valid=0)
    with pytest.raises(HookError, match="refuses"):
        es.Estates(h, FakeLive()).sell_titles("POR")
    assert h.reqs[-1][0].startswith("del ")


def test_crown_land_actions():
    h = _world()
    e = es.Estates(h, FakeLive())
    e.summon_diet("POR")
    r = _cmd_req(h)
    assert r[4] == "wr8 r0+0x3c 0xffff0000397d" and r[5:7] == ["wr4 r0+0x48 1", f"wr8 r0+0x4c {TAGV:#x}"]
    assert r[-2] == f"wr8 r0 {es.CROWN_VT}+0x10"
    with pytest.raises(HookError, match="not possible"):
        e.seize_land("POR")
    assert e.seize_land("POR", dry=True)["can_use"] is False


def test_choose_agenda_answers_the_diet_event():
    ev = {"pid": 77, "id": es.DIET_EVENT, "tag": "POR", "event": 0x900000, "scope": 0x910000, "n": 15, "opts": 0x920000}
    h = _world()
    for i in range(15):
        h.mem[0x920000 + 8 * i] = 0x930000 + i
    h.calls[es.OPT_ALLOWED] = lambda opt, scope: int(opt - 0x930000 in (4, 13))   # nobles, qizilbash
    live = FakeLive([ev])
    e = es.Estates(h, live)
    e._diet_estates = lambda: es.DIET_OPTIONS
    d = e.diet_options("POR")
    assert [(o["option"], o["estate"], o["agenda"]) for o in d["options"]] == [
        (5, "estate_nobles", "estate_nobles_build_fort"), (14, "estate_qizilbash", None)]
    res = e.choose_agenda("POR", "nobles")
    assert res["posted"] and live.closed == [77]
    assert _cmd_req(h)[1] == f"call {es.EVENT_CMD} r0 {0x900000} {0x910000} 4 77"
    with pytest.raises(HookError, match="offers"):
        e.choose_agenda("POR", "burghers")


def test_diet_option_order_matches_game_file():
    keys = es.Estates(StubHook(), FakeLive())._diet_estates()
    assert keys[:len(es.DIET_OPTIONS)] == es.DIET_OPTIONS
