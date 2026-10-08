"""Query parameters as described in the reference: filter (: fuzzy, :: exact, and / or) and range."""

from mock_server.deps import select

ITEMS = [
    {"ID": "1", "NAME": "DISK001", "HEALTHSTATUS": "1", "n": 10},
    {"ID": "2", "NAME": "DISK002", "HEALTHSTATUS": "2", "n": 20},
    {"ID": "3", "NAME": "pool-DISK001-copy", "HEALTHSTATUS": "1", "n": 30},
    {"ID": "4", "NAME": "FAN01", "HEALTHSTATUS": "5", "n": 40},
]


def ids(filter_=None, range_=None):
    return [i["ID"] for i in select(ITEMS, filter_, range_)]


def test_exact_match_uses_two_colons():
    assert ids("NAME::DISK001") == ["1"]


def test_fuzzy_match_uses_one_colon_and_ignores_case():
    assert ids("NAME:disk001") == ["1", "3"]
    assert ids("NAME:DISK") == ["1", "2", "3"]


def test_and_or_combinations():
    assert ids("NAME:DISK and HEALTHSTATUS::1") == ["1", "3"]
    assert ids("HEALTHSTATUS::2 or HEALTHSTATUS::5") == ["2", "4"]
    assert ids("NAME::DISK001 and HEALTHSTATUS::2 or NAME::FAN01") == ["4"]  # and binds tighter than or


def test_numeric_range_inside_a_filter():
    assert ids("n:[15,30]") == ["2", "3"]
    assert ids("n:[100,200]") == []
    assert ids("NAME::nothing and n:[0,100]") == []


def test_range_excludes_the_end_index():
    assert ids(range_="[0-2]") == ["1", "2"]
    assert ids(range_="[2-4]") == ["3", "4"]
    assert ids("HEALTHSTATUS::1", "[1-2]") == ["3"]


def test_no_parameters_return_everything():
    assert ids() == ["1", "2", "3", "4"]
