# Prompt: summarise and timeline the whole EU4 campaign

Paste everything below the line into a fresh Claude Code session (any directory; it only reads files). It is written for the person
who watched the game and wants an entertaining account, not an audit.

---

You are a war correspondent and court chronicler writing the story of a finished (or interrupted) Europa Universalis IV campaign that an AI
(Claude) played as **<COUNTRY (TAG)>** from **<START DATE>**, goals: **<GOALS>**. (Fill these in before pasting.)
The reader watched it live and wants a fun, accurate recap and a timeline. Be vivid and a little dry-humoured, but never invent facts:
every date, number, province, war and decision must come from the files below. When the files do not say, say "the log does not say".

## Where the material is (all read-only; never modify, never run the game or any eu4 tool)
Archive folder: `~/.local/share/eu4-mcp/archive/`
- `sessions/session-NNN-<wall start>-game<first game date>.jsonl`: the play log, one file per play session, in order. This is the main source.
  Read the files in name order. They can be big: use `wc -l`, then read in chunks (`sed -n 1,200p`), or grep by kind; do not cat everything at once.
  If `sessions/` is empty or missing, use `journal.full.jsonl` (the whole log in one file) or `~/.local/share/eu4-mcp/journal.jsonl`.
- `notes/` (copy of the strategist's notes): `plan.md`, `commitments.md`, `standing_rules.md`, `journal.md` (the strategist's own dated log), `review.md`
  (critic reviews), `intel/<TAG>.md` (what was learned about other countries). Read `plan.md` and `journal.md` first for the overall arc.
- `lessons.txt` (lessons learned), `mechanics_todo.txt` (mechanics noticed but not usable), `issues.txt` (tool problems).
- `game_starts.txt`: epoch times when the game process started (a restart marks a new session).
- The game saves are PLAIN TEXT (Clausewitz format, `EU4txt` header, ~40 MB; fully readable, cheap Haiku subagents can read them in chunks):
  `~/.local/share/eu4-mcp/archive/saves/latest.eu4.gz` (gzip; newest snapshot, `zcat` it to a scratch file first) and `save-<year>-<date>.eu4.gz`
  (one every 5 game years: use them to measure the state at each era). The live saves are in
  `~/.local/share/Paradox Interactive/Europa Universalis IV/save games/` (`autosave.eu4` newest). Use the save for the FACTS the log lacks: final date (`date=`),
  the player's tag block (`<TAG>={`: treasury, income, development, provinces owned, army, rulers, ideas, tech, colonies, subjects, allies, rivals),
  war history (`previous_war=`), province ownership, great-power ranking. Delegate this: ask Haiku agents (e.g. `haiku-reader`) to grep/read specific blocks of the
  extracted save and return the numbers with exact quotes and line numbers; never load the whole file yourself.

## Log format (JSON lines, keys `t` wall-clock epoch, `kind`, `date` = in-game date)
- `kind: "say"`: the strategist's (and helpers') own comments to viewers, `who` + `text`. The best source for intent and "why".
- `kind: "story"`: plain-language narration written by a small model about the orders; handy but sometimes slightly wrong (stale dates, odd numbers). Prefer `say`/`act` when they disagree.
- `kind: "act"`: an order or change (`tool`, `args`, `out`, `ok`): declare_war, peace, recruit, build, advance, answer (events), estates, hire, develop, colonise, etc.
  `ok:false` = the game refused it. `advance` results contain what happened during the time skipped (wars, events, deaths, income).
- `kind: "read"`: look-ups; ignore except to understand a decision. `kind: "note"`: notes written.
Game dates are `YYYY.M.D`. Session boundaries are the file boundaries.

## What to produce
Write one Markdown file `~/.local/share/eu4-mcp/archive/summary/campaign_summary.md` (create the folder) containing, in this order:
1. **The short version** (5-8 lines): where the country ended (date reached, rank, provinces/development, colonies, wars won/lost, form Spain yes/no), and the one-sentence headline.
2. **Scorecard**: a small table of the goals (the goals you filled in above) with the date achieved or "not reached", and key stats at the end if known (treasury, army, income).
3. **Timeline**: a dated table or list, oldest first, one line per milestone: date | what happened | why it mattered. Include: every war (declared, key battles/sieges, peace and what was gained), rulers and heirs changing, alliances and royal marriages, major events and which option was picked, disasters or rebellions, idea groups and technology steps, key buildings/economy moves, colonies founded, exploration, Spain formed, big diplomacy. Aim for completeness over the whole span, not just the early years: if the log is long, process it chunk by chunk and keep a running timeline.
4. **Chapters**: group the timeline into 4-8 named eras (e.g. "The First Great War 1444-1450") with 1-3 paragraphs each in an entertaining voice: what the strategist was trying, what went right, what went wrong, memorable decisions, funny or dramatic moments (refused orders, tool mishaps that cost time, near-disasters, lucky breaks). Quote a short line from a `say` entry now and then (with its date).
5. **Cast and places**: short list of the key rulers, generals, rival countries, and provinces that mattered, one line each.
6. **Mistakes and lessons**: the 5-10 biggest mistakes, delays or tool problems (from `ok:false` runs, `issues.txt`, `lessons.txt`, `review.md`) and what was learned.
7. **Stats from the log**: counts you can compute with grep/jq/python: number of in-game years covered, sessions, wars declared, events answered, orders refused vs accepted, and the busiest in-game year.
8. **Epilogue**: two or three sentences on what happens next if the game was interrupted, or how it ended.

## Rules
- Facts only from the files. Mark uncertain items with "(?)". Do not guess results of wars or years the log does not show.
- Dates in the timeline are in-game dates; mention wall-clock time only for sessions/outages.
- Keep it readable: the whole file should be skimmable in 10 minutes (about 1,500-3,000 words plus the timeline table), no raw JSON.
- Do not paste secrets or file paths of credentials; there should be none in the logs.
- When done, print the path of the file and a 5-line teaser of the story. Do not do anything else.
