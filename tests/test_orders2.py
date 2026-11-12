"""Offline checks of eu4mcp.orders2 against a scripted stand-in for HookClient.vm (no game, no socket)."""
import pytest

from eu4mcp import orders2 as o2
from eu4mcp.hookclient import HookError

GS, COUNTRY, MAP, MAPOBJ = 0x100000, 0x200000, 0x7000, 0x7100


def _tagv(t: str, idx: int = 5) -> int:
    return int.from_bytes(t.encode() + b"\0" + idx.to_bytes(2, "little") + b"\x01", "little")


class StubHook:
    """Evaluates vm steps against a fake memory; ``calls`` maps a symbol to f(*args) -> value; records requests."""

    def __init__(self, mem=None, strs=None, calls=None, clists=None, valid=1):
        self.mem, self.strs, self.calls, self.clists = dict(mem or {}), dict(strs or {}), dict(calls or {}), dict(clists or {})
        self.valid, self.reqs, self.next_new, self.next_buf = valid, [], 0xB000, 0xD000

    def _op(self, tok, regs):
        cut = next((i for i in range(1, len(tok)) if tok[i] in "+-"), None)
        base, off = (tok, 0) if cut is None else (tok[:cut], int(tok[cut:], 0))
        if base.startswith("r") and base[1:].isdigit():
            v = regs[int(base[1:])]
        elif base.startswith("@"):
            v = MAP if base == o2.MAP_PTR else 0x9999
        else:
            v = int(base, 0)
        return v + off

    def vm(self, *steps):
        self.reqs.append(list(steps))
        out, regs = [], []
        for s in steps:
            t = s.split()
            op, v, res = t[0], 0, None
            if op == "tag":
                v = _tagv(t[1])
            elif op == "gs":
                v = GS
            elif op == "mkstr":
                v = self.next_buf = self.next_buf + 0x40
            elif op == "buf":
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
            elif op == "str":
                res = self.strs.get(self._op(t[1], regs), "")
            elif op == "clist":
                res = self.clists.get(self._op(t[1], regs), [])
            elif op == "post":
                v = 1
            out.append(v if res is None else res)
            regs.append(v)
        return out


class FakeLive:
    def __init__(self, units):
        self._units = units

    def country_ptr(self, tag):
        return COUNTRY

    def units(self, tag):
        return list(self._units)

    def unit(self, tag, uid):
        for u in self._units:
            if u["id"] == uid:
                return u
        raise HookError("no unit")

    def _prov(self, province):
        return ["gs", f"call {o2.GET_PROVINCE} r0 {int(province)}"]


NAVY = {"ptr": 0x5000, "id": 7, "name": "Fleet", "navy": True, "loc": 1200}
ARMY = {"ptr": 0x6000, "id": 8, "name": "Army", "navy": False, "loc": 800}


def test_explore_sea_builds_toggle_command_and_frees_vectors():
    coast_buf, sea_buf, regions = 0x40000, 0x41000, 0x42000
    mem = {COUNTRY + 0x1C: _tagv("POR"), MAP: MAPOBJ, MAPOBJ + 0x510: regions, MAPOBJ + 0x518: regions + 8 * 50,
           coast_buf: 11, sea_buf: 22, sea_buf + 4: 23}
    strs = {}
    for i in (11, 22, 23):
        mem[regions + 8 * i] = 0x50000 + i * 0x100
        strs[0x50000 + i * 0x100 + 0x10] = f"region_{i}"
        strs[0x50000 + i * 0x100 + 0x30] = f"Region {i}"
    h = StubHook(mem, strs)

    def sel(c, navy, coast, sea):  # fill the two std::vector<int> like the game
        assert (c, navy) == (COUNTRY, NAVY["ptr"])
        h.mem.update({coast: coast_buf, coast + 8: coast_buf + 4, sea: sea_buf, sea + 8: sea_buf + 8})
        return 0

    h.calls[o2.SELECTABLE_REGIONS] = sel
    d = o2.Orders2(h, FakeLive([NAVY, ARMY]))
    regs = d.explore_regions("POR", 7)
    assert [x["key"] for x in regs["coast"]] == ["region_11"] and [x["id"] for x in regs["sea"]] == [22, 23]
    frees = [s for r in h.reqs for s in r if s.startswith(f"call {o2.OP_DELETE}")]
    assert frees == [f"call {o2.OP_DELETE} {coast_buf}", f"call {o2.OP_DELETE} {sea_buf}"]

    res = d.explore("POR", 7, "sea", "region_23", dry=True)
    assert res == {"what": "explore sea region_23 with Fleet", "valid": True, "posted": False}
    cmd_req = [r for r in h.reqs if any(s.startswith("new") for s in r)][-1]
    assert cmd_req[0] == f"new {o2.EXPLORE_SIZE:#x}"
    assert cmd_req[1] == f"call {o2.EXPLORE_CMD} r0 {NAVY['ptr']} 0x36 23 2"
    assert h.reqs[-1][0].startswith("del ")
    with pytest.raises(HookError, match="not explorable"):
        d.explore("POR", 7, "coast", "region_23")


def test_explore_land_uses_location_and_kind_checks():
    h = StubHook()
    d = o2.Orders2(h, FakeLive([NAVY, ARMY]))
    d.explore("POR", 8, "land")
    assert h.reqs[-2][1] == f"call {o2.EXPLORE_CMD} r0 {ARMY['ptr']} 0x36 800 1" and h.reqs[-1][0].startswith("post")
    with pytest.raises(HookError, match="army"):
        d.explore("POR", 8, "sea", 3)
    with pytest.raises(HookError, match="fleet"):
        d.explore("POR", 7, "land")


def test_refused_command_is_deleted_not_posted():
    h = StubHook(valid=0)
    d = o2.Orders2(h, FakeLive([NAVY]))
    with pytest.raises(HookError, match="refuses"):
        d.stop_mission("POR", 7)
    assert h.reqs[-1][0].startswith("del ") and not any(s.startswith("post") for r in h.reqs for s in r)


def test_leaders_decode_and_assign():
    lp = 0x30000
    mem = {lp + o2.L_ID: 41, lp + o2.L_TYPE: 3, lp + o2.L_MANEUVER: 4, lp + o2.L_FIRE: 1, lp + o2.L_UNIT: NAVY["ptr"]}
    h = StubHook(mem, {lp + o2.L_NAME: "Vasco"}, {o2.LEADER_CAN_REASSIGN: lambda *a: 1},
                 clists={COUNTRY + o2.C_LEADERS: [lp]})
    d = o2.Orders2(h, FakeLive([NAVY, ARMY]))
    ls = d.leaders("POR")
    assert ls[0]["name"] == "Vasco" and ls[0]["type"] == "explorer" and ls[0]["unit"] == 7 and ls[0]["maneuver"] == 4
    d.assign_leader("POR", 41, 7)
    assert h.reqs[-2][1] == f"call {o2.ASSIGN_LEADER_CMD} r0 {NAVY['ptr']} {lp}"
    d.unassign_leader("POR", 7, dry=True)
    assert h.reqs[-2][0] == f"call {o2.NULL_LEADER}" and h.reqs[-2][2] == f"call {o2.ASSIGN_LEADER_CMD} r1 {NAVY['ptr']} r0"


def test_advisors_walk_pool_and_hire():
    pool, n1, n2, a1, a2, typ = 0x60000, 0x61000, 0x62000, 0x63000, 0x64000, 0x65000
    mem = {pool + 0x10: n1, n1 + 0x18: n2, n2 + 0x18: 0, a1 + o2.A_ID: 900, a2 + o2.A_ID: 901,
           a1 + o2.A_TYPE: typ, a2 + o2.A_TYPE: typ, typ + o2.AT_ABILITY: 1, a1 + o2.A_SKILL: 2}
    objs = {n1 + 8: a1, n2 + 8: a2}
    h = StubHook(mem, {typ + o2.AT_KEY: "trader", a1 + o2.A_NAME: "Joao"},
                 {o2.ADV_AVAILABLE: lambda mgr, t: pool if mgr == GS + o2.GS_ADVISORS else 0,
                  o2.OBJ_FROM_ID: lambda p: objs.get(p, 0), o2.ADV_CAN_HIRE: lambda a, t: 1})
    d = o2.Orders2(h, FakeLive([]))
    a = d.advisors("POR")
    assert [x["id"] for x in a["available"]] == [900, 901] and a["available"][0]["type"] == "trader"
    assert a["available"][0]["slot"] == "DIP" and a["hired"] == []
    res = d.hire_advisor("POR", 900)
    assert res["posted"] and h.reqs[-2][2] == f"call {o2.HIRE_CMD} r1 r0 {a1} 1"


def test_send_merchant_arg_order():
    nodes, d0, m0 = 0x70000, 0x71000, 0x72000
    env, mvec = 0x73000, 0x74000
    mem = {GS + o2.GS_NODES: nodes, GS + o2.GS_NODE_COUNT: 1, nodes + o2.N_DEF: d0, d0 + o2.D_LOCATION: 1300,
           nodes + o2.N_INDEX: 0, COUNTRY + 0x1C: _tagv("POR"), COUNTRY + o2.C_ENVOYS: env, env + 8: 0x75000,
           0x75000 + o2.E_MEMBERS: mvec, 0x75000 + o2.E_MEMBERS + 8: mvec + 8, mvec: m0, m0 + o2.M_ID: 3}
    h = StubHook(mem, {d0 + o2.D_KEY: "lisbon", m0 + o2.M_NAME: "Merchant"})
    d = o2.Orders2(h, FakeLive([]))
    assert d.trade_nodes("POR")[0]["key"] == "lisbon"
    r = d.send_merchant("POR", "lisbon", "steer")
    assert r["posted"] and h.reqs[-2][4] == f"call {o2.SEND_TRADER_CMD} r3 r1 r2 {m0} 1"
    with pytest.raises(HookError, match="unknown trade node"):
        d.send_merchant("POR", "nowhere")


def test_colonise_checks_province_before_building():
    h = StubHook()  # GetProvinceOrNullptr returns 0
    d = o2.Orders2(h, FakeLive([]))
    with pytest.raises(HookError, match="no province"):
        d.colonise("POR", 99999)
    assert not any(s.startswith("new") for r in h.reqs for s in r)
    h.calls[o2.GET_PROVINCE] = lambda g, i: 0x80000
    d.colonise("POR", 1100, dry=True)
    assert h.reqs[-2][4] == f"call {o2.COLONIST_CMD} r3 r2 r1 0" and h.reqs[-1][0].startswith("del ")
