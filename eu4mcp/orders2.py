"""Colonisation-campaign orders on the hook's vm op: exploration, leaders, advisors, merchants, colonists.

Built from STATIC reverse engineering only (1.37.5 Linux binary, re/fdis.py on every function called below); nothing
here has run against the game yet. docs/re/orders2_prove_plan.txt lists the live tests, safest first.
Conventions (as live.py / diplo.py):
 - every command is new(size) -> ctor -> IsValid (vptr+0x48, checked to be IsValid in each class's vtable) -> post;
   refused or dry-run objects are freed with `del` (slot 1 = D0, the deleting destructor);
 - CCountryTag goes by value in one register (`tag XXX`); the CCountryTag high byte (bit 56..63) is the "valid" flag;
 - functions returning CFixedPoint by value (CAdvisor::GetPurchaseCost / GetMonthlyCost) are called sret:
   rdi = 4-byte result buffer, rsi = this;
 - the vm has no conditional step, so lists of unknown length are walked in several requests and pointers are reused
   across requests: keep the game PAUSED while using this module.
Every public method has a PROVEN-STATIC or UNSURE comment naming what was checked.
"""
from __future__ import annotations

import re
from typing import Any

from .hookclient import HookClient, HookError
from .live import GET_PROVINCE, Live, _s32

OBJ_FROM_ID = "@_ZN16CReferenceObject15GetObjectFromIDERK3CID"   # @0x1fea3e8 (rdi = CID const*) -> object or 0
OP_DELETE = "@_ZdlPv"

# ---- exploration ------------------------------------------------------------------------------------------------
EXPLORE_CMD = "@_ZN21CToggleExploreCommandC1EPK5CUnit18EStopMissionReasoni20EXPLORATION_MISSIONS"   # @0x191b856
STOP_MISSION_CMD = "@_ZN23CStopUnitMissionCommandC1EPK5CUnit18EStopMissionReason3CID"                # @0x191ff86
SELECTABLE_REGIONS = "@_ZN24CExplorationNavalMission20GetSelectableRegionsERK8CCountryPK5CNavyR6CArrayIiES8_"  # @0x15648c6
UNIT_CAN_EXPLORE = "@_ZNK5CUnit10CanExploreEP7CString"            # @0x190135c (rdi unit, rsi CString* or 0) -> bool
UNIT_MISSION_TYPE = "@_ZNK5CUnit14GetMissionTypeEv"              # @0x18f3a06 ([unit+0x1f0]->vptr+0x58, 0 if none)
MAP_PTR = "@_ZN4CMap5_pMapE"                                     # CMap*; regions std::vector<CRegion*> at +0x510
EXPLORE_SIZE = STOP_SIZE = 0x68                                  # operator new size in CExploreMissionEntry::Select / NavalEvade
STOP_REASON = 0x36                                               # EStopMissionReason every GUI site passes
EXPLORE_MISSIONS = {"land": 1, "sea": 2, "coast": 3, "circumnavigate": 4}
MISSION_TYPES = {5: "explore_sea", 11: "circumnavigate", 12: "explore_land"}   # CUnit mission GetType values
U_LEADER = 0xD0                                                  # CUnit: CLeader* (CUnit::CanExplore @0x19013bc)
MAP_REGIONS, REGION_KEY, REGION_NAME = 0x510, 0x10, 0x30

# ---- leaders ----------------------------------------------------------------------------------------------------
ASSIGN_LEADER_CMD = "@_ZN20CAssignLeaderCommandC1EP5CUnitP7CLeader"   # @0x14ba4da size 0x68
NULL_LEADER = "@_ZN11CNullLeader13GetNullLeaderEv"                    # @0x14adf6e
LEADER_CAN_REASSIGN = "@_ZNK7CLeader21CanBeReassignedToUnitEPK5CUnitP7CString"   # @0x14af7ce
C_LEADERS = 0x1458                                                    # CCountry: CList<CLeader*> (AddLeader @0xff1f36)
L_ID, L_NAME, L_TYPE, L_SIEGE, L_MANEUVER, L_FIRE, L_SHOCK, L_UNIT = 0x10, 0x18, 0x38, 0x3C, 0x40, 0x44, 0x48, 0x50
LEADER_TYPES = ("general", "admiral", "conquistador", "explorer")

# ---- advisors ---------------------------------------------------------------------------------------------------
ADV_AVAILABLE = "@_ZN18CAdvisorManagement12GetAvailableE11CCountryTag"            # @0xe94d22
ADV_PURCHASE = "@_ZNK8CAdvisor15GetPurchaseCostE11CCountryTag"                    # @0xe8bd06 sret
ADV_MONTHLY = "@_ZNK8CAdvisor14GetMonthlyCostE11CCountryTagP7CString"            # @0xe8bdb2 sret
ADV_CAN_HIRE = "@_ZNK8CAdvisor7CanHireE11CCountryTag"                             # @0xe8cf76
ADV_CAN_FIRE = "@_ZNK8CAdvisor7CanFireEP7CString"                                # @0xe8e014
HIRE_CMD = "@_ZN21CChangeAdvisorCommandC1E11CCountryTagPK8CAdvisor14MonarchAbility"   # @0xea38d8 size 0x68
FIRE_CMD = "@_ZN22CDismissAdvisorCommandC1E11CCountryTagP8CAdvisor"                  # @0xea3f98 size 0x60
GS_ADVISORS = 0x1BF0                                       # CAdvisorManagement in CGameState (CChangeAdvisorCommand::IsValid)
POOL_HEAD, NODE_CID, NODE_NEXT = 0x10, 0x8, 0x18           # CAdvisorPool list (HasAdvisor @0xe94ac0)
A_ID, A_NAME, A_SKILL, A_TYPE = 0x10, 0x18, 0x38, 0x280     # CAdvisor (WriteMembers @0xe8b7a8, GetSkill, GetType)
AT_KEY, AT_ABILITY = 0x110, 0xEC                           # CAdvisorType (ctor @0xe8ecfc; GetMonthlyCost reads +0xec)
C_ADVISORS = 0x14D0                                        # CCountry: CAdvisor*[3] by MonarchAbility (CSelectNewAdvisor::Select)
ABILITY = ("ADM", "DIP", "MIL")

# ---- trade ------------------------------------------------------------------------------------------------------
SEND_TRADER_CMD = "@_ZN18CSendTraderCommandC1EP9CProvince11CCountryTagP12CEnvoyMember17TraderMissionType"  # @0xf46c4c 0x60
CANCEL_TRADER_CMD = "@_ZN20CCancelTraderCommandC1EPK10CTradeNode11CCountryTag"   # @0xf47180 size 0x58
STEER_CMD = "@_ZN18CSteerTradeCommandC1E11CCountryTagii"                         # @0x12926d2 size 0x58
NODE_HAS_TRADER = "@_ZNK10CTradeNode9HasTraderE11CCountryTag"                    # @0x175fea2
NODE_STEER_TO = "@_ZNK10CTradeNode10GetSteerToE11CCountryTag"                    # @0x1761df0
NODE_CAN_STEER = "@_ZNK10CTradeNode8CanSteerE11CCountryTagP7CString"             # @0x1760bb8
ENVOY_LOCATION = "@_ZNK12CEnvoyMember11GetLocationE11CCountryTag"                # @0x1392750 -> CProvince*
GS_NODES, GS_NODE_COUNT, NODE_SIZE = 0x2148 + 0x18, 0x2148 + 0x24, 0x138   # CTradeManager@gs+0x2148, CPdxArray<CTradeNode> +0x10
N_POWER, N_DEF, N_INDEX = 0x18, 0xA8, 0x120                # CTradeNode: per-country array (stride 0xc0), definition, index
POWER_STRIDE, P_MISSION = 0xC0, 0xAC                       # CTradePower: +0xa8 steer, +0xac mission byte, +0xae trader
D_KEY, D_LOCATION, D_LINKS, LINK_SIZE, LINK_KEY = 0x10, 0xD4, 0x98, 0x78, 0x10
C_ENVOYS = 0x1470                                          # CCountry: CEnvoy*[] by EnvoyType (0 colonists, 1 merchants)
E_MEMBERS, M_CONSTRUCTION, M_BUSY, M_NAME, M_ID = 0x20, 0x10, 0x18, 0x20, 0x44
TRADER_MISSIONS = {"collect": 0, "steer": 1}

# ---- colonists --------------------------------------------------------------------------------------------------
COLONIST_CMD = "@_ZN20CSendColonistCommandC1E11CCountryTagPK9CProvinceb"          # @0x1108704 size 0x60
CANCEL_COLONIST_CMD = "@_ZN22CCancelColonistCommandC1E11CCountryTagPK9CProvinceb"  # @0x1108c20 size 0x58
POTENTIAL_COLONY = "@_ZNK8CCountry30IsPotentialColonistDestinationEPK9CProvince"  # @0x102e97c
CAN_PLACE_COLONIST = "@_ZNK8CCountry16CanPlaceColonistEPK9CProvince"              # @0x102ca06
HAS_COLONIST = "@_ZNK9CProvince11HasColonistEv"                                   # @0x1655666
GS_PROV_VEC, GS_PROV_BASE, PROVINCE_SIZE, P_OWNER, P_ID = 0x1C60, 0x1C78, 0x2D20, 0x448, 0x1C

_KEY_RE = re.compile(r"^[A-Za-z0-9_]+$")
_FMT_RE = re.compile(r"§.|£[a-z_]*£?")


def _clean(s: Any) -> str:
    return _FMT_RE.sub("", s).strip() if isinstance(s, str) else ""


def _tag_index(tagv: int) -> int:
    """CCountryTag -> country index (int16 at bits 32..47, as every callee does `shr 0x20; movsx`)."""
    i = (int(tagv) >> 32) & 0xFFFF
    return i - 0x10000 if i & 0x8000 else i


def _tag_str(v: int) -> str:
    return (int(v) & 0xFFFFFF).to_bytes(3, "little").decode("latin1")


class Orders2:
    def __init__(self, hook: HookClient, live: Live | None = None):
        self.h = hook
        self.live = live or Live(hook)

    # ---- helpers ------------------------------------------------------------------------------------------------
    def _cmd(self, pre: list[str], size: int, ctor: str, args: str, what: str, dry: bool = False) -> dict:
        """new(size); ctor(cmd, args); IsValid (vptr+0x48); then post, or del when refused / dry.
        Same sequence as Live.command (proven live for recruit/build/merge...)."""
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
        """CCountryTag value as stored at CCountry+0x1c (8 bytes). PROVEN (live.py / diplo.py)."""
        c = self.live.country_ptr(tag)
        return self.h.vm(f"rd8 {c}+0x1c")[0]

    def _unit(self, tag: str, unit_id: int) -> dict:
        return self.live.unit(tag, unit_id)

    # ================================================================================================================
    # 1. exploration
    # ================================================================================================================
    # PROVEN-STATIC: CUnit::GetMissionType @0x18f3a06 (null-safe), CUnit::CanExplore(CString*) @0x190135c (fills the
    # reason into a std::string when rsi != 0), leader at unit+0xd0 (read by CanExplore), CLeader type at +0x38.
    # Mission type values: CExplorationNavalMission::GetType=5, CCircumNavigateNavalMission=11, CExplorationArmyMission=12.
    def explorers(self, tag: str) -> list[dict]:
        """Units of ``tag`` with their exploration state: can_explore (+ reason text when not), mission, leader type."""
        units = self.live.units(tag)
        if not units:
            return []
        steps: list[str] = []
        for u in units:
            b = len(steps)
            steps += [f"call {UNIT_MISSION_TYPE} {u['ptr']}", "mkstr", f"call {UNIT_CAN_EXPLORE} {u['ptr']} r{b + 1}",
                      f"str r{b + 1}", f"rd8 {u['ptr']}+{U_LEADER:#x}"]
        r = self.h.vm(*steps)
        out = []
        for k, u in enumerate(units):
            mt, _, can, why, leader = r[k * 5:k * 5 + 5]
            out.append({"id": u["id"], "name": u["name"], "navy": u["navy"], "loc": u["loc"], "mission": mt & 0xFFFFFFFF,
                        "exploring": MISSION_TYPES.get(mt & 0xFFFFFFFF), "can_explore": bool(can & 0xFF),
                        "why_not": "" if can & 0xFF else _clean(why), "leader": leader})
        led = [x for x in out if x["leader"]]
        if led:
            lt = self.h.vm(*(f"rd4 {x['leader']}+{L_TYPE:#x}" for x in led))
            for x, t in zip(led, lt):
                x["leader_type"] = LEADER_TYPES[t] if t < 4 else t
        for x in out:
            x.pop("leader")
        return [x for x in out if x["can_explore"] or x["exploring"] or "leader_type" in x]

    # PROVEN-STATIC: CExplorationNavalMission::GetSelectableRegions @0x15648c6 is static: rdi=CCountry&, rsi=CNavy*
    # (CNavy::GetNavy @0x1555164 returns this, so the CUnit* of a navy is the CNavy*), rdx=coast CArray<int>&,
    # rcx=sea CArray<int>&; CArray<int> is a bare std::vector<int> (push_back at [v+8] vs [v+0x10], realloc_insert(v)).
    # CExploreMissionDialog ctor @0x1c41a0f: entries of the rdx array get mission 3 (coast), of rcx mission 2 (sea).
    # Region id = index in CMap+0x510 (CMap::AddRegionToMap @0x14cc57d stores it at region+0x74; key std::string at +0x10,
    # localised name at +0x30, CRegionBase ctor @0x1695b04). The vectors' buffers are ours: freed with operator delete.
    def explore_regions(self, tag: str, unit_id: int) -> dict:
        """Regions the fleet ``unit_id`` may explore now: {"sea": [...], "coast": [...]}, each {id, key, name}."""
        u = self._unit(tag, unit_id)
        if not u["navy"]:
            raise HookError(f"{u['name']} is an army: armies explore land (mission 'land'), no region list")
        c = self.live.country_ptr(tag)
        r = self.h.vm("buf 0x18", "buf 0x18", f"call {SELECTABLE_REGIONS} {c} {u['ptr']} r0 r1",
                      "rd8 r0", "rd8 r0+8", "rd8 r1", "rd8 r1+8")
        vecs = {"coast": (r[3], r[4]), "sea": (r[5], r[6])}
        ids: dict[str, list[int]] = {"coast": [], "sea": []}
        try:
            steps, where = [], []
            for k, (b, e) in vecs.items():
                n = (e - b) // 4 if b and e >= b else 0
                for i in range(min(n, 500)):
                    steps.append(f"rd4 {b}+{4 * i:#x}")
                    where.append(k)
            if steps:
                for k, v in zip(where, self.h.vm(*steps)):
                    ids[k].append(_s32(v))
        finally:
            frees = [f"call {OP_DELETE} {b}" for b, _ in vecs.values() if b]
            if frees:
                self.h.vm(*frees)
        names = self._region_names(sorted(set(ids["coast"] + ids["sea"])))
        return {k: [{"id": i, **names.get(i, {"key": "?", "name": "?"})} for i in v] for k, v in ids.items()}

    def _region_names(self, ids: list[int]) -> dict[int, dict]:
        if not ids:
            return {}
        r = self.h.vm(f"rd8 {MAP_PTR}", f"rd8 r0+{MAP_REGIONS:#x}", f"rd8 r0+{MAP_REGIONS + 8:#x}")
        base, n = r[1], (r[2] - r[1]) // 8 if r[1] else 0
        ok = [i for i in ids if 0 <= i < n]
        if not ok:
            return {}
        steps: list[str] = []
        for i in ok:
            b = len(steps)
            steps += [f"rd8 {base}+{8 * i:#x}", f"str r{b}+{REGION_KEY:#x}", f"str r{b}+{REGION_NAME:#x}"]
        rr = self.h.vm(*steps)
        return {i: {"key": rr[k * 3 + 1], "name": _clean(rr[k * 3 + 2])} for k, i in enumerate(ok)}

    # PROVEN-STATIC: CToggleExploreCommand ctor @0x191b856 (rdi this, rsi CUnit*, edx EStopMissionReason -> byte +0x60,
    # ecx int -> +0x58, r8d EXPLORATION_MISSIONS -> +0x5c), size 0x68 and reason 0x36 from CExploreMissionEntry::Select
    # @0x1c4169c / CNavalExplorationDialog::OnAccept @0x1c425f0 / CToggleLandExploreAction::DoInternal @0x1f9c0c0.
    # Execute @0x191b8e4 jump table @0x26fd2f8: 1 -> army: StopMission if exploring else SetMission<CExplorationArmyMission>;
    # 2,3 -> navy SetMission<CExplorationNavalMission>(id, mission); 4 -> navy SetMission<CCircumNavigateNavalMission>.
    # IsValid @0x191bb32: mission 0 always invalid; 1 needs 0 < id < province count (GUI passes the unit's location id);
    # 2/3 need 0 <= id < region count; then not a mothballed navy and (IsExploring or CanExplore).
    # UNSURE: the circumnavigation entry is only offered by the dialog under an EUnitMission==5 condition (dialog ctor
    # @0x1c41957); IsValid does not check it, Execute may then still refuse silently.
    def explore(self, tag: str, unit_id: int, mission: str = "sea", region: int | str | None = None, dry: bool = False) -> dict:
        """Start exploring. mission: 'sea' / 'coast' (fleet + region id or key from explore_regions),
        'circumnavigate' (fleet), 'land' (conquistador army; TOGGLES: a second call stops it)."""
        if mission not in EXPLORE_MISSIONS:
            raise HookError(f"mission: {', '.join(EXPLORE_MISSIONS)}")
        u = self._unit(tag, unit_id)
        m = EXPLORE_MISSIONS[mission]
        if mission == "land":
            if u["navy"]:
                raise HookError(f"{u['name']} is a fleet: use mission sea/coast/circumnavigate")
            rid = int(u["loc"])
        else:
            if not u["navy"]:
                raise HookError(f"{u['name']} is an army: only mission 'land'")
            rid = 0
            if mission != "circumnavigate":
                if region is None:
                    raise HookError("give region= (id or key; explore_regions lists them)")
                regs = self.explore_regions(tag, unit_id)[mission]
                hit = [x for x in regs if str(region) in (str(x["id"]), x["key"])]
                if not hit:
                    raise HookError(f"region {region} is not explorable as '{mission}' by {u['name']} now; "
                                    f"options: {', '.join(x['key'] for x in regs) or 'none'}")
                rid = hit[0]["id"]
        return self._cmd([], EXPLORE_SIZE, EXPLORE_CMD, f"{u['ptr']} {STOP_REASON:#x} {rid} {m}",
                         f"explore {mission} {region if region is not None else rid} with {u['name']}", dry)

    # PROVEN-STATIC: CStopUnitMissionCommand ctor @0x191ff86 (rsi CUnit*, dl reason -> +0x58, rcx CID by value -> +0x5c),
    # size 0x68, args (navy, 0x36, 0) from CSingleUnitPanel::NavalEvade @0x1f64c56. Execute @0x192000e =
    # CUnit::StopMission(1, reason, 0, CID) - the same call CToggleExploreCommand uses to stop land exploration.
    # IsValid @0x192013a: armies always; navies: mission 0 refused, mission 5 only if CNavy::CanCancelExploration.
    def stop_mission(self, tag: str, unit_id: int, dry: bool = False) -> dict:
        """Cancel the unit's current mission (exploration, circumnavigation, land exploration, ...)."""
        u = self._unit(tag, unit_id)
        return self._cmd([], STOP_SIZE, STOP_MISSION_CMD, f"{u['ptr']} {STOP_REASON:#x} 0", f"stop mission of {u['name']}", dry)

    # ================================================================================================================
    # 2. leaders
    # ================================================================================================================
    # PROVEN-STATIC: leader list = CList<CLeader*> at country+0x1458 (CCountry::AddLeader @0xff1ea2 walks it: elem +0,
    # next +0x10, i.e. the layout `clist` reads). CLeader: CID at +0xc (CAssignLeaderCommand ctor reads it), name
    # std::string +0x18 (GetName @0x14ae088), type +0x38 (SetType), siege +0x3c, maneuver +0x40, fire +0x44, shock +0x48
    # (getters/setters), CUnit* +0x50 (GetUnit @0x14ae002). Types 0..3 = general, admiral, conquistador, explorer (catalogue 13).
    # UNSURE: whether the monarch-as-general leader is in the same list (probably yes).
    def leaders(self, tag: str) -> list[dict]:
        """Leaders of ``tag``: id, name, type, pips (fire/shock/maneuver/siege), unit id they lead (None = free)."""
        c = self.live.country_ptr(tag)
        ptrs = self.h.vm(f"clist {c}+{C_LEADERS:#x} 200")[0] or []
        if not ptrs:
            return []
        steps: list[str] = []
        for p in ptrs:
            steps += [f"rd4 {p}+{L_ID:#x}", f"str {p}+{L_NAME:#x}", f"rd4 {p}+{L_TYPE:#x}", f"rd4 {p}+{L_FIRE:#x}",
                      f"rd4 {p}+{L_SHOCK:#x}", f"rd4 {p}+{L_MANEUVER:#x}", f"rd4 {p}+{L_SIEGE:#x}", f"rd8 {p}+{L_UNIT:#x}"]
        r = self.h.vm(*steps)
        by_ptr = {u["ptr"]: u for u in self.live.units(tag)}
        out = []
        for k, p in enumerate(ptrs):
            lid, name, t, fire, shock, man, siege, unit = r[k * 8:k * 8 + 8]
            u = by_ptr.get(unit)
            out.append({"id": lid, "name": name, "type": LEADER_TYPES[t] if t < 4 else t, "fire": fire, "shock": shock,
                        "maneuver": man, "siege": siege, "unit": u["id"] if u else None,
                        "unit_name": u["name"] if u else None, "ptr": p})
        return out

    def _leader(self, tag: str, leader_id: int) -> dict:
        for x in self.leaders(tag):
            if x["id"] == int(leader_id):
                return x
        raise HookError(f"{tag} has no leader with id {leader_id} (leaders() lists them)")

    # PROVEN-STATIC: CAssignLeaderCommand ctor @0x14ba4da (rsi CUnit* -> +0x48 + CID [unit+0x34], rdx CLeader* -> +0x58
    # + CID [leader+0xc]), size 0x68 (CLeaderAssignmentPanel::NoLeader @0x1f7346f). Pre-check
    # CLeader::CanBeReassignedToUnit(rdi leader, rsi unit, rdx CString*) @0x14af7ce as CLeaderAssignmentEntry::Assign
    # @0x1f71ee6 does; IsValid @0x14ba6ac repeats it plus combat / retreat / loaded-army / territory checks.
    # UNSURE: the GUI routes an assignment of an already-assigned leader through CConfirmReassignmentOfLeader (a confirm
    # dialog) - we post the command directly, which is what that dialog's OnAccept does.
    def assign_leader(self, tag: str, leader_id: int, unit_id: int, dry: bool = False) -> dict:
        """Put leader ``leader_id`` (leaders()) in command of unit ``unit_id``."""
        lead = self._leader(tag, leader_id)
        u = self._unit(tag, unit_id)
        r = self.h.vm("mkstr", f"call {LEADER_CAN_REASSIGN} {lead['ptr']} {u['ptr']} r0", "str r0")
        if not r[1] & 0xFF and not dry:
            raise HookError(f"{lead['name']} cannot lead {u['name']}: {_clean(r[2]) or 'refused'}")
        res = self._cmd([], 0x68, ASSIGN_LEADER_CMD, f"{u['ptr']} {lead['ptr']}", f"assign {lead['name']} to {u['name']}", dry)
        if dry:
            res["can_reassign"] = bool(r[1] & 0xFF)
            res["why_not"] = _clean(r[2])
        return res

    # PROVEN-STATIC: unassign = CAssignLeaderCommand(unit, CNullLeader::GetNullLeader()) exactly as
    # CLeaderAssignmentPanel::NoLeader @0x1f73496 (GetNullLeader @0x14adf6e lazily creates a singleton, no args).
    def unassign_leader(self, tag: str, unit_id: int, dry: bool = False) -> dict:
        """Remove the leader from unit ``unit_id`` (the leader stays in the pool)."""
        u = self._unit(tag, unit_id)
        return self._cmd([f"call {NULL_LEADER}"], 0x68, ASSIGN_LEADER_CMD, f"{u['ptr']} r0", f"remove leader of {u['name']}", dry)

    # ================================================================================================================
    # 3. advisors
    # ================================================================================================================
    def _advisor_rows(self, tag: str, ptrs: list[int]) -> list[dict]:
        """Details of CAdvisor pointers. PROVEN-STATIC: name std::string +0x18 and type token via +0x280
        (WriteMembers @0xe8b7a8), skill +0x38 (GetSkill @0xe8bbc6), type key std::string +0x110 (CAdvisorType ctor
        @0xe8ed8b), category +0xec (GetMonthlyCost @0xe8bdc7 passes it as MonarchAbility), GetPurchaseCost /
        GetMonthlyCost sret (rdi result, rsi this, rdx tag[, rcx CString*]), CanHire(rdi this, rsi tag) = cost < treasury."""
        if not ptrs:
            return []
        steps = [f"tag {tag}"]
        for a in ptrs:
            b = len(steps)
            steps += [f"rd4 {a}+{A_ID:#x}", f"str {a}+{A_NAME:#x}", f"rd4 {a}+{A_SKILL:#x}", f"rd8 {a}+{A_TYPE:#x}",
                      f"str r{b + 3}+{AT_KEY:#x}", f"rd4 r{b + 3}+{AT_ABILITY:#x}",
                      "buf 8", f"call {ADV_PURCHASE} r{b + 6} {a} r0", f"rs4 r{b + 6}",
                      "buf 8", f"call {ADV_MONTHLY} r{b + 9} {a} r0 0", f"rs4 r{b + 9}",
                      f"call {ADV_CAN_HIRE} {a} r0"]
        r = self.h.vm(*steps)
        out = []
        for k, a in enumerate(ptrs):
            v = r[1 + k * 13:1 + (k + 1) * 13]
            ab = v[5]
            out.append({"id": v[0], "name": v[1], "skill": v[2], "type": v[4], "slot": ABILITY[ab] if ab < 3 else ab,
                        "ability": ab, "cost": _s32(v[8]) / 1000, "monthly": _s32(v[11]) / 1000,
                        "affordable": bool(v[12] & 0xFF), "ptr": a})
        return out

    # PROVEN-STATIC: CAdvisorManagement at gs+0x1bf0 (CChangeAdvisorCommand::IsValid @0xea3f06), GetAvailable(rdi mgr,
    # rsi tag) @0xe94d22 returns [mgr+0x10][tag index] (CAdvisorPool*, may be 0); pool list head [pool+0x10], node
    # {cached ptr +0, CID +8, next +0x18}, advisor = GetObjectFromID(node+8) (CAdvisorPool::HasAdvisor @0xe94ac0).
    # The list is walked one node per request (no conditional step).
    def advisors(self, tag: str) -> dict:
        """{"hired": [...], "available": [...]}: id, name, type key, slot ADM/DIP/MIL, skill, cost, monthly, affordable."""
        c = self.live.country_ptr(tag)
        r = self.h.vm("gs", f"tag {tag}", f"call {ADV_AVAILABLE} r0+{GS_ADVISORS:#x} r1",
                      *(f"rd8 {c}+{C_ADVISORS + 8 * k:#x}" for k in range(3)))
        pool, hired = r[2], [p for p in r[3:6] if p]
        avail: list[int] = []
        if pool:
            node = self.h.vm(f"rd8 {pool}+{POOL_HEAD:#x}")[0]
            for _ in range(200):
                if not node:
                    break
                nx, adv = self.h.vm(f"rd8 {node}+{NODE_NEXT:#x}", f"call {OBJ_FROM_ID} {node}+{NODE_CID:#x}")
                if adv:
                    avail.append(adv)
                node = nx
        rows = []
        for k, ptr in enumerate(r[3:6]):   # slot = array position; an empty slot holds the "No Advisor" placeholder (id 0)
            for x in self._advisor_rows(tag, [ptr] if ptr else []):
                if x["id"] and x["type"] != "noAdvisorType":
                    x["slot"], x["ability"] = ABILITY[k], k
                    rows.append(x)
        return {"hired": rows, "available": self._advisor_rows(tag, avail)}

    # PROVEN-STATIC: CChangeAdvisorCommand ctor @0xea38d8 (rsi tag -> +0x48, rdx CAdvisor* -> +0x50 + CID +0x58,
    # ecx MonarchAbility slot -> +0x60), size 0x68 (CConfirmNewAdvisor::OnAccept @0x1a5d4bb). IsValid @0xea3ede only
    # checks the advisor is still in the pool (and returns TRUE for a null advisor) and Execute @0xea3a4a charges
    # GetPurchaseCost without a money check, so we refuse unless CanHire. A filled slot needs CAdvisor::CanFire on the
    # incumbent, as CSelectNewAdvisor::Select @0x1a601c7 requires; Execute replaces him.
    # UNSURE: slot = the advisor type's category (+0xec). The GUI passes the slot of the court row the player clicked.
    def hire_advisor(self, tag: str, advisor_id: int, replace: bool = False, dry: bool = False) -> dict:
        """Hire available advisor ``advisor_id``; replace=True also dismisses the advisor in that slot."""
        a = self.advisors(tag)
        cand = [x for x in a["available"] if x["id"] == int(advisor_id)]
        if not cand:
            raise HookError(f"advisor {advisor_id} is not available to {tag} (advisors() lists the pool)")
        x = cand[0]
        if x["ability"] > 2:
            raise HookError(f"advisor {x['name']} has no ADM/DIP/MIL category ({x['ability']})")
        if not x["affordable"] and not dry:
            raise HookError(f"{x['name']} costs {x['cost']} ducats; not enough money")
        cur = [h for h in a["hired"] if h["ability"] == x["ability"]]
        if cur and not dry:
            if not replace:
                raise HookError(f"{x['slot']} slot holds {cur[0]['name']}; pass replace=True to swap")
            if not self.h.vm(f"call {ADV_CAN_FIRE} {cur[0]['ptr']} 0")[0] & 0xFF:
                raise HookError(f"{cur[0]['name']} cannot be dismissed now")
        res = self._cmd([f"tag {tag}"], 0x68, HIRE_CMD, f"r0 {x['ptr']} {x['ability']}", f"hire {x['type']} {x['name']}", dry)
        res.update({"cost": x["cost"], "affordable": x["affordable"], "replaces": cur[0]["name"] if cur else None})
        return res

    # PROVEN-STATIC: CDismissAdvisorCommand ctor @0xea3f98 (rsi tag -> +0x48, rdx CAdvisor* -> +0x50 + CID +0x58),
    # size 0x60 (CConfirmDismissAdvisor::OnAccept @0x1a5d9f8); IsValid @0xea442a = CCountry::CanDismissAdvisor.
    def fire_advisor(self, tag: str, which: int | str, dry: bool = False) -> dict:
        """Dismiss a hired advisor, by advisor id or by slot 'ADM'/'DIP'/'MIL'."""
        hired = self.advisors(tag)["hired"]
        w = str(which).upper()
        cand = [x for x in hired if x["slot"] == w or str(x["id"]) == str(which)]
        if not cand:
            raise HookError(f"{tag} has no hired advisor {which}")
        x = cand[0]
        if not self.h.vm(f"call {ADV_CAN_FIRE} {x['ptr']} 0")[0] & 0xFF:
            raise HookError(f"{x['name']} cannot be dismissed now")
        # Firing a hired advisor = CChangeAdvisorCommand with a null advisor for that slot (Execute @0xea3bca empties the
        # slot and returns him to the pool). CDismissAdvisorCommand is something else: paying to remove a pool candidate.
        return self._cmd([f"tag {tag}"], 0x68, HIRE_CMD, f"r0 0 {x['ability']}", f"dismiss {x['name']}", dry)

    # ================================================================================================================
    # 4. merchants
    # ================================================================================================================
    # PROVEN-STATIC: CTradeManager at gs+0x2148 (CCurrentGameState::InitTradeManager @0x13cfd8e), CPdxArray<CTradeNode>
    # at +0x10 (data +0x18, count +0x24), stride 0x138 (CTradeManager::GetNode @0x1758492); CTradeNode: definition +0xa8
    # (GetDefinition @0x14f5b84), index +0x120 (ctor @0x175c7c0 stores the array position); definition key std::string
    # +0x10 (ctor @0x1771808), location province id +0xd4 (GetLocation @0x17721b0), outgoing links std::vector at
    # +0x98 stride 0x78, link target key std::string at +0x10 (AddOutgoingConnection @0x1772412).
    # HasTrader(rdi node, rsi tag) @0x175fea2 and GetSteerTo @0x1761df0 are null-safe; mission byte = power entry
    # +0xac (CTradePower::SetTrader @0x1765edc), read only where HasTrader is true (array then exists).
    # UNSURE: CTradeManager::AccessNode refuses index 0 (`test esi,esi; jle`): node 0 may be a dummy; it is listed.
    def trade_nodes(self, tag: str, mine_only: bool = False) -> list[dict]:
        """Trade nodes: index, key, location, our merchant (None/'collect'/'steer'), steer link, outgoing links."""
        tagv = self._tagv(tag)
        r = self.h.vm("gs", f"rd8 r0+{GS_NODES:#x}", f"rd4 r0+{GS_NODE_COUNT:#x}")
        data, n = r[1], min(r[2], 400)
        if not data or not n:
            return []
        steps: list[str] = []
        for i in range(n):
            nd = data + i * NODE_SIZE
            b = len(steps)
            steps += [f"rd8 {nd}+{N_DEF:#x}", f"str r{b}+{D_KEY:#x}", f"rd4 r{b}+{D_LOCATION:#x}", f"rd4 {nd}+{N_INDEX:#x}",
                      f"call {NODE_HAS_TRADER} {nd} {tagv}", f"call {NODE_STEER_TO} {nd} {tagv}",
                      f"rd8 r{b}+{D_LINKS:#x}", f"rd8 r{b}+{D_LINKS + 8:#x}"]
        rr = self.h.vm(*steps)
        out = []
        for i in range(n):
            d, key, loc, idx, has, steer, lb, le = rr[i * 8:i * 8 + 8]
            nl = (le - lb) // LINK_SIZE if lb and le >= lb else 0
            out.append({"index": idx, "key": key, "location": loc, "trader": bool(has & 0xFF), "steer_to": _s32(steer),
                        "ptr": data + i * NODE_SIZE, "def": d, "_links": (lb, min(nl, 16))})
        mine = [x for x in out if x["trader"]]
        if mine:
            off = _tag_index(tagv) * POWER_STRIDE + P_MISSION
            st: list[str] = []
            for x in mine:
                st += [f"rd8 {x['ptr']}+{N_POWER:#x}", f"rd1 r{len(st)}+{off:#x}"]
            mv = self.h.vm(*st)
            for k, x in enumerate(mine):
                m = mv[k * 2 + 1]
                x["mission"] = {0: "collect", 1: "steer"}.get(m, m)
        for x in out:
            x.setdefault("mission", None)
        if mine_only:
            out = mine
        self._fill_links(out)
        for x in out:
            x.pop("_links", None)
        return out

    def _fill_links(self, nodes: list[dict]) -> None:
        steps, where = [], []
        for x in nodes:
            lb, nl = x["_links"]
            for j in range(nl):
                steps.append(f"str {lb}+{j * LINK_SIZE + LINK_KEY:#x}")
                where.append(x)
        for x in nodes:
            x["links"] = []
        if steps:
            for x, k in zip(where, self.h.vm(*steps)):
                x["links"].append(k)

    def _node(self, tag: str, node: int | str) -> dict:
        for x in self.trade_nodes(tag):
            if str(node) in (str(x["index"]), x["key"]):
                return x
        raise HookError(f"unknown trade node {node} (trade_nodes() lists keys like 'english_channel')")

    # PROVEN-STATIC: merchant envoys = CEnvoy* [[country+0x1470]+8] (type 1, CSendTraderCommand::IsValid @0xf470bc);
    # members std::vector<CEnvoyMember*> at +0x20/+0x28 (CEnvoy::GetEnvoy @0x1391d46); member id +0x44, name std::string
    # +0x20, construction* +0x10 (0 = at home; CEnvoyMember ctor @0x1391ee8, GetLocation @0x1392750 which returns the
    # construction's location or the capital [country+0x14a0]), +0x18 byte counted as "free" when 0 (CanPlaceColonist).
    def envoys(self, tag: str, kind: int = 1) -> list[dict]:
        """Envoys of one kind (0 colonists, 1 merchants): id, name, busy, location province id (None = unknown)."""
        c = self.live.country_ptr(tag)
        r = self.h.vm(f"rd8 {c}+{C_ENVOYS:#x}", f"rd8 r0+{8 * kind:#x}", f"rd8 r1+{E_MEMBERS:#x}", f"rd8 r1+{E_MEMBERS + 8:#x}")
        b, e = r[2], r[3]
        n = min((e - b) // 8, 64) if b and e >= b else 0
        if not n:
            return []
        ms = self.h.vm(*(f"rd8 {b}+{8 * i:#x}" for i in range(n)))
        steps = [f"tag {tag}"]
        for m in ms:
            steps += [f"rd4 {m}+{M_ID:#x}", f"str {m}+{M_NAME:#x}", f"rd1 {m}+{M_BUSY:#x}", f"rd8 {m}+{M_CONSTRUCTION:#x}",
                      f"call {ENVOY_LOCATION} {m} r0"]
        rr = self.h.vm(*steps)
        out = []
        for k, m in enumerate(ms):
            i, name, busy, con, loc = rr[1 + k * 5:1 + k * 5 + 5]
            out.append({"id": i, "name": name, "busy": bool(busy), "travelling_or_placed": bool(con), "loc_ptr": loc, "ptr": m})
        locs = [x for x in out if x["loc_ptr"]]
        if locs:
            for x, v in zip(locs, self.h.vm(*(f"rd4 {x['loc_ptr']}+{P_ID:#x}" for x in locs))):
                x["location"] = v
        for x in out:
            x.setdefault("location", None)
            x.pop("loc_ptr")
        return out

    # PROVEN-STATIC: CSendTraderCommand ctor @0xf46c4c (rdi this, rsi CProvince* = node location -> +0x50, rdx tag ->
    # +0x48, rcx CEnvoyMember* (its id +0x44 -> +0x58), r8b TraderMissionType -> +0x5c), size 0x60
    # (CSendMerchantItem::Select @0x1bbc921 passes [gs+0x1c78]+[def+0xd4]*0x2d20 and the envoy from its list item).
    # Mission 0 = collect (SendOffice @0x1efdbbb), 1 = steer/transit (catalogue 12). Execute @0xf46cd2: if the node
    # already has our trader and the envoy is there, only the mission changes (CTradeNode::SetTrader); else the envoy
    # travels (CMerchantConstruction). IsValid @0xf4709e: merchants not locked + envoy id exists.
    # UNSURE: which envoy the GUI list offers (we take one already in the node, else one with construction == 0).
    def send_merchant(self, tag: str, node: int | str, mission: str = "collect", dry: bool = False) -> dict:
        """Send a merchant to trade node ``node`` (key or index) to 'collect' or 'steer'."""
        if mission not in TRADER_MISSIONS:
            raise HookError(f"mission: {', '.join(TRADER_MISSIONS)}")
        nd = self._node(tag, node)
        ms = self.envoys(tag, 1)
        here = [m for m in ms if m["location"] == nd["location"] and m["travelling_or_placed"]] if nd["trader"] else []
        free = [m for m in ms if not m["travelling_or_placed"]]
        pick = (here or free or [None])[0]
        if pick is None:
            raise HookError(f"{tag} has no free merchant ({len(ms)} merchants, all placed); recall one first")
        res = self._cmd(self.live._prov(nd["location"]) + [f"tag {tag}"], 0x60, SEND_TRADER_CMD,
                        f"r1 r2 {pick['ptr']} {TRADER_MISSIONS[mission]}", f"merchant {pick['name']} -> {nd['key']} ({mission})", dry)
        res["merchant"] = pick["name"]
        return res

    # PROVEN-STATIC: CCancelTraderCommand ctor @0xf47180 (rsi CTradeNode const* -> +0x50, rdx tag -> +0x48), size 0x58
    # (CTradeNodeView::SendOffice @0x1efda88 when the node already has our trader); IsValid @0xf474ac = not locked and
    # CTradeNode::HasTrader(tag).
    def recall_merchant(self, tag: str, node: int | str, dry: bool = False) -> dict:
        """Recall our merchant from trade node ``node``."""
        nd = self._node(tag, node)
        return self._cmd([f"tag {tag}"], 0x58, CANCEL_TRADER_CMD, f"{nd['ptr']} r0", f"recall merchant from {nd['key']}", dry)

    # PROVEN-STATIC: CSteerTradeCommand ctor @0x12926d2 (rsi tag -> +0x48, edx link index -> +0x50, ecx node index
    # -> +0x54), size 0x58 (CTradeRouteIcon::OnUp @0x1f27f65: link index = position in the from-node's outgoing link
    # vector, node index = [trade node of the location province]+0x120). Execute @0x1292744: CTradeManager::AccessNode,
    # CanSteer(tag) then SetSteerTo(tag, link). IsValid @0x1292920: merchants not locked, tag valid, not rebels/pirates/natives.
    # UNSURE: what the steer direction does with no steering merchant in the node (Execute only needs CanSteer).
    def steer(self, tag: str, node: int | str, toward: str, dry: bool = False) -> dict:
        """Point our steering in ``node`` down the outgoing link to node key ``toward`` (trade_nodes()[i]['links'])."""
        nd = self._node(tag, node)
        if toward not in nd["links"]:
            raise HookError(f"{nd['key']} does not flow to {toward}; downstream: {', '.join(nd['links']) or 'none (end node)'}")
        link = nd["links"].index(toward)
        r = self.h.vm(f"tag {tag}", "mkstr", f"call {NODE_CAN_STEER} {nd['ptr']} r0 r1", "str r1")
        if not r[2] & 0xFF and not dry:
            raise HookError(f"{tag} cannot steer in {nd['key']}: {_clean(r[3]) or 'refused'}")
        res = self._cmd([f"tag {tag}"], 0x58, STEER_CMD, f"r0 {link} {nd['index']}", f"steer {nd['key']} -> {toward}", dry)
        res["can_steer"] = bool(r[2] & 0xFF)
        return res

    # ================================================================================================================
    # 5. colonists
    # ================================================================================================================
    # PROVEN-STATIC: CSendColonistCommand(tag, CProvince const*, bool) @0x1108704 reads [prov+0x1c] in the ctor, so the
    # province must be checked non-null in a separate request first (Live.colonise does not). Size 0x60 and bool 0 from
    # CColonisationView::Colonize @0x19e51e8; bool 1 = settlement growth (IsValid @0x1108b35 then asks
    # CSettlementGrowthConstruction::CanCreate). With bool 0 IsValid = CColonyConstruction::CanCreate(country, province).
    def colonise(self, tag: str, province: int, dry: bool = False) -> dict:
        """Send a colonist to ``province`` (the GUI 'Colonize' button)."""
        if not self.h.vm(*self.live._prov(province))[1]:
            raise HookError(f"no province {province}")
        return self._cmd(self.live._prov(province) + [f"tag {tag}"], 0x60, COLONIST_CMD, "r2 r1 0", f"send colonist to {province}", dry)

    # PROVEN-STATIC: CCancelColonistCommand(tag, CProvince const*, bool) @0x1108c20, size 0x58, bool 0 from
    # CColonisationView::CancelColony @0x19db927; IsValid @0x1108e9c: CProvince::GetCurrentColoniser() == tag.
    def recall_colonist(self, tag: str, province: int, dry: bool = False) -> dict:
        """Recall our colonist from ``province`` (cancels the colony in progress)."""
        if not self.h.vm(*self.live._prov(province))[1]:
            raise HookError(f"no province {province}")
        return self._cmd(self.live._prov(province) + [f"tag {tag}"], 0x58, CANCEL_COLONIST_CMD, "r2 r1 0",
                         f"recall colonist from {province}", dry)

    # PROVEN-STATIC: province pointer = [gs+0x1c78] + id*0x2d20 for 0 <= id < ([gs+0x1c68]-[gs+0x1c60])/8
    # (GetProvinceOrNullptr @0x13d874c); CCountry::IsPotentialColonistDestination(rdi country, rsi province) @0x102e97c
    # is the predicate the colonisation map mode evaluates for every province (UpdateMapColorProvinceBuffer) and includes
    # IsInColonizationRange; CanPlaceColonist @0x102ca06 additionally needs a free colonist (envoy type 0, byte +0x18 == 0)
    # and the anonymous CanColonize (the GUI gate). Owner qword [prov+0x448], high byte 0 = no owner.
    # UNSURE: cost of ~5000 predicate calls per listing (batched 500 per request); call it paused, not every tick.
    def colonisable(self, tag: str, limit: int = 60) -> list[dict]:
        """Provinces ``tag`` may colonise (in colonial range, unowned): id, has_colonist, can_now (a colonist is free)."""
        c = self.live.country_ptr(tag)
        r = self.h.vm("gs", f"rd8 r0+{GS_PROV_VEC:#x}", f"rd8 r0+{GS_PROV_VEC + 8:#x}", f"rd8 r0+{GS_PROV_BASE:#x}")
        n, base = (r[2] - r[1]) // 8 if r[1] else 0, r[3]
        if not n or not base:
            return []
        hits: list[int] = []
        ids = list(range(1, min(n, 20000)))
        for k in range(0, len(ids), 500):
            chunk = ids[k:k + 500]
            res = self.h.vm(*(f"call {POTENTIAL_COLONY} {c} {base + i * PROVINCE_SIZE}" for i in chunk))
            hits += [i for i, v in zip(chunk, res) if v & 0xFF]
        out = []
        for k in range(0, len(hits), 150):
            chunk = hits[k:k + 150]
            steps: list[str] = []
            for i in chunk:
                p = base + i * PROVINCE_SIZE
                steps += [f"rd8 {p}+{P_OWNER:#x}", f"call {HAS_COLONIST} {p}", f"call {CAN_PLACE_COLONIST} {c} {p}"]
            rr = self.h.vm(*steps)
            for j, i in enumerate(chunk):
                owner, col, can = rr[j * 3:j * 3 + 3]
                if owner >> 56:
                    continue  # owned (our own colony in progress counts as owned by us)
                out.append({"id": i, "has_colonist": bool(col & 0xFF), "can_now": bool(can & 0xFF)})
                if len(out) >= limit:
                    return out
        return out

    # PROVEN-STATIC: colonist envoys = [[country+0x1470]+0] (CCountry::CanPlaceColonist @0x102ca11), free = byte +0x18 == 0.
    def colonists(self, tag: str) -> dict:
        """Colonists: total and free."""
        e = self.envoys(tag, 0)
        return {"total": len(e), "free": sum(1 for x in e if not x["busy"]), "list": e}
