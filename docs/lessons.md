# Lessons (tool traps and play rules)

The critic agent appends dated blocks here (`PLAY`, `TOOL`, `GUIDE` lines); the strategist reads this file at the start of a campaign.
Start with these generic ones, found while playing with the tools:

- TOOL: `eu4_status` may show a stale snapshot of another game; call `eu4_refresh` first.
- TOOL: `eu4_economy action=loan` ignores `dry=true` and takes a real loan. Never call it to probe.
- TOOL: `eu4_economy build` answers "the game refuses this order now" when the treasury is below the building cost or the technology gate is not met. It is not a tool bug: save up or buy technology.
- TOOL: `eu4_answer` takes 1-based option numbers; option texts may show as "?" for events whose text the tool cannot read.
- PLAY: after rebels leave, a province can stay rebel-controlled for years; list provinces held by REB after any uprising and retake them.
- PLAY: a proposal from the AI (`eu4_diplo incoming`, "Sue for Peace") can end a lost war when your own offers are declined; terms are not shown, so check it every few months.
- PLAY: fix stability before declaring war; negative stability with unhappy estates spawns rebels mid-war.
