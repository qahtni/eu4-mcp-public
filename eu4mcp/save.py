"""Lazy EU4 save reader.

A save is either a plain ``EU4txt`` file or a zip with members ``meta``, ``gamestate`` and ``ai``
(the digests only need ``gamestate``; ``meta`` is used as a fallback for ``date``/``player``).

``Save(path)`` reads the bytes and builds an index of the top-level keys in one regex pass over
column-0 lines (top-level keys are never indented in saves; province blocks are ``-<id>={`` and
start with ``-`` so they do not collide).  Nothing is parsed until asked for:

* ``save.top("date")``            scalar / parsed block of a top-level key (cached)
* ``save.countries()``            tag -> (start, end) block index of ``countries``
* ``save.country(tag)``           parsed country dict (cached), ``None`` if unknown
* ``save.province(pid)``          parsed province dict (province keys are negative in the file: ``-1={``)
* ``save.province_table()``       id -> {name, owner, controller} via one regex pass (no parsing)
* ``save.diplomacy()``            relation type -> list of relations
* ``save.wars()``                 list of parsed ``active_war`` blocks
* ``save.people(tag)``            id -> person dict (monarch/heir/queen/leader) from the country's history

``open_save(path)`` caches by (path, mtime, size) and keeps only the last few saves in memory.
"""
from __future__ import annotations

import os
import re
import zipfile
from pathlib import Path
from typing import Any

from . import paths
from .clausewitz import Date, Multi, decode, scalar, ParseError, aslist, find_matching_brace, parse, parse_block, parse_value_at

_TOP = re.compile(rb'^([A-Za-z_][A-Za-z0-9_]*)(?:[ \t]*=[ \t]*)?(\{|"[^"\n]*"|[^\s{}=]+)?', re.M)
_COUNTRY_KEY = re.compile(rb"\n\t([A-Za-z0-9\-]{3})=\{")
_COUNTRY_NAME = re.compile(rb'\n\t([A-Za-z0-9\-]{3})=\{|\n\t\tname="([^"\n]*)"')
_PROV_KEY = re.compile(rb"\n-(\d+)=\{")
_PROV_FIELDS = re.compile(rb'\n-(\d+)=\{|\n\t\t(owner|controller|name)="([^"\n]*)"')

_COUNTRY_LISTS = frozenset({"army", "navy", "regiment", "ship", "loan", "estate", "modifier", "leader", "mercenary_company"})
_WAR_LISTS = frozenset({"participants", "add_attacker", "add_defender", "rem_attacker", "rem_defender", "battle"})
_DIPLO_LISTS = frozenset(
    {"casus_belli", "dependency", "royal_marriage", "alliance", "guarantee", "transfer_trade_power", "military_access", "warning", "subsidies", "support_independence", "steer_trade", "union"}
)


def _text(b: bytes) -> str:
    """Save strings are Windows-1252 (Le\xf3n); accept UTF-8 when it decodes cleanly."""
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("cp1252", "replace")


class SaveError(RuntimeError):
    pass


class Save:
    def __init__(self, path: str | os.PathLike):
        self.path = Path(path)
        st = self.path.stat()
        self.mtime_ns = st.st_mtime_ns
        self.size = st.st_size
        raw = self.path.read_bytes()
        self.meta: bytes | None = None
        if raw[:4] == b"PK\x03\x04":
            with zipfile.ZipFile(self.path) as zf:
                names = zf.namelist()
                if "gamestate" not in names:
                    raise SaveError(f"{self.path.name}: zip save without 'gamestate' member (has {names})")
                self.data = zf.read("gamestate")
                if "meta" in names:
                    self.meta = zf.read("meta")
            self.zipped = True
        else:
            self.data = raw
            self.zipped = False
        if self.data[:3] == b"\xef\xbb\xbf":
            self.data = self.data[3:]
        if self.data[:8].lstrip().startswith(b"EU4txt") and not self.data.startswith(b"EU4txt"):
            self.data = self.data.lstrip()
        if not self.data.startswith(b"EU4txt"):
            raise SaveError(f"{self.path.name}: not an EU4 text save (header {self.data[:8]!r}; ironman/binary saves are unsupported)")
        self._idx: dict[str, list[tuple[int, int]]] = {}
        self._cache: dict[Any, Any] = {}
        self._country_idx: dict[str, tuple[int, int]] | None = None
        self._prov_idx: dict[int, tuple[int, int]] | None = None
        self._index_top()

    # ---- top-level index -----------------------------------------------------------------
    def _index_top(self) -> None:
        """Index top-level keys (offsets of each value), without parsing.

        The save writer indents inconsistently (``map_area_data`` and ``provinces`` write children at column 0;
        chapter1 even has an unbalanced brace inside ``pending_events``), so neither "column-0 key" nor brace
        counting is reliable.  What is reliable in every save seen: a top-level block is closed by a ``}`` at
        column 0 and the line after it starts at column 0 (a stray column-0 ``}`` inside
        ``religion_instance_data`` is followed by indented text).  So a top-level block ends at the first such ``\n}``
        (single-line ``key={ ... }`` blocks are detected first).  If the result lacks ``countries`` or
        ``provinces`` fall back to real brace matching (about 1 s per 30 MB).
        """
        data = self.data
        idx: dict[str, list[tuple[int, int]]] = {}
        try:
            idx = self._index_top_fast(data)
        except ParseError:
            idx = {}
        if "countries" not in idx or "provinces" not in idx:
            try:
                idx = self._index_top_slow(data)
            except ParseError:
                pass
        self._idx = idx
        if "countries" not in idx or "provinces" not in idx:
            raise SaveError(f"{self.path.name}: save lacks 'countries'/'provinces' (found {sorted(idx)[:12]}...)")

    @staticmethod
    def _index_top_fast(data: bytes) -> dict[str, list[tuple[int, int]]]:
        idx: dict[str, list[tuple[int, int]]] = {}
        n = len(data)
        pos = data.find(b"\n") + 1  # skip the EU4txt header line
        match = _TOP.match
        find = data.find
        while pos < n:
            m = match(data, pos)
            if m is None:
                nl = find(b"\n", pos)
                if nl < 0:
                    break
                pos = nl + 1
                continue
            key = m[1].decode("ascii")
            v = m[2]
            if v is None:
                pos = m.end()
            elif v == b"{":
                vs = m.start(2)
                eol = find(b"\n", vs)
                eol = n if eol < 0 else eol
                line = data[vs:eol]
                if line.count(b"{") == line.count(b"}") and line.count(b"{") > 0:  # one-liner, e.g. flags={ }
                    e = vs + line.rfind(b"}")
                else:
                    e = vs
                    while True:
                        e = find(b"\n}", e)
                        if e < 0:
                            raise ParseError(f"top-level block '{key}' is never closed at column 0")
                        q = e + 2
                        while data[q : q + 1] in (b"\n", b"\r"):  # blank lines
                            q += 1
                        nxt = data[q : q + 1]  # first char of the next non-blank line
                        e += 1
                        if nxt not in (b" ", b"\t"):  # a stray "\n}" is followed by indented text
                            break
                idx.setdefault(key, []).append((vs, e))
                pos = e + 1
            else:
                idx.setdefault(key, []).append((m.start(2), m.end(2) - 1))
                pos = m.end()
            # advance to the next line start
            nl = find(b"\n", pos)
            if nl < 0:
                break
            pos = nl + 1
        return idx

    @staticmethod
    def _index_top_slow(data: bytes) -> dict[str, list[tuple[int, int]]]:
        from .clausewitz import block_index

        idx: dict[str, list[tuple[int, int]]] = {}
        for key, s, e in block_index(data, data.find(b"\n") + 1, len(data)):
            if isinstance(key, str):
                idx.setdefault(key, []).append((s, e))
        return idx

    def keys(self) -> list[str]:
        return list(self._idx)

    def span(self, key: str, n: int = 0) -> tuple[int, int] | None:
        v = self._idx.get(key)
        return v[n] if v and n < len(v) else None

    def count(self, key: str) -> int:
        return len(self._idx.get(key, ()))

    def _value(self, s: int, e: int, always=frozenset()) -> Any:
        if self.data[s] == 123:
            return parse(self.data, s + 1, e, always)
        tok = self.data[s : e + 1]
        if tok[:1] == b'"':
            return decode(tok[1:-1])
        return scalar(decode(tok))

    def top(self, key: str, default: Any = None) -> Any:
        """Parsed value of a top-level key (first occurrence), cached."""
        k = ("top", key)
        if k in self._cache:
            return self._cache[k]
        sp = self.span(key)
        v = default if sp is None else self._value(*sp)
        self._cache[k] = v
        return v

    def top_all(self, key: str, always=frozenset()) -> list:
        k = ("topall", key)
        if k not in self._cache:
            self._cache[k] = [self._value(s, e, always) for s, e in self._idx.get(key, ())]
        return self._cache[k]

    # ---- basic facts ---------------------------------------------------------------------
    @property
    def date(self) -> Date | None:
        d = self.top("date")
        if d is None and self.meta:
            m = re.search(rb"^date=(\S+)", self.meta, re.M)
            d = Date.parse(m[1].decode()) if m else None
        return d if isinstance(d, Date) else None

    @property
    def player(self) -> str | None:
        p = self.top("player")
        if p is None and self.meta:
            m = re.search(rb'^player="?(\w+)', self.meta, re.M)
            p = m[1].decode() if m else None
        return p

    # ---- countries -----------------------------------------------------------------------
    def countries(self) -> dict[str, tuple[int, int]]:
        if self._country_idx is None:
            s, e = self._idx["countries"][0]
            starts = [(m.group(1).decode("latin-1"), m.end() - 1) for m in _COUNTRY_KEY.finditer(self.data, s, e)]
            out: dict[str, tuple[int, int]] = {}
            for i, (tag, bs) in enumerate(starts):
                nxt = starts[i + 1][1] if i + 1 < len(starts) else e
                out[tag] = (bs, self.data.rfind(b"}", bs, nxt))
            self._country_idx = out
        return self._country_idx

    def country_names(self) -> dict[str, str]:
        """tag -> ``name`` key of the country block (set for formed/colonial/released tags), one regex pass."""
        k = ("cnames",)
        if k not in self._cache:
            s, e = self._idx["countries"][0]
            out: dict[str, str] = {}
            cur = None
            for m in _COUNTRY_NAME.finditer(self.data, s, e):
                if m[1] is not None:
                    cur = m[1].decode("latin-1")
                elif cur is not None and cur not in out:
                    out[cur] = decode(m[2])
            self._cache[k] = out
        return self._cache[k]

    def country_tags(self) -> list[str]:
        return [t for t in self.countries() if t != "---"]

    def country(self, tag: str) -> dict | None:
        k = ("c", tag)
        if k not in self._cache:
            sp = self.countries().get(tag)
            self._cache[k] = None if sp is None else parse(self.data, sp[0] + 1, sp[1], _COUNTRY_LISTS)
        return self._cache[k]

    def country_raw(self, tag: str) -> bytes | None:
        sp = self.countries().get(tag)
        return None if sp is None else self.data[sp[0] : sp[1] + 1]

    # ---- provinces -----------------------------------------------------------------------
    def provinces_index(self) -> dict[int, tuple[int, int]]:
        if self._prov_idx is None:
            s, e = self._idx["provinces"][0]
            starts = [(int(m.group(1)), m.end() - 1) for m in _PROV_KEY.finditer(self.data, s, e)]
            out = {}
            for i, (pid, bs) in enumerate(starts):
                nxt = starts[i + 1][1] if i + 1 < len(starts) else e
                out[pid] = (bs, self.data.rfind(b"}", bs, nxt))
            self._prov_idx = out
        return self._prov_idx

    def province(self, pid: int) -> dict | None:
        k = ("p", pid)
        if k not in self._cache:
            sp = self.provinces_index().get(pid)
            self._cache[k] = None if sp is None else parse(self.data, sp[0] + 1, sp[1], frozenset({"unit", "modifier"}))
        return self._cache[k]

    def province_table(self) -> dict[int, dict[str, str]]:
        """id -> {name, owner, controller}, extracted with one regex pass (keys at exactly two tabs)."""
        k = ("ptable",)
        if k not in self._cache:
            s, e = self._idx["provinces"][0]
            tab: dict[int, dict[str, str]] = {}
            cur: dict[str, str] | None = None
            for m in _PROV_FIELDS.finditer(self.data, s, e):
                if m[1] is not None:
                    cur = tab[int(m[1])] = {}
                elif cur is not None:
                    cur[m[2].decode("ascii")] = _text(m[3])
            self._cache[k] = tab
        return self._cache[k]

    def owned_by(self, tag: str) -> list[int]:
        """Province ids owned by ``tag``: the country's ``owned_provinces`` list (falls back to a table scan)."""
        c = self.country(tag)
        if c and isinstance(c.get("owned_provinces"), list):
            return list(c["owned_provinces"])
        return sorted(p for p, v in self.province_table().items() if v.get("owner") == tag)

    # ---- diplomacy / wars ----------------------------------------------------------------
    def diplomacy(self) -> dict[str, list[dict]]:
        k = ("diplo",)
        if k not in self._cache:
            sp = self.span("diplomacy")
            raw = parse(self.data, sp[0] + 1, sp[1], _DIPLO_LISTS) if sp else {}
            self._cache[k] = {t: aslist(v) for t, v in raw.items()} if isinstance(raw, dict) else {}
        return self._cache[k]

    def wars(self) -> list[dict]:
        k = ("wars",)
        if k not in self._cache:
            self._cache[k] = self.top_all("active_war", _WAR_LISTS)
        return self._cache[k]

    # ---- people --------------------------------------------------------------------------
    def people(self, tag: str) -> dict[int, dict]:
        """id -> person for the country's rulers/heirs/consorts/leaders, from ``history`` (dated entries).

        Each person dict gets ``_role`` (monarch, heir, queen, leader).  Later entries win.
        """
        k = ("people", tag)
        if k in self._cache:
            return self._cache[k]
        out: dict[int, dict] = {}
        c = self.country(tag) or {}
        hist = c.get("history")
        if isinstance(hist, dict):
            for dk, ev in hist.items():
                if not isinstance(dk, Date):
                    continue
                for e in aslist(ev):
                    if not isinstance(e, dict):
                        continue
                    for role in ("monarch", "heir", "queen", "leader"):
                        for p in aslist(e.get(role)):
                            if isinstance(p, dict) and isinstance(p.get("id"), dict):
                                pid = p["id"].get("id")
                                if pid is not None:
                                    out[pid] = dict(p, _role=role, _date=dk)
        self._cache[k] = out
        return out

    def ref(self, tag: str, key: str) -> dict | None:
        """Person referenced by the country's ``monarch``/``heir``/``queen`` key ({id,type} -> person dict)."""
        c = self.country(tag) or {}
        r = c.get(key)
        if isinstance(r, dict) and "id" in r:
            return self.people(tag).get(r["id"])
        return None

    def advisors(self) -> dict[int, dict]:
        """advisor id -> advisor dict, from ``advisor={...}`` entries inside province histories (3 tabs deep)."""
        k = ("advisors",)
        if k not in self._cache:
            s, e = self._idx["provinces"][0]
            out: dict[int, dict] = {}
            pat = re.compile(rb"\n\t\t\tadvisor=\{")
            for m in pat.finditer(self.data, s, e):
                try:
                    a = parse_block(self.data, m.end() - 1)
                except ParseError:
                    continue
                if isinstance(a, dict) and isinstance(a.get("id"), dict):
                    out[a["id"].get("id")] = a
            self._cache[k] = out
        return self._cache[k]

    def hired_advisors(self, tag: str) -> list[dict]:
        """Advisors listed for ``tag`` in the top-level ``active_advisors`` block, resolved via ``advisors()``."""
        aa = self.top("active_advisors") or {}
        lst = aslist(aa.get(tag, {}).get("advisor")) if isinstance(aa.get(tag), dict) else []
        adv = self.advisors()
        return [adv[a["id"]] for a in lst if isinstance(a, dict) and a.get("id") in adv]


# ---- save discovery and cache -----------------------------------------------------------------
def save_dir(base: Path | None = None) -> Path:
    base = Path(base) if base else paths.user_dir()
    sg = base / "save games"
    return sg if sg.is_dir() else base


def latest_save(base: Path | str | None = None) -> Path | None:
    """Newest ``*.eu4`` (including ``autosave.eu4``) in ``<base>/save games`` (or in ``base`` itself)."""
    d = save_dir(Path(base) if base else None)
    best: tuple[int, Path] | None = None
    try:
        for p in d.iterdir():
            if p.suffix == ".eu4" and p.is_file():
                m = p.stat().st_mtime_ns
                if best is None or m > best[0]:
                    best = (m, p)
    except OSError:
        return None
    return best[1] if best else None


_OPEN: dict[str, Save] = {}
_MAX_OPEN = 3


def open_save(path: Path | str) -> Save:
    """Cached ``Save`` keyed by path + mtime + size; keeps the most recent few in memory."""
    p = Path(path)
    st = p.stat()
    key = str(p.resolve())
    s = _OPEN.get(key)
    if s is not None and s.mtime_ns == st.st_mtime_ns and s.size == st.st_size:
        _OPEN[key] = _OPEN.pop(key)  # refresh LRU position
        return s
    s = Save(p)
    _OPEN.pop(key, None)
    _OPEN[key] = s
    while len(_OPEN) > _MAX_OPEN:
        _OPEN.pop(next(iter(_OPEN)))
    return s
