"""Filesystem locations, all overridable through environment variables.

EU4_GAME_DIR   game install (read-only)        default ~/games/eu4
EU4_USER_DIR   Paradox user dir or a save dir  default ~/.local/share/Paradox Interactive/Europa Universalis IV
EU4_MCP_STATE  hook socket / runtime state     default ~/.config/eu4-mcp
EU4_MCP_NOTES  strategist notes root           default ~/.local/share/eu4-mcp/notes
EU4_MCP_CACHE  parsed game data cache          default ~/.cache/eu4-mcp
EU4_DISPLAY    X display the game runs on       default :99

All of these (and the EU4_BIND / EU4_*_PORT values the tools/ scripts use) can also be set in one config file of plain KEY=value lines:
$EU4_MCP_CONFIG, default ~/.config/eu4-mcp/config.env. Real environment variables win over the file.
"""
from __future__ import annotations

import os
from pathlib import Path


def _load_config_file() -> None:
    if os.environ.get("EU4_MCP_NO_CONFIG"):
        return
    base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    f = Path(os.environ.get("EU4_MCP_CONFIG") or Path(base) / "eu4-mcp" / "config.env").expanduser()
    try:
        lines = f.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for ln in lines:
        ln = ln.strip()
        if ln.startswith("export "):
            ln = ln[7:].strip()
        if not ln or ln.startswith("#") or "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        v = v.strip().strip("'\"")
        if k.strip().startswith("EU4_"):
            os.environ.setdefault(k.strip(), os.path.expandvars(v))


_load_config_file()


def _env(name: str, default: str) -> Path:
    return Path(os.environ.get(name) or default).expanduser()


def game_dir() -> Path:
    return _env("EU4_GAME_DIR", "~/games/eu4")


def user_dir() -> Path:
    return _env("EU4_USER_DIR", "~/.local/share/Paradox Interactive/Europa Universalis IV")


def state_dir() -> Path:
    return _env("EU4_MCP_STATE", "~/.config/eu4-mcp")


def notes_dir() -> Path:
    return _env("EU4_MCP_NOTES", "~/.local/share/eu4-mcp/notes")


def cache_dir() -> Path:
    return _env("EU4_MCP_CACHE", "~/.cache/eu4-mcp")


def display() -> str:
    return os.environ.get("EU4_DISPLAY") or ":99"


def xauthority() -> Path:
    return state_dir() / "Xauthority"


def hook_socket() -> Path:
    return state_dir() / "hook.sock"
