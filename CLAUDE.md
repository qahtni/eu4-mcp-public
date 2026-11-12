# eu4-mcp

MCP server + LD_PRELOAD hook that lets Claude play Europa Universalis IV (native Linux 1.37.5). Setup, configuration and the tool list: README.md.

Rules that always apply:
- Never use console cheats in a game that counts; test orders on a separate, throwaway game.
- Do not give game orders or advance time from a second session while another session is playing; read-only tools are fine.
- Addresses are never hard-coded (resolve by symbol name); disassemble (`re/fdis.py`) before calling a game function through `vm`.
- Tool results are digests (< ~1,500 tokens by default).
- Long jobs run as systemd user units (`systemd-run --user --collect --unit=...`).
- Tests: `.venv/bin/python -m pytest -q`.
