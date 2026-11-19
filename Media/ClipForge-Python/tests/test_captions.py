import re

import pytest

from clipforge.config import CaptionStyleCfg
from clipforge.render.captions import (
    MAX_PHRASE_CHARS, MAX_PHRASE_WORDS, build_events, group_words, margin_bottom_px, render_ass,
)
from clipforge.rules import CAPTION_SAFE_BOTTOM_PX
from clipforge.types import Word

KARAOKE = CaptionStyleCfg(name="karaoke", mode="karaoke", font="DejaVu Sans", size=78)
POP = CaptionStyleCfg(name="pop", mode="word", font="DejaVu Sans", size=104)
TEXT = "Look closely at this. The reflection moves before the car does, which is unusual."
WORDS = [Word(token, 0.2 + i * 0.4, 0.5 + i * 0.4) for i, token in enumerate(TEXT.split())]


@pytest.mark.parametrize("style", [KARAOKE, POP])
def test_one_event_per_word_starting_when_it_is_spoken(style):
    events = build_events(WORDS, style)
    assert [e.start for e in events] == [w.start for w in WORDS]


@pytest.mark.parametrize("style", [KARAOKE, POP])
def test_events_never_overlap_and_have_positive_length(style):
    events = build_events(WORDS, style)
    assert all(e.end > e.start for e in events)
    assert all(a.end <= b.start + 1e-9 for a, b in zip(events, events[1:]))


def test_caption_clears_before_the_next_phrase_after_a_pause():
    words = [Word("first", 0.0, 0.4), Word("second", 5.0, 5.4)]
    first, second = build_events(words, KARAOKE)
    assert first.end < 1.0 and second.start == 5.0


def test_phrases_are_short_and_break_at_sentence_ends():
    phrases = group_words(WORDS)
    assert [w for phrase in phrases for w in phrase] == WORDS
    for phrase in phrases:
        assert len(phrase) <= MAX_PHRASE_WORDS
        assert len(" ".join(w.text for w in phrase)) <= MAX_PHRASE_CHARS or len(phrase) == 1
        assert all(w.text[-1] not in ".!?" for w in phrase[:-1])


def test_karaoke_highlights_exactly_the_spoken_word():
    events = build_events(WORDS[:3], KARAOKE)
    plain = [re.sub(r"\{[^}]*\}", "", e.text) for e in events]
    assert plain == ["Look closely at"] * 3
    assert "}Look{" in events[0].text and "}closely{" in events[1].text and "}at{" in events[2].text


def test_ass_file_keeps_platform_ui_zones_clear():
    ass = render_ass(WORDS, KARAOKE, 1080, 1920)
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    style = next(line for line in ass.splitlines() if line.startswith("Style:")).split(",")
    alignment, margin_v = int(style[18]), int(style[21])
    assert alignment == 2  # bottom-centre, so text grows upward from the margin
    assert margin_v == margin_bottom_px() >= CAPTION_SAFE_BOTTOM_PX
    # One caption line of this size sits far below the 220 px top zone.
    assert 1920 - margin_v - KARAOKE.size > 220


def test_ass_timestamps_and_escaping():
    ass = render_ass([Word("{odd}", 61.239, 61.5), Word("b", 3600.0, 3600.2)], KARAOKE, 1080, 1920)
    dialogue = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    assert dialogue[0].split(",")[1] == "0:01:01.24"
    assert dialogue[1].split(",")[1] == "1:00:00.00"
    assert "(odd)" in dialogue[0] and "{odd}" not in dialogue[0]
