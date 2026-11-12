"""Estates on the hook's vm op: list estates, grant / revoke privileges, crown-land interactions, diet agendas.

Built from STATIC reverse engineering only (1.37.5 Linux binary, re/fdis.py on every function called below); nothing
here has run against the game yet. docs/re/estates_prove_plan.txt lists the live tests, safest first.
Conventions (as orders2.py), with one difference:
 - the three estate commands are CCommandTemplate<...> classes whose constructors are INLINED at every GUI site (no
   ctor symbol exists). `_tcmd` therefore rebuilds the object exactly as the GUI lambdas do (operator new(0x58),
   CCommand base fields, CString::CString() at +0x10, the template fields, vptr = vtable+0x10), then IsValid
   (vptr+0x48 = slot 9, checked in each vtable) -> post, or `del` (slot 1 = D0: frees the CString and the object);
 - CCountryTag goes by value (8 bytes as stored at CCountry+0x1c, high byte = valid flag);
 - CFixedPoint results by value (GetCrownLandShare, CEstate::GetInfluence) are called sret: rdi = 8-byte buffer;
 - the vm has no conditional step, so pointers are reused across requests: keep the game PAUSED while using this.
Every public method has a PROVEN-STATIC or UNSURE comment naming what was checked.
Not possible through commands (1.37.5): changing an active agenda, cancelling one, choosing which estate gets land
(sale of titles goes to all estates unless a government attribute says otherwise; that is scripted in the interaction).
"""
from __future__ import annotations

import re
from typing import Any

from .hookclient import HookClient, HookError
from .live import EVENT_CMD, OPT_ALLOWED, Live, _s32

# ---- command objects (inlined ctors; layout from the GUI lambdas) -----------------------------------------------
# CGrantEstatePrivilegeCommand: GUI = (anon)::CEstatePrivilegeEntry::PrivilegeAction()::$_0::_M_invoke @0x1c1d8ce
# CRevokeEstatePrivilegeCommand: GUI = CPrivilegeItem::OnClick()::{lambda()#1}::_M_invoke @0x1ac12da
# CCrownLandInteractionCommand: GUI = CCrownLandInteractionItem::Execute()::{lambda()#1}::_M_invoke @0x1ac0230
GRANT_VT = "@_ZTV28CGrantEstatePrivilegeCommand"          # @0x25bef08 (+0x10 written; slot 9 = IsValid @0xdb63f6)
REVOKE_VT = "@_ZTV29CRevokeEstatePrivilegeCommand"        # @0x25bf108 (slot 9 = IsValid @0xdb675e)
CROWN_VT = "@_ZTV28CCrownLandInteractionCommand"          # @0x25bf268 (slot 9 = IsValid @0xdb6876)
CSTRING_CTOR = "@_ZN7CStringC1Ev"                         # @0x254b128 (std::string SSO init, no other effect)
CMD_SIZE = 0x58                                           # operator new size in all three lambdas
GRANT_ID, REVOKE_ID, CROWN_ID = 14714, 14716, 14717       # CCommandTemplate id, stored at +0x3c (0xffff at +0x40)
# Template fields: grant/revoke +0x48 privilege index, +0x4c estate-type index, +0x50 CCountryTag;
# crown land +0x48 interaction index, +0x4c CCountryTag (8 bytes, unaligned).

# ---- estates ----------------------------------------------------------------------------------------------------
C_ESTATES = 0x2198               # CCountry: std::vector<CEstate> begin/end (GetEstates @0x104fe76, GetEstate @0x104fc7e)
ESTATE_SIZE = 0xC8               # stride in GetEstate / GetCrownLandShare
E_TYPE, E_LOYALTY, E_LAND = 0x10, 0x18, 0x20   # CEstate: CEstateType*, loyalty CFixedPoint (GetFormattedLoyalty),
#                                                land share CFixedPoint (GetFormattedTerritory, GetCrownLandShare)
T_KEY, T_INDEX = 0x10, 0x30      # CEstateType: key std::string (ctor @0x13af2b4), index (ctor; CEstate::GetIndex)
T_PRIVS, T_NPRIVS = 0x4F8, 0x504  # CEstateType: CPdxArray<CEstatePrivilege const*> data / count (Grant IsValidDerived)
PR_INDEX, PR_KEY = 0xC, 0x10     # CEstatePrivilege: index, key std::string (ctor @0x13b54f4)
C_CACHED = 0x2898                # CCountry: CCountryCachedData (GetFormattedInfluence @0x13a52a2)
CROWN_SHARE = "@_ZNK8CCountry17GetCrownLandShareEv"                         # @0xfb65fe sret (rdi res, rsi country)
GET_MODIFIERS = "@_ZNK18CCountryCachedData12GetModifiersEv"                # @0x1576b32 -> CCountryModifiers*, +8 = CModifier
INFLUENCE = "@_ZNK7CEstate12GetInfluenceERK9CModifier"                     # @0x13a5772 sret (rdi res, rsi estate, rdx mod)
HAS_PRIV = "@_ZNK7CEstate12HasPrivilegeEPK16CEstatePrivilege"              # @0x13ab074 -> bool
PRIV_VALID = "@_ZNK16CEstatePrivilege7IsValidER11CEventScopeP7CString"  # GUI list filter (UpdatePrivilegesList @0x1c1b4a3)
SCOPE_CTOR = "@_ZN11CEventScopeC2E11CCountryTagii"
SCOPE_SETTAG = "@_ZN11CEventScope13SetCountryTagE11CCountryTag"
CAN_GRANT = "@_ZNK7CEstate26CanGrantAndAffordPrivilegeERK11CCountryTagPK16CEstatePrivilegeP7CString"  # @0x13aada6
CAN_REVOKE = "@_ZNK7CEstate18CanRevokePrivilegeE11CCountryTagPK16CEstatePrivilegebP7CString"           # @0x13ab0ae

# ---- agendas ----------------------------------------------------------------------------------------------------
C_ACTIVE_AGENDA = 0x21B0         # CCountry: CPdxScopedPtr<CActiveEstateAgenda> (SelectActiveEstateAgenda @0x10c76cc)
C_OFFERED, C_NOFFERED = 0x21C0, 0x21CC   # CCountry: CPdxArray<CActiveEstateAgenda*> data / count = agendas offered by
#                                          the diet event (FindCachedActiveEstateAgenda @0x10c79a8)
AA_AGENDA, AA_DATE, AA_ESTATE = 0x0, 0x8, 0x10   # CActiveEstateAgenda (WriteMembers @0x13b336e)
AGENDA_KEY = 0x10                # CEstateAgenda: key CString
DIET_EVENT = "estate_privileges_and_agendas_events.3"
# Option order of DIET_EVENT in events/EstatePrivilegesAndAgendasEvents.txt (1.37.5); used when the file is unreadable.
DIET_OPTIONS = ("estate_brahmins", "estate_church", "estate_maratha", "estate_rajput", "estate_nobles", "estate_burghers",
                "estate_jains", "estate_vaisyas", "estate_cossacks", "estate_nomadic_tribes", "estate_dhimmi",
                "estate_janissaries", "estate_eunuchs", "estate_qizilbash", "estate_ghulams")

# ---- crown land interactions ------------------------------------------------------------------------------------
CL_DB = "@_ZN18CCrownLandDataBase11GetInstanceEv"          # @0x13b3d38 -> AccessInstance (lazy singleton)
CL_CAN = "@_ZNK8CCountry25CanDoCrownLandInteractionEPK21CCrownLandInteractionP7CString"   # @0x10caf70 -> bool
CL_DATA, CL_COUNT, CL_SIZE = 0x58, 0x64, 0x120             # CPdxArray<CCrownLandInteraction> (GetInteraction(int))
CL_INDEX, CL_KEY = 0xC, 0x18                               # ctor @0x13b3616; key std::string (GetName localises it)
CROWN_ACTIONS = {"sell_titles": "sale_of_titles", "summon_diet": "summon_the_diet", "seize_land": "seize_land"}

_FMT_RE = re.compile(r"§.|£[a-z_]*£?")


def _clean(s: Any, n: int = 0) -> str:
    t = " ".join(_FMT_RE.sub("", s).split()) if isinstance(s, str) else ""
    return t[:n] + "..." if n and len(t) > n else t


def _fp(v: int) -> float:
    return _s32(v) / 1000


class Estates:
    def __init__(self, hook: HookClient, live: Live | None = None):
        self.h = hook
        self.live = live or Live(hook)

    # ---- helpers ------------------------------------------------------------------------------------------------
    def _tcmd(self, pre: list[str], vtable: str, type_id: int, fields: list[tuple[int, int, int | str]], what: str,
              dry: bool = False) -> dict:
        """Build an inlined-ctor CCommandTemplate object like the GUI lambdas, IsValid (vptr+0x48), post or del.
        PROVEN-STATIC (all three lambdas, identical base part): [+8]=0x165, byte +0xc=0, CString() at +0x10,
        qword +0x30=0xffffffff, word +0x38=0, qword +0x3c=0xffff0000|id, dword +0x44=0 (zero bytes come from `new`)."""
        n = len(pre)
        o = f"r{n}"
        steps = pre + [f"new {CMD_SIZE:#x}", f"wr4 {o}+0x8 0x165", f"call {CSTRING_CTOR} {o}+0x10",
                       f"wr8 {o}+0x30 0xffffffff", f"wr8 {o}+0x3c {0xFFFF00000000 | type_id:#x}"]
        steps += [f"wr{w} {o}+{off:#x} {val}" for w, off, val in fields]
        steps += [f"wr8 {o} {vtable}+0x10", f"vcall {o} 0x48"]
        r = self.h.vm(*steps)
        cmd, ok = r[n], bool(r[-1] & 0xFF)
        if dry or not ok:
            self.h.vm(f"del {cmd}")
            if dry:
                return {"what": what, "valid": ok, "posted": False}
            raise HookError(f"{what}: the game refuses this order now")
        if not self.h.vm(f"post {cmd}")[0]:
            raise HookError(f"{what}: could not be posted")
        return {"what": what, "valid": True, "posted": True}

    def _cmd(self, pre: list[str], size: int, ctor: str, args: str, what: str, dry: bool = False) -> dict:
        """Same as Orders2._cmd (exported ctor): used for the diet event answer."""
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

    def _tagv(self, tag: str) -> int:
        """CCountryTag value as stored at CCountry+0x1c (8 bytes). PROVEN (live.py / diplo.py / orders2.py)."""
        c = self.live.country_ptr(tag)
        return self.h.vm(f"rd8 {c}+0x1c")[0]

    # PROVEN-STATIC: estates = std::vector<CEstate> at country+0x2198/+0x21a0, stride 0xc8 (CCountry::GetEstate
    # @0x104fc7e uses /0xc8 via the 0x8f5c28f5c28f5c29 multiply); CEstate+0x10 = CEstateType*, +0x18 loyalty,
    # +0x20 land share (all CFixedPoint x1000). Influence = CEstate::GetInfluence(sret, estate, CModifier) with the
    # CModifier = CCountryCachedData::GetModifiers(country+0x2898)+8, clamped 0..100 exactly as GetFormattedInfluence.
    def _estates(self, tag: str) -> tuple[int, list[dict]]:
        c = self.live.country_ptr(tag)
        r = self.h.vm(f"rd8 {c}+{C_ESTATES:#x}", f"rd8 {c}+{C_ESTATES + 8:#x}")
        b, e = r[0], r[1]
        n = min((e - b) // ESTATE_SIZE, 20) if b and e >= b else 0
        if not n:
            return c, []
        steps = [f"call {GET_MODIFIERS} {c}+{C_CACHED:#x}"]
        for i in range(n):
            p = b + i * ESTATE_SIZE
            k = len(steps)
            steps += [f"rd8 {p}+{E_TYPE:#x}", f"str r{k}+{T_KEY:#x}", f"rd4 r{k}+{T_INDEX:#x}", f"rs4 {p}+{E_LOYALTY:#x}",
                      f"rs4 {p}+{E_LAND:#x}", "buf 8", f"call {INFLUENCE} r{k + 5} {p} r0+8", f"rs4 r{k + 5}",
                      f"rd8 r{k}+{T_PRIVS:#x}", f"rd4 r{k}+{T_NPRIVS:#x}"]
        rr = self.h.vm(*steps)
        out = []
        for i in range(n):
            t, key, idx, loy, land, _, _, infl, pd, pn = rr[1 + i * 10:1 + (i + 1) * 10]
            out.append({"key": key, "index": idx, "loyalty": _fp(loy), "influence": min(max(_fp(infl), 0.0), 100.0),
                        "land": _fp(land), "ptr": b + i * ESTATE_SIZE, "type": t, "_privs": (pd, min(pn, 100))})
        return c, out

    def _find_estate(self, rows: list[dict], estate: str) -> dict:
        e = estate.lower()
        for x in rows:
            if x["key"] in (e, "estate_" + e):
                return x
        raise HookError(f"no estate {estate}; this country has: {', '.join(x['key'] for x in rows) or 'none'}")

    # PROVEN-STATIC: the privileges an estate type allows = CPdxArray<CEstatePrivilege const*> at type+0x4f8, count
    # +0x504 (the membership test in CGrant/CRevokeEstatePrivilegeCommand::IsValidDerived @0xf870ca/@0xf872fa).
    # CEstatePrivilege index +0xc (what both GUI lambdas pass), key std::string +0x10.
    # CEstate::HasPrivilege(estate, priv) @0x13ab074 (std::map at +0x90). CanGrantAndAffordPrivilege(rdi estate,
    # rsi CCountryTag const&, rdx priv, rcx CString* reason) @0x13aada6 = what Grant IsValid calls (with rcx=0).
    def _privileges(self, tagv: int, row: dict) -> list[dict]:
        pd, pn = row["_privs"]
        if not pd or not pn:
            return []
        ptrs = self.h.vm(*(f"rd8 {pd}+{8 * k:#x}" for k in range(pn)))
        # is_valid trigger (what the GUI list filters on): CEstatePrivilege::IsValid(scope, 0) with a country scope.
        steps = ["buf 8", f"wr8 r0 {tagv:#x}", "buf 0xd0", f"call {SCOPE_CTOR} r2 {tagv:#x} 0 0", f"call {SCOPE_SETTAG} r2 {tagv:#x}"]
        for pp in ptrs:
            k = len(steps)
            steps += [f"rd4 {pp}+{PR_INDEX:#x}", f"str {pp}+{PR_KEY:#x}", f"call {HAS_PRIV} {row['ptr']} {pp}", "mkstr",
                      f"call {CAN_GRANT} {row['ptr']} r0 {pp} r{k + 3}", f"str r{k + 3}", f"call {PRIV_VALID} {pp} r2 0"]
        rr = self.h.vm(*steps)
        out = []
        for j, pp in enumerate(ptrs):
            idx, key, has, _, can, why, valid = rr[5 + j * 7:5 + (j + 1) * 7]
            if not valid & 0xFF and not has & 0xFF:
                continue  # not offered to this country at all
            out.append({"key": key, "index": idx, "granted": bool(has & 0xFF), "can_grant": bool(can & 0xFF) and not has & 0xFF,
                        "why_not": "" if can & 0xFF else _clean(why), "ptr": pp})
        granted = [x for x in out if x["granted"]]
        if granted:
            st: list[str] = []
            for x in granted:
                k = len(st)
                st += ["mkstr", f"call {CAN_REVOKE} {row['ptr']} {tagv:#x} {x['ptr']} 0 r{k}", f"str r{k}"]
            rv = self.h.vm(*st)
            for j, x in enumerate(granted):
                x["can_revoke"] = bool(rv[j * 3 + 1] & 0xFF)
                x["why_not"] = "" if x["can_revoke"] else _clean(rv[j * 3 + 2])
        return out

    def _find_priv(self, privs: list[dict], estate_key: str, privilege: str) -> dict:
        p = privilege.lower()
        for x in privs:
            if x["key"] in (p, f"{estate_key}_{p}"):
                return x
        raise HookError(f"{estate_key} has no privilege {privilege}; keys: {', '.join(x['key'] for x in privs)[:600]}")

    # ================================================================================================================
    # listing
    # ================================================================================================================
    # PROVEN-STATIC: crown land = CCountry::GetCrownLandShare (sret) = 100 - sum(estate land shares); active agenda =
    # CActiveEstateAgenda* at country+0x21b0 (0 = none) {+0 CEstateAgenda* (key CString +0x10), +8 CEU3Date,
    # +0x10 CEstateType*}; agendas offered by a pending diet = CPdxArray at +0x21c0 (count +0x21cc), same element type.
    # UNSURE: whether +8 of CActiveEstateAgenda is the start or the deadline date (reported raw as an int).
    def list(self, tag: str, detail: bool = False) -> dict:
        """Estates of ``tag``: loyalty, influence, land share, granted / grantable privileges; crown land; agendas;
        crown-land actions (sell_titles / summon_diet / seize_land) with can_use. detail=True adds refusal reasons."""
        tagv = self._tagv(tag)
        c, rows = self._estates(tag)
        crown, active, offered = self._agenda_state(c, rows)
        out_rows = []
        for x in rows:
            privs = self._privileges(tagv, x)
            row = {"key": x["key"], "loyalty": x["loyalty"], "influence": x["influence"], "land": x["land"],
                   "granted": [p["key"] for p in privs if p["granted"]],
                   "grantable": [p["key"] for p in privs if p["can_grant"]]}
            if detail:
                row["not_revocable"] = {p["key"]: _clean(p["why_not"], 200) for p in privs if p["granted"] and not p.get("can_revoke")}
                row["not_grantable"] = {p["key"]: _clean(p["why_not"], 160) for p in privs if not p["granted"] and not p["can_grant"]}
            out_rows.append(row)
        return {"estates": out_rows, "crown_land": crown, "active_agenda": active,
                "offered_agendas": offered, "actions": [{k: v for k, v in a.items() if k != "ptr" and (detail or k != "index")}
                                                        for a in self.interactions(tag)]}

    def _agenda_state(self, c: int, rows: list[dict]) -> tuple[float, dict | None, list[dict]]:
        """(crown land %, active agenda or None, offered agendas)."""
        r = self.h.vm("buf 8", f"call {CROWN_SHARE} r0 {c}", "rs4 r0", f"rd8 {c}+{C_ACTIVE_AGENDA:#x}",
                      f"rd8 {c}+{C_OFFERED:#x}", f"rd4 {c}+{C_NOFFERED:#x}")
        crown, active, od, on = _fp(r[2]), r[3], r[4], min(r[5], 20)
        by_type = {x["type"]: x["key"] for x in rows}
        act = self._agendas([active] if active else [], by_type)
        offered = self._agendas(self.h.vm(*(f"rd8 {od}+{8 * k:#x}" for k in range(on))) if od and on else [], by_type)
        return crown, act[0] if act else None, offered

    def _agendas(self, ptrs: list[int], by_type: dict[int, str]) -> list[dict]:
        ptrs = [p for p in ptrs if p]
        if not ptrs:
            return []
        steps: list[str] = []
        for p in ptrs:
            k = len(steps)
            steps += [f"rd8 {p}+{AA_AGENDA:#x}", f"str r{k}+{AGENDA_KEY:#x}", f"rd8 {p}+{AA_ESTATE:#x}", f"rd4 {p}+{AA_DATE:#x}"]
        rr = self.h.vm(*steps)
        return [{"agenda": rr[k * 4 + 1], "estate": by_type.get(rr[k * 4 + 2], "?"), "date": rr[k * 4 + 3]}
                for k in range(len(ptrs))]

    # ================================================================================================================
    # privileges
    # ================================================================================================================
    # PROVEN-STATIC: CGrantEstatePrivilegeCommand IsValidDerived @0xf870ca: tag valid and not rebels/pirates/natives,
    # estate type = CEstateDatabase::GetEstateFromIndex([+0x4c]) (index <= 0 -> null type -> refused), privilege =
    # CEstatePrivilegeDataBase::GetPrivilege([+0x48]), privilege in the type's list, country has the estate, not
    # already granted, CanGrantAndAffordPrivilege. ExecuteDerived @0xf8703a: AddPrivilege, UsePower(ADM,
    # ESTATE_PRIVILEGE_ADMIN_COST, reason 0x2a), UpdateCountryAfterPrivilegeChange. GUI path: confirm dialog then the
    # lambda (we post directly = the dialog's accept).
    def grant_privilege(self, tag: str, estate: str, privilege: str, dry: bool = False) -> dict:
        """Grant ``privilege`` (key, or key without the 'estate_x_' prefix) to ``estate`` ('nobles' or 'estate_nobles')."""
        tagv = self._tagv(tag)
        _, rows = self._estates(tag)
        e = self._find_estate(rows, estate)
        p = self._find_priv(self._privileges(tagv, e), e["key"], privilege)
        if p["granted"] and not dry:
            raise HookError(f"{p['key']} is already granted")
        if not p["can_grant"] and not dry:
            raise HookError(f"cannot grant {p['key']} now: {_clean(p['why_not'], 400) or 'refused'}")
        res = self._tcmd([], GRANT_VT, GRANT_ID, [(4, 0x48, p["index"]), (4, 0x4C, e["index"]), (8, 0x50, f"{tagv:#x}")],
                         f"grant {p['key']} to {e['key']}", dry)
        res.update({"can_grant": p["can_grant"], "why_not": _clean(p["why_not"], 400)})
        return res

    # PROVEN-STATIC: CRevokeEstatePrivilegeCommand IsValidDerived @0xf872fa: same lookups, HasPrivilege, then
    # CEstate::CanRevokePrivilege(estate, tag, priv, false, 0) @0x13ab0ae. ExecuteDerived @0xf87252: RemovePrivilege
    # then loyalty loss (CCountry::GetLoyaltyChangeOnPrivilegeRevoked, as the confirm text in CPrivilegeItem::OnClick).
    def revoke_privilege(self, tag: str, estate: str, privilege: str, dry: bool = False) -> dict:
        """Revoke a granted ``privilege`` of ``estate`` (costs estate loyalty)."""
        tagv = self._tagv(tag)
        _, rows = self._estates(tag)
        e = self._find_estate(rows, estate)
        p = self._find_priv(self._privileges(tagv, e), e["key"], privilege)
        if not p["granted"] and not dry:
            raise HookError(f"{p['key']} is not granted to {e['key']}")
        if p["granted"] and not p.get("can_revoke") and not dry:
            raise HookError(f"cannot revoke {p['key']} now: {_clean(p['why_not'], 400) or 'refused'}")
        res = self._tcmd([], REVOKE_VT, REVOKE_ID, [(4, 0x48, p["index"]), (4, 0x4C, e["index"]), (8, 0x50, f"{tagv:#x}")],
                         f"revoke {p['key']} from {e['key']}", dry)
        res["can_revoke"] = bool(p.get("can_revoke"))
        return res

    # ================================================================================================================
    # crown land interactions (common/estate_crown_land/00_interactions.txt: sale_of_titles, summon_the_diet, seize_land)
    # ================================================================================================================
    # PROVEN-STATIC: CCrownLandDataBase::GetInstance() -> AccessInstance (lazy singleton); interactions =
    # CPdxArray<CCrownLandInteraction> data [db+0x58], count [db+0x64], stride 0x120 (GetInteraction(int) @0x13b43e4:
    # i*9<<5). CCrownLandInteraction ctor @0x13b3616: index +0xc; std::string at +0x18 is what GetName localises and
    # the loc file has 'sale_of_titles' etc. as keys. CCountry::CanDoCrownLandInteraction(country, inter, CString*)
    # @0x10caf70 = cooldown (GetInteractionLastUsedDate + cooldown_months) + trigger; it is the command's IsValid.
    def interactions(self, tag: str) -> list[dict]:
        """Crown-land actions: key, can_use now, why_not (cooldown date or failed trigger)."""
        c = self.live.country_ptr(tag)
        r = self.h.vm(f"call {CL_DB}", f"rd8 r0+{CL_DATA:#x}", f"rd4 r0+{CL_COUNT:#x}")
        data, n = r[1], min(r[2], 20)
        if not data or not n:
            return []
        steps: list[str] = []
        for i in range(n):
            el = data + i * CL_SIZE
            k = len(steps)
            steps += [f"rd4 {el}+{CL_INDEX:#x}", f"str {el}+{CL_KEY:#x}", "mkstr", f"call {CL_CAN} {c} {el} r{k + 2}", f"str r{k + 2}"]
        rr = self.h.vm(*steps)
        out = []
        for i in range(n):
            idx, key, _, can, why = rr[i * 5:(i + 1) * 5]
            out.append({"key": key, "index": idx, "can_use": bool(can & 0xFF), "why_not": "" if can & 0xFF else _clean(why, 300),
                        "ptr": data + i * CL_SIZE})
        return out

    # PROVEN-STATIC: CCrownLandInteractionCommand fields +0x48 = [interaction+0xc], +0x4c = CCountryTag (lambda
    # @0x1ac0295/@0x1ac0298); IsValidDerived @0xf874a0 = valid tag + GetInteraction(index) + CanDoCrownLandInteraction;
    # ExecuteDerived @0xf8746e = CCrownLandInteraction::ExecuteEffect(tag) (the script effect, incl. the cooldown).
    # UNSURE: the GUI first shows a confirm dialog built in CCrownLandInteractionItem::Execute; we post directly.
    def crown_action(self, tag: str, key: str, dry: bool = False) -> dict:
        """Run crown-land interaction ``key`` (sale_of_titles / summon_the_diet / seize_land, or the method names)."""
        key = CROWN_ACTIONS.get(key, key)
        acts = self.interactions(tag)
        hit = [a for a in acts if a["key"] == key]
        if not hit:
            raise HookError(f"no crown-land interaction {key}; known: {', '.join(a['key'] for a in acts) or 'none'}")
        a = hit[0]
        if not a["can_use"] and not dry:
            raise HookError(f"{key} not possible now: {a['why_not'] or 'refused'}")
        tagv = self._tagv(tag)
        res = self._tcmd([], CROWN_VT, CROWN_ID, [(4, 0x48, a["index"]), (8, 0x4C, f"{tagv:#x}")], key, dry)
        res.update({"can_use": a["can_use"], "why_not": a["why_not"]})
        return res

    def sell_titles(self, tag: str, dry: bool = False) -> dict:
        """Sale of titles: ducats for crown land (needs crown land >= 10%; 5-year cooldown)."""
        return self.crown_action(tag, "sale_of_titles", dry)

    def seize_land(self, tag: str, dry: bool = False) -> dict:
        """Seize land: crown land from the estates, loyalty loss (5-year cooldown)."""
        return self.crown_action(tag, "seize_land", dry)

    def summon_diet(self, tag: str, dry: bool = False) -> dict:
        """Summon the diet: fires event estate_privileges_and_agendas_events.3; then diet_options / choose_agenda."""
        return self.crown_action(tag, "summon_the_diet", dry)

    # ================================================================================================================
    # diet agendas (picked by answering the diet event; no agenda command exists for the player)
    # ================================================================================================================
    # PROVEN-STATIC: the summon_the_diet effect fires DIET_EVENT, whose immediate block generates the offers
    # (generate_estate_agenda -> the cached array at country+0x21c0) and each option `start_estate_agenda = X`
    # (CCountry::SelectActiveEstateAgenda @0x10c76cc moves that estate's cached offer to +0x21b0). Option i (0-based,
    # file order) is allowed when estate X was picked (has_country_flag trigger), checked with CEventOption::IsAllowed
    # like Live.events. Answer = CSelectEventOptionCommand (size 0x138, Live.answer, proven live) + close the window.
    # The event has 15 options; Live.events only evaluates 12, so the options are read here.
    # CCreateNewEstateAgendaCommand exists but only CGenerateEstateAgendaEffect::Execute @0x134aa69 builds it.
    def _diet_estates(self) -> tuple[str, ...]:
        """Estate of each DIET_EVENT option, in file order (the `start_estate_agenda = X` lines of the event block).
        Read directly: gamedata.event_def only returns the first option (its depth counter re-reads the closing brace)."""
        try:
            from . import paths
            txt = (paths.game_dir() / "events" / "EstatePrivilegesAndAgendasEvents.txt").read_text("latin1")
            m = re.search(r"\bid\s*=\s*" + re.escape(DIET_EVENT) + r"\s", txt)
            end = txt.find("country_event", m.end()) if m else -1
            body = re.sub(r"#[^\n]*", "", txt[m.end():end if end > 0 else len(txt)]) if m else ""
            keys = tuple(re.findall(r"start_estate_agenda\s*=\s*(\w+)", body))
            if len(keys) == len(re.findall(r"\boption\s*=\s*\{", body)) and keys:
                return keys
        except OSError:
            pass
        return DIET_OPTIONS

    def diet_options(self, tag: str) -> dict:
        """The pending diet of ``tag``: {"pid", "options": [{option (1-based), estate, agenda}]} (allowed ones only)."""
        ev = [x for x in self.live.events(tag) if x["id"] == DIET_EVENT]
        if not ev:
            raise HookError(f"{tag} has no pending diet event (summon_diet first)")
        x = ev[0]
        n = min(x["n"], 20)
        ptrs = self.h.vm(*(f"rd8 {x['opts']}+{8 * i:#x}" for i in range(n)))
        allowed = [bool(v & 0xFF) for v in self.h.vm(*(f"call {OPT_ALLOWED} {p} {x['scope']}" for p in ptrs))]
        keys = self._diet_estates()
        c, rows = self._estates(tag)
        offer = {a["estate"]: a["agenda"] for a in self._agenda_state(c, rows)[2]}
        opts = [{"option": i + 1, "estate": keys[i] if i < len(keys) else "?", "agenda": offer.get(keys[i] if i < len(keys) else "")}
                for i in range(n) if allowed[i]]
        return {"pid": x["pid"], "options": opts, "_ev": x}

    def choose_agenda(self, tag: str, estate: str, dry: bool = False) -> dict:
        """Answer the pending diet by picking ``estate``'s agenda ('nobles' or 'estate_nobles')."""
        d = self.diet_options(tag)
        e = estate.lower()
        hit = [o for o in d["options"] if o["estate"] in (e, "estate_" + e)]
        if not hit:
            raise HookError(f"the diet offers: {', '.join(o['estate'] for o in d['options']) or 'nothing'}")
        o, x = hit[0], d["_ev"]
        res = self._cmd([], 0x138, EVENT_CMD, f"{x['event']} {x['scope']} {o['option'] - 1} {x['pid']}",
                        f"diet: agenda of {o['estate']} ({o['agenda']})", dry)
        if res["posted"]:
            self.live.close_event_windows(x["pid"])
        res["agenda"] = o["agenda"]
        return res
