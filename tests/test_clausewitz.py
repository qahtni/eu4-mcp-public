import pytest

from eu4mcp.clausewitz import Date, Multi, ParseError, aslist, block_index, find_matching_brace, parse, parse_block, parse_value_at


def test_scalars_and_types():
    d = parse(b'a=1 b=-2 c=3.5 d="x y" e=yes f=no g=1444.11.11 h=word i=0.000')
    assert d == {"a": 1, "b": -2, "c": 3.5, "d": "x y", "e": True, "f": False, "g": Date(1444, 11, 11), "h": "word", "i": 0.0}
    assert str(d["g"]) == "1444.11.11"


def test_quoted_value_stays_string():
    assert parse(b'a="123" b="yes"') == {"a": "123", "b": "yes"}


def test_duplicate_keys_become_multi():
    d = parse(b"x=1 x=2 y={z=1} y={z=2}")
    assert isinstance(d["x"], Multi) and d["x"] == [1, 2]
    assert [b["z"] for b in d["y"]] == [1, 2]
    assert aslist(d["x"]) == [1, 2] and aslist(None) == [] and aslist(5) == [5]


def test_always_list():
    assert parse(b"a={b=1}", always_list={"a"})["a"] == [{"b": 1}]


def test_lists_and_nested_anonymous_blocks():
    d = parse(b"l={ 1 2 3 } t={ SWE DAN } n={ {id=0} {id=1} } e={}")
    assert d["l"] == [1, 2, 3] and d["t"] == ["SWE", "DAN"]
    assert d["n"] == [{"id": 0}, {"id": 1}] and d["e"] == {}


def test_mixed_block():
    d = parse(b"m={ a=1 7 8 }")
    assert d["m"]["a"] == 1 and d["m"]["_"] == [7, 8]


def test_comments_and_whitespace_and_bom():
    assert parse(b"\xef\xbb\xbfa=1 # comment { }\n b = 2\r\n#x\nc\t=\t3") == {"a": 1, "b": 2, "c": 3}


def test_key_without_equals_before_brace():
    assert parse(b"map_area_data{ k=v }") == {"map_area_data": {"k": "v"}}


def test_negative_int_and_date_keys():
    d = parse(b"-1={ a=1 } 1444.1.1={ b=2 }")
    assert d[-1] == {"a": 1} and d[Date(1444, 1, 1)] == {"b": 2}


def test_operators_accepted():
    assert parse(b"a>=3 b<=4 c?=5") == {"a": 3, "b": 4, "c": 5}


def test_string_with_braces_newline_and_hash():
    d = parse(b'a="{ not a block } # nope"\nb="\n\t"\nc=1')
    assert d["a"] == "{ not a block } # nope" and d["b"] == "\n\t" and d["c"] == 1


def test_cp1252_and_utf8_strings():
    assert parse('a="Cristóbal"'.encode("cp1252"))["a"] == "Cristóbal"
    assert parse('a="Cristóbal"'.encode("utf-8"))["a"] == "Cristóbal"


def test_find_matching_brace_respects_quotes_and_comments():
    data = b'k={ a="}" # }\n b={ c=1 } d="{" }tail'
    i = data.index(b"{")
    j = find_matching_brace(data, i)
    assert data[j:] == b"}tail"
    assert parse_block(data, i)["b"] == {"c": 1}


def test_unbalanced_raises():
    with pytest.raises(ParseError):
        find_matching_brace(b"a={ b={ }", 2)
    with pytest.raises(ParseError):
        parse(b"a={ b=1")
    with pytest.raises(ParseError):
        parse(b"a=1 }")


def test_parse_slice_only_touches_slice():
    data = b"junk{{{ x=1 y={ z=2 } }}} ((("
    start = data.index(b"x=")
    end = data.index(b"}}}")
    assert parse(data, start, end) == {"x": 1, "y": {"z": 2}}


def test_block_index_skips_nested():
    data = b"a={ b={ c=1 } } d=5 e=\"s s\" f={}"
    idx = list(block_index(data, 0, len(data)))
    assert [k for k, _, _ in idx] == ["a", "d", "e", "f"]
    k, s, e = idx[0]
    assert data[s : e + 1] == b"{ b={ c=1 } }"


def test_parse_value_at():
    data = b'x = { a=1 } y="q"'
    assert parse_value_at(data, 1) == {"a": 1}
    assert parse_value_at(data, data.index(b"y") + 1) == "q"
