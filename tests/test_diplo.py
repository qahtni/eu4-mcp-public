"""Offline checks of eu4mcp.diplo against a scripted stand-in for HookClient.vm (no game, no socket)."""
import pytest

from eu4mcp import diplo
from eu4mcp.hookclient import HookError


def _tagv(t: str) -> int:
    return int.from_bytes(t.encode() + b"\0\0\0\0\x01", "little")


class StubHook:
    """Answers vm steps by pattern; records every request."""

    def __init__(self, token=0x293D, total=5, chances=((-3, "Relations"), (8, "Trust"))):
        self.reqs: list[list[str]] = []
        self.token, self.total, self.chances = token, total, chances
        self.countries: dict[int, str] = {}

    def vm(self, *steps):
        self.reqs.append(list(steps))
        out, regs = [], []
        for s in steps:
            t = s.split()
            op = t[0]
            arg = lambda k: regs[int(t[k][1:])] if t[k].startswith("r") else int(t[k].split("+")[0], 0)  # noqa: E731
            v = 0
            if op == "tag":
                v = _tagv(t[1])
            elif op == "db":
                v = 0x10
            elif op == "call" and t[1] == diplo.GET_COUNTRY:
                v = 0x1000 + 0x100 * len(self.countries)
                self.countries[v] = diplo.tag_str(arg(3))
            elif op == "rd8" and t[1].endswith("+0x1c"):
                v = _tagv(self.countries[int(t[1].split("+")[0], 0)])
            elif op == "call" and t[1] == diplo.CREATE_ACTION:
                v = 0xA000
            elif op == "new":
                v = 0xB000
            elif op == "vcall" and t[2] == "0xa0":
                v = self.token
            elif op == "vcall" and t[2] == "0x128":
                v = self.total & 0xFFFFFFFF
            elif op == "rd8" and t[1] == str(0xB000):
                v = 0xC000
            elif op == "rd8" and t[1] == f"{0xB000}+8":
                v = 0xC000 + 0x30 * len(self.chances)
            elif op == "rs4":
                v = self.chances[(int(t[1], 0) - 0xC000) // 0x30][0] & 0xFFFFFFFF
            elif op == "str":
                k = (int(t[1], 0) - 0xC008) // 0x30
                v = f"§G{self.chances[k][1]}§!"
                out.append(v)
                regs.append(0)
                continue
            out.append(v)
            regs.append(v)
        return out


def test_actions_table_ids_unique():
    ids = [v[0] for v in diplo.ACTIONS.values()]
    assert len(ids) == len(set(ids))
    assert diplo.ACTIONS["alliance"][0] == 0x293D and diplo.ACTIONS["declare_war"][0] == 0x293B
    assert diplo.tag_str(_tagv("FRA")) == "FRA"


def test_chance_reads_reasons_and_frees_everything():
    h = StubHook()
    r = diplo.Diplo(h).chance("FRA", "CAS", "alliance")
    assert r["total"] == 5 and r["accepts"]
    assert [(x["value"], x["text"]) for x in r["reasons"]] == [(-3, "Relations"), (8, "Trust")]
    last = h.reqs[-1]
    assert last == [f"call {diplo.DIPLOCHANCE_DTOR} {0xB000}", f"call {diplo.OP_DELETE} {0xB000}", f"del {0xA000}"]


def test_chance_token_mismatch_still_frees():
    h = StubHook(token=0x1234)
    with pytest.raises(HookError, match="token"):
        diplo.Diplo(h).chance("FRA", "CAS", "alliance")
    assert h.reqs[-1][-1] == f"del {0xA000}"


def test_send_refuses_unwired_and_own_methods():
    d = diplo.Diplo(StubHook())
    with pytest.raises(HookError, match="declare_war"):
        d.send("FRA", "CAS", "declare_war")
    with pytest.raises(HookError, match="setter"):
        d.send("FRA", "CAS", "steal_maps")
    with pytest.raises(HookError, match="unknown diplomatic action"):
        d.send("FRA", "CAS", "hug")
