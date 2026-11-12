"""Mercenary companies on the hook's vm op: list hireable companies, hire one, list hired ones, disband one.

Built from STATIC reverse engineering only (1.37.5 Linux binary, re/fdis.py on every function called below); nothing
here has run against the game yet. docs/re/mercs_prove_plan.txt lists the live tests, safest first.
Conventions (as orders2.py / estates.py):
 - HIRE is NOT a dedicated command: the mercenary GUI posts the ordinary CConstructUnitCommand with a null unit
   definition and the CMercenaryCompany* (copied from CMacroBuildInterface::DoInterfaceBasedSelection @0x1d34be6,
   mercenary-tab branch @0x1d34df7..0x1d34e76: GetObjectFromID(company CID) -> new(0x98) -> ctor(player tag, 0,
   [selectable+0xc] = province id, company) -> vptr+0x48 -> CSession::Post). new -> ctor -> IsValid -> post / del;
 - CCountryTag goes by value (`tag XXX`, as the GUI passes CInGameIdler::GetPlayer());
 - functions returning CFixedPoint by value (CalcHiringCostFor, CalcMaintenanceFor) and the composition struct
   (GetComposition, 0x14 bytes) are called sret: rdi = result buffer, rsi = this;
 - CMercenaryCompany::GetName returns a CString BY VALUE: it is called sret with an EMPTY `mkstr` string as the
   result slot (an empty std::string owns no heap, the ctor overwrites it in place, and the hook deletes the mkstr
   string at the end of the request, which frees the new text: no leak; a `buf` slot would leak long names);
 - the vm has no conditional step, so pointers are reused across requests: keep the game PAUSED while using this.
Every public method has a PROVEN-STATIC or UNSURE comment naming what was checked.
Not possible through a command: dismissing a company is the NORMAL unit disband (CDisbandCommand; CCountry::
DismissMercenaryCompany is only called from CCountry::RemoveUnit, and the AI's CAIRecruitMinister::DisbandMercenaryUnit
@0xe57646 posts CDisbandCommand(unit, false)); `disband` here only maps a company to its unit and posts that.
Choosing a company's leader, merging/splitting a company (CanBeSplitAndMerged is a rule check, the orders are the
normal merge/split) and the "free company" re-generation are not separate commands.
"""
from __future__ import annotations

import re
from typing import Any

from .hookclient import HookClient, HookError
from .live import DISBAND_CMD, GET_PROVINCE, UNIT_CMD, Live, _s32

# ---- command ----------------------------------------------------------------------------------------------------
# UNIT_CMD = CConstructUnitCommand(CCountryTag, CSubUnitDefinition const*, int, CMercenaryCompany const*) @0xf40252,
# size 0x98 (operator new at 0x1d34e2c). Layout: +0x48 tag, +0x50 def (0 -> CNullSubUnitDefinition), +0x58 province id,
# +0x60 company*, +0x68 company CID (= [company+0xc]), +0x70 = 1 when a company is given.
# IsValid @0xf41274 (vtable slot 9 = vptr+0x48, re/vtab.py): null def -> company = GetObjectFromID(+0x68), company
# IsValid (vptr+0x58, always true), 0 < province id < province count, then
# CMercenaryCompany::CanBeHiredIn(company, country, province, 0) = MayBeHiredIn + CanBeHired(false) + CanRecruitIn.
# Execute @0xf407ae: CalcHiringCostFor -> GetMercenaryBuildTime -> SetRecruit(true) -> construction in the province;
# the army appears when it finishes (CMilitaryConstruction::BuildMercenaryUnit -> CCountry::HireMercenaryCompany).
CMD_SIZE = 0x98
CAN_DISBAND = "@_ZN8CCountry14CanDisbandUnitEPK5CUnitP7CString"   # @0x101e470 (rdi country, rsi unit, rdx CString*)
DISBAND_SIZE = 0x60                                               # operator new size at CAIRecruitMinister @0xe577ec

# ---- CMercenaryCompany (ctor @0x1542e48, CReferenceObject: CID at +0xc) -------------------------------------------
C_MERCS, C_NMERCS = 0x23F8, 0x2404   # CCountry: CPdxArray<CMercenaryCompany*> at +0x23f0 (data +8, count +0x14);
#                                      the mercenary list window shows exactly this array (CMercenaryCompanyListWindow::
#                                      Update @0x1d917bc); UpdatePossibleMercenaryCompanies @0xfe44da keeps hired and
#                                      recruiting companies in it and drops the ones no longer available.
M_TEMPLATE = 0x20                    # CMercenaryTemplate const* (GetName / GetOrigin / IsForeign read it)
M_RECRUIT = 0x28                     # byte: being recruited (IsInRecruit @0x15451aa, SetRecruit @0x1546076)
M_LEADER = 0x40                      # CLeader* (GetLeader @0x1544188); leader name std::string at +0x18 (orders2 L_NAME)
M_HIRED, M_DISMISSED = 0x48, 0x4C    # dates; IsHired @0x15451a0 = hired > dismissed (SetHired @0x154565c)
M_MANPOWER, M_MAX_MANPOWER = 0x50, 0x54   # CFixedPoint (GetManpower @0x1545696; HireMercenaryCompany sets both)
T_KEY, T_HOME = 0x18, 0x24C          # CMercenaryTemplate: key std::string (ctor @0x1547cfe), home province id
#                                      (GetOrigin @0x154227c; 0 = no home province)
L_NAME = 0x18

MERC_NAME = "@_ZNK17CMercenaryCompany7GetNameEv"                                         # @0x1544ac6 sret CString
MERC_COMPOSITION = "@_ZNK17CMercenaryCompany14GetCompositionERK8CCountry"                # @0x154572c sret 0x14 bytes
MERC_HIRE_COST = "@_ZNK17CMercenaryCompany17CalcHiringCostForERK8CCountryP7CString"      # @0x1544e12 sret CFixedPoint
MERC_MAINTENANCE = "@_ZNK17CMercenaryCompany18CalcMaintenanceForERK8CCountryP7CString"   # @0x1545bb4 sret CFixedPoint
MERC_CAN_HIRE = "@_ZNK17CMercenaryCompany10CanBeHiredERK8CCountrybP7CString"             # @0x1544ae4 -> bool
MERC_CAN_HIRE_IN = "@_ZNK17CMercenaryCompany12CanBeHiredInERK8CCountryRK9CProvinceP7CString"   # @0x154530a -> bool
MERC_GET_UNIT = "@_ZNK17CMercenaryCompany7GetUnitEv"                                     # @0x1545510 -> CUnit* or 0
PROV_NAME = "@_ZNK9CProvince7GetNameEv"                                                  # @0x1612348 -> CString const*
# Composition (GetComposition @0x1545a67..0x1545b01): +0 infantry, +4 cavalry, +8 artillery, +0xc total regiments,
# +0x10 starting manpower (CFixedPoint, MERCENARY_COMPANY_MANPOWER_PER_REGIMENT per regiment, times the modifier).
COMP_SIZE = 0x18
# CCountry: CPdxArray<int> of province ids at +0x1320 (data +0x1328, count +0x1334): the list CanBeHired(.., true, ..)
# walks with MayBeHiredIn (@0x1544d0a). UNSURE whether it is "owned" or "all controlled" provinces.
C_PROVS, C_NPROVS = 0x1328, 0x1334

_FMT_RE = re.compile(r"§.|£[a-z_]*£?")


def _clean(s: Any, n: int = 0) -> str:
    t = " ".join(_FMT_RE.sub("", s).split()) if isinstance(s, str) else ""
    return t[:n] + "..." if n and len(t) > n else t


def _fp(v: int) -> float:
    return round(_s32(v) / 1000, 3)


class Mercs:
    def __init__(self, hook: HookClient, live: Live | None = None):
        self.h = hook
        self.live = live or Live(hook)

    # ---- helpers ------------------------------------------------------------------------------------------------
    def _cmd(self, pre: list[str], size: int, ctor: str, args: str, what: str, dry: bool = False) -> dict:
        """Same as Orders2._cmd: new(size); ctor(cmd, args); IsValid (vptr+0x48); then post, or del when refused / dry."""
        n = len(pre)
        r = self.h.vm(*pre, f"new {size:#x}", f"call {ctor} r{n} {args}", f"vcall r{n} 0x48")
        cmd, ok = r[n], bool(r[n + 2] & 0xFF)
        if dry or not ok:
            self.h.vm(f"del {cmd}")
            if dry:
                return {"what": what, "valid": ok, "posted": False}
            raise HookError(f"{what}: the game refuses this order now")
        if not self.h.vm(f"post {cmd}")[0]:
            raise HookError(f"{what}: could not be posted")
        return {"what": what, "valid": True, "posted": True}

    # PROVEN-STATIC: company array (see C_MERCS); template key/home (ctor @0x1547cfe, GetOrigin); GetName(sret, company)
    # = custom name std::string [template+0x38] if non-empty else the localised key (CMercenaryTemplate::GetName
    # @0x1542238); GetComposition(sret, company, country&) / CalcHiringCostFor / CalcMaintenanceFor(sret, company,
    # country&, CString* = 0) exactly as CMercenaryCompanyEntry::Update @0x1d928f4 calls them (the GUI row: name,
    # inf/cav/art, manpower, monthly maintenance, hiring cost). CanBeHired(company, country&, true, CString*) is the
    # GUI's "hire" button state (Entry::Update @0x1d929dc passes edx=1): cost > treasury, bankruptcy, already hired,
    # recruiting, waiting on the availability date, then "no province where it may be hired" (MayBeHiredIn).
    # UNSURE: whether the reason CString is written for every refusal (PdxLocalizeOptional writes when rcx != 0).
    def _companies(self, tag: str) -> tuple[int, list[dict]]:
        c = self.live.country_ptr(tag)
        r = self.h.vm(f"rd8 {c}+{C_MERCS:#x}", f"rd4 {c}+{C_NMERCS:#x}")
        data, n = r[0], min(r[1], 200)
        if not data or not n:
            return c, []
        ptrs = [p for p in self.h.vm(*(f"rd8 {data}+{8 * i:#x}" for i in range(n))) if p]
        tpls = self.h.vm(*(f"rd8 {p}+{M_TEMPLATE:#x}" for p in ptrs)) if ptrs else []
        live = [(p, t) for p, t in zip(ptrs, tpls) if t]
        out: list[dict] = []
        per = 22
        for i in range(0, len(live), 10):
            chunk = live[i:i + 10]
            steps: list[str] = []
            for p, t in chunk:
                k = len(steps)
                steps += [f"str {t}+{T_KEY:#x}", f"rs4 {t}+{T_HOME:#x}",
                          "mkstr", f"call {MERC_NAME} r{k + 2} {p}", f"str r{k + 2}",
                          f"buf {COMP_SIZE:#x}", f"call {MERC_COMPOSITION} r{k + 5} {p} {c}",
                          f"rs4 r{k + 5}", f"rs4 r{k + 5}+4", f"rs4 r{k + 5}+8", f"rs4 r{k + 5}+0x10",
                          "buf 8", f"call {MERC_HIRE_COST} r{k + 11} {p} {c} 0", f"rs4 r{k + 11}",
                          "buf 8", f"call {MERC_MAINTENANCE} r{k + 14} {p} {c} 0", f"rs4 r{k + 14}",
                          "mkstr", f"call {MERC_CAN_HIRE} {p} {c} 1 r{k + 17}", f"str r{k + 17}",
                          f"rs4 {p}+{M_HIRED:#x}", f"rs4 {p}+{M_DISMISSED:#x}"]
            rr = self.h.vm(*steps)
            for j, (p, t) in enumerate(chunk):
                x = rr[j * per:(j + 1) * per]
                hired = _s32(x[20]) > _s32(x[21])
                out.append({"key": x[0], "name": _clean(x[4]) or x[0], "home": _s32(x[1]) or None,
                            "infantry": _s32(x[7]), "cavalry": _s32(x[8]), "artillery": _s32(x[9]),
                            "manpower": _s32(x[10]), "cost": _fp(x[13]), "monthly": _fp(x[16]),
                            "can_hire": bool(x[18] & 0xFF), "why_not": "" if x[18] & 0xFF else _clean(x[19], 300),
                            "hired": hired, "ptr": p, "template": t})
        if out:
            rec = self.h.vm(*(f"rd1 {x['ptr']}+{M_RECRUIT:#x}" for x in out))
            for x, v in zip(out, rec):
                x["recruiting"] = bool(v & 0xFF)
            self._home_names(out)
        return c, out

    def _home_names(self, rows: list[dict]) -> None:
        """Home province names: CProvince::GetName @0x1612348 = [prov+0x20]+0x10 (a reference, not sret)."""
        homes = sorted({x["home"] for x in rows if x["home"]})
        if not homes:
            return
        steps: list[str] = ["gs"]
        for pid in homes:
            k = len(steps)
            steps += [f"call {GET_PROVINCE} r0 {pid}", f"call {PROV_NAME} r{k}", f"str r{k + 1}"]
        rr = self.h.vm(*steps)
        names = {pid: rr[1 + 3 * i + 2] for i, pid in enumerate(homes) if rr[1 + 3 * i]}
        for x in rows:
            if x["home"]:
                x["home_name"] = names.get(x["home"], "?")

    def _find(self, rows: list[dict], company: str) -> dict:
        c = str(company).strip().lower()
        k = c.replace(" ", "_")
        for x in rows:
            if x["key"].lower() in (k, "merc_" + k, "merc__local_" + k) or x["name"].lower() == c:
                return x
        raise HookError(f"no mercenary company {company}; this country has: {', '.join(x['key'] for x in rows)[:600] or 'none'}")

    @staticmethod
    def _public(x: dict) -> dict:
        return {k: v for k, v in x.items() if k not in ("ptr", "template", "hired", "recruiting")}

    # ================================================================================================================
    # listing
    # ================================================================================================================
    # PROVEN-STATIC: see _companies. A company is listed when it is neither hired (IsHired) nor being recruited
    # (IsInRecruit), i.e. the rows the list window shows with a hire button; can_hire/why_not = CanBeHired(.., true, ..).
    def available(self, tag: str) -> list[dict]:
        """Companies ``tag`` may hire: key, name, home province, regiments, manpower, cost, monthly, can_hire, why_not."""
        _, rows = self._companies(tag)
        return [self._public(x) for x in rows if not x["hired"] and not x["recruiting"]]

    # PROVEN-STATIC: hired = IsHired (dates +0x48 > +0x4c) or recruiting (+0x28). Unit = CMercenaryCompany::GetUnit
    # @0x1545510 (GetObjectFromID(company+0x38) - 0x28, null stays null; it refreshes the cache at +0x30, as every
    # caller does); id/location come from Live.units (same CUnit*). Manpower +0x50 / max +0x54 (CFixedPoint, raw =
    # men, as GetComposition +0x10). UNSURE: the manpower scale until checked against the GUI.
    def hired(self, tag: str) -> list[dict]:
        """Companies in service (or being recruited): key, name, unit id, location, manpower, monthly."""
        _, rows = self._companies(tag)
        rows = [x for x in rows if x["hired"] or x["recruiting"]]
        if not rows:
            return []
        steps: list[str] = []
        for x in rows:
            k = len(steps)
            steps += [f"call {MERC_GET_UNIT} {x['ptr']}", f"rs4 {x['ptr']}+{M_MANPOWER:#x}",
                      f"rs4 {x['ptr']}+{M_MAX_MANPOWER:#x}", f"rd8 {x['ptr']}+{M_LEADER:#x}"]
        rr = self.h.vm(*steps)
        units = {u["ptr"]: u for u in self.live.units(tag)} if any(rr[4 * i] for i in range(len(rows))) else {}
        leaders = [rr[4 * i + 3] for i in range(len(rows)) if rr[4 * i + 3]]
        lnames = dict(zip(leaders, self.h.vm(*(f"str {p}+{L_NAME:#x}" for p in leaders)))) if leaders else {}
        out = []
        for i, x in enumerate(rows):
            up, mp, mx, lp = rr[4 * i:4 * i + 4]
            u = units.get(up)
            out.append({"key": x["key"], "name": x["name"], "state": "hired" if x["hired"] else "recruiting",
                        "unit": u["id"] if u else None, "location": u["loc"] if u else None,
                        "manpower": _s32(mp), "max_manpower": _s32(mx), "monthly": x["monthly"],
                        "leader": lnames.get(lp) if lp else None, "home": x["home"]})
        return out

    # ================================================================================================================
    # hire
    # ================================================================================================================
    # PROVEN-STATIC: the command and its IsValid (see UNIT_CMD notes). The GUI path: pick the company in the military
    # tab (CMercenaryCompanyEntry::OnClick -> CMacroLandUnitsView::OnSelectMercenaryCompany -> CMacroBuildInterface::
    # SetCurrentMercenaryCompany stores {ptr, CID} at +0x60/+0x68), then click a province: DoInterfaceBasedSelection
    # posts CConstructUnitCommand(player, 0, province id, company) without a pre-check (IsValid is the gate).
    # The province check here = CanBeHiredIn(company, country&, province&, CString*) @0x154530a, which is exactly
    # the IsValid call but with a reason string. Default province: the home province if allowed, else the first
    # province of the CanBeHired list (C_PROVS, UNSURE) where CanBeHiredIn is true.
    def can_hire_in(self, tag: str, company: str, province: int) -> dict:
        """Read-only: may ``company`` be hired in ``province`` now? {can, why_not}."""
        c, rows = self._companies(tag)
        x = self._find(rows, company)
        return self._check_in(c, x, [int(province)])[0]

    def _check_in(self, c: int, x: dict, provs: list[int]) -> list[dict]:
        steps: list[str] = ["gs"]
        for pid in provs:
            k = len(steps)
            steps += [f"call {GET_PROVINCE} r0 {pid}", "mkstr", f"call {MERC_CAN_HIRE_IN} {x['ptr']} {c} r{k} r{k + 1}",
                      f"str r{k + 1}"]
        rr = self.h.vm(*steps)
        out = []
        for i, pid in enumerate(provs):
            prov, _, can, why = rr[1 + 4 * i:1 + 4 * (i + 1)]
            if not prov:
                out.append({"province": pid, "can": False, "why_not": f"no province {pid}"})
                continue
            out.append({"province": pid, "can": bool(can & 0xFF), "why_not": "" if can & 0xFF else _clean(why, 300) or "refused"})
        return out

    def _pick_province(self, c: int, x: dict) -> dict:
        cand: list[int] = [x["home"]] if x["home"] else []
        r = self.h.vm(f"rd8 {c}+{C_PROVS:#x}", f"rd4 {c}+{C_NPROVS:#x}")
        if r[0] and r[1]:
            ids = self.h.vm(*(f"rs4 {r[0]}+{4 * i:#x}" for i in range(min(r[1], 300))))
            cand += [_s32(v) for v in ids if _s32(v) > 0 and _s32(v) not in cand]
        if not cand:
            raise HookError(f"{x['key']}: no province to hire it in")
        first = None
        for i in range(0, len(cand), 40):
            for chk in self._check_in(c, x, cand[i:i + 40]):
                first = first or chk
                if chk["can"]:
                    return chk
        return first or {"province": cand[0], "can": False, "why_not": "refused"}

    def hire(self, tag: str, company: str, province: int | None = None, dry: bool = False) -> dict:
        """Hire ``company`` (key like merc_lombard_free_company, key without 'merc_', or its name) in ``province``
        (default: home province or the first allowed one). Costs the hiring cost; the army appears after a short build."""
        c, rows = self._companies(tag)
        x = self._find(rows, company)
        if not x["can_hire"] and not dry:
            raise HookError(f"cannot hire {x['key']} now: {x['why_not'] or 'refused'}")
        chk = self._check_in(c, x, [int(province)])[0] if province is not None else self._pick_province(c, x)
        if not chk["can"] and not dry:
            raise HookError(f"cannot hire {x['key']} in {chk['province']}: {chk['why_not']}")
        res = self._cmd([f"tag {tag.upper()}"], CMD_SIZE, UNIT_CMD, f"r0 0 {chk['province']} {x['ptr']}",
                        f"hire {x['key']} in {chk['province']}", dry)
        res.update({"province": chk["province"], "cost": x["cost"], "monthly": x["monthly"],
                    "can_hire": x["can_hire"] and chk["can"], "why_not": x["why_not"] or chk["why_not"]})
        return res

    # ================================================================================================================
    # disband (= the normal unit disband)
    # ================================================================================================================
    # PROVEN-STATIC: no mercenary-specific command exists; CDisbandCommand(unit, false) (ctor @0x19151d2, size 0x60,
    # what the AI uses for mercenaries) -> Execute -> CGameState::DeleteUnit -> CCountry::RemoveUnit ->
    # DismissMercenaryCompany. IsValid @_ZNK15CDisbandCommand7IsValidEv = CCountry::CanDisbandUnit(country, unit, 0)
    # + sub-unit count; the reason here comes from CanDisbandUnit with a CString (it appends, after a newline).
    def disband(self, tag: str, company_or_unit: str | int, dry: bool = False) -> dict:
        """Dismiss a hired company, by company key/name or by its unit id (the normal disband order)."""
        rows = self.hired(tag)
        if isinstance(company_or_unit, int) or str(company_or_unit).isdigit():
            uid = int(company_or_unit)
            hit = [x for x in rows if x["unit"] == uid]
            if not hit:
                raise HookError(f"unit {uid} is not a mercenary company of {tag}; use the normal disband")
        else:
            c = str(company_or_unit).strip().lower()
            hit = [x for x in rows if x["key"].lower() in (c, "merc_" + c) or x["name"].lower() == c]
            if not hit:
                raise HookError(f"{company_or_unit} is not in service; hired: {', '.join(x['key'] for x in rows) or 'none'}")
        x = hit[0]
        if x["unit"] is None:
            raise HookError(f"{x['key']} is still being recruited (no army yet)")
        u = self.live.unit(tag, x["unit"])
        cp = self.live.country_ptr(tag)
        r = self.h.vm("mkstr", f"call {CAN_DISBAND} {cp} {u['ptr']} r0", "str r0")
        can, why = bool(r[1] & 0xFF), "" if r[1] & 0xFF else _clean(r[2], 300) or "refused"
        if not can and not dry:
            raise HookError(f"cannot disband {x['key']} now: {why}")
        res = self._cmd([], DISBAND_SIZE, DISBAND_CMD, f"{u['ptr']} 0", f"disband {x['key']} (unit {x['unit']})", dry)
        res.update({"unit": x["unit"], "can_disband": can, "why_not": why})
        return res
