import math

from petrogis.core.extent import Extent, union_all


def test_basic_properties():
    e = Extent(0, 10, 4, 18)
    assert (e.width, e.height, e.center) == (4, 8, (2, 14))
    assert e.is_valid()
    assert Extent.from_bounds([0, 10, 4, 18]) == e


def test_union_and_intersection():
    a, b = Extent(0, 0, 10, 10), Extent(5, 5, 20, 20)
    assert a.union(b) == Extent(0, 0, 20, 20)
    assert a.intersection(b) == Extent(5, 5, 10, 10)
    assert a.intersection(Extent(11, 11, 12, 12)) is None
    assert not a.intersects(Extent(11, 0, 12, 1))


def test_union_all_skips_none_and_invalid():
    assert union_all([]) is None
    assert union_all([None, Extent(math.nan, 0, 1, 1), Extent(0, 0, 1, 1)]) == Extent(0, 0, 1, 1)


def test_buffered_has_minimum_for_points():
    e = Extent(5, 5, 5, 5).buffered(0.1, minimum=50)
    assert e == Extent(-45, -45, 55, 55)
