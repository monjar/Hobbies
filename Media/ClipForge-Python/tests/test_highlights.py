import pytest

from clipforge.analyse.highlights import candidate_windows, pick_window
from clipforge.costs import CostTracker
from clipforge.errors import ProviderError, ValidationFailed
from clipforge.providers.base import LLMResult
from clipforge.providers.mock import MockLLM, MockVision
from clipforge.types import Word

CUTS = [5.0, 12.0, 30.0, 34.0, 38.0, 41.0, 44.0, 47.0, 52.0, 58.0, 75.0, 110.0]


def test_windows_respect_length_bounds_and_source():
    candidates = candidate_windows(120.0, CUTS, [], min_len=23, max_len=40)
    assert candidates
    for candidate in candidates:
        assert 23 <= candidate.span.length <= 40
        assert 0 <= candidate.span.start and candidate.span.end <= 120.0


def test_windows_start_on_shot_boundaries():
    for candidate in candidate_windows(120.0, CUTS, [], min_len=23, max_len=40):
        assert candidate.span.start in [0.0, *CUTS]


def test_densest_action_ranks_first():
    best = candidate_windows(120.0, CUTS, [], min_len=23, max_len=40)[0]
    # The run of quick cuts between 30 s and 58 s is the busiest part of the source.
    assert 30.0 <= best.span.start <= 38.0


def test_speech_raises_a_windows_score():
    words = [Word("w", 80 + i * 0.3, 80.2 + i * 0.3) for i in range(80)]
    silent = candidate_windows(120.0, [75.0], [], min_len=23, max_len=40)
    spoken = candidate_windows(120.0, [75.0], words, min_len=23, max_len=40)
    assert spoken[0].span.start == 75.0
    assert spoken[0].word_rate > 0 and silent[0].word_rate == 0


def test_candidates_do_not_heavily_overlap():
    candidates = candidate_windows(120.0, CUTS, [], min_len=23, max_len=40, top_k=3)
    for i, a in enumerate(candidates):
        for b in candidates[i + 1 :]:
            shared = min(a.span.end, b.span.end) - max(a.span.start, b.span.start)
            assert shared <= 0.5 * min(a.span.length, b.span.length) + 1e-9


def test_selection_is_deterministic():
    assert candidate_windows(120.0, CUTS, [], 23, 40) == candidate_windows(120.0, CUTS, [], 23, 40)


def test_source_without_cuts_still_yields_a_window():
    (only,) = candidate_windows(60.0, [], [], min_len=23, max_len=40)
    assert only.span.start == 0.0 and 23 <= only.span.length <= 40


def test_source_shorter_than_minimum_fails():
    with pytest.raises(ValidationFailed):
        candidate_windows(10.0, [], [], min_len=23, max_len=40)


class OutOfRangeLLM(MockLLM):
    def complete_json(self, *, task, system, user, schema):
        return LLMResult(data={"choice": 7, "reason": "x"}, usd=0.0, model="fake")


def test_llm_pick_outside_candidates_fails(db, inbox_clip, tmp_path):
    job = db.create_job(angle="a", live=0, source_path="/x", source_sha256="s", template="t", caption_style="c")
    candidates = candidate_windows(32.0, [7.0, 13.0], [], min_len=20, max_len=30)
    with pytest.raises(ProviderError):
        pick_window(
            source=inbox_clip, candidates=candidates, words=[], angle="a", llm=OutOfRangeLLM(),
            vision=MockVision(), costs=CostTracker(db, job, 1.0), frames_dir=tmp_path / "frames",
        )
