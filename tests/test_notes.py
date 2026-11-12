import os

import pytest

from eu4mcp import notes as notes_mod
from eu4mcp.notes import Notes, NotesError


def test_roundtrip_and_listing(tmp_path):
    n = Notes("c1", tmp_path)
    assert n.list() == [] and n.read("plan") == ""
    n.write("plan", "Take Granada\n- step 1")
    n.write("commitments", "alliance with POR")
    n.write("standing_rules", "never go below 100 ducats")
    n.write("intel/fra", "FRA: strong\nsecond line")
    n.append("journal", "war declared", date="1492.2.6")
    n.append("journal", "peace\nmore", date="1493.1.1")
    assert n.read("plan").startswith("Take Granada")
    assert dict(n.list())["intel/FRA"] > 0
    assert [s for s, _ in n.list()][:4] == ["plan", "commitments", "standing_rules", "journal"]
    j = n.read("journal")
    assert "- 1492.2.6: war declared" in j and "- 1493.1.1: peace\n  more" in j
    assert "last 1 of" in n.read("journal", tail=1)
    # plain markdown files on disk
    assert (tmp_path / "c1" / "intel" / "FRA.md").read_text().startswith("FRA: strong")
    n.append("plan", "- step 2")
    assert n.read("plan").endswith("- step 2\n")


def test_journal_rules_and_validation(tmp_path):
    n = Notes("c1", tmp_path)
    with pytest.raises(NotesError):
        n.write("journal", "x")
    with pytest.raises(NotesError):
        n.append("journal", "no date")
    with pytest.raises(NotesError):
        n.write("../etc/passwd", "x")
    with pytest.raises(NotesError):
        n.write("intel/../../x", "x")
    with pytest.raises(NotesError):
        Notes("../bad", tmp_path)
    with pytest.raises(NotesError):
        n.delete("plan")


def test_delete_intel(tmp_path):
    n = Notes("c", tmp_path)
    n.write("intel/CAS", "x")
    assert n.delete("intel/CAS") is True and n.delete("intel/CAS") is False
    assert n.list() == []


def test_atomic_write_failure_keeps_old_content(tmp_path, monkeypatch):
    n = Notes("c", tmp_path)
    n.write("plan", "old")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(notes_mod.os, "replace", boom)
    with pytest.raises(OSError):
        n.write("plan", "new")
    monkeypatch.undo()
    assert n.read("plan") == "old\n"
    assert [p.name for p in (tmp_path / "c").iterdir()] == ["plan.md"]  # no stray temp files


def test_brief_is_small_and_complete(tmp_path):
    n = Notes("c", tmp_path)
    n.write("plan", "P" * 9000)
    n.write("commitments", "C" * 9000)
    n.write("standing_rules", "R" * 9000)
    for i in range(30):
        n.append("journal", f"event {i}", date=f"1450.1.{i + 1}")
    for t in ("AAA", "BBB"):
        n.write(f"intel/{t}", f"{t} first line\nrest")
    b = n.brief()
    assert len(b) < 7500
    assert "event 29" in b and "event 19" not in b and "event 20" in b
    assert "AAA" in b and "BBB (" in b and "(truncated" in b


def test_empty_brief(tmp_path):
    b = Notes("c", tmp_path).brief()
    assert "(empty)" in b and "(none)" in b


def test_env_default(tmp_path, monkeypatch):
    monkeypatch.setenv("EU4_MCP_NOTES", str(tmp_path))
    Notes().write("plan", "x")
    assert (tmp_path / "default" / "plan.md").exists()
