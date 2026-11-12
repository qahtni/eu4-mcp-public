"""Names and localisation from the game files (read-only), cached as a pickle.

Sources (all under the game dir):
* ``localisation/*_l_english.yml``  (UTF-8 with BOM)  ``KEY:0 "text"`` lines: country names are
  keyed by tag (``CAS:0 "Castile"``), provinces by ``PROV<id>``, everything else (buildings, advisor
  types, ideas, CBs, tech groups, religions, cultures ...) by its script key.
* ``common/country_tags/*.txt``   (Windows-1252)  list of static tags.
* ``common/units/*.txt``          regiment/ship type -> ``type = infantry|cavalry|artillery|galley|...``.
* ``common/buildings/*.txt``      building -> ``fort_level`` (modifier block) for fort levels.
* ``map/definition.csv``          (Windows-1252) province id -> name, fallback when localisation lacks it.

The cache lives in ``<cache dir>/gamedata-<hash>.pickle`` keyed by the mtimes/sizes of ``eu4_rev.txt``,
``eu4_branch.txt`` and the localisation/units files, so a game update rebuilds it.
"""
from __future__ import annotations

import hashlib
import os
import pickle
import re
from pathlib import Path

from . import paths
from .clausewitz import parse

_LOC_LINE = re.compile(r'^\s*([^\s:#"]+):\d*\s+"(.*)"\s*(?:#.*)?$')
_COLOR = re.compile(r"§.|§!")
_TAG_LINE = re.compile(rb'^\s*([A-Za-z0-9]{3})\s*=\s*"', re.M)
_UNIT_TYPE = re.compile(rb"^\s*type\s*=\s*(\w+)", re.M)


def clean(text: str) -> str:
    """Strip Paradox colour codes (``§Y ... §!``) and newlines escapes from a localisation string."""
    return _COLOR.sub("", text).replace("\\n", " ")


def _version_key(game: Path) -> str:
    h = hashlib.sha1(str(game).encode())
    for rel in ("eu4_rev.txt", "eu4_branch.txt"):
        try:
            st = (game / rel).stat()
            h.update(f"{rel}:{st.st_mtime_ns}:{st.st_size}".encode())
        except OSError:
            h.update(f"{rel}:missing".encode())
    for sub in ("localisation", "common/units", "common/country_tags"):
        d = game / sub
        try:
            for p in sorted(d.iterdir()):
                if p.name.endswith(("_l_english.yml", ".txt")):
                    st = p.stat()
                    h.update(f"{p.name}:{st.st_mtime_ns}:{st.st_size}".encode())
        except OSError:
            h.update(f"{sub}:missing".encode())
    return h.hexdigest()[:16]


class GameData:
    """Lookup tables built from game files. Use ``GameData.load()`` (cached)."""

    def __init__(self, loc: dict[str, str], provs: dict[int, str], tags: set[str], units: dict[str, str], version: str = "", forts: dict[str, int] | None = None):
        self.forts = forts or {}
        self.loc = loc
        self.provs = provs
        self.tags = tags
        self.units = units
        self.version = version
        self._country_names: dict[str, str] | None = None

    # ---- lookups -------------------------------------------------------------------------
    def loc_get(self, key: str, default: str | None = None) -> str | None:
        v = self.loc.get(key)
        return clean(v) if v is not None else default

    def l(self, key: str) -> str:
        """Localised text for a script key, falling back to a prettified key (``fort_15th`` -> ``fort 15th``)."""
        v = self.loc.get(key)
        if v is None:
            v = self.loc.get("building_" + key)
        if v is not None:
            return clean(v)
        return str(key).replace("_", " ")

    def country_name(self, tag: str) -> str | None:
        v = self.loc.get(tag)
        return clean(v) if v else None

    def province_name(self, pid: int) -> str | None:
        return self.provs.get(pid)

    def unit_class(self, unit_type: str) -> str | None:
        return self.units.get(unit_type)

    def country_names(self) -> dict[str, str]:
        if self._country_names is None:
            self._country_names = {t: clean(self.loc[t]) for t in self.tags if t in self.loc}
        return self._country_names

    def find_countries(self, text: str, extra: dict[str, str] | None = None) -> list[tuple[str, str]]:
        """Countries whose tag equals or whose name contains ``text`` (case-insensitive). Exact matches first."""
        return _rank(text, {**self.country_names(), **(extra or {})})

    def find_provinces(self, text: str) -> list[tuple[int, str]]:
        return _rank(text, self.provs)

    # ---- loading -------------------------------------------------------------------------
    @classmethod
    def load(cls, game: Path | None = None, cache: Path | None = None) -> "GameData":
        game = game or paths.game_dir()
        cache = cache or paths.cache_dir()
        key = _version_key(game)
        f = cache / f"gamedata-{key}.pickle"
        try:
            with open(f, "rb") as fh:
                obj = pickle.load(fh)
            return cls(obj["loc"], obj["provs"], obj["tags"], obj["units"], key, obj["forts"])
        except Exception:
            pass
        gd = cls._build(game, key)
        try:
            cache.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(f".tmp{os.getpid()}")
            with open(tmp, "wb") as fh:
                pickle.dump({"loc": gd.loc, "provs": gd.provs, "tags": gd.tags, "units": gd.units, "forts": gd.forts}, fh, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, f)
            for old in cache.glob("gamedata-*.pickle"):
                if old != f:
                    old.unlink(missing_ok=True)
        except OSError:
            pass
        return gd

    @classmethod
    def _build(cls, game: Path, version: str) -> "GameData":
        loc: dict[str, str] = {}
        locdir = game / "localisation"
        for p in sorted(locdir.glob("*_l_english.yml")):
            with open(p, encoding="utf-8-sig", errors="replace") as fh:
                for line in fh:
                    m = _LOC_LINE.match(line)
                    if m:
                        loc[m[1]] = m[2]
        provs: dict[int, str] = {}
        try:
            with open(game / "map" / "definition.csv", "rb") as fh:
                next(fh, None)
                for raw in fh:
                    parts = raw.decode("cp1252", "replace").split(";")
                    if len(parts) >= 5 and parts[0].isdigit() and parts[4] and parts[4] != "x":
                        provs[int(parts[0])] = parts[4]
        except OSError:
            pass
        for k, v in loc.items():
            if k.startswith("PROV") and k[4:].isdigit():
                provs[int(k[4:])] = clean(v)
        tags: set[str] = set()
        for p in (game / "common" / "country_tags").glob("*.txt"):
            tags.update(m.decode("ascii") for m in _TAG_LINE.findall(p.read_bytes()))
        units: dict[str, str] = {}
        for p in (game / "common" / "units").glob("*.txt"):
            m = _UNIT_TYPE.search(p.read_bytes())
            if m:
                units[p.stem] = m[1].decode("ascii")
        forts: dict[str, int] = {}
        for p in (game / "common" / "buildings").glob("*.txt"):
            try:
                doc = parse(p.read_bytes())
            except ValueError:
                continue
            for name, b in doc.items():
                if isinstance(b, dict):
                    mod = b.get("modifier")
                    lvl = mod.get("fort_level") if isinstance(mod, dict) else b.get("fort_level")
                    if isinstance(lvl, (int, float)):
                        forts[str(name)] = int(lvl)
        return cls(loc, provs, tags, units, version, forts)


def _rank(text: str, table: dict) -> list[tuple]:
    t = text.strip().lower()
    if not t:
        return []
    exact, prefix, sub = [], [], []
    for k, name in table.items():
        n = name.lower()
        if str(k).lower() == t or n == t:
            exact.append((k, name))
        elif n.startswith(t):
            prefix.append((k, name))
        elif t in n:
            sub.append((k, name))
    key = lambda kv: str(kv[1])
    return sorted(exact, key=key) + sorted(prefix, key=key) + sorted(sub, key=key)


_EVENT_FILES: dict[str, bytes] = {}


def event_def(event_id: str, game: Path | None = None) -> dict | None:
    """Title/description keys and the options (name key + compact script) of an event, read from events/*.txt."""
    import re

    from . import paths
    from .clausewitz import find_matching_brace

    d = (game or paths.game_dir()) / "events"
    pat = re.compile(rb"\bid\s*=\s*\"?" + re.escape(event_id.encode()) + rb"\"?\s")
    if not _EVENT_FILES:
        for f in sorted(d.glob("*.txt")):
            try:
                _EVENT_FILES[f.name] = f.read_bytes()
            except OSError:
                pass
    ns = event_id.split(".")[0].encode()
    for data in _EVENT_FILES.values():
        if ns not in data:
            continue
        for m in pat.finditer(data):
            # the definition is a top-level country_event/province_event block; skip references such as
            # ``country_event = { id = war_of_the_roses.105 days = 1 }`` inside another event's option
            start = max(data.rfind(b"country_event", 0, m.start()), data.rfind(b"province_event", 0, m.start()))
            ob = data.find(b"{", start)
            if start < 0 or ob < 0 or ob > m.start() or (start > 0 and data[start - 1:start] not in (b"\n", b"\r")):
                continue
            break
        else:
            continue
        body = data[ob:find_matching_brace(data, ob) + 1].decode("latin1")
        body = re.sub(r"#[^\n]*", "", body)
        out: dict = {"id": event_id, "options": []}
        for k in ("title", "desc"):
            mm = re.search(rf"\b{k}\s*=\s*\"?([\w.]+)", body)
            out[k] = mm.group(1) if mm else None
        depth = 0
        i = 0
        while i < len(body):
            ch = body[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            elif depth == 1 and body.startswith("option", i) and re.match(r"option\s*=\s*\{", body[i:i + 20]):
                o = body.index("{", i)
                e = find_matching_brace(body.encode("latin1"), o)
                txt = body[o + 1:e]
                mm = re.search(r"\bname\s*=\s*\"?([\w.]+)", txt)
                script = " ".join(re.sub(r"\bname\s*=\s*\"?[\w.]+\"?", "", txt, count=1).split())
                out["options"].append({"name": mm.group(1) if mm else "", "script": script})
                i = e + 1  # past the option's closing brace (re-reading it dropped depth to 0: only the first option was found)
                continue
            i += 1
        return out
    return None
