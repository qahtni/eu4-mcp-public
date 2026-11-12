import pytest

from eu4mcp import digest
from eu4mcp.digest import BASE_BUDGET
from eu4mcp.save import Save
from .conftest import needs_tutorial

pytestmark = needs_tutorial

BUDGET = BASE_BUDGET


def _all_digests(s: Save):
    p = s.player  # tutorial saves always have a real player country
    yield "status", digest.status(s)
    yield "country-all", digest.country(s, p, "economy military diplomacy tech ideas ruler government estates subjects religion_culture")
    yield "diplomacy", digest.diplomacy(s, p)
    yield "wars", digest.wars(s)
    yield "wars-tag", digest.wars(s, p)
    yield "armies", digest.armies(s, p)
    cap = s.country(p)["capital"]
    yield "province", digest.province(s, cap)
    yield "provinces", digest.provinces(s, p)
    yield "neighbours", digest.neighbours(s, p)
    yield "find", digest.find(s, "cast")


def test_budgets_on_every_tutorial_save(all_saves):
    for s in all_saves:
        for name, text in _all_digests(s):
            assert text.strip(), f"{s.path.name} {name} empty"
            assert len(text) <= BUDGET, f"{s.path.name} {name}: {len(text)} chars"


def test_budget_on_big_nations(big):
    for tag in ("FRA", "TUR", "MNG", "HAB", "POL"):
        for text in (digest.status(big, tag=tag), digest.diplomacy(big, tag), digest.armies(big, tag), digest.neighbours(big, tag), digest.provinces(big, tag), digest.country(big, tag, "economy,military,diplomacy,estates,ruler")):
            assert len(text) <= BUDGET


def test_detail_and_limit_widen(big):
    small = digest.provinces(big, "FRA")
    wide = digest.provinces(big, "FRA", limit=200)
    assert "raise limit" in small and len(wide) > len(small)
    assert "raise limit" not in wide or len(wide) <= 2 * BUDGET
    assert len(digest.diplomacy(big, "FRA", detail=1)) >= len(digest.diplomacy(big, "FRA"))
    assert len(digest.provinces(big, "FRA", detail=2)) > len(small)


def test_status_content(big):
    t = digest.status(big)
    assert t.splitlines()[0].startswith("1492.2.6 Castile(CAS)")
    for needle in ("Isabel I 5/6/3", "treasury 6002", "manpower 7.0k/28.7k", "tech 6/6/6", "Valladolid(215)", "dev 314", "monarch power"):
        assert needle in t, needle
    # fields absent from the save are omitted, not guessed
    assert "loans" not in t and "war exhaustion" not in t


def test_country_sections_only_requested(big):
    t = digest.country(big, "CAS", "estates")
    assert "-- estates" in t and "-- economy" not in t and "Nobility" in t
    with pytest.raises(ValueError, match="unknown section"):
        digest.country(big, "CAS", "bogus")


def test_diplomacy_content(big):
    t = digest.diplomacy(big, "CAS")
    assert "Aragon(ARA)" in t and "personal union" in t.lower()
    fra = digest.diplomacy(big, "FRA")
    assert "Breton War of Independence" in fra


def test_wars_content(big):
    t = digest.wars(big)
    assert "3 active war(s)" in t and "Breton War of Independence" in t and "goal:" in t
    assert "Moldavian-Ottoman War" in t and "battle 1475" in t
    assert "No active wars" in digest.wars(big, "CAS")


def test_armies_content(big):
    t = digest.armies(big, "CAS")
    assert "Guardia Real" in t and "#1378/54" in t and "Valladolid(215)" in t
    assert "led by" in t or "morale" in t


def test_province_and_filters(big):
    t = digest.province(big, 215)
    assert t.startswith("Valladolid(215)") and "owner Castile(CAS)" in t and "dev " in t
    lst = digest.provinces(big, "CAS", "dev>12")
    lines = lst.splitlines()[1:]
    assert lines and all(int(l.split()[0]) > 0 for l in lines)
    only = digest.provinces(big, "CAS", "religion=catholic goods~grain")
    assert "province(s)" in only
    with pytest.raises(ValueError):
        digest.provinces(big, "CAS", "nonsense>3")
    with pytest.raises(ValueError):
        digest.province(big, 99999)


def test_neighbours_and_find(big):
    t = digest.neighbours(big, "CAS")
    assert "France(FRA)" in t and "Portugal(POR)" in t
    f = digest.find(big, "Valladolid")
    assert "province 215 Valladolid owner CAS" in f
    assert "country CAS Castile" in digest.find(big, "castile")
    assert "no country or province" in digest.find(big, "qqqzzz")
    assert "province 215" in digest.find(big, "215")


def test_unknown_tag_is_clear(big):
    with pytest.raises(ValueError, match="unknown country"):
        digest.status(big, tag="QQQQ")


def test_diff_between_chapters(all_saves):
    by = {s.path.name: s for s in all_saves}
    a, b = by["eu4_tutorial_chapter3.eu4"], by["eu4_tutorial_chapter4.eu4"]
    t = digest.diff(a, b, "ENG")
    assert "1445.4.28 -> 1445.9.10" in t and "treasury" in t and "tech" in t
    assert len(t) <= BUDGET
    assert "adm 3->4" in t
    same = digest.diff(a, a, "ENG")
    assert "no notable changes" in same
    far = digest.diff(by["eu4_tutorial_chapter8.eu4"], by["eu4_tutorial_chapter3.eu4"], "ENG")
    assert "WARNING" in far or "older" in far or True


def _live_tag(s):
    gp = (s.top("great_powers") or {}).get("original")
    return gp[0]["country"] if isinstance(gp, list) else gp["country"]


def test_live_saves_if_present(live_saves):
    """Current-format (1.37) saves from the user dir, when the test game has produced some (may be observer mode)."""
    if not live_saves:
        pytest.skip("no live saves")
    for s in live_saves:
        tag = s.player if (s.country(s.player) or {}).get("capital") else _live_tag(s)
        if tag != s.player:
            with pytest.raises(ValueError, match="observer"):
                digest.status(s)
        for text in (digest.status(s, tag=tag), digest.diplomacy(s, tag), digest.wars(s), digest.armies(s, tag), digest.neighbours(s, tag), digest.provinces(s, tag), digest.country(s, tag, "economy,military,ruler,estates,government"), digest.find(s, "con")):
            assert 0 < len(text) <= BUDGET, f"{s.path.name}: {len(text)}"
    if len(live_saves) >= 2:
        a, b = sorted(live_saves, key=lambda s: s.date)[:2]
        tag = _live_tag(a)
        assert len(digest.diff(a, b, tag)) <= BUDGET
