# Playing guide for the strategist (generic)

Written for any country and start date. It deliberately holds no game data: look facts up with the tools
(`eu4_country`, `eu4_province`, `eu4_diplomacy`, ...) or in your own copy of the game files.

## Loop
1. Start of a session, and after context compaction: `eu4_notes_brief`, then `eu4_status` (`eu4_refresh` first if the snapshot looks stale).
2. Orders are given while the game is paused. Time moves only through `eu4_advance`; read its ALERTS lines before the next order.
3. Keep the notes current: `plan`, `commitments`, `standing_rules`, and `intel/<TAG>` for other countries.
4. Delegate routine work (clock, steward, scout, general) so the main context stays small.
5. After each war, and every ~25 game years, run the critic and fold its advice into the plan.

## Opening checklist (any country)
- Check the stability, treasury, manpower and rulers; fix negative stability before anything else.
- Look at estates, missions, available ideas and your rivals/allies. Use the tools instead of assuming.
- Pick one first target. Fabricate claims during a truce rather than declaring without a good casus belli.

## War checklist
- Stability >= +1 before declaring; check manpower, treasury and allies (call yours on day one).
- War score is net: enemy occupation of your land cancels yours. Keep a field stack against landings, a siege stack on sieges.
- Manpower refills slowly (about a tenth of the maximum per year): plan the next war from that, not from the truce end.
- Cut maintenance on idle fleets; avoid loans.
- Take the best peace when manpower and treasury are nearly gone.

See `docs/lessons.md` for tool traps found while playing.
