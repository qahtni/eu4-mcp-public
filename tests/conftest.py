import os
import sys
from pathlib import Path

os.environ["EU4_MCP_NO_CONFIG"] = "1"   # tests ignore the user config file

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GAME = Path(os.environ.get("EU4_GAME_DIR") or "~/games/eu4").expanduser()
TUT = GAME / "tutorial"
USER = Path(os.environ.get("EU4_USER_DIR") or "~/.local/share/Paradox Interactive/Europa Universalis IV").expanduser()


def tutorial_saves():
    return sorted(TUT.glob("*.eu4"))


needs_tutorial = pytest.mark.skipif(not tutorial_saves(), reason="tutorial saves not installed")


@pytest.fixture(scope="session")
def big_path():
    saves = tutorial_saves()
    if not saves:
        pytest.skip("no tutorial saves")
    return max(saves, key=lambda p: p.stat().st_size)


@pytest.fixture(scope="session")
def big(big_path):
    from eu4mcp.save import Save

    return Save(big_path)


@pytest.fixture(scope="session")
def gd():
    from eu4mcp.gamedata import GameData

    return GameData.load()


@pytest.fixture(scope="session")
def all_saves():
    from eu4mcp.save import Save

    return [Save(p) for p in tutorial_saves()]


@pytest.fixture(scope="session")
def live_saves():
    """Current-format saves from the user dir, if any (read-only)."""
    from eu4mcp.save import Save

    d = USER / "save games"
    out = [Save(p) for p in sorted(d.glob("*.eu4"))] if d.is_dir() else []
    return out
