"""Persistent strategist notes: plain markdown files a human can read.

Layout under ``<notes root>/<campaign>/``::

    plan.md  commitments.md  standing_rules.md  journal.md  intel/<TAG>.md

* ``plan``           the standing plan (replace)
* ``commitments``    promises/treaties/obligations (replace)
* ``standing_rules`` rules the routine-upkeep subagent follows (replace)
* ``journal``        append-only, each entry ``- <in-game date>: text`` (the caller passes the date)
* ``intel/<TAG>``    one file per country

Writes are atomic: data goes to a temp file in the same directory, is fsynced, then ``os.replace``d.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from . import paths

FIXED = ("plan", "commitments", "standing_rules", "journal", "review")
_INTEL = re.compile(r"^intel/([A-Za-z0-9_\-]{1,16})$")
_CAMPAIGN = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")
MAX_SECTION_BYTES = 200_000


class NotesError(ValueError):
    pass


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class Notes:
    def __init__(self, campaign: str | None = None, root: Path | str | None = None):
        campaign = campaign or os.environ.get("EU4_MCP_CAMPAIGN") or "default"
        if not _CAMPAIGN.match(campaign):
            raise NotesError(f"bad campaign name {campaign!r}")
        self.campaign = campaign
        self.dir = Path(root) / campaign if root else paths.notes_dir() / campaign

    # ---- names -----------------------------------------------------------------------
    def _path(self, section: str) -> Path:
        section = section.strip().strip("/")
        if section in FIXED:
            return self.dir / f"{section}.md"
        m = _INTEL.match(section)
        if m:
            return self.dir / "intel" / f"{m[1].upper()}.md"
        raise NotesError(f"unknown section '{section}': use {', '.join(FIXED)} or intel/<TAG>")

    # ---- API -------------------------------------------------------------------------
    def list(self) -> list[tuple[str, int]]:
        """(section, size in bytes) for every existing section, fixed ones first."""
        out = []
        for name in FIXED:
            p = self.dir / f"{name}.md"
            if p.exists():
                out.append((name, p.stat().st_size))
        idir = self.dir / "intel"
        if idir.is_dir():
            for p in sorted(idir.glob("*.md")):
                out.append((f"intel/{p.stem}", p.stat().st_size))
        return out

    def read(self, section: str, tail: int | None = None) -> str:
        """Section text; ``tail`` keeps only the last N lines (use for the journal)."""
        p = self._path(section)
        if not p.exists():
            return ""
        text = p.read_text(encoding="utf-8")
        if tail:
            lines = text.splitlines()
            if len(lines) > tail:
                return f"(showing last {tail} of {len(lines)} lines)\n" + "\n".join(lines[-tail:])
        return text

    def write(self, section: str, text: str) -> int:
        """Replace a section. The journal is append-only and cannot be replaced."""
        if section.strip().strip("/") == "journal":
            raise NotesError("the journal is append-only: use append with the in-game date")
        if len(text.encode("utf-8")) > MAX_SECTION_BYTES:
            raise NotesError(f"section too large (> {MAX_SECTION_BYTES} bytes)")
        if not text.endswith("\n") and text:
            text += "\n"
        atomic_write(self._path(section), text)
        return len(text)

    def append(self, section: str, text: str, date: str | None = None) -> int:
        """Append to a section. For the journal ``date`` (in-game date) is required and prefixed."""
        sec = section.strip().strip("/")
        p = self._path(sec)
        text = text.strip("\n")
        if sec == "journal":
            if not date:
                raise NotesError("journal entries need the in-game date (e.g. 1492.2.6)")
            first, *rest = text.splitlines() or [""]
            text = f"- {date}: {first}" + "".join(f"\n  {r}" for r in rest)
        old = p.read_text(encoding="utf-8") if p.exists() else ""
        if old and not old.endswith("\n"):
            old += "\n"
        new = old + text + "\n"
        if len(new.encode("utf-8")) > MAX_SECTION_BYTES:
            raise NotesError(f"section would exceed {MAX_SECTION_BYTES} bytes; rewrite it shorter")
        atomic_write(p, new)
        return len(new)

    def delete(self, section: str) -> bool:
        """Delete an ``intel/<TAG>`` entry (other sections are cleared with write)."""
        if not _INTEL.match(section.strip().strip("/")):
            raise NotesError("only intel/<TAG> entries can be deleted")
        p = self._path(section)
        if p.exists():
            p.unlink()
            return True
        return False

    def brief(self, journal_lines: int = 10, budget: int = 6000) -> str:
        """plan + commitments + standing_rules + last journal lines + intel index, within ``budget`` characters."""
        shares = {"plan": 0.34, "commitments": 0.2, "standing_rules": 0.2}
        parts = []
        used_journal = ""
        for name, share in shares.items():
            txt = self.read(name).strip()
            cap = int(budget * share)
            if len(txt) > cap:
                txt = txt[:cap].rstrip() + f"\n(truncated; eu4_notes_read {name})"
            parts.append(f"## {name}\n{txt if txt else '(empty)'}")
        rev = self.read("review").strip()
        if rev:
            parts.append("## review (latest advice from the reviewer)\n" + (rev if len(rev) <= 1200 else "(earlier part: eu4_notes_read review)\n" + rev[-1200:]))
        j = self.read("journal").splitlines()
        # entries can be multi-line; keep lines starting a new entry plus continuations of the kept ones
        tail = j[-journal_lines:]
        used_journal = "\n".join(l[:300] for l in tail)
        parts.append(f"## journal (last {len(tail)} of {len(j)} lines)\n{used_journal if tail else '(empty)'}")
        idx = []
        idir = self.dir / "intel"
        if idir.is_dir():
            for p in sorted(idir.glob("*.md")):
                first = next((l.strip() for l in p.read_text(encoding="utf-8").splitlines() if l.strip()), "")
                idx.append(f"{p.stem} ({p.stat().st_size}B): {first[:80]}")
        shown = idx[:25]
        parts.append("## intel index\n" + ("\n".join(shown) + (f"\n(+{len(idx) - 25} more, eu4_notes_read intel/<TAG>)" if len(idx) > 25 else "") if idx else "(none)"))
        text = "\n\n".join(parts)
        return text if len(text) <= budget + 1500 else text[: budget + 1500] + "\n(brief truncated)"
