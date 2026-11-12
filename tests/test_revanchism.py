from eu4mcp import revanchism as R


def sv(date, occ, owned=20, lb=""):
    return {"date": date, "owned": owned, "occupied": occ, "last_bankrupt": lb}


def test_peace_jump_grants_no_cooldown(tmp_path, monkeypatch):
    monkeypatch.setenv("EU4_MCP_STATE", str(tmp_path))
    monkeypatch.setenv("EU4_MCP_NOTES", str(tmp_path / "notes"))
    st = {}
    assert R.decide(st, (1450, 1, 1), 0, None) is None            # first read only records
    assert R.decide(st, (1450, 2, 1), 0, None) is None
    assert R.decide(st, (1450, 3, 1), 40000, None) == "provinces lost in a peace deal"
    R.grant(st, (1450, 3, 1), "x", "CAS")
    assert st["until"] == "1455.3.1" and "cooldown_until" not in st
    assert R.active(st, "1455.2.28") == "1455.3.1" and R.active(st, "1455.3.1") is None
    st["last_value"] = 50000
    assert R.decide(st, (1455, 2, 1), 90000, None) is None        # still running
    st["last_value"] = 50000
    assert R.decide(st, (1455, 3, 1), 90000, None)                # no cooldown after the end
    assert R.decide(st, (1460, 4, 1), 89000, None) is None        # decay is not a trigger


def test_occupation_needs_six_months_in_a_row():
    st = {}
    assert R.decide(st, (1450, 1, 1), None, sv("1450.1.1", 4)) is None
    assert R.decide(st, (1450, 5, 1), None, sv("1450.5.1", 5)) is None
    assert R.decide(st, (1450, 6, 1), None, sv("1450.6.1", 3)) is None and "occ_since" not in st   # below a fifth: reset
    assert R.decide(st, (1450, 7, 1), None, sv("1450.7.1", 4)) is None
    assert R.decide(st, (1450, 12, 31), None, sv("1450.12.31", 4)) is None
    assert "enemy-held" in R.decide(st, (1451, 1, 1), None, sv("1451.1.1", 4))


def test_bankruptcy_only_when_new():
    st = {}
    assert R.decide(st, (1450, 1, 1), None, sv("1450.1.1", 0, lb="1446.1.1")) is None   # old one seen at start
    assert R.decide(st, (1450, 2, 1), None, sv("1450.2.1", 0, lb="1446.1.1")) is None
    assert R.decide(st, (1450, 3, 1), None, sv("1450.3.1", 0, lb="1450.2.15")) == "bankruptcy"


def test_label_and_status_line(tmp_path, monkeypatch):
    monkeypatch.setenv("EU4_MCP_STATE", str(tmp_path))
    assert R.status_line("1450.1.1") == ""
    R._store({"until": "1466.11.1"})
    assert R.label("1466.11.1") == "Revanchism 100% until Nov 1466"
    assert "Nov 1466" in R.status_line("1463.5.1") and R.status_line("1466.11.1") == ""
