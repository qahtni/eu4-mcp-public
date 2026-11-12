#!/bin/sh
# Put the current tool code into every running eu4 MCP server (the player session included) WITHOUT a restart:
# runs the tests, then writes a new reload stamp; each server loads the code again on its next request and tells
# its Claude session when the tool list changed. `tools/deploy.sh -f` skips the tests. Changes to eu4mcp/front.py
# still need a session restart.
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE" || exit 1
if [ "$1" != "-f" ]; then
  .venv/bin/python -m pytest -q -x > "${TMPDIR:-/tmp}/eu4-deploy-tests.$$" 2>&1; rc=$?
  tail -3 "${TMPDIR:-/tmp}/eu4-deploy-tests.$$"; rm -f "${TMPDIR:-/tmp}/eu4-deploy-tests.$$"
  [ $rc -eq 0 ] || { echo "tests FAILED: nothing deployed"; exit 1; }
fi
.venv/bin/python -c "import eu4mcp.front as f; f.mcp.load(first=True); print(len(f.mcp._names()), 'tools load cleanly')" || { echo "import FAILED: nothing deployed"; exit 1; }
S="${EU4_MCP_STATE:-$HOME/.config/eu4-mcp}/reload"
date +%s%N > "$S" && echo "deployed: running servers reload on their next request ($S)"
