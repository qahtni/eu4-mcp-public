# eu4-mcp

An MCP server plus an `LD_PRELOAD` hook that lets Claude play **Europa Universalis IV** (native Linux build **1.37.5**) in a
single-player game: it reads the game state, gives the same orders as the GUI buttons, and advances time. Around it: a live
**watch page** (video + play log), an optional **narrator**, a **camera** helper, a **pop-up watcher**, an optional Discord
screen-share setup, and helper scripts to start and stop everything.

> **Unofficial.** Not affiliated with Paradox Interactive. You need your own legally owned copy of the game. Single-player only.
> No game data is included. See [NOTICE.md](NOTICE.md). Licence: Unlicense, public domain ([LICENSE](LICENSE)).

## How it fits together
```
Claude Code ──stdio──> bin/eu4-mcp (eu4mcp/server.py: tools) ──unix socket──> hook/eu4hook.so (inside the game process)
                                  └── reads saves / game files (digest.py, save.py, gamedata.py)
watch page (eu4mcp/watch.py) ← play log (journal) + HLS video of the game screen (tools/stream.sh)
```
The hook is injected with `LD_PRELOAD`, runs a command queue on the game's main thread, and resolves game functions by symbol
name (no hard-coded addresses). Notes on the internals: `docs/re/spec.md`.

## Requirements
- Linux x86-64, native EU4 **1.37.5** (the hook resolves symbols of this build; other builds are unsupported).
- Python 3.10+ (developed on 3.14), `g++` to build the hook, Claude Code (`claude`) for the MCP server and agents.
- A display for the game: a desktop session, or a headless X server (`tools/display.sh`, needs `sudo`, an NVIDIA GPU in the
  author's setup, see `display/README.md`). The video stream uses `ffmpeg` with NVENC by default (`EU4_ENCODER` overrides).
- Optional: `tmux` (player session), `x11vnc` + `websockify` + `novnc` (game viewer), `xdotool`-free XTEST via python-xlib,
  Vesktop + Chromium (Discord share, `tools/discord.sh`, not included).

## Install
```
git clone <this repo> && cd eu4-mcp
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
make -C hook                      # builds hook/eu4hook.so (not shipped: it is a compiled binary)
cp config.env.example ~/.config/eu4-mcp/config.env   # optional; edit EU4_GAME_DIR etc. (mkdir -p the folder first)
.venv/bin/python -m pytest -q     # offline tests; tests that need the game's tutorial saves skip themselves without a game
```

## Configuration
One file, `~/.config/eu4-mcp/config.env` (or `$EU4_MCP_CONFIG`), plain `KEY=value` lines; environment variables win. It is read by
the `tools/*.sh` scripts and the Python modules. All keys and defaults are listed in [config.env.example](config.env.example):
game directory, user (save) directory, state/notes/cache directories, X display, bind address (**default `127.0.0.1`**), ports
(watch 6081, viewer 6080, Discord 6082), Xorg config, player tmux session and prompt file.
The watch page and viewer have **no login**: only bind them to a LAN address on a network you trust.

## Register the MCP server with Claude Code
```
claude mcp add eu4 -- /absolute/path/to/eu4-mcp/bin/eu4-mcp
```
(or open Claude Code inside the repo: `.mcp.json` registers it as `./bin/eu4-mcp`, relative path untested.) `.claude/settings.json`
pre-allows `mcp__eu4` and adds a reminder after context compaction; trim it if you do not want that.

## Start a game
1. Start EU4 with the hook: `tools/game.sh start` (sets `LD_PRELOAD`, `DISPLAY`, the hook socket directory; starts the headless X
   display first if it is not running). On a desktop session use `EU4_DISPLAY=:0` and start the game yourself with
   `LD_PRELOAD=$PWD/hook/eu4hook.so EU4_MCP_STATE=~/.config/eu4-mcp ./eu4`.
2. Begin or load a single-player campaign (non-ironman). `tools/hookc.py info` should answer.
3. In Claude Code: ask it to call `eu4_status` (or `eu4_refresh` first).
4. Everything at once: `tools/eu4 start` (display, game, stream, viewer, watch page, narrator, camera), `tools/eu4 play`
   (a Claude session in tmux; copy `tools/player_prompt.example.txt`, fill in country/goals, set `EU4_PLAYER_PROMPT`),
   `tools/eu4 status`, `tools/eu4 stop`. `tools/eu4 help` lists the rest.
5. Watch page: `http://<EU4_HOST>:6081/`.

## Tools (MCP)
Read: `eu4_status`, `eu4_alerts`, `eu4_country`, `eu4_diplomacy`, `eu4_wars`, `eu4_armies`, `eu4_province`, `eu4_provinces`,
`eu4_neighbours`, `eu4_find`, `eu4_changes`, `eu4_refresh`, `eu4_playlog`, `eu4_events`.
Orders: `eu4_army` (move, split, merge, disband, assault, leaders, mercenaries ...), `eu4_economy` (build, recruit, develop, tech,
ideas, advisors, maintenance, mission ...), `eu4_loans`, `eu4_diplo` (alliances, marriages, claims, war, peace ...), `eu4_estates`,
`eu4_answer` (events), `eu4_time`, `eu4_advance` (run to a date, then pause, save and report; runs the alerts), `eu4_console` (raw
console command; do not use it to cheat).
Memory/log: `eu4_notes_brief|read|write|append`, `eu4_say` (comment for the watch page), `eu4_issue`, `eu4_learn`.

## Agent roles (`.claude/agents/`)
`eu4-strategist` (main player), `eu4-clock` (advances time), `eu4-steward` (routine economy), `eu4-scout` (read-only questions),
`eu4-general` (war micro), `eu4-critic` (reviews a stretch; appends to `docs/lessons.md`). Guide: `docs/strategist_guide.md`;
alert rules: `docs/alerts.txt`; campaign recap prompt: `docs/summary_prompt.md`.

## Optional: revanchism buff (cheat, OFF)
`eu4mcp/revanchism.py` can hold the player's revanchism at 100% when losing. Disabled unless `EU4_MCP_REVANCHISM=1`; leave it off
for a normal game.

## Regenerating data that is not shipped
Nothing extracted from the game is in the repo. Game files are read from your install at run time (parsed cache in `EU4_MCP_CACHE`).
The reverse-engineering tables (`commands.tsv`, `diplomatic_actions.tsv`, `console_commands.txt`) are built from your own binary by
the scripts in `re/` (see `re/README.md`).

## Known limits and unproven parts
- Build 1.37.5 Linux only; no Windows/macOS. Single-player only.
- Proven in the author's game: move, stability, develop, tech, recruit, build, loans, maintenance, leaders, split/merge/disband,
  ideas, events, advisors, merchants, estates, several diplomatic actions, war declaration.
- Wired but **unproven on a positive case**: core, assault, colonise/recall colonist, land exploration, mission, decision, real loan
  repay, mercenary hire/disband (listing and dry runs proven). See `docs/re/*_prove_plan.txt` for how to prove them in a throwaway game.
- Not reachable yet: government reforms, institutions, missionaries, states/edicts, culture conversion, fleet missions, loading
  transports, drilling, trade companies, native policy, colonial nation interactions, papal actions, age abilities, holy orders.
- **Not tested outside the author's machine**: clean-machine install from this README, `.mcp.json` relative path, Xorg start without the
  example config, desktop-display mode, non-NVIDIA stream encoders, the Discord scripts, `re/` regeneration, `tools/eu4 play` with a fresh prompt.
- The narrator and blocker watch run extra `claude -p` calls (usage cost) while enabled.
