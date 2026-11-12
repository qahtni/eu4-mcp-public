# EU4 1.37.5 Linux: command layer and hook entry points (static RE)

> The generated tables this note mentions (`commands.tsv`, `diplomatic_actions.tsv`, `console_commands.txt`) are not shipped, because they are extracted from the game binary.
> Regenerate them from your own copy: see `re/README.md`. Addresses are valid for the Linux 1.37.5 build only.

Target `<game dir>/eu4`: ELF64 **EXEC (non-PIE)**, loads at 0x400000, so every address below is absolute and needs no load base.
Method: `nm`, an `objdump -d` of `.text` (`re/out/text.dis`), a capstone helper (`re/elfutil.py`, `re/fdis.py`, `re/ctx.py`, `re/callers.py`), call xrefs (`re/out/calls.tsv`), and `operator new` sizes (`re/out/newsites.tsv`).
Notation: `fn@addr+off` is an instruction offset inside a function. Slot `k` of a vtable is at `vptr+8k`, where vptr = `_ZTV…`+0x10.
Nothing was run. A read-only `sudo gdb` attach was attempted and refused by the permission system, so every runtime fact is still marked **UNVERIFIED**.

## 0. Binary facts
- Linkage: NEEDED is libsteam_api, libnakama-cpp, libdl, libX11, libpthread, libtbb.so.2, libGL, libGLU, libpops_api, libm, libgcc_s, libc. **libstdc++ is not static.** `_Znwm`, `_ZdlPv`, `__cxa_*` and `_ZNSs*` are undefined imports. libtbb.so.2 and libnakama-cpp.so pull in libstdc++.so.6.
- Inlined libstdc++ code: `std::__cxx11::basic_string` (685 weak instantiations, e.g. `basic_string(char const*)`@0x25a59c0) is compiled into the exe. That means the C++11 SSO string ABI.
- **SDL2 is static** (2512 `SDL_*` symbols). `.debug_info` (106 CUs) covers only SDL/X11 C sources (`re/out/debuginfo_cus.txt`). There is no game type info.
- Compiler (`.comment`): clang 10.0.1, with GCC 9.4 runtime objects.
- **All global symbols are in `.dynsym`** (88,978 T/W functions in both tables, i.e. the exe was linked with `--export-dynamic`). `dlsym(RTLD_DEFAULT,"_ZN8CSession4PostEP8CCommand")` works. Only local (`t`/`b`, e.g. `_ZL20pApplicationInstance`) symbols need a manual `.symtab` read.
- Internal calls are direct `call rel32` (e.g. `CSession::Post` call sites in `re/out/xref_CSession_Post.tsv`). **Interposing a game symbol by name does not hook internal calls.** Only PLT imports can be interposed.

## a. Command base class `CCommand` (base `CPersistent`; RTTI `_ZTI8CCommand`@0x27d34b0)
Vtable `_ZTV8CCommand`@0x27d33e0, 22 slots. Pure slots were named from `_ZTV12CMoveCommand`@0x26fd320.

| slot (vptr+) | method |
|---|---|
| 0 (+0x00) / 1 (+0x08) | `~CCommand()` complete / deleting |
| 2 (+0x10) | `CPersistent::Write(CWriter&)` |
| 3 (+0x18) | `WriteMembers(CWriter&)` (serialization) |
| 4 (+0x20) | `CPersistent::Read(CReader&)` |
| 5 (+0x28) | `ReadMember(CReader&,int)` |
| 6,7,8 | `InitPostRead()`, `InitPostRead(CString const&,int,int)`, `OnError(CReader const&)` |
| **9 (+0x48)** | **`IsValid() const` -> bool (validation)** |
| **10 (+0x50)** | **`Execute() const` (apply to the game state)** |
| 11 (+0x58) | `GetSpecificTokenType() const` -> int (serialization token) |
| 12 (+0x60) | `MustSynch() const` |
| 13 (+0x68) | `Clone(CString) const` |
| 14,15 (+0x70,+0x78) | `PreExecuteProcess()`, `PostExecuteProcess()` |
| 16 (+0x80) | `OkDuringHotJoin() const` |
| 17 (+0x88) | `IgnoreIsAllowedToPostCheck() const` (default false, `db5dc6`) |
| 18-21 | Get/SetRecipientAdress/Port |

Base layout, from the `CCommand(CCommand const&)`@0xdb5fca copy and the inline default ctor in `CDiplomaticAction::CreateCommand`@0x11af181..+0x6d. Base size is **0x48**; subclass fields start at +0x48.
- +0x00 vptr
- +0x08 int = 0x165
- +0x0c byte = 0
- +0x10 CString (session name)
- +0x30 int = -1 (Post stores session+0x8c here)
- +0x34 int = 0
- +0x38 u16 = 0
- +0x3c int = 0x2934
- +0x40 u16 = 0xffff (Post stores the sender id here)
- +0x42 byte (when nonzero, Post skips the sequence-number increment)
- +0x43 byte
- +0x44 u32 sequence number (`CDummyServer::Post`@0x2002f1e)

Serialization: `CDummyServer::Post`@0x2002edc writes every command to a `CBlob` via `CWriter::Write(GetSpecificTokenType(), cmd)` (+0x75..+0xa9). It then **re-creates it via `CCommandFactory::CreateFromReader`**@0x1ff46c8 (+0x17b) and pushes the copy onto a `tbb::concurrent_queue<CCommand*>` (+0x1be). Even single-player round-trips through WriteMembers/ReadMember, so a command whose members do not serialize is lost.
Token registration: `CCommandFactory::Register(CCommand*)`@0x1ff4674, plus `CCommandTemplate<…>::CRegistrator` static objects.
Execution: `CSession::ProcessCommands()`@0x200702e. It calls `MustSynch` (+0x1af), `IgnoreIsAllowedToPostCheck` (+0x1ea), then **`IsValid` (+0x1fe, skips on false)**, `PreExecuteProcess` (+0x223), then `ExecuteWithPadding` -> lambda@0x2009052 -> `Execute` (+0x7b), then `PostExecuteProcess` (+0x2c6).
Templated commands: `CCommandTemplate<Derived, token, Args...>` (47 classes). Their ctors are often fully inlined, so `commands.tsv` gives the template ctor or "no ctor symbol" plus the Clone size.

## b. GUI submit path
**Submit function:** `CSession::Post(CCommand*)`@0x200691c -> bool (`_ZN8CSession4PostEP8CCommand`, 589 call sites).
- The unique_ptr overload `CSession::Post(std::unique_ptr<CCommand>)`@0x200690a moves the pointer out and tail-calls the raw one.
- `CEU4Application::Post(CCommand*)`@0xee2842 does `this+0x318` -> CSession, then the same call.

Body of Post:
1. Return false if cmd==0 or `session+0x58` (CServer*) == 0.
2. If `session+0x55==0 && session+0x7f5==0`, it posts only when `IgnoreIsAllowedToPostCheck()` or `MustSynch()` is true. Otherwise it returns false **without deleting the command**.
3. Set `cmd+0x30=session+0x8c` and `cmd+0x40=min(session+0x68,0x7fff)`.
4. Call `server->vtbl+0x38` (`CDummyServer::Post` slot 7 in `_ZTV12CDummyServer`@0x27d4620; `CNetworkServer::Post`@0x1ffd02e and `CProxyServer::Post`@0x20005b8 in MP).

**Ownership:** `CDummyServer::Post` deletes the caller's object through the virtual deleting dtor (`2002ef6` saves rbp=cmd; `20030ea..f2` `call [vptr+8]`). The hook must allocate commands with the global `operator new(size)` (libstdc++'s, shared), construct them with the game ctor, hand them to Post, and `delete` them itself only when Post returns false.

**Reaching the session:**
- `CCurrentGameState::_pInstance`@0x2fca490 is a CGameState*.
- `gs+0x1dd0` is a `CInGameIdler*`, proven by a direct call `CInGameIdler::GetSession()` with `rdi=gs[0x1dd0]` at `CImproveRelationButtonDiplomatEntry::OnDiplomatLeftClick`@0x1ce15f8.
- `CInGameIdler::GetSession()`@0x146e0a8 returns `*(idler+0x328)` -> CEU4Application, then `+0x318` -> CSession. It is also virtual at idler vptr+0x88.
- Equivalent: `*(CSession**)(CApplication::GetInstance()+0x318)`. `GetInstance`@0x1fc38b6 returns `pApplicationInstance`@0x3328d08, which `CApplication::InitPostRead`@0x1fc37ea sets to `this`. CEU4Application single-inherits CApplication.

Three GUI sites (new -> ctor -> Post):
1. Maintenance slider: `CCountry::ChangeMaintenanceSliders(CFixedPoint,CFixedPoint)`@0x102c216. Session from `(country+0x1870)->vtbl[0x88]` (+0x16..+0x26); `new(0x58)` (+0x29); `CChangeMaintenanceCommand(CCountryTag,CFixedPoint,CFixedPoint)`@0x1291e26 (+0x50); `CSession::Post` (+0x5b). `CCountryEconomyView::PostSliders` is the caller.
2. Unit move (right click): `CUnit::PerformAction(CProvince*,bool,bool)`@0x18ff692. `new(0x98)` (+0xb0f); `CMoveCommand(CUnit*, int provId=prov+0x1c, bool,0,0,bool)`@0x19134a4 (+0xb3d); **`IsValid()` called first** at `[vptr+0x48]` (+0xb49); session `(unit+0x138)->vtbl[0x88]`; `Post` (+0xb64).
3. Diplomatic action: `CStandardDiplomacyDialogue::OnAccept`@0x1a7115c.
   - `action=dlg+0x250`; `action+0x24 = gs+0x1da0` (current date) at 1a7128c.
   - Session from `(dlg+0x248)->vtbl[0x88]`.
   - `action->CreateCommand(&up)` at `[vptr+0x48]` (1a712b4).
   - `CSession::Post(unique_ptr)` (1a712bd). Assault is similar: `CSiegeView::Assault`@0x1ee47bc -> `CSpendPowerCommand(CCountryTag,CPowerSpending&)`@0xf7320a -> Post.

**Thread:**
- `main` calls `CApplication::Run()`@0x1fc2980 directly (`main`@0x14c07a5).
- Run loops `UpdateOneFrame(bool)`@0x1fc23a2 -> idler `Idle` (virtual) -> `CEU4Application::Update` -> `CSession::Update()`@0x2006998 -> `ProcessCommands`.
- `CSession::Update` checks `ThreadIsMainThread()`@0x2564b9d (exported) and logs "CSession::Update called from other than main thread" (+0xc..+0x3a).
- The day tick itself is a command: `CGameState::HandleDailyTick`@0x13d91ca's only caller is `CDailyTickCommand::Execute`@0x1780564. **The simulation runs on the main thread, inside ProcessCommands.**
- Post has no mutex and no main-thread check. AI code posts through `PostAICommand(unique_ptr)`@0xd9b70c, and the queue is a tbb concurrent_queue, so Post is likely tolerant of other threads. Still: **call Post only on the main thread.** Cross-thread safety is UNVERIFIED; the session+0x55/+0x7f5 flags and the CWriter path are unsynchronised.

## c. Console
Raw line entry: `CConsole::ExecuteCmd(CString const&)`@0x222e002 on `CConsole::_pInstance`@0x34113d8.
- It puts the text into the edit box (`editbox->vtbl[0x88]`) and calls `CConsole::HandleInput()`@0x222cd4e.
- HandleInput does `CString::Split('|')`@0x254c9b6 (+0x21c), `Trim`, `Split(' ')` (+0x248), then `CConsoleCmdManager::Execute` (+0x287). It logs the result and `AddLine`s it to the console window.
- The synced MP variant `CConsoleCommand(CString const&)`@0x14999ea has an Execute that calls `CConsole::ExecuteCmd`.

Programmatic path, as the game itself does it in `CGameState::HandleDailyTick`+0xa89..+0xb05:
build `std::vector<CString>{"run_commands", g_RunYearCommand}`, then `CConsoleCmdManager::Execute(&res, CConsoleCmdManager::_pInstance, &vec)`.
- **`CConsoleCmdManager::Execute(CArray<CString> const&)`@0x253f7e0.** SysV sret: `rdi`=result*, `rsi`=this, `rdx`=args.
- Instance: `CConsoleCmdManager::_pInstance`@0x3445380 (exported B). `CreateInstance()`@0x253f2c4 makes it (`new(0x48)`).
- **Result `CConsoleCmd::SResult`** (0x28): byte +0 (ok), CString +8 (message). Written at Execute+0x149..+0x155; handlers write the same (`OnExecute_Speed`@0x19f83ab).

`CString` = `std::__cxx11::string` (0x20), from `CString::CString()`@0x254b128: +0 char*, +8 size, +0x10 16-byte SSO buffer (no separate capacity field while in SSO; the first 8 bytes of the buffer are the capacity when heap-allocated).
`CArray<T>` = `std::vector<T>` (begin +0, end +8, cap +0x10; count = (end-begin)>>5 for CString, as at Execute+0x1a3..+0x1c7).

Gates in Execute:
1. `FindCommandData(args[0], true)` (+0x38).
2. If `data->byte0==0` and `mgr+0x18` (**`SetIsRelease`**@0x253f4aa) -> "Command available only for developers." (+0x45, +0x11c..+0x13f).
3. If `mgr+0x19` (`SetIsMultiplayer`@0x253f304; set by `CEU3Idler::RestoreDeviceObjects`@0x137c8b8+0x5b..+0x8c to `IsMultiplayerWithPlayers() || CIronman::IsSet(gs+0x2470)`), every command except `enable_all_commands` and `stats` gets "Command not available in multiplayer or ironman mode." (+0x297..+0x41d).
4. `mgr+0x44=1` once any command other than `stats` runs (+0x293).
5. `g_pOnCheckConsoleHacking`@0x3445378 (= `OnCheckConsoleHacking`@0x14c120f, set in `main`+0x5b) is called before every handler (+0x4a9). In MP/ironman it sets `player_country+0x287b=1`.
6. Handler: `data+0x38(SResult*, CArray<CString> const& argsWithoutName, data+0x40)` (+0x4bf..+0x4cb).

`isRelease` comes from `CConsoleCmdImpl::IsReleaseMode()`@0x1a32ec0, which always returns true. `enable_all_commands` clears both flags only after a SHA3 password check (`OnExecute_EnableAllCommands`@0x19f0d30).

Table: `gConsoleCmdData`@0x2f95030 (.data, exported), 0x17a entries x 0xa0 (`SOldCommandData`): +0 release_ok, +8 name, +0x30 desc, +0x38 handler. Registered by `AddCommands` from `RestoreDeviceObjects`+0xbf. `SCommandData` (0xa8) is the manager-side copy, made by `CopyToNewCommandStructure`@0x2568626. Dump: `console_commands.txt` (378 entries, 324 usable in release, e.g. `run`, `tag`, `speed`, `date`, `cash`, `add_opinion`).

## d. Per-frame hook without inline patching
- PLT imports: GL 1.x immediate functions, `glXGetProcAddressARB`, and only `XGetImage`, `XGetWindowAttributes`, `XQueryPointer` from X11. **`glXSwapBuffers`, `XPending` and `XNextEvent` are not imported**: static SDL loads them with dlopen/dlsym (SDL_x11dyn.c, SDL_x11opengl.c) and swaps via `SDL_GL_SwapWindow`@0x2121e40, which is internal.
- **Recommended:** interpose **`SteamAPI_RunCallbacks`** (PLT@0xd44580, from libsteam_api.so). It is called unconditionally as the 1st call of `CApplication::UpdateOneFrame(bool)` (+0xd, return address **0x1fc23b4**), once per frame, on the main thread (main -> Run -> UpdateOneFrame).
- Other callers: `CSteamMatchmakingContext::LobbyJoin`, `GetGameServerFromLobby`, `CSteamNetContext::Update`@0x221e423, `PdxSteamUpdate`@0x23917e4. The interposer should run its queue only when `__builtin_return_address(0)==0x1fc23b4`, then forward to `dlsym(RTLD_NEXT,…)`.
- At that point the command queue for the frame has not run yet (ProcessCommands runs later inside Idle), so posted commands execute in the same frame (UNVERIFIED by observation).
- Fallback inline-hook target: `CApplication::UpdateOneFrame(bool)`@0x1fc23a2. First 32 bytes: `55 41 56 53 48 83 ec 10 89 f5 48 89 fb e8 cc 21 d8 fe e8 0f 1d 24 00 48 89 c7 e8 db 25 24 00 e8`. Also `CSession::ProcessCommands`@0x200702e: `55 41 57 41 56 41 55 41 54 53 48 81 ec 08 03 00 00 49 89 fc 48 8b 7f 58 48 8b 07 ff 50 50 49 89`.

## e. State accessors (all exported unless noted)
- Game state: `CCurrentGameState::_pInstance`@0x2fca490.
- Player: `CGameState::GetPlayer() const`@0x13d174a returns CCountryTag (gs+0x1e30, or +0x1e38 in an observer case).
- Date: `gs+0x1da0` int `CEU3Date` (written by `SetCurrentDate`@0x13d6fb4+0x2b5). Readable string via `CEU3Date::GetEu3String() const`@0x1378eb4 (sret CString).
- Speed: `gs+0x1dd8` = 0..4 (`SetGamespeed(int)`@0x13d8e3a+0x17).
- Pause: `CInGameIdler::AccessGameSpeed()`@0x149044a (idler vptr+0x108) -> `CGameSpeed*`, then `CGameSpeed::IsActuallyPaused() const`@0x1447ab6.
- **CCountryTag** (8 bytes, passed in one register): low 3 bytes ASCII tag; bits 32..47 = int16 index (`CCountryDataBase::GetCountry`@0xf52f2c: `shr rsi,32; movsx`). Not found = "---" (`GetTag` +0xa5).
- Country DB: `CCountryDataBase::_pInstance`@0x2fc52f8.
  - `GetTag(char const*, bool) const`@0xf514c8 (tag from "TUR").
  - `GetCountry(CCountryTag)`@0xf52f2c -> CCountry* (= `db+0xf8` array[index]).
  - `IDToTag(int)`@0xf54c7a. The country's own tag is at `country+0x1c`.
- Province: `CGameState::GetProvinceOrNullptr(SProvinceId) const`@0x13d874c (id in esi; array `gs+0x1c78`, stride 0x2d20). `CProvince::GetProvinceID()`@0x14f371c.
- Unit: `CReferenceObject::GetObjectFromID(CID const&)`@0x1fea3e8, then `CUnit* = ret-0x28` when ret != 0 (`CMoveCommand::Execute`@0x19137f0..f5).
- Country units: `country+0xcf8` is a `CList<CUnit*>` {head +0, tail +8, int count +0x10}; node {T +0, prev +8, next +0x10} (`CList<CCommand*>::Append`@0x200930c, `CCountry::SortUnitsList`@0x101bdc2).
- CCountry fields (CFixedPoint = int32 x1000, `CFixedPoint::GetTruncated`@0x14f4120):
  - treasury +0xb90 (`SetTreasury`@0xfb580e+0x5b)
  - manpower +0xd98 (`SetManpower`@0x102c3b4+0x66)
  - stability +0xbb8 (`SetStability`@0x101766a)
  - prestige +0xcd8 (`AddPrestige`@0xfb568e+0x57)
  - monarch power int[3] at +0x258+4*ability (`AddPower`@0xfb5ecc+0x60)
- Calls on CCountry:
  - `IsAtWarWithCountry(CCountryTag) const`@0x100192c
  - `GetCurrentWars(CArray<CWar*>&) const`@0x103b106
  - `GetOpinionValue(CCountryTag, CString*) const`@0x1054606
  - `GetNumOfAllies() const`@0x10367f2
  - `GetSubjects() const`@0xf96c84
  - `CalculateAllianceDesireability(CCountryTag, CArray<CDiploChance>*, bool) const`@0xfc07fc
  - 892 getters total in `re/out/ccountry_getters.txt`
- Setters mirroring these exist as commands: `CSetTreasuryCommand`, `CAddMonarchPowerCommand`.

## f. Army orders (ctor, object size). Full list: `commands.tsv`
| order | class: ctor @addr, size |
|---|---|
| move | `CMoveCommand(CUnit*, int prov, bool,bool,bool,bool)`@0x19134a4, `(CUnit*, CArray<int> const&, bool,bool,bool)`@0x1913600, `(CUnit*, CList<int> const&)`@0x19136f8; 0x98 |
| merge / split | `CMergeCommand(CPdxArray<CUnit*,int> const&)`@0x1915e14 or `(CList<CUnit*> const&)`@0x1915d0a, 0x60; `CSplitUnitCommand(CUnit const*, int)`@0x19187c8 / `(CUnit const*, CUnit const*, int)`@0x1918852, 0x70 |
| attach / detach | `CAttachUnitCommand(CUnit const*, CUnit const*)`@0x191dc04 0x68; `CDetachUnitCommand(CUnit const*)`@0x191de7a 0x58; `CDetachSubUnitCommand(CUnit const*, SubUnitType, ESelectType)`@0x1918d1e 0x60 |
| siege / assault | `CDetachSiegeCommand(CUnit const*, bool)`@0x191992a 0x60; `CCarpetSiegeCommand(CUnit const*, EStopMissionReason)`@0x191af26 0x78; assault = `CSpendPowerCommand(CCountryTag, CPowerSpending&)`@0xf7320a 0x88 (from `CSiegeView::Assault`). There is no CAssault class |
| embark | `CLoadArmyCommand(CUnit* army, CUnit* navy)`@0x191d896 0x68; `CUnloadArmyCommand(CUnit*)`@0x191d572 0x58; `CConnectToShipCommand(CUnit const*)`@0x1914b20 0x60; `CStartAutoFleetTransportCommand(CArray<CArmy*>&, CArray<CNavy*>&, int, bool)`@0x191f7b8 0x80 |
| recruit / build | `CConstructUnitCommand(CCountryTag, CSubUnitDefinition const*, int prov, CMercenaryCompany const*)`@0xf40252 0x98; `(…, ESpecialUnitCategory)`@0xf40476; `CConstructUnitTemplateCommand(EType, CCountryTag, int, CUnitMacroTemplate*)`@0xf4157e 0x78; `CCancelUnitConstructionCommand`@0xf43376 0x68 |
| general | `CCreateLeaderCommand(CCountryTag, LeaderType, CString const&, bool)`@0x14b7750 0x80; `CAssignLeaderCommand(CUnit*, CLeader*)`@0x14ba4da 0x68; `CDismissLeaderCommand`@0x14b8de2 0x58 |
| stance-like | `CForceMarchCommand(CUnit const*)`@0x191bc64; `CHuntRebelCommand`@0x191a8ec; `CToggleDrillingArmyCommand`@0x191b36a; `CMainArmyCommand(CUnit*, bool,bool,bool)`@0x191c824; `CPatrolCommand`@0x191a646; `CStopUnitMissionCommand`@0x191ff86; `CCancelMovementCommand(CUnit*, EStopMissionReason, bool)`@0x1914832; `CDisbandCommand(CUnit const*, bool)`@0x19151d2; `CReinforceCommand`@0x191693a; `CPerformActionCommand(CUnit*, int, bool)`@0x191b600 |
The meaning of the bools in CMoveCommand and the `EStopMissionReason` values are UNVERIFIED. The safest move entry is `CUnit::PerformAction(CProvince*, bool, bool)`@0x18ff692, which the GUI calls and which builds and posts the right command itself.

## g. Diplomacy
- `CDiplomaticAction` (base CPersistent, vtable@0x2610600). 86 direct subclasses; ctors, sizes and overrides are in `diplomatic_actions.tsv`.
- Base ctor `(CCountryTag actor, CCountryTag, CCountryTag recipient, CEU3Date const&, EType, int, bool)`@0x11b05d4. Layout: tags +0xc/+0x14/+0x1c, dates +0x24/+0x28, int +0x2c, **EType +0x30**, bool +0x34.
- Example: `CImproveRelationAction(CCountryTag from, CCountryTag to, CEU3Date)`@0x1234612 (+0xc=+0x14=from, +0x1c=to), size 0x40. `CAllianceAction`@0x11d3b06 0x40; `CRoyalMarriageAction`@0x11b387a 0x68; `CDeclareWarAction(CCountryTag, CCountryTag, bool, CEU3Date, CWarGoal const*)`@0x11c45e2 0x90.

Vtable (vptr+):
- `CreateCommand() const` +0x48 (sret unique_ptr<CCommand>)
- `ExecuteDerived` +0x50
- `GetName` +0x60
- **`IsValid(CString* reason, bool)` +0x88 -> bool**
- `IsSelectable(CString*, bool)` +0x90
- `IsGUISelectable(CString*, bool)` +0x98
- `GetToken` +0xa0
- `Clone() const` +0xb8
- **`WillAIAccept(CArray<CDiploChance>*) const` +0xc0 -> bool**
- `GetAIAcceptance` +0xc8
- `RequireAcceptance` +0xd8
- `GetAIAcceptanceDerived(CArray<CDiploChance>*, bool)` +0x128

Action -> command: `CDiplomaticAction::CreateCommand`@0x11af14c clones the action (+0xe), sets `clone+0x28` = current date, then `new(0x50)` `CDiplomaticActionCommand` with the clone at +0x48. `CDiplomaticActionCommand::IsValid`@0x11b03aa ends with `action->IsValid(nullptr,false)` (+0xc2..+0xcd).

AI acceptance: `WillAIAccept`@0x11b1c9a returns `GetAIAcceptanceDerived(list, list!=0) > 0`. With a null list it uses a TLS temporary; pass a `std::vector<CDiploChance>` to get the reasons (CDiploChance layout UNVERIFIED). Legal-action report = `IsValid(&str,false)` + `IsSelectable` + `WillAIAccept`.

## h. Time, ticks, notifications, answers
- Commands:
  - `CPauseGame(CString const&, bool, bool, bool, CString const&)`@0x149abaa 0x90
  - `CIncreaseGameSpeedCommand()`@0x17807f0 0x48
  - `CDecreaseGameSpeedCommand(CString const&)`@0x178092c 0x68
  - `CDailyTickCommand(CEU3Date const&)`@0x17804f6 (posted by the engine)
- Local functions:
  - `CGameSpeed::Pause(bool, CString const&, bool, CString const&)`@0x1447c2c, `UnPause(bool, CString const&)`@0x1447f24, `TogglePause`@0x144812c
  - `CInGameIdler::IncreaseGameSpeed()`@0x1481cd2, `DecreaseGameSpeed()`@0x1481d96
  - Console `speed N` (`OnExecute_Speed`@0x19f839b: N=0 pauses, otherwise UnPause plus `SetGamespeed(N-1)`)
- Tick hooks (internal, so inline hook or polling): `CDailyTickCommand::Execute`@0x1780564 -> `CGameState::HandleDailyTick`@0x13d91ca; `MonthlyUpdate(bool)`@0x13da214; `YearlyUpdate`@0x13daed4. Polling `gs+0x1da0` from the frame hook needs no patch.
- Pending events:
  - `CGameState::AddPendingEvent(CEvent const*, CEventScope const&)`@0x13d8538 stores `CPendingEvent` in a container at gs+0x1e68 (layout UNVERIFIED).
  - The popup is `CInGameIdler::AddEventWindow(CEvent const*, CEventScope&, int)`@0x148abfe.
  - Answer with `CSelectEventOptionCommand(CEvent const*, CEventScope&, int, int)`@0x1383a40 (0x138). `CRemovePendingEventCommand(int)`@0x1384d3a removes one.
- Incoming proposals: `CInGameIdler::AddDiploIcon(CDiplomaticActionDialog*, EDiplomacyDialogIndex, CCountryTag, CDiplomaticAction const*)`@0x148acf6, plus `CAlertManager::AddDiploIcon`@0x19af3a0. Answer: set `action+0x30` to **2 = accept** (`CDiplomaticActionDialog::OnAccept`@0x1bed8a4+0x15) or **1 = decline** (`DoDecline`@0x1bed9b8+0x15), then `CreateCommand` -> `Post`. Peace offers use the same path (`CRequestPeaceAction`).
- Alerts: `CAlertManager::AddAlert(ALERT)`@0x19b5704, `Update`@0x1994c88. Messages: `CInGameIdler::LogMessage(unique_ptr<CMessage>&&)`@0x147119c.

## i. Risks
1. **C++ runtime:** libstdc++ is shared (dynamic), so a hook .so built with system g++ against libstdc++.so.6 shares `operator new/delete` and the malloc heap.
   - CStrings or vectors the game frees may be built by the hook; commands must come from `::operator new`.
   - **Do not define `operator new/delete`, `malloc` or any `std::` symbol in the hook.** The exe exports 685 `std::__cxx11::basic_string` and many `std::vector` instantiations, and they bind first.
   - Do not link the hook against OpenSSL, curl, SDL or physfs: the exe exports its own copies (e.g. `a2d_ASN1_OBJECT`, `SDL_*`) and symbols would bind to the exe's.
   - The ABI is cxx11 SSO strings (matches GCC >= 5 default). Building the hook with `-D_GLIBCXX_USE_CXX11_ABI=1` (default) is required.
2. **Visibility:** non-PIE, all globals in .dynsym, so plain `dlsym` works. Locals (`_ZL…`, `(anonymous namespace)`) require a `.symtab` read; base offset is 0. Pin the exe by SHA-256 before trusting any address.
3. **Integrity:** no self-check of the executable was found.
   - `/proc/self/exe` is used only by `CApplication::DoRestart`@0x1fc29da+0x5e, `SDL_GetBasePath_REAL` and physfs.
   - "TracerPid:" is used by `NAssert::IsDebuggerAttached`@0x2544597 (crash reporter / asserts).
   - `CApplication::CalculateGameFilesChecksum`@0x1fc26a0 reports `CVersion::GetExeChkSum` from the manifest (`checksum_manifest.txt`), not a hash of the running exe (UNVERIFIED that it never hashes the binary). This affects MP/ironman compatibility, not running the game.
   - `CGameState::VerifyIntegrity`@0x13e788a and OOS checks are MP game-state checksums.
4. Commands are serialized even in SP. Ironman/MP disables the console. Console use sets `mgr+0x44` and, in MP/ironman, `country+0x287b` (achievements/"cheated" flags, UNVERIFIED meaning).
5. Pointers inside commands (CUnit*, CProvince*, CEvent*) are converted to IDs on write. Never post a half-built command (prior-art crash).

## j. First runtime experiment
1. Build `libeu4hook.so` that defines only `SteamAPI_RunCallbacks()`. It resolves the real one via `dlsym(RTLD_NEXT,…)` and runs a queue only when `__builtin_return_address(0)==(void*)0x1fc23b4`. On load, verify SHA-256 of `/proc/self/exe` and `dlsym` of `_ZN18CConsoleCmdManager7ExecuteERK6CArrayI7CStringE` == 0x253f7e0.
2. Launch with `LD_PRELOAD`. Log the tid of the first qualifying call next to `getpid()`, and the result of calling `ThreadIsMainThread()`@0x2564b9d (expect equal and true). Count calls per second (expect the frame rate).
3. After the map is loaded (`CCurrentGameState::_pInstance` != 0 and `gs+0x1dd0` != 0), build `std::vector<std::string>{"help"}`. On the frame thread call `Execute(&res, *(void**)0x3445380, &vec)`, with `res` 0x28 bytes and aligned. Expect `res.ok` and a non-empty message. Then try `speed 0`/`speed 1` and watch the pause banner.
4. Read `GetPlayer()`, `gs+0x1da0` and `GetCountry(tag)->treasury(+0xb90)/1000`, and compare with the top bar.
5. Post one harmless command (the game runs without Steam, but the PLT call to SteamAPI_RunCallbacks at UpdateOneFrame+0xd is unconditional; the libsteam_api stub still gets called): `new(0x58) CChangeMaintenanceCommand(player, cur, cur)` -> `CSession::Post(session)`. Session = `*(void**)(*(char**)(idler+0x328)+0x318)`. Expect true, and the slider unchanged after the next frame.

## UNVERIFIED (settle at runtime)
- The SteamAPI_RunCallbacks caller tid is the main thread and the call happens once per frame (step 2).
- Post from a non-main thread is safe: assume not.
- session+0x55/+0x7f5 meanings: if Post returns false in SP, log them.
- Commands posted in the hook execute in the same frame.
- `CPendingEvent` container layout at gs+0x1e68.
- CDiploChance layout.
- CMoveCommand bool parameters and `EStopMissionReason` values.
- The meaning of `mgr+0x44` and `country+0x287b`.
- `gs+0x1dd8` speed semantics (0-based).
- Exe checksum is manifest-only.
