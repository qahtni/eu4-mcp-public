import os
import time
import zipfile

import pytest

from eu4mcp.clausewitz import Date
from eu4mcp.save import Save, SaveError, latest_save, open_save
from .conftest import needs_tutorial, tutorial_saves

pytestmark = needs_tutorial


def test_index_top_level(big):
    assert big.date == Date(1492, 2, 6) and big.player == "CAS"
    for k in ("date", "provinces", "countries", "diplomacy", "active_war", "trade", "player"):
        assert k in big.keys()
    assert big.count("active_war") == 3
    assert big.count("rebel_faction") > 100


def test_fast_index_matches_real_brace_matching(big):
    assert Save._index_top_slow(big.data) == big._idx


def test_all_tutorial_saves_open_and_index(all_saves):
    assert len(all_saves) >= 8
    for s in all_saves:
        assert s.date and s.player
        assert len(s.countries()) > 1000 and len(s.provinces_index()) > 4000
        assert s.player in s.countries()


def test_country_and_province_blocks(big):
    c = big.country("CAS")
    assert c["capital"] == 215 and c["technology"]["adm_tech"] == 6
    p = big.province(215)
    assert p["name"] == "Valladolid" and p["owner"] == "CAS"
    assert big.province_table()[215] == {"name": "Valladolid", "owner": "CAS", "controller": "CAS"}
    assert big.country("QQQQ") is None
    assert 215 in big.owned_by("CAS")


def test_country_index_matches_slow_scan(big):
    from eu4mcp.clausewitz import block_index

    s, e = big._idx["countries"][0]
    assert {k: (a, b) for k, a, b in block_index(big.data, s + 1, e)} == big.countries()


def test_diplomacy_wars_people(big):
    dip = big.diplomacy()
    assert {"alliance", "casus_belli", "dependency"} <= set(dip)
    assert all(isinstance(v, list) for v in dip.values())
    names = [w["name"] for w in big.wars()]
    assert "Breton War of Independence" in names
    mon = big.ref("CAS", "monarch")
    assert mon["name"] == "Isabel I" and (mon["ADM"], mon["DIP"], mon["MIL"]) == (5, 6, 3)


def test_unicode_names_survive(big):
    assert big.country_names() is not None


def test_zip_save(tmp_path, big_path):
    src = big_path.read_bytes()
    z = tmp_path / "zipped.eu4"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("meta", b"EU4txt\ndate=1492.2.6\nplayer=\"CAS\"\n")
        zf.writestr("gamestate", src)
        zf.writestr("ai", b"EU4txt\n")
    s = Save(z)
    assert s.zipped and s.date == Date(1492, 2, 6) and s.player == "CAS"
    assert s.country("CAS")["capital"] == 215


def test_bad_files(tmp_path):
    p = tmp_path / "x.eu4"
    p.write_bytes(b"\x00\x01binary ironman")
    with pytest.raises(SaveError):
        Save(p)
    p.write_bytes(b"EU4txt\ndate=1444.1.1\n")
    with pytest.raises(SaveError):
        Save(p)


def test_latest_save_and_cache(tmp_path, big_path):
    d = tmp_path / "save games"
    d.mkdir()
    a, b = d / "a.eu4", d / "autosave.eu4"
    a.write_bytes(big_path.read_bytes()[:10])
    b.write_bytes(b"x")
    os.utime(a, (1000, 1000))
    os.utime(b, (2000, 2000))
    (d / "newer.txt").write_text("x")
    assert latest_save(tmp_path) == b  # parent dir with 'save games'
    assert latest_save(d) == b
    os.utime(a, (3000, 3000))
    assert latest_save(d) == a
    assert latest_save(tmp_path / "nothing") is None
    s1 = open_save(big_path)
    assert open_save(big_path) is s1


def test_timings(big_path, capsys):
    from eu4mcp import digest

    t = time.time()
    s = Save(big_path)
    out = digest.status(s)
    first = time.time() - t
    t = time.time()
    s2 = open_save(big_path)
    digest.status(s2)
    digest.status(s2)
    again = (time.time() - t) / 2
    with capsys.disabled():
        print(f"\n[timing] open+status {first:.2f}s on {big_path.name} ({big_path.stat().st_size >> 20} MB); cached-save status {again:.2f}s")
    assert first < 3.0
    assert again < 1.0
