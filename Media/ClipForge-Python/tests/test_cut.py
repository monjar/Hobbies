from clipforge.render.cut import JUMP_GAP_S, MIN_SEGMENT_S, plan_segments, longest_uninterrupted
from clipforge.rules import MAX_SOURCE_SEGMENT_S
from clipforge.types import Span


def test_long_window_without_cuts_is_broken_up():
    segments = plan_segments(Span(10.0, 50.0), [])
    assert len(segments) >= 3
    assert longest_uninterrupted(segments) <= MAX_SOURCE_SEGMENT_S


def test_segments_are_separated_by_a_jump_cut():
    segments = plan_segments(Span(0.0, 36.0), [9.0, 20.0, 31.0])
    for earlier, later in zip(segments, segments[1:]):
        assert later.start - earlier.end >= JUMP_GAP_S - 1e-9


def test_segments_stay_inside_the_window_in_order():
    window = Span(5.0, 41.0)
    segments = plan_segments(window, [7.0, 18.5, 30.0, 40.6])
    assert segments[0].start == window.start
    assert all(window.start <= s.start < s.end <= window.end for s in segments)
    assert all(a.end <= b.start for a, b in zip(segments, segments[1:]))
    assert all(s.length >= MIN_SEGMENT_S for s in segments)


def test_splits_prefer_shot_boundaries():
    segments = plan_segments(Span(0.0, 30.0), [10.0, 20.0])
    assert [s.end for s in segments] == [10.0, 20.0, 30.0]


def test_touching_segments_count_as_one_stretch():
    assert longest_uninterrupted([Span(0, 10), Span(10, 20), Span(25, 30)]) == 20
    assert longest_uninterrupted([Span(0, 10), Span(10.5, 20)]) == 10
