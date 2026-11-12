"""Diplomacy, war and peace through the hook's vm op, built the way the GUI does it.

Source: docs/re/diplomacy_actions.txt, every call below re-disassembled (re/fdis.py) for argument registers,
hidden sret pointer and return register. Conventions (all checked):
 - CCountryTag = 8 bytes in one register; we always use the tag copied from CCountry+0x1c.
 - Functions returning CString by value (GetTag/NameKey vptr+0xf0, GetName vptr+0x60, CWarGoal::GetName vptr+0x48,
   CPeaceTreaty::GetListItemText vptr+0x108) take the result pointer in rdi and `this` in rsi, so they are called with
   `call <fn read from the vtable> <mkstr> <this>`, never with vcall (vcall puts `this` in rdi).
 - CreateCommand (vptr+0x48) is also sret: rdi = unique_ptr slot (a `buf 8`), rsi = action.
 - Validation happens in one request and the post in the next one (the vm has no conditional step). Pointers to game
   objects are only reused across requests while the game is paused; the caller should keep it paused.
 - Ownership: every object we create is freed in a `finally` request: actions via `del` (deleting dtor vptr+8),
   CPdxArray<CDiplomaticAction*> data via operator delete[] (its D0 would free the array object itself),
   CPdxArray<CPdxScopedPtrImpl<CWarGoal>> via its D2 (which ALSO deletes every goal), std::vector buffers via
   operator delete, CArray<CDiploChance> via its D2 then operator delete of the vector object we allocated.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

from .hookclient import HookClient, HookError

# ---- symbols (resolved by name in the hook; never addresses) ------------------------------------------------
GET_COUNTRY = "@_ZN16CCountryDataBase10GetCountryE11CCountryTag"
GET_PROVINCE = "@_ZNK10CGameState20GetProvinceOrNullptrE11SProvinceId"
IS_CONTROLLER_AI = "@_ZNK10CGameState14IsControllerAIE11CCountryTag"
IS_AT_WAR = "@_ZNK8CCountry18IsAtWarWithCountryE11CCountryTag"
OP_DELETE = "@_ZdlPv"        # operator delete(void*)   (libstdc++, the game calls it through the PLT)
OP_DELETE_ARR = "@_ZdaPv"    # operator delete[](void*)

CREATE_VALID = "@_ZNK8CCountry28CreateValidDiplomaticActionsE11CCountryTagR9CPdxArrayIP17CDiplomaticActioniE"
CREATE_ACTION = "@_ZNK8CCountry22CreateDiplomaticActionEi11CCountryTagbPK37CScriptableDiplomaticActionDefinition"
VT_ACTION_ARR = "@_ZTV9CPdxArrayIP17CDiplomaticActioniE+0x10"
DIPLOCHANCE_DTOR = "@_ZN6CArrayI12CDiploChanceED2Ev"
WILL_AI_ACCEPT = "@_ZNK17CDiplomaticAction12WillAIAcceptEP6CArrayI12CDiploChanceE"

GIFT_SET_MONEY = "@_ZN15CSendGiftAction8SetMoneyE11CFixedPoint"
SUBSIDY_SET_AMOUNT = "@_ZN11CWarSubsidy9SetAmountE11CFixedPoint"
SUBSIDY_SET_DURATION = "@_ZN11CWarSubsidy11SetDurationEi"
LOAN_SET_AMOUNT = "@_ZN10COfferLoan9SetAmountEi"
LOAN_SET_INTEREST = "@_ZN10COfferLoan10SetIntrestE11CFixedPoint"
LOAN_SET_DURATION = "@_ZN10COfferLoan11SetDurationEi"
FABRICATE_SET_PROVINCE = "@_ZN21CFabricateClaimAction11SetProvinceEP9CProvince"
CLAIM_STATE_SET_PROVINCE = "@_ZN17CClaimStateAction11SetProvinceEP9CProvince"
THREATEN_SET_PROVINCE = "@_ZN18CThreatenWarAction11SetProvinceEi"
THREATEN_SET_TAKER = "@_ZN18CThreatenWarAction8SetTakerE11CCountryTag"
ENFORCE_SET_TARGET = "@_ZN19CEnforcePeaceAction21SetEnforcePeaceTargetE11CCountryTag"
INTERVENE_SET_ENEMY = "@_ZN21CInterveneInWarAction14SetEnemyLeaderE11CCountryTag"
INSULT_DB = "@_ZN15CInsultDatabase14AccessInstanceEv"
INSULT_VALID = "@_ZNK15CInsultDatabase15GetValidInsultsE11CCountryTagS0_R9CPdxArrayIPK7CInsultiE"
VT_INSULT_ARR = "@_ZTV9CPdxArrayIPK7CInsultiE+0x10"
SET_INSULT = "@_ZN13CInsultAction9SetInsultEPK7CInsult"

CB_DB = "@_ZN19CCasusBelliDatabase14AccessInstanceEv"
CB_GET = "@_ZNK19CCasusBelliDatabase8GetByKeyERK7CString"
CB_CAN_DEMAND_LAND = "@_ZNK15CCasusBelliType21CanAttackerDemandLandEv"
WG_VALID = ("@_ZN8CWarGoal19CreateValidWarGoalsE11CCountryTagS0_R9CPdxArrayI17CPdxScopedPtrImplIS_Lb0EEiE"
            "RKSt8functionIFbPK12CWarGoalTypeEE")
VT_WG_ARR = "@_ZTV9CPdxArrayI17CPdxScopedPtrImplI8CWarGoalLb0EEiE+0x10"
WG_ARR_DTOR = "@_ZN9CPdxArrayI17CPdxScopedPtrImplI8CWarGoalLb0EEiED2Ev"
DW_SET_GOAL = "@_ZN17CDeclareWarAction10SetWarGoalEP8CWarGoal"
DW_ADD_CALL_ALLY = "@_ZN17CDeclareWarAction19AddAttackerCallAllyE11CCountryTag"
DW_CLEAR_CALL = "@_ZN17CDeclareWarAction23ClearAttackerCallAlliesEv"
DW_CLEAR_PROMISED = "@_ZN17CDeclareWarAction31ClearAttackerAlliesPromisedLandEv"

GET_WARS = "@_ZNK8CCountry14GetCurrentWarsER6CArrayIP4CWarE"
WAR_SCORE = "@_ZNK4CWar11GetWarScoreE11CCountryTagS0_P7CStringb"
WAR_ENEMY_LEADER = "@_ZNK4CWar17GetEnemyWarLeaderE11CCountryTag"

PEACE_CTOR = "@_ZN11CPeaceOfferC1E11CCountryTagS0_b"
PEACE_CAN_SELECT = "@_ZNK11CPeaceOffer15CanSelectTreatyEPK12CPeaceTreatyR6CArrayI15SPeaceTreatyTagEbP8CToolTip"
PEACE_SELECT = "@_ZN11CPeaceOffer12SelectTreatyEP12CPeaceTreatybb"
PEACE_COST = "@_ZNK11CPeaceOffer15GetWarScoreCostEb"
RPA_CTOR = "@_ZN19CRequestPeaceActionC1E11CCountryTagS0_bRK11CPeaceOffer8CEU3Date"

ADD_RIVAL = "@_ZN16CAddRivalCommandC1E11CCountryTagS0_"
REMOVE_RIVAL = "@_ZN19CRemoveRivalCommandC1E11CCountryTagS0_"
REPLACE_RIVAL = "@_ZN20CReplaceRivalCommandC1E11CCountryTagS0_S0_"

# ---- layouts / slots (vptr-relative) ---------------------------------------------------------------------------
A_ACTOR, A_TARGET, A_DATE, A_ETYPE, A_VALUE, A_ID = 0xC, 0x1C, 0x24, 0x30, 0x34, 0x38
V_CREATE_CMD, V_NAME, V_IS_GUI_SELECTABLE, V_TOKEN, V_NAMEKEY, V_AI_DERIVED = 0x48, 0x60, 0x98, 0xA0, 0xF0, 0x128
C_INCOMING = 0x18C0                       # CCountry: CList<CDiplomaticAction*> head; node {elem, prev, next(+0x10)}
GS_PROVINCES, PROVINCE_SIZE = 0x1C78, 0x2D20
GS_PACT_DATE, GS_PACT_TAG = 0x1D60, 0x1D64  # CStandardDiplomacyDialogue::OnAccept bookkeeping
PACT_TOKENS = (0x293D, 0x3B85, 0x331D, 0x3320)
WG_CB, WG_PROVINCE, WG_COUNTRY, WG_NAME = 0x110, 0xD8, 0xE0, 0x48
PT_TAKER, PT_IS_PROV, PT_PROVINCE, PT_TYPE, PT_COST, PT_TEXT, PT_SELECTED = 0x48, 0x58, 0x60, 0x80, 0x128, 0x108, 0x1C
PO_VALID = 0x80                           # CPeaceOffer: std::vector<CPeaceTreaty*> (GetValidTreaties)
PEACE_OFFER_SIZE, RPA_SIZE, RPA_FORCE = 0x1A0, 0x1E8, 0x1E0
RPA_TYPE, RPA_OFFER = 0x165, 0x40         # CRequestPeaceAction ctor@0x1255d50: dword +8 = 0x165, CPeaceOffer copy at +0x40
CALL_ALLY, DECLARE_WAR, REQUEST_PEACE = 0x294D, 0x293B, 0x293C

# name: (GetToken id, notes). Ids = the (id,value) pairs CCountry::CreateValidDiplomaticActions@0x10387c6 tries,
# token checked against each class's GetToken; "value=False" = the cancel twin it also tries.
ACTIONS: dict[str, tuple[int, str]] = {
    "alliance": (0x293D, "ALLIANCE; value=False dissolves"),
    "royal_marriage": (0x28DF, "value=False breaks the marriage"),
    "military_access": (0x2943, "ask; value=False cancels"),
    "offer_military_access": (0x2944, "value=False revokes"),
    "fleet_basing": (0x2DD6, "ask; value=False cancels"),
    "offer_fleet_basing": (0x2DD7, "value=False revokes"),
    "improve_relations": (0x2DDB, "value=False recalls the diplomat"),
    "guarantee": (0x2941, "value=False revokes"),
    "warn": (0x2942, ""),
    "embargo": (0x293E, "value=False lifts"),
    "insult": (0x2947, "opt scornful=True: first CInsultDatabase::GetValidInsults entry (GUI checkbox)"),
    "send_gift": (0x2948, "opt money= ducats (required)"),
    "subsidies": (0x2A25, "opts money= per month, months=; value=False cancels"),
    "offer_loan": (0x2A24, "opts amount= int ducats, interest= fraction (0.04), months="),
    "claim_throne": (0x2949, ""),
    "support_independence": (0x2F2E, "value=False cancels"),
    "vassalize": (0x2940, "value=False cancels / breaks the vassalage"),
    "annex": (0x293F, "CDemandAnnexationAction (diplomatic annexation); value=False stops"),
    "integrate": (0x2A9C, "CDemandIntegrationAction (union integration); value=False stops"),
    "designate_march": (0x301D, "value=False revokes"),
    "ask_for_march": (0x3021, ""),
    "tributary": (0x35BD, "value=False cancels"),
    "ask_for_tributary": (0x35C4, ""),
    "convert_tributary_to_vassal": (0x3C89, ""),
    "abandon_union": (0x34AF, ""),
    "call_to_arms": (0x294D, "CCallAllyAction with CreateEmptyAction defaults; +0x4c=0 for an AI ally as the GUI"),
    "coalition": (0x2DE0, "value=False leaves"),
    "enforce_peace": (0x2DDA, "opt enemy=TAG (SetEnforcePeaceTarget, meaning UNSURE)"),
    "intervene_in_war": (0x343E, "opt enemy=TAG (SetEnemyLeader, meaning UNSURE)"),
    "unconditional_surrender": (0x33C4, ""),
    "religious_unity": (0x2D48, ""),
    "grant_electorate": (0x2B1C, ""),
    "remove_electorate": (0x3177, ""),
    "grant_free_city": (0x31F6, ""),
    "remove_free_city": (0x31F7, ""),
    "demand_unlawful_territory": (0x2E0B, ""),
    "call_crusade": (0x2CD5, ""),
    "excommunicate": (0x2CD6, "value=False lifts"),
    "invite_to_federation": (0x2F0F, "value=False kicks"),
    "join_federation": (0x2F0E, "value=False leaves"),
    "invite_to_trade_league": (0x331D, "value=False kicks / leaves"),
    "join_trade_league": (0x3320, ""),
    "invite_to_hre": (0x3B85, ""),
    "imperial_relations": (0x2D46, ""),
    "imperial_realm_war": (0x3A6B, ""),
    "knowledge_sharing": (0x3776, "offer; value=False cancels"),
    "ask_knowledge_sharing": (0x3EDC, ""),
    "trade_favors_gold": (0x3BCE, ""),
    "trade_favors_manpower": (0x3BCF, ""),
    "trade_favors_sailors": (0x3BD0, ""),
    "trade_favors_heir": (0x3BD2, ""),
    "trade_favors_trust": (0x3BD3, ""),
    "trade_favors_war_prep": (0x3BD4, ""),
    "return_core_favor": (0x3BCC, "province/return-tag setters not wired"),
    "reduce_relations_favor": (0x3BCD, ""),
    "break_alliance_favor": (0x3BCB, ""),
    "break_alliance_great_power": (0x3375, ""),
    "influence_nation": (0x346E, ""),
    "take_on_debt": (0x3469, ""),
    "support_heir": (0x2FF6, "value=False stops"),
    "curry_favors": (0x3ACC, "value=False stops"),
    "build_spy_network": (0x3378, "value=False stops"),
    "counter_espionage": (0x33F9, "value=False stops"),
    "fabricate_claim": (0x2AF1, "opt province= (required)"),
    "claim_state": (0x36B6, "opt province= (required; a province of the state, UNSURE which one the GUI passes)"),
    "justify_trade_conflict": (0x2F5E, ""),
    "support_rebels": (0x2DDE, "rebel faction setter not wired"),
    "steal_maps": (0x33C8, "region setter not wired"),
    "slander_merchants": (0x337B, ""),
    "sabotage_recruitment": (0x33BF, ""),
    "sow_discontent": (0x2DDF, ""),
    "sabotage_reputation": (0x2DDD, ""),
    "corrupt_officials": (0x337C, ""),
    "infiltrate_administration": (0x2DDC, ""),
    "agitate_for_liberty": (0x3242, ""),
    "transfer_trade_power": (0x2E09, "trade node field not extracted; value=False cancels"),
    "steer_trade": (0x3778, "value=False cancels"),
    "threaten_war": (0x3307, "opts province= (required), taker=TAG (default actor)"),
    "share_map": (0x3323, "region setter not wired"),
    "sell_ships": (0x3028, "unit setter not wired"),
    "sell_province": (0x2A29, "price/province setters not wired"),
    "condottieri": (0x33B9, "not wired"),
    "charter_company": (0x3866, ""),
    "appoint_cardinal": (0x39C1, ""),
    "declare_war": (DECLARE_WAR, "use Diplo.declare_war"),
    "peace": (REQUEST_PEACE, "use Diplo.peace"),
}
ID_NAMES = {v[0]: k for k, v in ACTIONS.items()}
# send() refuses these with value=True: the object needs a field the catalogue has no confirmed setter for.
NEEDS_UNWIRED = {"return_core_favor", "support_rebels", "steal_maps", "transfer_trade_power", "share_map", "sell_ships",
                 "sell_province", "condottieri"}
OWN_METHOD = {"declare_war", "peace"}

TREATY_TYPES = ["gold", "become_vassal", "subjugate_vassal", "cede_province", "revoke_core", "return_core", "give_up_claims",
                "war_reparations", "transfer_trade_power", "humiliate_rival", "humiliate", "concede_defeat", "revoke_reform",
                "change_hre_religion", "annul_treaties", "release_nation", "enforce_military_access", "enforce_fleet_basing",
                "grant_independence", "release_vassal", "transfer_vassal", "change_religion", "change_government",
                "dismantle_revolution", "revoke_elector", "enforce_rebel_demands", "form_personal_union",
                "concede_colonial_region", "end_rivalry", "take_mandate", "become_tributary", "subjugate_tributary",
                "steer_trade", "join_empire", "spread_revolution", "force_migration", "force_out_colonizers",
                "pillage_capital_state", "guaranteed_join_empire", "scripted"]

_TAG_RE = re.compile(r"^[A-Z0-9]{3}$")
_CB_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _s32(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v & 0x80000000 else v


def tag_str(v: int) -> str:
    """CCountryTag value -> 'FRA' (bytes 0-2)."""
    return (int(v) & 0xFFFFFF).to_bytes(3, "little").decode("latin1")


def _fixed(x: float) -> int:
    return int(round(float(x) * 1000)) & 0xFFFFFFFF


class _P:
    """vm program builder: each step returns its register name ("rN"); after run(), p[reg] is its value."""

    def __init__(self) -> None:
        self.steps: list[str] = []
        self.res: list = []

    def __call__(self, step: str) -> str:
        self.steps.append(step)
        return f"r{len(self.steps) - 1}"

    def sret(self, obj: Any, slot: int, *args: Any) -> str:
        """CString-by-value virtual: fn = vtable[slot]; fn(&out, obj, args...); returns the `str` step's register."""
        out = self("mkstr")
        vt = self(f"rd8 {obj}")
        fn = self(f"rd8 {vt}+{slot:#x}")
        self(f"call {fn} {out} {obj} " + " ".join(str(a) for a in args))
        return self(f"str {out}")

    def create_command_post(self, obj: Any) -> str:
        """CreateCommand (vptr+0x48, sret unique_ptr) then CSession::Post; returns the post step (1 = posted)."""
        up = self("buf 8")
        vt = self(f"rd8 {obj}")
        fn = self(f"rd8 {vt}+{V_CREATE_CMD:#x}")
        self(f"call {fn} {up} {obj}")
        cmd = self(f"rd8 {up}")
        return self(f"post {cmd}")

    def run(self, h: HookClient) -> "_P":
        self.res = h.vm(*self.steps) if self.steps else []
        return self

    def __getitem__(self, reg: str) -> Any:
        return self.res[int(reg[1:])]


class Diplo:
    def __init__(self, hook: HookClient):
        self.h = hook

    # ---- helpers ------------------------------------------------------------------------------------------------
    def _resolve(self, *tags: str) -> list[tuple[int, int]]:
        """[(CCountry*, CCountryTag from country+0x1c)] for each tag, in one request. PROVEN (live.py country_ptr)."""
        p = _P()
        db = p("db")
        regs = []
        for t in tags:
            t = str(t).upper()
            if not _TAG_RE.match(t):
                raise HookError(f"bad country tag {t!r}")
            tg = p(f"tag {t}")
            c = p(f"call {GET_COUNTRY} {db} {tg}")
            regs.append((t, c))
        p2 = _P()
        try:
            p.run(self.h)
            for t, c in regs:
                if not p[c]:
                    raise HookError(f"unknown country tag {t}")
                p2(f"rd8 {p[c]}+0x1c")
            p2.run(self.h)
        except HookError as e:
            if "unknown country" in str(e):
                raise
            raise HookError(f"could not resolve {', '.join(tags)}: {e}") from None
        out = []
        for k, (t, c) in enumerate(regs):
            tv = p2.res[k]
            if tag_str(tv) != t:
                raise HookError(f"unknown country tag {t}")
            out.append((p[c], tv))
        return out

    def _province(self, pid: int) -> int:
        r = self.h.vm("gs", f"call {GET_PROVINCE} r0 {int(pid)}")
        if not r[1]:
            raise HookError(f"no province {pid}")
        return r[1]

    @staticmethod
    def _action_id(action: str | int) -> tuple[str, int]:
        if isinstance(action, int):
            return ID_NAMES.get(action, f"id_{action:#x}"), action
        key = str(action).lower().replace(" ", "_").replace("-", "_")
        if key not in ACTIONS:
            raise HookError(f"unknown diplomatic action {action!r}; known: {', '.join(sorted(ACTIONS))}")
        return key, ACTIONS[key][0]

    def _create(self, country: int, aid: int, target_tag: int, value: bool, extra_new: Iterable[int] = ()) -> list[int]:
        """CCountry::CreateDiplomaticAction(id, target, value, nullptr) (+ zeroed `new` blocks) in a request of its own.
        PROVEN-STATIC @0x1036eb2: rdi=country, esi=id, rdx=tag, ecx=bool, r8=def; heap action in rax (unowned)."""
        steps = [f"call {CREATE_ACTION} {country} {aid:#x} {target_tag:#x} {int(bool(value))} 0"]
        steps += [f"new {n:#x}" for n in extra_new]
        r = self.h.vm(*steps)
        if not r[0]:
            for x in r[1:]:
                self.h.vm(f"call {OP_DELETE} {x}")
            raise HookError(f"the game built no action for id {aid:#x}")
        return r

    def _setters(self, p: _P, a: int, name: str, actor_tag: int, target_tag: int, value: bool, opts: dict) -> None:
        """Extra fields the GUI dialogs set. Every setter disassembled: plain stores, CFixedPoint by pointer."""
        def fp(x: float) -> str:
            b = p("buf 4")
            p(f"wr4 {b} {_fixed(x)}")
            return b

        if name == "send_gift" and value:
            if "money" not in opts:
                raise HookError("send_gift needs money= (ducats)")
            p(f"call {GIFT_SET_MONEY} {a} {fp(opts['money'])}")          # @0x11fface: mov eax,[rsi]; mov [rdi+0x3c],eax
        elif name == "subsidies" and value:
            if "money" in opts:
                p(f"call {SUBSIDY_SET_AMOUNT} {a} {fp(opts['money'])}")  # @0x120cdc4 -> +0x3c
            if "months" in opts:
                p(f"call {SUBSIDY_SET_DURATION} {a} {int(opts['months'])}")  # @0x120cdca -> +0x40
        elif name == "offer_loan":
            if "amount" in opts:
                p(f"call {LOAN_SET_AMOUNT} {a} {int(opts['amount'])}")   # @0x120ad22 -> +0x3c (int)
            if "interest" in opts:
                p(f"call {LOAN_SET_INTEREST} {a} {fp(opts['interest'])}")  # @0x120ad26 -> +0x40
            if "months" in opts:
                p(f"call {LOAN_SET_DURATION} {a} {int(opts['months'])}")  # @0x120ad2c -> +0x44
        elif name in ("fabricate_claim", "claim_state") and value:
            if "province" not in opts:
                raise HookError(f"{name} needs province=")
            prov = self._province(int(opts["province"]))
            fn = FABRICATE_SET_PROVINCE if name == "fabricate_claim" else CLAIM_STATE_SET_PROVINCE
            p(f"call {fn} {a} {prov}")                                   # @0x1237ab0 / @0x126384e -> qword +0x40
        elif name == "threaten_war":
            if "province" not in opts:
                raise HookError("threaten_war needs province=")
            taker = self._resolve(opts["taker"])[0][1] if opts.get("taker") else actor_tag
            p(f"call {THREATEN_SET_PROVINCE} {a} {int(opts['province'])}")  # @0x12106da -> +0x3c (int)
            p(f"call {THREATEN_SET_TAKER} {a} {taker:#x}")                 # @0x12106e2 -> +0x40 (tag)
        elif name in ("enforce_peace", "intervene_in_war") and opts.get("enemy"):
            enemy = self._resolve(opts["enemy"])[0][1]
            fn = ENFORCE_SET_TARGET if name == "enforce_peace" else INTERVENE_SET_ENEMY
            p(f"call {fn} {a} {enemy:#x}")                               # @0x12345c4 / @0x1432782 -> +0x3c (tag)
        elif name == "insult" and opts.get("scornful"):
            # CSendInsultDialogue::ToggleScornful picks one of GetValidInsults(actor, target); unchecked = SetInsult(nullptr).
            q = _P()
            arr = q("buf 0x18")
            q(f"wr8 {arr} {VT_INSULT_ARR}")
            db = q(f"call {INSULT_DB}")
            q(f"call {INSULT_VALID} {db} {actor_tag:#x} {target_tag:#x} {arr}")  # @0x14a8b8c, appends into arr
            n = q(f"rd4 {arr}+0x14")
            data = q(f"rd8 {arr}+8")
            q.run(self.h)
            if q[n] == 0:
                raise HookError("no scornful insult is valid against this target")
            p(f"call {SET_INSULT} {a} " + p(f"rd8 {q[data]}"))             # @0x11fdfea: [insult+0xc] -> +0x3c
            p(f"call {OP_DELETE_ARR} {q[data]}")

    # ---- 1. actions ---------------------------------------------------------------------------------------------
    # PROVEN-STATIC: CreateValidDiplomaticActions@0x10387c6 (rdi=actor CCountry*, rsi=target tag, rdx=CPdxArray&;
    # void), CPdxArray Append/GrowCapacity@0x10f4f90 need a valid vptr (DeleteDataPtr vptr+0x18), GetToken vptr+0xa0
    # (eax), GetTag vptr+0xf0 (sret, CAllianceAction::GetTag@0x11d87d4), IsGUISelectable vptr+0x98 (base@0x11b15f6,
    # rsi=CString*, edx=bool, al), elements deleted via vptr+8 like CCountryDiplomacyView::UpdateActionList, data via
    # operator delete[] (CPdxArray D2@0x10d682a).
    def actions(self, actor: str, target: str) -> list[dict]:
        (ca, at), (_, tt) = self._resolve(actor, target)
        p = _P()
        arr = p("buf 0x18")
        p(f"wr8 {arr} {VT_ACTION_ARR}")
        p(f"call {CREATE_VALID} {ca} {tt:#x} {arr}")
        n = p(f"rd4 {arr}+0x14")
        data = p(f"rd8 {arr}+8")
        p.run(self.h)
        count, buf = _s32(p[n]), p[data]
        ptrs: list[int] = []
        try:
            if count > 0:
                ptrs = self.h.vm(*(f"rd8 {buf}+{8 * i:#x}" for i in range(count)))
            q = _P()
            rows = []
            for a in ptrs:
                tok = q(f"vcall {a} {V_TOKEN:#x}")
                key = q.sret(a, V_NAMEKEY)
                val = q(f"rd1 {a}+{A_VALUE:#x}")
                why = q("mkstr")
                ok = q(f"vcall {a} {V_IS_GUI_SELECTABLE:#x} {why} 0")
                txt = q(f"str {why}")
                rows.append((tok, key, val, ok, txt))
            q.run(self.h)
        finally:
            self.h.vm(*([f"del {a}" for a in ptrs] + [f"call {OP_DELETE_ARR} {buf}"]))
        out = []
        for tok, key, val, ok, txt in rows:
            i = q[tok] & 0xFFFFFFFF
            out.append({"id": i, "name": ID_NAMES.get(i, ""), "key": q[key], "value": bool(q[val] & 0xFF),
                        "selectable": bool(q[ok] & 0xFF), "why": _clean(q[txt])})
        return out

    # ---- 2. chance ----------------------------------------------------------------------------------------------
    # PROVEN-STATIC: GetAIAcceptanceDerived vptr+0x128 (rsi=CArray<CDiploChance>*, edx=bool, int in eax; WillAIAccept
    # @0x11b1c9a calls it the same way), CDiploChance 0x30 bytes (+0 int, +8 CString, +0x28 byte), freed by
    # CArray<CDiploChance>::~CArray@0x1ab2002 (= vector dtor @0x10ea700, does not free the vector object).
    # UNSURE: meaning of the +0x28 flag; scale of total (> 0 = accepts).
    def chance(self, actor: str, target: str, action: str | int, value: bool = True, **opts: Any) -> dict:
        name, aid = self._action_id(action)
        (ca, at), (_, tt) = self._resolve(actor, target)
        a, vec = self._create(ca, aid, tt, value, extra_new=[0x18])
        try:
            p = _P()
            self._setters(p, a, name, at, tt, value, opts)
            tok = p(f"vcall {a} {V_TOKEN:#x}")
            tot = p(f"vcall {a} {V_AI_DERIVED:#x} {vec} 1")
            b = p(f"rd8 {vec}")
            e = p(f"rd8 {vec}+8")
            p.run(self.h)
            if p[tok] & 0xFFFFFFFF != aid:
                raise HookError(f"action object has token {p[tok] & 0xFFFFFFFF:#x}, expected {aid:#x}")
            reasons = self._read_chances(p[b], p[e])
        finally:
            self.h.vm(f"call {DIPLOCHANCE_DTOR} {vec}", f"call {OP_DELETE} {vec}", f"del {a}")
        total = _s32(p[tot])
        return {"action": name, "total": total, "accepts": total > 0, "reasons": reasons}

    def _read_chances(self, b: int, e: int, limit: int = 60) -> list[dict]:
        n = min(max((e - b) // 0x30, 0), limit)
        if not n:
            return []
        q = _P()
        rows = [(q(f"rs4 {b + 0x30 * i:#x}"), q(f"str {b + 0x30 * i + 8:#x}"), q(f"rd1 {b + 0x30 * i + 0x28:#x}")) for i in range(n)]
        q.run(self.h)
        return [{"value": _s32(q[v]), "text": _clean(q[t]), "flag": q[f] & 0xFF} for v, t, f in rows]

    # ---- 3. send ------------------------------------------------------------------------------------------------
    # PROVEN-STATIC: CreateDiplomaticAction@0x1036eb2, setters (see _setters), date +0x24 = current date and the
    # call-ally / last-pact bookkeeping exactly as CStandardDiplomacyDialogue::OnAccept@0x1a7115c (+0x4c=0 when
    # CGameState::IsControllerAI@0x13df40a(target); gs+0x1d60/0x1d64 for 0x293d/0x3b85/0x331d/0x3320 with value),
    # IsGUISelectable(why,false) vptr+0x98 like CDiplomaticActionItem::Update, CreateCommand vptr+0x48
    # (@0x11af14c: rdi=&unique_ptr, rsi=action; clones), post, original deleted via vptr+8.
    # UNSURE: validation and post run in consecutive frames (no conditional step); semantics of some opts (see ACTIONS).
    def send(self, actor: str, target: str, action: str | int, value: bool = True, dry: bool = False, **opts: Any) -> dict:
        name, aid = self._action_id(action)
        if name in OWN_METHOD:
            raise HookError(f"{name}: use Diplo.{name}()")
        if value and name in NEEDS_UNWIRED:
            raise HookError(f"{name}: needs a field with no confirmed setter (not wired)")
        (ca, at), (_, tt) = self._resolve(actor, target)
        (a,) = self._create(ca, aid, tt, value)
        try:
            p = _P()
            self._setters(p, a, name, at, tt, value, opts)
            tok = p(f"vcall {a} {V_TOKEN:#x}")
            key = p.sret(a, V_NAMEKEY)
            gs = p("gs")
            ai = p(f"call {IS_CONTROLLER_AI} {gs} {tt:#x}")
            d = p("date")
            p(f"wr4 {a}+{A_DATE:#x} {d}")
            why = p("mkstr")
            ok = p(f"vcall {a} {V_IS_GUI_SELECTABLE:#x} {why} 0")
            txt = p(f"str {why}")
            p.run(self.h)
            token = p[tok] & 0xFFFFFFFF
            if token != aid:
                raise HookError(f"action object has token {token:#x}, expected {aid:#x}")
            res = {"action": name, "key": p[key], "value": bool(value), "selectable": bool(p[ok] & 0xFF), "why": _clean(p[txt])}
            if not res["selectable"]:
                raise HookError(f"{name} {actor}->{target} refused: {res['why'] or 'not selectable now'}")
            if dry:
                res["posted"] = False
                return res
            q = _P()
            if aid == CALL_ALLY and p[ai] & 0xFF:
                q(f"wr1 {a}+0x4c 0")
            if value and aid in PACT_TOKENS:
                g = q("gs")
                dd = q("date")
                q(f"wr4 {g}+{GS_PACT_DATE:#x} {dd}")
                q(f"wr8 {g}+{GS_PACT_TAG:#x} {at:#x}")
            q(f"wr4 {a}+{A_DATE:#x} " + q("date"))
            post = q.create_command_post(a)
            q.run(self.h)
            res["posted"] = bool(q[post])
            if not res["posted"]:
                raise HookError(f"{name}: CSession::Post refused the command")
            return res
        finally:
            self.h.vm(f"del {a}")

    # ---- 4. incoming / answer -----------------------------------------------------------------------------------
    # PROVEN-STATIC: list at CCountry+0x18c0 (AddIncomingAction@0x1039644 -> CList::Append@0x10dbb3e: node {elem,
    # prev, next +0x10}); fields +0xc proposer, +0x30 EType, +0x34 value, +0x38 id; GetToken vptr+0xa0, GetTag vptr+0xf0
    # and GetName vptr+0x60 (@0x11b14b0, sret). The list is walked by hand inside the reading request so every pointer
    # is used in the frame it was read in.
    def incoming(self, tag: str) -> list[dict]:
        ((c, _),) = self._resolve(tag)
        n = self.h.vm(f"clist {c}+{C_INCOMING:#x} 64")[0]
        if not n:
            return []
        p = _P()
        node = p(f"rd8 {c}+{C_INCOMING:#x}")
        rows = []
        for i in range(len(n)):
            a = p(f"rd8 {node}")
            rows.append((p(f"vcall {a} {V_TOKEN:#x}"), p(f"rd8 {a}+{A_ACTOR:#x}"), p(f"rd4 {a}+{A_ID:#x}"),
                         p(f"rd1 {a}+{A_VALUE:#x}"), p(f"rd4 {a}+{A_ETYPE:#x}"), p.sret(a, V_NAMEKEY), p.sret(a, V_NAME),
                         a, p(f"rd4 {a}+8")))
            if i + 1 < len(n):
                node = p(f"rd8 {node}+0x10")
        p.run(self.h)
        out = []
        for i, (tok, frm, aid, val, et, key, nm, a, kind) in enumerate(rows):
            t = p[tok] & 0xFFFFFFFF
            out.append({"index": i, "id": p[aid], "action_id": t, "name": ID_NAMES.get(t, ""), "key": p[key],
                        "from": tag_str(p[frm]), "value": bool(p[val] & 0xFF), "etype": p[et], "text": _clean(p[nm])})
            # PROVEN-STATIC: a peace proposal is a CRequestPeaceAction (type 0x165 at +8) with its CPeaceOffer at +0x40.
            # The copy keeps the selection: CPeaceOffer copy ctor@0x15b067e -> CopyTreaties@0x15b0a38 clones every treaty of
            # +0x80 and re-files the IsSelected ones.  Not yet seen with a live incoming offer.
            if p[kind] & 0xFFFFFFFF == RPA_TYPE:
                try:
                    out[-1]["terms"] = [r for r in self._treaties(p[a] + RPA_OFFER, False, 400, full=False)[1] if r["selected"]]
                except HookError as e:
                    out[-1]["terms_error"] = str(e)
        return out

    # PROVEN-STATIC: CDiplomaticActionDialog::OnAccept@0x1bed8a4 / DoDecline@0x1bed9b8: old=+0x30; +0x30 = 2|1;
    # CreateCommand vptr+0x48; Post; +0x30 = old. The list owns the action (not deleted).
    # UNSURE: if the list changes between the check request and the answer request (game running) the index can
    # point at another proposal; keep the game paused.
    def answer(self, tag: str, index: int, accept: bool) -> dict:
        lst = self.incoming(tag)
        if not 0 <= int(index) < len(lst):
            raise HookError(f"{tag} has {len(lst)} pending proposal(s); no index {index}")
        want = lst[int(index)]
        ((c, _),) = self._resolve(tag)
        p = _P()
        node = p(f"rd8 {c}+{C_INCOMING:#x}")
        for _ in range(int(index)):
            node = p(f"rd8 {node}+0x10")
        a = p(f"rd8 {node}")
        aid = p(f"rd4 {a}+{A_ID:#x}")
        old = p(f"rd4 {a}+{A_ETYPE:#x}")
        p(f"wr4 {a}+{A_ETYPE:#x} {2 if accept else 1}")
        post = p.create_command_post(a)
        p(f"wr4 {a}+{A_ETYPE:#x} {old}")
        p.run(self.h)
        if p[aid] != want["id"]:
            raise HookError(f"proposal list changed: answered id {p[aid]} instead of {want['id']} (posted={bool(p[post])})")
        if not p[post]:
            raise HookError("CSession::Post refused the answer")
        return {**want, "answer": "accept" if accept else "decline", "posted": True}

    # ---- 5. war -------------------------------------------------------------------------------------------------
    def _goals(self, at: int, tt: int) -> tuple[int, list[int]]:
        """CWarGoal::CreateValidWarGoals 4-arg overload (what CDeclareWarDialogue::UpdateWarGoals@0x1a76644 calls):
        rdi=actor, rsi=target, rdx=&CPdxArray<CPdxScopedPtrImpl<CWarGoal>>, rcx=&std::function (empty = manager +0x10
        zero, checked @0x194a995). Returns (array object we own, goal pointers)."""
        arr = self.h.vm("new 0x18")[0]
        try:
            p = _P()
            p(f"wr8 {arr} {VT_WG_ARR}")
            f = p("buf 0x20")
            p(f"call {WG_VALID} {at:#x} {tt:#x} {arr} {f}")
            n = p(f"rd4 {arr}+0x14")
            d = p(f"rd8 {arr}+8")
            p.run(self.h)
            k = _s32(p[n])
            goals = self.h.vm(*(f"rd8 {p[d]}+{8 * i:#x}" for i in range(k))) if k > 0 else []
            return arr, goals
        except Exception:
            self._free_goals(arr)
            raise

    def _free_goals(self, arr: int) -> None:
        # D2@0x10e1076 deletes every goal ([vptr+8]) and the data buffer; then free the array object itself.
        self.h.vm(f"call {WG_ARR_DTOR} {arr}", f"call {OP_DELETE} {arr}")

    def _goal_rows(self, goals: list[int], names: bool) -> list[dict]:
        if not goals:
            return []
        p = _P()
        rows = []
        for g in goals:
            cb = p(f"vcall {g} {WG_CB:#x}")
            rows.append((cb, p(f"str {cb}+0x10"), p(f"vcall {g} {WG_PROVINCE:#x}"), p(f"vcall {g} {WG_COUNTRY:#x}"),
                         p.sret(g, WG_NAME) if names else None))
        p.run(self.h)
        return [{"index": i, "cb": p[k], "cb_ptr": p[cb], "province": _s32(p[pr]), "country": tag_str(p[ct]),
                 "name": _clean(p[nm]) if nm else ""} for i, (cb, k, pr, ct, nm) in enumerate(rows)]

    # PROVEN-STATIC: CreateValidWarGoals (4-arg)@0x194a8ae, CWarGoal::GetCasusBelli vptr+0x110 (@0x194e3cc, rax),
    # GetProvince vptr+0xd8 (eax), GetCountryTag vptr+0xe0 (rax), GetName vptr+0x48 (@0x1948d84, sret), CB key = CString at
    # CCasusBelliType+0x10 (hash-table Find@0xecee4a compares it), array D2@0x10e1076 frees the goals.
    def war_options(self, actor: str, target: str) -> list[dict]:
        (_, at), (_, tt) = self._resolve(actor, target)
        arr, goals = self._goals(at, tt)
        try:
            rows = self._goal_rows(goals, names=True)
        finally:
            self._free_goals(arr)
        for r in rows:
            r.pop("cb_ptr")
        return rows

    # PROVEN-STATIC: CCasusBelliDatabase::AccessInstance + GetByKey(CString const&)@0xecea44 (falls back to the first CB for
    # unknown keys -> key compared), the GUI's goal list (as war_options), CreateDiplomaticAction(0x293b) = CDeclareWarAction
    # with noPenalty 0 and the null goal (CreateEmptyAction@0xf9fab9), SetWarGoal@0x11c47fa (clones the goal),
    # CDeclareWarDialogue::SelectWarGoal@0x1a7579c (clears call/promised-land lists when !CanAttackerDemandLand@0xeca7d2),
    # AddAttackerCallAlly@0x1ab243c (what the ally checkbox does), then the send() sequence.
    # UNSURE: whether the GUI pre-checks any ally checkbox (call_allies defaults to none); the stability-hit confirm
    # popup of CDeclareWarDialogue::OnAccept is not reproduced.
    def declare_war(self, actor: str, target: str, cb: str, province: int = 0, call_allies: Iterable[str] = (),
                    dry: bool = False) -> dict:
        if not _CB_RE.match(cb or ""):
            raise HookError(f"bad casus belli key {cb!r}")
        (ca, at), (_, tt) = self._resolve(actor, target)
        allies = [t for _, t in self._resolve(*call_allies)] if call_allies else []
        p = _P()
        key = p(f"mkstr {cb}")
        db = p(f"call {CB_DB}")
        cbt = p(f"call {CB_GET} {db} {key}")
        k = p(f"str {cbt}+0x10")
        land = p(f"call {CB_CAN_DEMAND_LAND} {cbt}")
        p.run(self.h)
        if p[k] != cb:
            raise HookError(f"unknown casus belli {cb!r} (the database returned {p[k]!r})")
        cb_ptr, can_land = p[cbt], bool(p[land] & 0xFF)
        arr, goals = self._goals(at, tt)
        a = 0
        try:
            rows = [r for r in self._goal_rows(goals, names=True) if r["cb_ptr"] == cb_ptr]
            if not rows:
                raise HookError(f"{actor} has no valid {cb} war goal against {target} (see war_options)")
            if province:
                rows = [r for r in rows if r["province"] == int(province)]
                if not rows:
                    raise HookError(f"{cb} against {target} has no war goal on province {province}")
            elif len({r["province"] for r in rows}) > 1:
                raise HookError(f"{cb} has several goals; pass province= one of {sorted({r['province'] for r in rows})}")
            goal = goals[rows[0]["index"]]
            (a,) = self._create(ca, DECLARE_WAR, tt, True)
            q = _P()
            tok = q(f"vcall {a} {V_TOKEN:#x}")
            q(f"call {DW_SET_GOAL} {a} {goal}")
            if not can_land:
                q(f"call {DW_CLEAR_CALL} {a}")
                q(f"call {DW_CLEAR_PROMISED} {a}")
            for t in allies:
                q(f"call {DW_ADD_CALL_ALLY} {a} {t:#x}")
            q(f"wr4 {a}+{A_DATE:#x} " + q("date"))
            why = q("mkstr")
            ok = q(f"vcall {a} {V_IS_GUI_SELECTABLE:#x} {why} 0")
            txt = q(f"str {why}")
            q.run(self.h)
            if q[tok] & 0xFFFFFFFF != DECLARE_WAR:
                raise HookError(f"action object has token {q[tok] & 0xFFFFFFFF:#x}, expected {DECLARE_WAR:#x}")
            res = {"cb": cb, "province": rows[0]["province"], "goal": rows[0]["name"], "call_allies": list(call_allies),
                   "selectable": bool(q[ok] & 0xFF), "why": _clean(q[txt]), "posted": False}
            if not res["selectable"]:
                raise HookError(f"declare war {actor}->{target} refused: {res['why'] or 'not selectable now'}")
            if dry:
                return res
            s = _P()
            s(f"wr4 {a}+{A_DATE:#x} " + s("date"))
            post = s.create_command_post(a)
            s.run(self.h)
            res["posted"] = bool(s[post])
            if not res["posted"]:
                raise HookError("declare war: CSession::Post refused the command")
            return res
        finally:
            steps = [f"del {a}"] if a else []
            self.h.vm(*steps, f"call {WG_ARR_DTOR} {arr}", f"call {OP_DELETE} {arr}")

    # ---- 6. wars / peace ----------------------------------------------------------------------------------------
    # PROVEN-STATIC: GetCurrentWars@0x103b106 (rdi=country, rsi=std::vector<CWar*>&, push_back), CWar name CString at
    # +0x20 (GetName@0x193819e), attackers/defenders vector<CCountryTag> at +0x40/+0x70 (element 0 = leader),
    # GetEnemyWarLeader@0x194412e (rax tag, "---" when not a participant), GetWarScore@0x193be5a (me, other, CString*
    # nullptr, bool 0 like CPeaceView::SuggestOffer; int in eax). UNSURE: war-score scale (assumed percent).
    def wars(self, tag: str) -> list[dict]:
        ((c, t),) = self._resolve(tag)
        p = _P()
        v = p("buf 0x18")
        p(f"call {GET_WARS} {c} {v}")
        b = p(f"rd8 {v}")
        e = p(f"rd8 {v}+8")
        p.run(self.h)
        begin, n = p[b], max((p[e] - p[b]) // 8, 0)
        try:
            if not n:
                return []
            q = _P()
            rows = []
            for i in range(n):
                w = q(f"rd8 {begin + 8 * i:#x}")
                en = q(f"call {WAR_ENEMY_LEADER} {w} {t:#x}")
                rows.append((w, q(f"str {w}+0x20"), q(f"rd8 {w}+0x40"), q(f"rd8 {w}+0x48"), q(f"rd8 {w}+0x70"),
                             q(f"rd8 {w}+0x78"), en, q(f"call {WAR_SCORE} {w} {t:#x} {en} 0 0")))
            q.run(self.h)
            s = _P()
            sides = []
            for w, nm, ab, ae, db_, de, en, ws in rows:
                att = [s(f"rd8 {q[ab] + 8 * k:#x}") for k in range(min((q[ae] - q[ab]) // 8, 64))]
                dfn = [s(f"rd8 {q[db_] + 8 * k:#x}") for k in range(min((q[de] - q[db_]) // 8, 64))]
                sides.append((att, dfn))
            s.run(self.h)
        finally:
            self.h.vm(f"call {OP_DELETE} {begin}")
        out = []
        for (w, nm, *_rest, en, ws), (att, dfn) in zip(rows, sides):
            A = [tag_str(s[x]) for x in att]
            D = [tag_str(s[x]) for x in dfn]
            out.append({"name": q[nm], "attackers": A, "defenders": D, "side": "attacker" if tag.upper() in A else "defender",
                        "enemy_leader": tag_str(q[en]), "warscore": _s32(q[ws]), "ptr": q[w]})
        return out

    def _offer(self, actor: str, target: str, demand: bool) -> tuple[int, int, int, int, bool]:
        """CPeaceOffer as CPeaceView::SetOfferAndDemand@0x1e4762a builds it: demand -> (target, actor, 0),
        offer -> (actor, target, 1). Heap object of sizeof 0x1a0 (CPeaceView +0x48/+0x1e8, SPeaceOfferHistory stride,
        WillSuggestPeaceTo frame). Returns (offer, actor_tag, target_tag, provinces_base, is_offer)."""
        (ca, at), (_, tt) = self._resolve(actor, target)
        r = self.h.vm(f"call {IS_AT_WAR} {ca} {tt:#x}")
        if not r[0] & 0xFF:
            raise HookError(f"{actor} is not at war with {target}")
        is_offer = bool(demand)  # live check 1450: (actor, target, 1) lists the TARGET's provinces going to the actor, i.e. our demands
        a_, b_ = (at, tt) if is_offer else (tt, at)
        off = self.h.vm(f"new {PEACE_OFFER_SIZE:#x}")[0]
        try:
            self.h.vm(f"call {PEACE_CTOR} {off} {a_:#x} {b_:#x} {int(is_offer)}")
        except HookError:
            self.h.vm(f"call {OP_DELETE} {off}")
            raise
        return off, at, tt, 0, is_offer

    def _treaties(self, off: int, text: bool, limit: int, full: bool = True) -> tuple[list[int], list[dict]]:
        p = _P()
        b = p(f"rd8 {off}+{PO_VALID:#x}")
        e = p(f"rd8 {off}+{PO_VALID + 8:#x}")
        g = p("gs")
        base = p(f"rd8 {g}+{GS_PROVINCES:#x}")
        p.run(self.h)
        n = min(max((p[e] - p[b]) // 8, 0), limit)
        ptrs = self.h.vm(*(f"rd8 {p[b] + 8 * i:#x}" for i in range(n))) if n else []
        if not ptrs:
            return [], []
        q = _P()
        rows = []
        for t in ptrs:
            can = co = None
            if full:
                why = q("buf 0x18")
                can = q(f"call {PEACE_CAN_SELECT} {off} {t} {why} 0 0")
                q(f"call {OP_DELETE} " + q(f"rd8 {why}"))
                co = q(f"vcall {t} {PT_COST:#x} {off} 1")
            rows.append((q(f"vcall {t} {PT_TYPE:#x}"), q(f"vcall {t} {PT_PROVINCE:#x}"), q(f"vcall {t} {PT_TAKER:#x}"),
                         co, q(f"rd1 {t}+{PT_SELECTED:#x}"), can, q.sret(t, PT_TEXT, off) if text else None))
        q.run(self.h)
        out = []
        for i, (ty, pr, tk, co, se, can, tx) in enumerate(rows):
            typ = q[ty] & 0xFFFFFFFF
            prov = q[pr]
            row = {"index": i, "type": TREATY_TYPES[typ] if typ < len(TREATY_TYPES) else typ,
                   "province": (prov - p[base]) // PROVINCE_SIZE if prov else None, "taker": tag_str(q[tk]),
                   "selected": bool(q[se] & 0xFF)}
            if full:
                row.update(cost=_s32(q[co]), selectable=bool(q[can] & 0xFF))
            if text:
                row["text"] = _clean(q[tx])
            out.append(row)
        return ptrs, out

    # PROVEN-STATIC: CPeaceOffer ctor@0x15ae0e4 (rdi=this, rsi->+0x14, rdx->+0xc, cl->+0x1c; builds every valid treaty),
    # valid treaties = std::vector at +0x80 (GetValidTreaties@0x15b907a), CPeaceTreaty vptr+0x80 GetType (eax, rdx=def),
    # +0x60 GetProvince (CProvince* or null; id from the province array index), +0x48 GetActualTaker (tag), +0x128
    # GetWarScoreCost(offer, bool) (int, = offer cost with minus without), +0x108 GetListItemText (sret, offer in rdx),
    # IsSelected = byte +0x1c (@0x15da9fa), CanSelectTreaty(offer,t,CArray<SPeaceTreatyTag>&,false,nullptr)@0x15b7cd8 as
    # CPeaceView::CPeaceEntry::Select, offer freed by its D0 (vptr+8 @0x15b0e06).
    def peace_options(self, actor: str, target: str, demand: bool = True, text: bool = True, limit: int = 300) -> list[dict]:
        off = self._offer(actor, target, demand)[0]
        try:
            return self._treaties(off, text, limit)[1]
        finally:
            self.h.vm(f"del {off}")

    # PROVEN-STATIC: selection as CPeaceEntry::Select@0x1e53396 (CanSelectTreaty then SelectTreaty(t,false,false)
    # @0x15b8250), total = CPeaceOffer::GetWarScoreCost(true)@0x15b33fe (the bool CPeaceView::Update passes), then the
    # CPeaceView::SendOffer@0x1e4ab78 path: new 0x1e8 CRequestPeaceAction(actor, target, isOffer, offer, now)@0x1255d50
    # (date by value in r9d), acceptance = GetAIAcceptanceDerived vptr+0x128 = CalcAIPeaceAcceptance@0xe9b6dc with the
    # answering country (@0x1258de4), IsGUISelectable(nullptr-equivalent text, false), on send WillAIAccept(nullptr) ->
    # byte +0x1e0 (SetForceAccept@0x1258e84 stores sil there), CreateCommand, Post, delete (D0@0x1270dc4).
    # UNSURE: treaty indices are positions in a freshly built offer (stable only while paused); SendOffer evaluates
    # IsGUISelectable and posts in one frame, here they are consecutive frames.
    def peace(self, actor: str, target: str, demand: bool = True, treaties: Iterable[int | dict] = (), white: bool = False,
              send: bool = False) -> dict:
        treaties = list(treaties)
        if white and treaties:
            raise HookError("white peace takes no treaties")
        off, at, tt, _, is_offer = self._offer(actor, target, demand)
        act = vec = 0
        constructed = False
        try:
            ptrs, info = self._treaties(off, text=False, limit=2000, full=False)
            chosen = [self._pick_treaty(info, t) for t in treaties]
            prev = None
            for i in chosen + [None]:
                p = _P()
                if prev is not None:
                    p(f"call {PEACE_SELECT} {off} {ptrs[prev]} 0 0")
                can = None
                if i is not None:
                    why = p("buf 0x18")
                    can = p(f"call {PEACE_CAN_SELECT} {off} {ptrs[i]} {why} 0 0")
                    p(f"call {OP_DELETE} " + p(f"rd8 {why}"))
                p.run(self.h)
                if can is not None and not p[can] & 0xFF:
                    raise HookError(f"treaty {info[i]['type']} (index {i}, province {info[i]['province']}) cannot be "
                                    f"selected with the ones before it")
                prev = i
            act, vec = self.h.vm(f"new {RPA_SIZE:#x}", "new 0x18")
            q = _P()
            cost = q(f"call {PEACE_COST} {off} 1")
            q(f"call {RPA_CTOR} {act} {at:#x} {tt:#x} {int(is_offer)} {off} " + q("date"))
            q.run(self.h)
            constructed = True
            r = _P()
            tot = r(f"vcall {act} {V_AI_DERIVED:#x} {vec} 1")
            b = r(f"rd8 {vec}")
            e = r(f"rd8 {vec}+8")
            why = r("mkstr")
            ok = r(f"vcall {act} {V_IS_GUI_SELECTABLE:#x} {why} 0")
            txt = r(f"str {why}")
            r.run(self.h)
            total = _s32(r[tot])
            res = {"demand": bool(demand), "treaties": [info[i] for i in chosen], "cost": _s32(q[cost]),
                   "acceptance": total, "accepts": total > 0, "reasons": self._read_chances(r[b], r[e]),
                   "selectable": bool(r[ok] & 0xFF), "why": _clean(r[txt]), "posted": False}
            if send:
                if not res["selectable"]:
                    raise HookError(f"peace offer refused: {res['why'] or 'not selectable now'}")
                s = _P()
                will = s(f"call {WILL_AI_ACCEPT} {act} 0")
                s(f"wr1 {act}+{RPA_FORCE:#x} {will}")
                post = s.create_command_post(act)
                s.run(self.h)
                res["posted"] = bool(s[post])
                res["force_accept"] = bool(s[will] & 0xFF)
                if not res["posted"]:
                    raise HookError("peace: CSession::Post refused the command")
            return res
        finally:
            steps = []
            if vec:
                steps += [f"call {DIPLOCHANCE_DTOR} {vec}", f"call {OP_DELETE} {vec}"]
            if act:
                steps.append(f"del {act}" if constructed else f"call {OP_DELETE} {act}")
            steps.append(f"del {off}")
            self.h.vm(*steps)

    @staticmethod
    def _pick_treaty(info: list[dict], t: int | dict) -> int:
        if isinstance(t, int):
            if not 0 <= t < len(info):
                raise HookError(f"no treaty index {t} (peace_options lists {len(info)})")
            return t
        hits = [x["index"] for x in info if all(x.get(k) == v for k, v in t.items())]
        if len(hits) != 1:
            raise HookError(f"treaty {t} matches {len(hits)} entries; use an index from peace_options")
        return hits[0]

    # ---- 7. rivals ----------------------------------------------------------------------------------------------
    # PROVEN-STATIC: CAddRivalCommand(country, rival)@0xf75ea2 / CRemoveRivalCommand(country, rival)@0xf779ac (size 0x58)
    # and CReplaceRivalCommand(country, new, old)@0xf7694e (0x60) exactly as CConfirmAddRival::OnAccept@0x1ab423e and
    # CConfirmRemoveRival::OnAccept; IsValid() vptr+0x48 (@0xf76928 etc.) checked first (ProcessCommands checks it too).
    def _rival_cmd(self, size: int, ctor: str, tags: list[int], what: str) -> str:
        p = _P()
        cmd = p(f"new {size:#x}")
        p(f"call {ctor} {cmd} " + " ".join(f"{t:#x}" for t in tags))
        ok = p(f"vcall {cmd} 0x48")
        p.run(self.h)
        if not p[ok] & 0xFF:
            self.h.vm(f"del {p[cmd]}")
            raise HookError(f"{what}: the game refuses this now (not a valid rival, limit, or cost)")
        if not self.h.vm(f"post {p[cmd]}")[0]:
            raise HookError(f"{what}: could not be posted")
        return what

    def add_rival(self, actor: str, rival: str) -> str:
        (_, at), (_, rt) = self._resolve(actor, rival)
        return self._rival_cmd(0x58, ADD_RIVAL, [at, rt], f"{actor} rivals {rival}")

    def remove_rival(self, actor: str, rival: str) -> str:
        (_, at), (_, rt) = self._resolve(actor, rival)
        return self._rival_cmd(0x58, REMOVE_RIVAL, [at, rt], f"{actor} drops rival {rival}")

    def replace_rival(self, actor: str, new: str, old: str) -> str:
        (_, at), (_, nt), (_, ot) = self._resolve(actor, new, old)
        return self._rival_cmd(0x60, REPLACE_RIVAL, [at, nt, ot], f"{actor} replaces rival {old} with {new}")


_COLOR = re.compile(r"§.|\xa7.")


def _clean(s: Any) -> str:
    """Strip Clausewitz colour codes (§G ... §!) from localised text."""
    if not isinstance(s, str):
        return ""
    return _COLOR.sub("", s).strip()
