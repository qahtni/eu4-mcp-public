"""Offline checks of eu4mcp.mercs against a scripted stand-in for HookClient.vm (no game, no socket)."""
from pathlib import Path

import pytest

from eu4mcp import mercs as ms
from eu4mcp.hookclient import HookError

COUNTRY, ARR, GS, PROVS = 0x200000, 0x210000, 0x900000, 0x220000
M1, M2, M3 = 0x300000, 0x301000, 0x302000          # available+hireable, available+refused, hired
T1, T2, T3 = 0x400000, 0x401000, 0x402000
UNIT3, LEADER3 = 0x500000, 0x510000


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
            elif op == "gs":
                v = GS
            elif op == "tag":
                v = 0x0100000500524F50
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
    def __init__(self):
        self.unit_rows = [{"ptr": UNIT3, "id": 77, "name": "Swiss Guard", "loc": 151, "navy": False}]

    def country_ptr(self, tag):
        return COUNTRY

    def units(self, tag):
        return list(self.unit_rows)

    def unit(self, tag, uid):
        for u in self.unit_rows:
            if u["id"] == uid:
                return u
        raise HookError("no unit")


def _world(**kw):
    mem = {COUNTRY + ms.C_MERCS: ARR, COUNTRY + ms.C_NMERCS: 3, ARR: M1, ARR + 8: M2, ARR + 16: M3,
           M1 + ms.M_TEMPLATE: T1, M2 + ms.M_TEMPLATE: T2, M3 + ms.M_TEMPLATE: T3,
           T1 + ms.T_HOME: 4728, T2 + ms.T_HOME: 0, T3 + ms.T_HOME: 0,
           M3 + ms.M_HIRED: 100, M3 + ms.M_DISMISSED: 50, M3 + ms.M_MANPOWER: 8000, M3 + ms.M_MAX_MANPOWER: 12000,
           M3 + ms.M_LEADER: LEADER3, M2 + ms.M_HIRED: 10, M2 + ms.M_DISMISSED: 20,
           COUNTRY + ms.C_PROVS: PROVS, COUNTRY + ms.C_NPROVS: 2, PROVS: 223, PROVS + 4: 224}
    strs = {T1 + ms.T_KEY: "merc_lombard_free_company", T2 + ms.T_KEY: "merc_black_army",
            T3 + ms.T_KEY: "merc_swiss_guard", LEADER3 + ms.L_NAME: "Hans"}
    h = StubHook(mem, strs, **kw)
    names = {M1: "Lombard Free Company", M2: "§YBlack Army§!", M3: "Swiss Guard"}
    comp = {M1: (6, 2, 0, 9000), M2: (10, 4, 2, 32000), M3: (4, 0, 0, 8000)}

    def name(res, m):
        h.strs[res] = names[m]
        return res

    def composition(res, m, c):
        assert c == COUNTRY
        for i, v in enumerate(comp[m][:3]):
            h.mem[res + 4 * i] = v
        h.mem[res + 0x10] = comp[m][3]
        return res

    def fp(val):
        def f(res, m, c, why):
            assert c == COUNTRY and why == 0
            h.mem[res] = val[m]
            return res
        return f

    def can_hire(m, c, any_prov, why):
        assert c == COUNTRY and any_prov == 1
        if m == M1:
            return 1
        h.strs[why] = "§RNot enough money (need 300)§!" if m == M2 else "already hired"
        return 0

    def can_hire_in(m, c, prov, why):
        if prov == GS + 223:     # stub province pointer = gs + id
            return 1
        h.strs[why] = "out of range"
        return 0

    def get_province(gs, pid):
        return gs + pid if pid in (223, 224, 4728) else 0

    def prov_name(p):
        h.strs[p + 0x10] = {GS + 4728: "Pavia"}.get(p, "?")
        return p + 0x10

    def can_disband(c, u, why):
        return 1

    h.calls.update({ms.MERC_NAME: name, ms.MERC_COMPOSITION: composition,
                    ms.MERC_HIRE_COST: fp({M1: 120500, M2: 310000, M3: 50000}),
                    ms.MERC_MAINTENANCE: fp({M1: 2510, M2: 6000, M3: 1000}),
                    ms.MERC_CAN_HIRE: can_hire, ms.MERC_CAN_HIRE_IN: can_hire_in, ms.GET_PROVINCE: get_province,
                    ms.PROV_NAME: prov_name, ms.MERC_GET_UNIT: lambda m: UNIT3 if m == M3 else 0,
                    ms.CAN_DISBAND: can_disband})
    return h


def _cmd_req(h):
    return [r for r in h.reqs if any(s.startswith("new") for s in r)][-1]


def test_available_lists_unhired_companies_with_gui_values():
    rows = ms.Mercs(_world(), FakeLive()).available("POR")
    assert [r["key"] for r in rows] == ["merc_lombard_free_company", "merc_black_army"]
    a, b = rows
    assert (a["name"], a["home"], a["home_name"]) == ("Lombard Free Company", 4728, "Pavia")
    assert (a["infantry"], a["cavalry"], a["artillery"], a["manpower"]) == (6, 2, 0, 9000)
    assert (a["cost"], a["monthly"], a["can_hire"], a["why_not"]) == (120.5, 2.51, True, "")
    assert b["name"] == "Black Army" and b["home"] is None and not b["can_hire"]
    assert b["why_not"] == "Not enough money (need 300)"
    assert "ptr" not in a


def test_name_is_called_sret_into_an_empty_mkstr():
    h = _world()
    ms.Mercs(h, FakeLive()).available("POR")
    req = next(r for r in h.reqs if any(ms.MERC_NAME in s for s in r))
    i = next(i for i, s in enumerate(req) if s.startswith(f"call {ms.MERC_NAME}"))
    assert req[i - 1] == "mkstr" and req[i] == f"call {ms.MERC_NAME} r{i - 1} {M1}"
    j = next(i for i, s in enumerate(req) if s.startswith(f"call {ms.MERC_CAN_HIRE} "))
    assert req[j] == f"call {ms.MERC_CAN_HIRE} {M1} {COUNTRY} 1 r{j - 1}"


def test_hired_reads_unit_location_manpower_and_leader():
    rows = ms.Mercs(_world(), FakeLive()).hired("POR")
    assert rows == [{"key": "merc_swiss_guard", "name": "Swiss Guard", "state": "hired", "unit": 77, "location": 151,
                     "manpower": 8000, "max_manpower": 12000, "monthly": 1.0, "leader": "Hans", "home": None}]


def test_hire_dry_builds_construct_unit_command_with_company_and_deletes_it():
    h = _world()
    res = ms.Mercs(h, FakeLive()).hire("POR", "lombard_free_company", dry=True)
    assert res["valid"] and not res["posted"] and res["province"] == 223   # home 4728 refused, first allowed = 223
    r = _cmd_req(h)
    assert r == ["tag POR", f"new {ms.CMD_SIZE:#x}", f"call {ms.UNIT_CMD} r1 r0 0 223 {M1}", "vcall r1 0x48"]
    assert h.reqs[-1][0].startswith("del ")


def test_hire_posts_in_given_province():
    h = _world()
    res = ms.Mercs(h, FakeLive()).hire("POR", "Lombard Free Company", 223)
    assert res["posted"] and res["cost"] == 120.5
    assert h.reqs[-1][0].startswith("post ")


def test_hire_refuses_before_building():
    h = _world()
    m = ms.Mercs(h, FakeLive())
    with pytest.raises(HookError, match="Not enough money"):
        m.hire("POR", "merc_black_army")
    with pytest.raises(HookError, match="out of range"):
        m.hire("POR", "lombard_free_company", 224)
    with pytest.raises(HookError, match="no mercenary company"):
        m.hire("POR", "merc_nonexistent")
    assert not any(s.startswith("new") for r in h.reqs for s in r)
    assert m.hire("POR", "merc_black_army", dry=True)["can_hire"] is False


def test_refused_by_isvalid_is_deleted():
    h = _world(valid=0)
    with pytest.raises(HookError, match="refuses"):
        ms.Mercs(h, FakeLive()).hire("POR", "lombard_free_company", 223)
    assert h.reqs[-1][0].startswith("del ")


def test_disband_is_the_normal_disband_command():
    h = _world()
    m = ms.Mercs(h, FakeLive())
    res = m.disband("POR", "swiss_guard", dry=True)
    assert res["valid"] and not res["posted"] and res["unit"] == 77
    assert _cmd_req(h) == [f"new {ms.DISBAND_SIZE:#x}", f"call {ms.DISBAND_CMD} r0 {UNIT3} 0", "vcall r0 0x48"]
    assert m.disband("POR", 77)["posted"]
    with pytest.raises(HookError, match="normal disband"):
        m.disband("POR", 12)
    with pytest.raises(HookError, match="not in service"):
        m.disband("POR", "lombard_free_company")


SYMS = Path(__file__).resolve().parent.parent / "re" / "out" / "dynsyms.txt"


@pytest.mark.skipif(not SYMS.exists(), reason="re/out/dynsyms.txt not generated")
def test_every_symbol_exists_in_the_binary():
    syms = {ln.split()[-1] for ln in SYMS.read_text().splitlines() if ln.strip()}
    used = [v for k, v in vars(ms).items() if isinstance(v, str) and v.startswith("@")]
    assert used and all(v[1:] in syms for v in used), [v for v in used if v[1:] not in syms]
