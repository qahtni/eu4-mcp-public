"""Parser and fast scanners for Paradox (Clausewitz) text files.

Value model
-----------
* ``key=value`` blocks become ``dict`` (insertion ordered).
* A key that occurs more than once yields a ``Multi`` (a ``list`` subclass) so
  duplicates are distinguishable from a single value that happens to be a
  list.  Use ``aslist(v)`` to treat "absent / single / duplicated / list" uniformly.
  Keys listed in ``always_list`` always produce a ``Multi``.
* A block that only holds bare values is a plain ``list``.  A block that mixes
  ``key=value`` and bare values is a ``dict`` with the bare values collected
  under the key ``"_"``.
* Numbers become ``int``/``float``, ``yes``/``no`` become ``bool``, dates
  (``1444.11.11``) become ``Date`` tuples, quoted strings and other words
  become ``str``.  Integer-looking keys are ``int`` (province ids are negative
  in saves: ``-1={`` is province 1), date-looking keys are ``Date``.
* Comparison operators (``>=`` etc.) are accepted and treated as ``=``.
* ``key{...}`` (no ``=``) is accepted as ``key={...}``.

Lazy use
--------
``find_matching_brace(data, i)`` returns the index of the ``}`` closing the
``{`` at ``data[i]`` without parsing (respects quotes and ``#`` comments).
``parse_block(data, i)`` parses only that block; ``parse(data, start, end)``
parses a slice as a document.  ``block_index`` lists the direct children of a
block with their byte ranges.
"""
from __future__ import annotations

import re
from typing import Any, Iterator, NamedTuple


class Date(NamedTuple):
    y: int
    m: int
    d: int

    def __str__(self) -> str:  # 1444.11.11
        return f"{self.y}.{self.m}.{self.d}"

    @classmethod
    def parse(cls, s: str) -> "Date | None":
        m = _DATE_RE.match(s)
        if not m:
            return None
        return cls(int(m[1]), int(m[2]), int(m[3]))

    def ordinal(self) -> int:
        """Approximate day count (30.4-day months are irrelevant: used for ordering/deltas only)."""
        return self.y * 372 + (self.m - 1) * 31 + (self.d - 1)

    def months_until(self, other: "Date") -> int:
        return (other.y - self.y) * 12 + (other.m - self.m)


class Multi(list):
    """A key that occurred more than once (or was forced to be a list)."""


class ParseError(ValueError):
    pass


_DATE_RE = re.compile(r"^(-?\d{1,4})\.(\d{1,2})\.(\d{1,2})(?:\.\d+)?$")
_INT_RE = re.compile(r"^[+-]?\d+$")
_NUM_RE = re.compile(r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$")

_TOKEN = re.compile(
    rb'[ \t\r\n\xef\xbb\xbf]+|#[^\n]*|"([^"]*)"|([{}])|(<=|>=|\?=|!=|==|=|<|>)|([^\s{}="#<>?]+)|(.)'
)
_BRACE_SCAN = re.compile(rb'[{}"#]')


def decode(b: bytes) -> str:
    """UTF-8 when valid, else Windows-1252 (saves/common are one or the other)."""
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("cp1252", "replace")


def scalar(word: str) -> Any:
    """Convert an unquoted token to int/float/bool/Date/str."""
    if word == "yes":
        return True
    if word == "no":
        return False
    c = word[0]
    if c.isdigit() or c in "+-.":
        if _INT_RE.match(word):
            return int(word)
        if _NUM_RE.match(word):
            return float(word)
        d = Date.parse(word)
        if d is not None:
            return d
    return word


def aslist(v: Any) -> list:
    """Absent -> [], any list (Multi or a ``{a b c}`` list) -> itself, anything else -> [v]."""
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def find_matching_brace(data: bytes, i: int) -> int:
    """Index of the ``}`` matching the ``{`` at ``data[i]``; raises ParseError if unbalanced."""
    if data[i : i + 1] != b"{":
        raise ParseError(f"no '{{' at offset {i}")
    depth = 0
    pos = i
    search = _BRACE_SCAN.search
    n = len(data)
    while True:
        m = search(data, pos)
        if m is None:
            raise ParseError(f"unbalanced braces from offset {i}")
        p = m.start()
        c = data[p]
        if c == 123:  # {
            depth += 1
            pos = p + 1
        elif c == 125:  # }
            depth -= 1
            if depth == 0:
                return p
            pos = p + 1
        elif c == 34:  # "
            q = data.find(b'"', p + 1)
            if q < 0:
                raise ParseError(f"unterminated string at offset {p}")
            pos = q + 1
        else:  # comment
            q = data.find(b"\n", p)
            if q < 0:
                raise ParseError(f"unbalanced braces from offset {i}")
            pos = q + 1
        if pos >= n:
            raise ParseError(f"unbalanced braces from offset {i}")


def _key(word: str) -> Any:
    c = word[0]
    if c.isdigit() or c == "-":
        if _INT_RE.match(word):
            return int(word)
        d = Date.parse(word)
        if d is not None:
            return d
    return word


class _Frame:
    __slots__ = ("d", "items")

    def __init__(self) -> None:
        self.d: dict | None = None  # keyed children
        self.items: list | None = None  # bare values


_BARE = object()


def _store(fr: _Frame, key: Any, val: Any, always) -> None:
    d = fr.d
    if d is None:
        d = fr.d = {}
    if key in d:
        cur = d[key]
        if isinstance(cur, Multi):
            cur.append(val)
        else:
            d[key] = Multi([cur, val])
    elif key in always:
        d[key] = Multi([val])
    else:
        d[key] = val


def _bare(fr: _Frame, val: Any) -> None:
    if fr.items is None:
        fr.items = []
    fr.items.append(val)


def _finish(fr: _Frame) -> Any:
    if fr.d is None:
        return fr.items if fr.items is not None else {}
    if fr.items:
        fr.d["_"] = fr.items
    return fr.d


def parse(data: bytes | str, start: int = 0, end: int | None = None, always_list=frozenset()) -> Any:
    """Parse ``data[start:end]`` as a document of ``key=value`` pairs.

    Returns a dict (a list if the slice holds only bare values, ``{}`` if empty).
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    if end is None:
        end = len(data)
    stack: list[tuple[_Frame, Any]] = []
    cur = _Frame()
    pend: tuple[str, bool] | None = None  # (text, quoted)
    op = False
    nxt = _TOKEN.scanner(data, start, end).match
    while True:
        m = nxt()
        if m is None:
            break
        lg = m.lastindex
        if lg is None:
            continue
        if lg == 5:
            raise ParseError(f"unexpected character {m[5]!r} at offset {m.start()}")
        if lg == 1 or lg == 4:
            tok = (decode(m[1]), True) if lg == 1 else (decode(m[4]) if not m[4].isascii() else m[4].decode("ascii"), False)
            if pend is not None and op:
                k = pend[0] if pend[1] else _key(pend[0])
                _store(cur, k, tok[0] if tok[1] else scalar(tok[0]), always_list)
                pend = None
                op = False
            else:
                if pend is not None:
                    _bare(cur, pend[0] if pend[1] else scalar(pend[0]))
                pend = tok
                op = False
        elif lg == 3:
            if pend is None:
                raise ParseError(f"operator without key at offset {m.start()}")
            op = True
        elif m[2] == b"{":
            if pend is not None:
                k = pend[0] if pend[1] else _key(pend[0])
            else:
                k = _BARE
            stack.append((cur, k))
            cur = _Frame()
            pend = None
            op = False
        else:
            if pend is not None and not op:
                _bare(cur, pend[0] if pend[1] else scalar(pend[0]))
            pend = None
            op = False
            if not stack:
                raise ParseError(f"unexpected '}}' at offset {m.start()}")
            val = _finish(cur)
            cur, k = stack.pop()
            if k is _BARE:
                _bare(cur, val)
            else:
                _store(cur, k, val, always_list)
    if stack:
        raise ParseError("unbalanced braces: missing '}'")
    if pend is not None and not op:
        _bare(cur, pend[0] if pend[1] else scalar(pend[0]))
    return _finish(cur)


def parse_block(data: bytes, open_pos: int, always_list=frozenset()) -> Any:
    """Parse the ``{...}`` starting at ``data[open_pos]`` (only that block is touched)."""
    close = find_matching_brace(data, open_pos)
    return parse(data, open_pos + 1, close, always_list)


def parse_value_at(data: bytes, pos: int, always_list=frozenset()) -> Any:
    """Parse the value of a ``key=value`` whose ``=`` (or key) is before ``pos``; handles block or scalar."""
    n = len(data)
    while pos < n and data[pos] in b" \t\r\n=":
        pos += 1
    if data[pos : pos + 1] == b"{":
        return parse_block(data, pos, always_list)
    m = re.compile(rb'"([^"]*)"|[^\s{}]+').match(data, pos)
    if not m:
        raise ParseError(f"no value at offset {pos}")
    return decode(m[1]) if m[1] is not None else scalar(decode(m[0]))


_KEY_AT = re.compile(rb'[ \t\r\n]*(?:"([^"]*)"|([^\s{}="#<>]+))[ \t\r\n]*(?:(?:<=|>=|\?=|!=|==|=|<|>)[ \t\r\n]*)?')


def block_index(data: bytes, start: int, end: int, fast: bool = True) -> Iterator[tuple[Any, int, int]]:
    """Yield ``(key, value_start, value_end)`` for the direct children of the document ``data[start:end]``.

    For block values ``value_start`` is the ``{`` and ``value_end`` the matching ``}`` (inclusive of
    neither: ``data[value_start:value_end+1]`` is the whole block).  For scalars, the token range.
    Nested blocks are skipped via brace matching, never parsed.
    """
    pos = start
    match = _KEY_AT.match
    while pos < end:
        # skip whitespace/comments
        m = re.compile(rb"(?:[ \t\r\n]+|#[^\n]*)*").match(data, pos)
        pos = m.end()
        if pos >= end:
            return
        c = data[pos]
        if c == 123:  # bare block
            e = find_matching_brace(data, pos)
            yield (None, pos, e)
            pos = e + 1
            continue
        if c == 125:
            return
        m = match(data, pos)
        if not m or m.end() == pos:
            raise ParseError(f"cannot scan key at offset {pos}")
        raw = m[1] if m[1] is not None else m[2]
        key = decode(raw) if m[1] is not None else _key(decode(raw))
        pos = m.end()
        if pos < end and data[pos] == 123:
            e = find_matching_brace(data, pos)
            yield (key, pos, e)
            pos = e + 1
        else:
            vm = re.compile(rb'"[^"]*"|[^\s{}]+').match(data, pos)
            if not vm:
                raise ParseError(f"no value at offset {pos}")
            yield (key, vm.start(), vm.end() - 1)
            pos = vm.end()
