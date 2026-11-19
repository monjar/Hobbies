"""Turn the chosen window into source segments that respect the uninterrupted-footage limit."""

import math
from itertools import pairwise

from ..rules import MAX_SOURCE_SEGMENT_S
from ..types import Span

TARGET_SEGMENT_S = 12.0  # aim below the hard limit
JUMP_GAP_S = 0.5  # source footage skipped between segments, so each one is genuinely interrupted
MIN_SEGMENT_S = 1.0
CONTIGUOUS_TOLERANCE_S = 0.05


def plan_segments(window: Span, cuts: list[float]) -> list[Span]:
    """Split a window at shot boundaries into segments of at most TARGET_SEGMENT_S.

    Consecutive segments are separated by a JUMP_GAP_S jump cut, because two back-to-back
    segments are still one uninterrupted stretch of source footage.
    """
    assert TARGET_SEGMENT_S <= MAX_SOURCE_SEGMENT_S
    bounds = [window.start, *[c for c in sorted(cuts) if window.start < c < window.end], window.end]
    pieces = []
    for start, end in pairwise(bounds):
        parts = math.ceil((end - start) / TARGET_SEGMENT_S)
        step = (end - start) / parts
        pieces.extend(Span(start + i * step, start + (i + 1) * step) for i in range(parts))

    groups, current = [], pieces[0]
    for piece in pieces[1:]:
        if piece.end - current.start <= TARGET_SEGMENT_S:
            current = Span(current.start, piece.end)
        else:
            groups.append(current)
            current = piece
    groups.append(current)

    segments = []
    for index, group in enumerate(groups):
        segment = Span(group.start + (JUMP_GAP_S if index else 0.0), group.end)
        if segment.length >= MIN_SEGMENT_S:  # a sub-second flash is dropped rather than shown
            segments.append(Span(round(segment.start, 3), round(segment.end, 3)))
    return segments


def longest_uninterrupted(segments: list[Span]) -> float:
    """Longest stretch of continuous source footage, treating touching segments as one."""
    longest, run_start, run_end = 0.0, None, None
    for segment in segments:
        if run_end is not None and abs(segment.start - run_end) <= CONTIGUOUS_TOLERANCE_S:
            run_end = segment.end
        else:
            run_start, run_end = segment.start, segment.end
        longest = max(longest, run_end - run_start)
    return longest
