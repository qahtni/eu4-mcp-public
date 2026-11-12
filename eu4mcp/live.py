"""Live game access through the hook's vm op: read objects and issue orders the way the GUI does.

Every offset/symbol here was checked against the 1.37.5 disassembly and then against the running game
(docs/re/spec.md). Functions that return CFixedPoint/CString BY VALUE take a hidden result pointer first: never
call one as if it returned in rax (that corrupted a unit once).
"""
from __future__ import annotations

import re
from typing import Any

from .hookclient import HookClient, HookError

GET_COUNTRY = "@_ZN16CCountryDataBase10GetCountryE11CCountryTag"
GET_PROVINCE = "@_ZNK10CGameState20GetProvinceOrNullptrE11SProvinceId"
IS_LEGAL_MOVE = "@_ZNK5CUnit13IsLegalToMoveEPK9CProvince"
PERFORM_ACTION = "@_ZN5CUnit13PerformActionEP9CProvincebb"
PS_A = "@_ZN14CPowerSpendingC1E21PowerSpendingCategory14MonarchAbility"
PS_B = "@_ZN14CPowerSpendingC1E21PowerSpendingCategoryPK9CProvince14MonarchAbility"
PS_E = "@_ZN14CPowerSpendingC1EPK16CTechnologyTable14MonarchAbility"
PS_COST = "@_ZNK14CPowerSpending7GetCostE11CCountryTagP7CStringS2_"
PS_CAN = "@_ZNK14CPowerSpending10CanExecuteE11CCountryTag"
SPEND_CMD = "@_ZN18CSpendPowerCommandC1E11CCountryTagR14CPowerSpending"
TECH_DB = "@_ZN19CTechnologyDataBase14AccessInstanceEv"
TECH_TABLE = "@_ZNK19CTechnologyDataBase8GetTableE14MonarchAbility"
UNIT_DB = "@_ZN16CSubUnitDataBase14AccessInstanceEv"
UNIT_GET = "@_ZN16CSubUnitDataBase10GetSubUnitERK7CString"
UNIT_NULL = "@_ZN22CNullSubUnitDefinition24GetNullSubUnitDefinitionEv"
UNIT_CMD = "@_ZN21CConstructUnitCommandC1E11CCountryTagPK18CSubUnitDefinitioniPK17CMercenaryCompany"
BUILDING_DB = "@_ZN17CBuildingDataBase14AccessInstanceEv"
BUILDING_GET = "@_ZNK17CBuildingDataBase11GetBuildingERK7CString"
BUILDING_NULL = "@_ZN13CNullBuilding15GetNullBuildingEv"
BUILDING_CMD = "@_ZN25CConstructBuildingCommandC1EPK9CBuildingPK9CProvince11CCountryTagb"
CAN_BUILD = "@_ZNK8CCountry16CanBuildBuildingEPK9CBuildingPK9CProvinceP7CStringb"  # appends its reasons to the CString
TAKE_LOAN = "@_ZN8CCountry12TakeBankLoanEv"
# CPayLoanCommand(CCountryTag, CLoan*) @0x128fc7a, size 0x60 (operator new in CCountry::PayLoan @0x101aad6, which is
# what the GUI's confirm dialog calls). IsValid = the loan still exists; Execute does NOT check the treasury (the GUI
# greys the button out), so repay() checks it. Loans: CList<CLoan*> at CCountry+0xdc8 (count +0xdd8).
# CLoan: +0x10 id, +0x16 lender tag, +0x1d 0 = bank loan, +0x20 interest (x1000), +0x24 amount (whole ducats),
# +0x28 expiry (hours since year -5000).
PAY_LOAN_CMD = "@_ZN15CPayLoanCommandC1E11CCountryTagP5CLoan"
C_LOANS = 0xDC8
SET_MAINT = "@_ZN8CCountry24ChangeMaintenanceSlidersE11CFixedPointS0_"
C_MAINT_LAND = 0xDEC
OPT_ALLOWED = "@_ZNK12CEventOption9IsAllowedER11CEventScope"
EVENT_CMD = "@_ZN25CSelectEventOptionCommandC1EPK6CEventR11CEventScopeii"
GS_PENDING = 0x1E68
IS_EU3_DIALOG = "@_ZNK10CEU3Dialog11IsEU3DialogEv"  # vtable slot 3 of every CEU3Dialog (docs/re/open_windows.txt)
POPUPS = {"CEventWindow", "CEndOfWarPopup", "CNewAgePopup"}  # CUpdateable popups that are not CEU3Dialogs
PS_F = "@_ZN14CPowerSpendingC1EPK5CIdea14MonarchAbility"
COLONIST_CMD = "@_ZN20CSendColonistCommandC1E11CCountryTagPK9CProvinceb"
IDEA_DB = "@_ZN13CIdeaDataBase10_pInstanceE"
IDEA_GROUP = "@_ZNK13CIdeaDataBase12GetIdeaGroupERK7CString"
IDEA_GET = "@_ZNK13CIdeaDataBase7GetIdeaERK7CString"
IDEA_GROUP_CMD = "@_ZN20CAddIdeaGroupCommandC1E11CCountryTagPK10CIdeaGroup"
MISSION_DB = "@_ZN16CMissionDatabase14AccessInstanceEv"
MISSION_GET = "@_ZNK16CMissionDatabase10GetMissionERK7CString"
MISSION_CAN = "@_ZNK8CMission14CanBeCompletedE11CCountryTag"
MISSION_TPL = "@_ZN16CCommandTemplateI23CCompleteMissionCommandLi14310EJ11CCountryTag7CStringEEC2ERKS1_RKS2_"
MISSION_VT = "@_ZTV23CCompleteMissionCommand"
DECISION_DB = "@_ZN17CDecisionDatabase14AccessInstanceEv"
DECISION_GET = "@_ZN17CDecisionDatabase11GetDecisionERK7CString"
DECISION_POTENTIAL = "@_ZNK9CDecision11IsPotentialER11CEventScope"
DECISION_ALLOWED = "@_ZNK9CDecision9IsAllowedER11CEventScope"
DECISION_EXEC = "@_ZN17CDecisionDatabase15ExecuteDecisionEPK9CDecisionR11CEventScope"
SCOPE_CTOR = "@_ZN11CEventScopeC2E11CCountryTagii"
SCOPE_SETTAG = "@_ZN11CEventScope13SetCountryTagE11CCountryTag"
RECRUIT_LEADER = "@_ZN8CCountry7RecruitE10LeaderTypeRK7CStringb"
LIST_APPEND = "@_ZN5CListIP5CUnitE6AppendEOS1_"
MERGE_CMD = "@_ZN13CMergeCommandC1ERK5CListIP5CUnitE"
SPLIT_CMD = "@_ZN17CSplitUnitCommandC1EPK5CUniti"
DISBAND_CMD = "@_ZN15CDisbandCommandC1EPK5CUnitb"

# CCountry
C_TAG, C_TREASURY, C_MANPOWER, C_STAB, C_PRESTIGE, C_POWER, C_UNITS = 0x1C, 0xB90, 0xD98, 0xBB8, 0xCD8, 0x258, 0xCF8
# CUnit
U_TYPE, U_ID, U_LOC, U_PATH, U_PATHN, U_NAME, U_SUB0, U_SUB1, U_ISNAVY = 0x34, 0x38, 0xE0, 0x100, 0x108, 0x140, 0x10, 0x18, 0xA0
P_ID = 0x1C


def _plain(text: str) -> str:
    """Game tooltip text without colour codes (§X ... §!) and icons (£name)."""
    text = re.sub(r"£\w+\s*", "", re.sub(r"§.", "", text))
    return "; ".join(x.strip(" -") for x in text.splitlines() if x.strip(" -"))


def _s32(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v & 0x80000000 else v


class Live:
    def __init__(self, hook: HookClient):
        self.h = hook

    def info(self) -> dict:
        i = self.h.info()
        if not i.get("ingame"):
            raise HookError("the game is not in a running campaign (main menu or loading)")
        return i

    def player(self) -> str:
        return self.info()["player"]

    def country_ptr(self, tag: str) -> int:
        tag = tag.upper()
        r = self.h.vm(f"tag {tag}", "db", f"call {GET_COUNTRY} r1 r0", "rd4 r2+0x1c")
        if not r[2] or (r[3] & 0xFFFFFF).to_bytes(3, "little").decode("latin1") != tag:
            raise HookError(f"unknown country tag {tag}")
        return r[2]

    def country(self, tag: str) -> dict:
        c = self.country_ptr(tag)
        r = self.h.vm(*(f"rs4 {c}+{o:#x}" for o in (C_TREASURY, C_MANPOWER, C_STAB, C_PRESTIGE, C_POWER, C_POWER + 4, C_POWER + 8)))
        v = [_s32(x) / 1000 for x in r[:4]] + [_s32(x) for x in r[4:]]
        return {"treasury": v[0], "manpower": round(v[1] * 1000), "stability": v[2], "prestige": v[3], "adm": v[4], "dip": v[5], "mil": v[6]}

    def units(self, tag: str) -> list[dict]:
        c = self.country_ptr(tag)
        ptrs = self.h.vm(f"clist {c}+{C_UNITS:#x}")[0]
        if not ptrs:
            return []
        steps: list[str] = []
        for u in ptrs:
            steps += [f"rd4 {u}+{U_TYPE:#x}", f"rd4 {u}+{U_ID:#x}", f"rd8 {u}+{U_LOC:#x}", f"rd4 r{len(steps) + 2}+{P_ID:#x}",
                      f"str {u}+{U_NAME:#x}", f"vcall {u} {U_ISNAVY:#x}", f"rd4 {u}+{U_PATHN:#x}", f"rd8 {u}+{U_PATH:#x}",
                      f"rd8 {u}+{U_SUB0:#x}", f"rd8 {u}+{U_SUB1:#x}"]
        r = self.h.vm(*steps)
        out = []
        for k, u in enumerate(ptrs):
            t, i, _, loc, name, navy, pn, path, s0, s1 = r[k * 10:k * 10 + 10]
            out.append({"ptr": u, "id": i, "type": t, "name": name, "loc": loc, "navy": bool(navy & 0xFF), "moving": pn > 0,
                        "path": path, "n": (s1 - s0) // 8})
        moving = [x for x in out if x["moving"]]
        if moving:
            d = self.h.vm(*(f"rd4 {x['path']}" for x in moving))
            for x, v in zip(moving, d):
                x["dest"] = v
        return out

    def unit(self, tag: str, unit_id: int) -> dict:
        for u in self.units(tag):
            if u["id"] == int(unit_id):
                return u
        raise HookError(f"{tag} has no unit with id {unit_id} (eu4_army list shows the live ids)")

    def move(self, tag: str, unit_id: int, province: int) -> str:
        """Order a unit to a province through CUnit::PerformAction (what a right click does: path-finding, boarding)."""
        u = self.unit(tag, unit_id)
        r = self.h.vm("gs", f"call {GET_PROVINCE} r0 {int(province)}", f"call {IS_LEGAL_MOVE} {u['ptr']} r1")
        if not r[1]:
            raise HookError(f"no province {province}")
        if u["loc"] == int(province) and not u["moving"]:
            return f"{u['name']} (#{u['id']}) is already in {province}"
        if not r[2] & 0xFF:
            raise HookError(f"{u['name']} (#{u['id']}) may not move to {province} now (in combat, no access, or unreachable)")
        self.h.vm(f"call {PERFORM_ACTION} {u['ptr']} {r[1]} 0 0")
        v = self.unit(tag, unit_id)  # next frame: the posted command has run
        if v.get("dest") == int(province):
            return f"{v['name']} (#{v['id']}) moving {v['loc']} -> {province}"
        if v["loc"] == int(province):
            return f"{v['name']} (#{v['id']}) is in {province}"
        raise HookError(f"order refused: {v['name']} (#{v['id']}) at {v['loc']}, destination {v.get('dest', 'none')} (no path, or blocked)")

    # ---- commands (docs/re/economy_commands.txt) ---------------------------------------------
    def command(self, pre: list[str], size: int, ctor: str, args: str, what: str) -> None:
        """new(size); ctor(cmd, args); IsValid (vptr+0x48); Post. ``args`` may use rN of ``pre`` steps."""
        n = len(pre)
        r = self.h.vm(*pre, f"new {size:#x}", f"call {ctor} r{n} {args}", f"vcall r{n} 0x48")
        cmd = r[n]
        if not r[n + 2] & 0xFF:
            self.h.vm(f"del {cmd}")
            raise HookError(f"{what}: the game refuses this order now")
        if not self.h.vm(f"post {cmd}")[0]:
            raise HookError(f"{what}: could not be posted")

    def spend(self, tag: str, ctor: str, args: str, what: str, pre: list[str] | None = None, dry: bool = False) -> int:
        """CSpendPowerCommand around a CPowerSpending built by ``ctor`` (``{ps}`` = the object); returns the cost."""
        pre = list(pre or [])
        n = len(pre)
        steps = pre + ["buf 0x38", f"call {ctor} " + args.format(ps=f"r{n}"), f"tag {tag}",
                       f"call {PS_COST} r{n} r{n + 2} 0 0", f"call {PS_CAN} r{n} r{n + 2}", f"rd4 r{n}+0x10"]
        r = self.h.vm(*steps)
        cost, can, ability = _s32(r[n + 3]), r[n + 4] & 0xFF, r[n + 5]
        if dry:
            return cost if can else -cost - 1
        if not can:
            have = self.country(tag)[("adm", "dip", "mil")[ability]] if ability < 3 else "?"
            raise HookError(f"{what}: not possible now (costs {cost} {('ADM', 'DIP', 'MIL')[ability] if ability < 3 else ''}, have {have})")
        self.command(steps[:n + 3], 0x88, SPEND_CMD, f"r{n + 2} r{n}", what)
        return cost

    def _prov(self, province: int) -> list[str]:
        return ["gs", f"call {GET_PROVINCE} r0 {int(province)}"]

    def stability(self, tag: str, dry: bool = False) -> int:
        return self.spend(tag, PS_A, "{ps} 2 0", "raise stability", dry=dry)

    def reduce_inflation(self, tag: str, dry: bool = False) -> int:
        return self.spend(tag, PS_A, "{ps} 0xf 0", "reduce inflation", dry=dry)

    def reduce_war_exhaustion(self, tag: str, dry: bool = False) -> int:
        return self.spend(tag, PS_A, "{ps} 0x16 1", "reduce war exhaustion", dry=dry)

    def develop(self, tag: str, province: int, ability: int, dry: bool = False) -> int:
        return self.spend(tag, PS_B, f"{{ps}} 7 r1 {ability}", f"develop {province}", pre=self._prov(province), dry=dry)

    def core(self, tag: str, province: int, dry: bool = False) -> int:
        return self.spend(tag, PS_B, "{ps} 0x11 r1 0", f"core {province}", pre=self._prov(province), dry=dry)

    def assault(self, tag: str, province: int, dry: bool = False) -> int:
        return self.spend(tag, PS_B, "{ps} 9 r1 2", f"assault {province}", pre=self._prov(province), dry=dry)

    def tech(self, tag: str, ability: int, dry: bool = False) -> int:
        pre = [f"call {TECH_DB}", f"call {TECH_TABLE} r0 {ability}"]
        return self.spend(tag, PS_E, f"{{ps}} r1 {ability}", f"buy {('ADM', 'DIP', 'MIL')[ability]} technology", pre=pre, dry=dry)

    def _lookup(self, db: str, get: str, null: str, key: str, what: str) -> int:
        r = self.h.vm(f"call {db}", f"mkstr {key}", f"call {get} r0 r1", f"call {null}")
        if not r[2] or r[2] == r[3]:
            raise HookError(f"unknown {what} '{key}'")
        return r[2]

    def recruit(self, tag: str, province: int, unit: str) -> float:
        """Queue one regiment/ship of type ``unit`` (key from common/units) in a province; returns the money spent."""
        d = self._lookup(UNIT_DB, UNIT_GET, UNIT_NULL, unit, "unit type")
        before = self.country(tag)["treasury"]
        self.command([f"tag {tag}"], 0x98, UNIT_CMD, f"r0 {d} {int(province)} 0", f"recruit {unit} in {province}")
        return round(before - self.country(tag)["treasury"], 2)

    def build_check(self, tag: str, province: int, building: str) -> str | None:
        """Read-only: None when ``building`` can be built in ``province`` now, else the game's reason
        (CCountry::CanBuildBuilding: tech level, slots, port, money ...)."""
        b = self._lookup(BUILDING_DB, BUILDING_GET, BUILDING_NULL, building, "building")
        r = self.h.vm(*self._prov(province), f"tag {tag}", "db", f"call {GET_COUNTRY} r3 r2", "mkstr ",
                      f"call {CAN_BUILD} r4 {b} r1 r5 0", "str r5")
        if not r[1] or not r[4]:
            raise HookError(f"build {building} in {province}: unknown province or country")
        return None if r[6] & 0xFF else (_plain(r[7]) or "not allowed now")

    def build(self, tag: str, province: int, building: str) -> float:
        why = self.build_check(tag, province, building)
        if why:
            raise HookError(f"cannot build {building} in {province}: {why}")
        b = self._lookup(BUILDING_DB, BUILDING_GET, BUILDING_NULL, building, "building")
        before = self.country(tag)["treasury"]
        self.command(self._prov(province) + [f"tag {tag}"], 0x60, BUILDING_CMD, f"{b} r1 r2 0", f"build {building} in {province}")
        return round(before - self.country(tag)["treasury"], 2)

    def loan(self, tag: str) -> float:
        c = self.country_ptr(tag)
        before = self.country(tag)["treasury"]
        self.h.vm(f"call {TAKE_LOAN} {c}")
        return round(self.country(tag)["treasury"] - before, 2)

    def loans(self, tag: str) -> list[dict]:
        """Read-only: the country's loans, soonest expiry first: id, amount, interest %, expiry 'Y.M.D', bank, ptr."""
        c = self.country_ptr(tag)
        ptrs = self.h.vm(f"clist {c}+{C_LOANS:#x} 200")[0] or []
        if not ptrs:
            return []
        r = self.h.vm(*(s for p in ptrs for s in (f"rd4 {p}+0x10", f"rd1 {p}+0x1d", f"rs4 {p}+0x20", f"rs4 {p}+0x24", f"rs4 {p}+0x28")))
        out = []
        for k, p in enumerate(ptrs):
            i, nb, intr, amt, exp = r[k * 5:k * 5 + 5]
            days = _s32(exp) // 24
            y, doy, m = days // 365 - 5000, days % 365, 0
            for m, ml in enumerate((31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31), 1):
                if doy < ml:
                    break
                doy -= ml
            out.append({"id": i, "amount": _s32(amt), "interest": _s32(intr) / 1000, "expiry": f"{y}.{m}.{doy + 1}", "days": days,
                        "bank": not nb & 0xFF, "ptr": p})
        return sorted(out, key=lambda x: x["days"])

    def repay(self, tag: str, loan: int = 0, n: int = 1, dry: bool = False) -> dict:
        """Repay loan ``loan`` (id from loans()), or without an id the ``n`` loans that expire first, as far as the
        treasury allows. Returns {"repaid": [...], "spent", "left": loans remaining, "why": reason it stopped}."""
        ls = self.loans(tag)
        if loan:
            pick = [x for x in ls if x["id"] == int(loan)]
            if not pick:
                raise HookError(f"no loan {loan}; loans: {', '.join(str(x['id']) for x in ls) or 'none'}")
        else:
            pick = ls[:max(1, int(n))]
        if not pick:
            raise HookError("there is no loan to repay")
        money, done, why = self.country(tag)["treasury"], [], ""
        for x in pick:
            if money < x["amount"]:
                why = f"loan {x['id']} costs {x['amount']} ducats, treasury has {money:.0f}"
                break
            if not dry:
                self.command([f"tag {tag.upper()}"], 0x60, PAY_LOAN_CMD, f"r0 {x['ptr']}", f"repay loan {x['id']}")
            money -= x["amount"]
            done.append(x)
        return {"repaid": [{k: x[k] for k in ("id", "amount", "expiry")} for x in done], "spent": sum(x["amount"] for x in done),
                "left": len(ls) - (0 if dry else len(done)), "why": why, "dry": dry}

    def maintenance(self, tag: str, land: float | None = None, naval: float | None = None) -> dict:
        """Set army/navy maintenance sliders (0.0-1.0); returns the values in force."""
        c = self.country_ptr(tag)
        cur = [_s32(x) for x in self.h.vm(f"rs4 {c}+{C_MAINT_LAND:#x}", f"rs4 {c}+{C_MAINT_LAND + 4:#x}")]
        if land is not None or naval is not None:
            want = [cur[0] if land is None else round(min(1, max(0, land)) * 1000), cur[1] if naval is None else round(min(1, max(0, naval)) * 1000)]
            self.h.vm("buf 8", f"wr4 r0 {want[0]}", f"wr4 r0+4 {want[1]}", f"call {SET_MAINT} {c} r0 r0+4")
            cur = [_s32(x) for x in self.h.vm(f"rs4 {c}+{C_MAINT_LAND:#x}", f"rs4 {c}+{C_MAINT_LAND + 4:#x}")]
        return {"land": cur[0] / 1000, "naval": cur[1] / 1000}

    def colonise(self, tag: str, province: int) -> None:
        self.command(self._prov(province) + [f"tag {tag}"], 0x60, COLONIST_CMD, "r2 r1 0", f"send colonist to {province}")

    def idea_group(self, tag: str, group: str) -> None:
        r = self.h.vm(f"rd8 {IDEA_DB}", f"mkstr {group}", f"call {IDEA_GROUP} r0 r1")
        if not r[2]:
            raise HookError(f"unknown idea group '{group}' (keys as in common/ideas, e.g. exploration_ideas)")
        self.command([f"tag {tag}"], 0x58, IDEA_GROUP_CMD, f"r0 {r[2]}", f"pick idea group {group}")

    def idea(self, tag: str, idea: str, dry: bool = False) -> int:
        r = self.h.vm(f"rd8 {IDEA_DB}", f"mkstr {idea}", f"call {IDEA_GET} r0 r1")
        if not r[2]:
            raise HookError(f"unknown idea '{idea}' (keys as in common/ideas, e.g. quest_for_the_new_world)")
        ab = self.h.vm(f"rd8 {r[2]}+0x190", "rd4 r0+0x560")[1]
        if ab > 2:
            raise HookError(f"idea '{idea}' has no monarch-power category")
        return self.spend(tag, PS_F, f"{{ps}} {r[2]} {ab}", f"buy idea {idea}", dry=dry)

    def mission(self, tag: str, key: str) -> None:
        r = self.h.vm(f"call {MISSION_DB}", f"mkstr {key}", f"call {MISSION_GET} r0 r1", f"tag {tag}")
        if not r[2]:
            raise HookError(f"unknown mission '{key}' (keys as in common/missions)")
        if not self.h.vm(f"tag {tag}", f"call {MISSION_CAN} {r[2]} r0")[1] & 0xFF:
            raise HookError(f"mission {key} cannot be completed now (requirements or earlier missions missing)")
        pre = [f"tag {tag}", "buf 8", "wr8 r1 r0", f"mkstr {key}"]
        rr = self.h.vm(*pre, "new 0x70", f"call {MISSION_TPL} r4 r1 r3", f"wr8 r4 {MISSION_VT}+0x10", "vcall r4 0x48")
        if not rr[7] & 0xFF:
            self.h.vm(f"del {rr[4]}")
            raise HookError(f"mission {key}: the game refuses this now")
        self.h.vm(f"post {rr[4]}")

    def decision(self, tag: str, key: str, dry: bool = False) -> str:
        pre = [f"call {DECISION_DB}", f"mkstr {key}", f"call {DECISION_GET} r0 r1", f"tag {tag}", "buf 0xd0",
               f"call {SCOPE_CTOR} r4 r3 0 0", f"call {SCOPE_SETTAG} r4 r3"]
        r = self.h.vm(*pre[:3])
        if not r[2]:
            raise HookError(f"unknown decision '{key}' (keys as in decisions/*.txt)")
        r = self.h.vm(*pre, f"call {DECISION_POTENTIAL} r2 r4", f"call {DECISION_ALLOWED} r2 r4")
        pot, ok = r[7] & 0xFF, r[8] & 0xFF
        if not pot:
            raise HookError(f"decision {key} is not available to {tag}")
        if not ok:
            raise HookError(f"decision {key}: requirements not met")
        if dry:
            return "possible"
        self.h.vm(*pre, f"call {DECISION_EXEC} r0 r2 r4")
        return "enacted"

    def leader(self, tag: str, kind: str) -> None:
        kinds = {"general": 0, "admiral": 1, "conquistador": 2, "explorer": 3}
        if kind not in kinds:
            raise HookError(f"leader kind: {', '.join(kinds)}")
        c = self.country_ptr(tag)
        self.h.vm("mkstr", f"call {RECRUIT_LEADER} {c} {kinds[kind]} r0 0")

    def merge(self, tag: str, unit_ids: list[int]) -> None:
        us = [self.unit(tag, i) for i in unit_ids]
        if len(us) < 2 or len({u["loc"] for u in us}) != 1 or len({u["navy"] for u in us}) != 1:
            raise HookError("merge needs two or more units of the same kind in the same province")
        pre = ["buf 0x18"]
        for u in us:
            pre += ["buf 8", f"wr8 r{len(pre)} {u['ptr']}", f"call {LIST_APPEND} r0 r{len(pre)}"]
        self.command(pre, 0x60, MERGE_CMD, "r0", "merge")

    def split(self, tag: str, unit_id: int, n: int = 0) -> None:
        u = self.unit(tag, unit_id)
        n = n or (u["n"] + 1) // 2
        if not 0 < n < u["n"]:
            raise HookError(f"{u['name']} has {u['n']} regiments/ships; cannot split off {n}")
        self.command([], 0x70, SPLIT_CMD, f"{u['ptr']} {n}", "split")

    def disband(self, tag: str, unit_id: int) -> None:
        self.command([], 0x60, DISBAND_CMD, f"{self.unit(tag, unit_id)['ptr']} 0", "disband")

    # ---- events ----------------------------------------------------------------------------
    def events(self, tag: str | None = None) -> list[dict]:
        """Pending events (CPdxArray<CPendingEvent> at gs+0x1e68); ``tag`` filters by the country they wait for."""
        r = self.h.vm("gs", f"rd8 r0+{GS_PENDING + 8:#x}", f"rd4 r0+{GS_PENDING + 0x14:#x}")
        data, n = r[1], min(r[2], 400)
        if not n:
            return []
        def entry(k: int, b: int) -> list[str]:
            e = data + k * 0x28
            return [f"rd8 {e}+0x10", f"rd8 {e}+0x18", f"rd4 {e}+0x20", f"rd4 {e}+0x24", f"rd4 r{b + 1}+0x2c", f"str r{b}+0x18",
                    f"rd8 r{b}+0x768", f"rd8 r{b}+0x770"]

        rows: list[list] = []
        try:
            r = self.h.vm(*(s for k in range(n) for s in entry(k, k * 8)))
            rows = [r[k * 8:k * 8 + 8] for k in range(n)]
        except HookError:
            # an entry whose event/scope pointer is not readable (seen 1461-62: "fault (signal 11) in step 4" stopped
            # every eu4_advance): read entry by entry and leave the unreadable ones out
            for k in range(n):
                try:
                    rows.append(self.h.vm(*entry(k, 0)))
                except HookError:
                    continue
        out = []
        for ev, scope, expiry, pid, t, name, o0, o1 in rows:
            owner = (t & 0xFFFFFF).to_bytes(3, "little").decode("latin1")
            if tag and owner != tag.upper():
                continue
            out.append({"pid": pid, "id": name, "tag": owner, "event": ev, "scope": scope, "expiry": expiry, "n": (o1 - o0) // 8, "opts": o0})
        for x in out:
            if x["n"]:
                ptrs = self.h.vm(*(f"rd8 {x['opts']}+{8 * i:#x}" for i in range(min(x["n"], 32))))
                x["allowed"] = [bool(v & 0xFF) for v in self.h.vm(*(f"call {OPT_ALLOWED} {p} {x['scope']}" for p in ptrs))]
        return out

    def answer(self, tag: str, pid: int, option: int) -> str:
        """Choose option (1-based, file order) of the pending event ``pid`` waiting for ``tag``."""
        for x in self.events(tag):
            if x["pid"] == int(pid):
                break
        else:
            raise HookError(f"no pending event {pid} for {tag} (eu4_events lists them)")
        if not 1 <= option <= x["n"]:
            raise HookError(f"event {x['id']} has options 1-{x['n']}")
        if not x["allowed"][option - 1]:
            raise HookError(f"option {option} of {x['id']} is not available")
        self.command([], 0x138, EVENT_CMD, f"{x['event']} {x['scope']} {option - 1} {pid}", f"answer {x['id']}")
        self.close_event_windows(pid)
        return x["id"]

    def close_event_windows(self, pid: int | None = None) -> int:
        """Close the on-screen window of an answered event (it keeps the game paused otherwise). Does what
        CEventWindow::OptionSelected does after posting: clears the alive byte +0x124, so CEventWindow::Update returns
        false and the idler drops the window. Windows = CEventWindow entries (pid at +0x120) in the idler's
        updateable CList at +0x70. pid=None closes every event window whose event is no longer pending."""
        r = self.h.vm("idler", "clist r0+0x70 400", "buf 8", "wr8 r2 @_ZTV12CEventWindow+16", "rd8 r2")
        ptrs, vt = r[1] or [], r[4]
        if not ptrs:
            return 0
        v = self.h.vm(*[s for w in ptrs for s in (f"rd8 {w}", f"rd4 {w}+0x120")])
        wins = [(w, v[2 * k + 1]) for k, w in enumerate(ptrs) if v[2 * k] == vt]
        if pid is None:
            pending = {e["pid"] for e in self.events(self.player())}
            wins = [(w, p) for w, p in wins if p not in pending]
        else:
            wins = [(w, p) for w, p in wins if p == int(pid)]
        if wins:
            self.h.vm(*(f"wr1 {w}+0x124 0" for w, _ in wins))
        return len(wins)

    def open_windows(self) -> list[str]:
        """Read-only: class names of open dialogs/popups in the idler's updateable CList at +0x70 (the list
        close_event_windows walks). An entry counts when its vtable slot 3 is CEU3Dialog::IsEU3Dialog (every dialog:
        CMessageDialog = message popups, CDiplomacyDialog, CConfirmDialog ...) or it is a CEventWindow /
        CEndOfWarPopup / CNewAgePopup (popups that are not CEU3Dialogs). Name = Itanium typeinfo name at vptr-8.
        docs/re/open_windows.txt. Only reads memory; nothing is called."""
        r = self.h.vm("idler", "clist r0+0x70 400", "buf 8", f"wr8 r2 {IS_EU3_DIALOG}", "rd8 r2")
        ptrs, dlg = r[1] or [], r[4]
        if not ptrs:
            return []
        steps: list[str] = []
        for w in ptrs[:60]:
            k = len(steps)
            steps += [f"rd8 {w}", f"rd8 r{k}+0x18", f"rd8 r{k}-8", f"rd8 r{k + 2}+8", f"cstr r{k + 3}"]
        v = self.h.vm(*steps)
        out = []
        for k in range(len(ptrs[:60])):
            slot3, name = v[5 * k + 1], re.sub(r"^\d+", "", str(v[5 * k + 4] or "?"))
            if slot3 == dlg or name in POPUPS:
                out.append(name)
        return out

    def advisor_slots(self, tag: str) -> list[str]:
        """Read-only: ADM/DIP/MIL slots without an advisor (CCountry+0x14d0 CAdvisor*[3]; an empty slot is null or
        holds the 'No Advisor' placeholder with id 0, as in Orders2.advisors)."""
        from .orders2 import A_ID, A_TYPE, AT_KEY, C_ADVISORS

        c = self.country_ptr(tag)
        ptrs = self.h.vm(*(f"rd8 {c}+{C_ADVISORS + 8 * k:#x}" for k in range(3)))
        steps: list[str] = []
        for p in ptrs:
            if p:
                k = len(steps)
                steps += [f"rd4 {p}+{A_ID:#x}", f"rd8 {p}+{A_TYPE:#x}", f"str r{k + 1}+{AT_KEY:#x}"]
        v = self.h.vm(*steps) if steps else []
        out, i = [], 0
        for k, p in enumerate(ptrs):
            if not p:
                out.append(("ADM", "DIP", "MIL")[k])
                continue
            aid, _, key = v[i:i + 3]
            i += 3
            if not aid or key == "noAdvisorType":
                out.append(("ADM", "DIP", "MIL")[k])
        return out

def add_days(date: str, days: int) -> str:
    """EU4 calendar: 365-day years, no leap days."""
    ml = (31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    y, m, d = (int(x) for x in date.split("."))
    n = sum(ml[:m - 1]) + d - 1 + days
    y += n // 365
    n %= 365
    m = 0
    while n >= ml[m]:
        n -= ml[m]
        m += 1
    return f"{y}.{m + 1}.{n + 1}"
