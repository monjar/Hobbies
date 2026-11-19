import pytest

from clipforge.errors import ValidationFailed
from clipforge.rules import MAX_NARRATION_TEMPO
from clipforge.types import Span, Word
from clipforge.voice.providers.elevenlabs import words_from_alignment
from clipforge.voice.tts import LINE_GAP_S, coverage, layout_narration, timeline_words


def test_lines_start_at_their_targets_when_they_fit():
    layout = layout_narration([2.0, 3.0], [0.0, 5.0], 20.0)
    assert layout.starts == [0.0, 5.0] and layout.tempo == 1.0 and not layout.packed


def test_late_running_line_pushes_the_next_one_back():
    layout = layout_narration([4.0, 3.0], [0.0, 2.0], 20.0)
    assert layout.starts == [0.0, pytest.approx(4.0 + LINE_GAP_S)] and layout.tempo == 1.0


def test_overrun_is_packed_and_sped_up_to_end_inside_the_clip():
    durations = [10.0, 11.0]
    layout = layout_narration(durations, [0.0, 10.0], 20.0)
    assert layout.packed and 1.0 < layout.tempo <= MAX_NARRATION_TEMPO
    last = layout.span(1, durations[1])
    assert last.end <= 20.0
    assert layout.span(0, durations[0]).end <= last.start


def test_overrun_beyond_tempo_limit_fails():
    with pytest.raises(ValidationFailed):
        layout_narration([15.0, 15.0], [0.0, 10.0], 20.0)


def test_word_timings_follow_layout_and_tempo():
    layout = layout_narration([10.0, 11.0], [0.0, 10.0], 20.0)
    words = timeline_words([[Word("a", 1.0, 2.0)], [Word("b", 0.0, 1.1)]], layout)
    assert words[0].start == pytest.approx(1.0 / layout.tempo)
    assert words[1].start == pytest.approx(layout.starts[1])
    assert words[1].end == pytest.approx(layout.starts[1] + 1.1 / layout.tempo)


def test_coverage_merges_overlaps_and_clips_to_render():
    assert coverage([Span(0, 5), Span(4, 10)], 20.0) == pytest.approx(0.5)
    assert coverage([Span(15, 30)], 20.0) == pytest.approx(0.25)


def test_alignment_characters_become_words():
    chars = list("Hi there, you")
    starts = [i * 0.1 for i in range(len(chars))]
    ends = [s + 0.1 for s in starts]
    words = words_from_alignment(chars, starts, ends)
    assert [w.text for w in words] == ["Hi", "there,", "you"]
    assert words[0].start == 0.0 and words[0].end == pytest.approx(0.2)
    assert words[1].start == pytest.approx(0.3) and words[2].end == pytest.approx(1.3)
