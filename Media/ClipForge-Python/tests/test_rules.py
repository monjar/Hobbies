import pytest

from clipforge.config import OutputCfg
from clipforge.costs import CostTracker
from clipforge.errors import CostCapExceeded, ValidationFailed
from clipforge.render.validate import check_plan
from clipforge.types import Span

OUT = OutputCfg()
SEGMENTS = [Span(0, 10), Span(10.5, 20.5), Span(21, 26)]  # 25 s render


def test_plan_within_rules_passes():
    report = check_plan(SEGMENTS, [Span(0, 16)], OUT)
    assert report.render_s == 25 and report.narration_coverage == pytest.approx(0.64)
    assert report.max_source_segment_s == 10


def test_narration_under_sixty_percent_fails():
    with pytest.raises(ValidationFailed, match="narration covers 59%"):
        check_plan(SEGMENTS, [Span(0, 14.75)], OUT)


def test_narration_at_exactly_sixty_percent_passes():
    check_plan(SEGMENTS, [Span(0, 15)], OUT)


def test_source_segment_over_fifteen_seconds_fails():
    with pytest.raises(ValidationFailed, match="uninterrupted source segment"):
        check_plan([Span(0, 15.5), Span(16, 26)], [Span(0, 25)], OUT)


def test_back_to_back_segments_cannot_dodge_the_segment_limit():
    with pytest.raises(ValidationFailed, match="uninterrupted source segment"):
        check_plan([Span(0, 10), Span(10, 20), Span(21, 26)], [Span(0, 25)], OUT)


@pytest.mark.parametrize("segments", [[Span(0, 10), Span(11, 19)], [Span(0, 14), Span(15, 29), Span(30, 43)]])
def test_render_length_outside_range_fails(segments):
    total = sum(s.length for s in segments)
    with pytest.raises(ValidationFailed, match="allowed range"):
        check_plan(segments, [Span(0, total)], OUT)


@pytest.fixture
def tracker(db):
    job = db.create_job(angle="a", live=0, source_path="/x", source_sha256="s", template="t", caption_style="c")
    return CostTracker(db, job, 0.50)


def test_costs_accumulate_per_job(tracker):
    tracker.add("p", "script", "m", 0.20)
    tracker.add("p", "narration", "m", 0.25)
    assert tracker.total == pytest.approx(0.45)


def test_spend_over_cap_aborts_and_is_still_recorded(tracker):
    tracker.add("p", "script", "m", 0.30)
    with pytest.raises(CostCapExceeded):
        tracker.add("p", "script", "m", 0.30)
    assert tracker.total == pytest.approx(0.60)


def test_known_price_is_refused_before_spending(tracker):
    tracker.add("p", "script", "m", 0.45)
    with pytest.raises(CostCapExceeded):
        tracker.reserve(0.10, "narration")
    tracker.reserve(0.05, "narration")
    assert tracker.total == pytest.approx(0.45)
