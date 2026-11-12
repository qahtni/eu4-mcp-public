# Reverse-engineering helpers

Scripts that read **your own copy** of the EU4 Linux binary (`$EU4_GAME_DIR/eu4`, default `~/games/eu4/eu4`) and write their
results to `re/out/` (gitignored). Nothing extracted from the game is shipped in this repository.

Needs `binutils` (`nm`, `objdump`, `c++filt`) and `capstone` (`pip install capstone`). Run from the repo root, in this order:

    python3 re/elfutil.py        # helpers shared by the others (import only)
    python3 re/build_commands.py # re/out/commands.tsv        (needs the intermediate files written by xref.py / typeinfo.py)
    python3 re/build_diplo.py    # re/out/diplomatic_actions.tsv
    python3 re/console_tab.py    # re/out/console_commands.txt

The order and the intermediate files are the author's workflow and were not re-run for the public release: UNTESTED on a clean
machine. `docs/re/spec.md` explains what the results are used for; `fdis.py` disassembles one function (see CLAUDE.md rule:
disassemble before calling a game function through the hook's `vm`).
