"""Burned-in word-level captions as an ASS subtitle file."""

from dataclasses import dataclass

from ..config import CaptionStyleCfg
from ..rules import CAPTION_SAFE_BOTTOM_PX
from ..types import Word

MAX_PHRASE_WORDS = 3
MAX_PHRASE_CHARS = 18
MAX_PHRASE_GAP_S = 0.5
LINGER_S = 0.15  # how long a phrase's last word stays up after it is spoken
BOTTOM_PADDING_PX = 40
SIDE_MARGIN_PX = 60
LONG_WORD_CHARS = 12


@dataclass(frozen=True)
class Event:
    start: float
    end: float
    text: str  # ASS text, including override tags


def group_words(words: list[Word]) -> list[list[Word]]:
    """Break narration into short phrases that fit on one caption line."""
    phrases: list[list[Word]] = []
    for word in words:
        current = phrases[-1] if phrases else None
        if (
            current
            and len(current) < MAX_PHRASE_WORDS
            and len(" ".join(w.text for w in current)) + 1 + len(word.text) <= MAX_PHRASE_CHARS
            and word.start - current[-1].end <= MAX_PHRASE_GAP_S
            and current[-1].text[-1] not in ".!?"
        ):
            current.append(word)
        else:
            phrases.append([word])
    return phrases


def _escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")")


def _ass_colour(rrggbb: str) -> str:
    return f"&H00{rrggbb[4:6]}{rrggbb[2:4]}{rrggbb[0:2]}".upper()


def build_events(words: list[Word], style: CaptionStyleCfg) -> list[Event]:
    """One event per spoken word; events never overlap and follow the narration timing."""
    events = []
    phrases = group_words(words)
    for index, phrase in enumerate(phrases):
        next_phrase_start = phrases[index + 1][0].start if index + 1 < len(phrases) else None
        for position, word in enumerate(phrase):
            if position + 1 < len(phrase):
                end = phrase[position + 1].start
            else:
                end = word.end + LINGER_S
                if next_phrase_start is not None:
                    end = min(end, next_phrase_start)
            end = max(end, word.start + 0.01)
            if style.mode == "karaoke":
                parts = [
                    f"{{\\c{_ass_colour(style.highlight)}}}{_escape(w.text)}{{\\c{_ass_colour(style.primary)}}}"
                    if i == position
                    else _escape(w.text)
                    for i, w in enumerate(phrase)
                ]
                text = " ".join(parts)
            else:
                shown = _escape(word.text).upper()
                fit = min(100, round(100 * LONG_WORD_CHARS / max(len(shown), 1)))
                text = f"{{\\fscx{fit * 0.8:.0f}\\fscy80\\t(0,80,\\fscx{fit}\\fscy100)}}{shown}"
            events.append(Event(word.start, end, text))
    return events


def _timestamp(seconds: float) -> str:
    centis = max(round(seconds * 100), 0)
    return f"{centis // 360000}:{centis // 6000 % 60:02d}:{centis // 100 % 60:02d}.{centis % 100:02d}"


def margin_bottom_px() -> int:
    """Distance from the frame bottom to the caption baseline; keeps platform UI clear."""
    return CAPTION_SAFE_BOTTOM_PX + BOTTOM_PADDING_PX


def render_ass(words: list[Word], style: CaptionStyleCfg, width: int, height: int) -> str:
    primary = _ass_colour(style.highlight if style.mode == "word" else style.primary)
    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding\n"
        # Alignment 2 is bottom-centre: text grows upward from MarginV, away from the bottom UI zone.
        f"Style: Cap,{style.font},{style.size},{primary},{primary},&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,"
        f"{style.outline_px},2,2,{SIDE_MARGIN_PX},{SIDE_MARGIN_PX},{margin_bottom_px()},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = [
        f"Dialogue: 0,{_timestamp(e.start)},{_timestamp(e.end)},Cap,,0,0,0,,{e.text}"
        for e in build_events(words, style)
    ]
    return header + "\n".join(lines) + "\n"
