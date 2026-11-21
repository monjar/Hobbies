"""M2: audio-aware ranking, duplicate refusal, subject-aware cropping, and the evaluation harnesses."""

import json
import subprocess
from pathlib import Path

import pytest

from clipforge.analyse import dedup, spoiler
from clipforge.analyse.audio import energy_per_second, mean_energy
from clipforge.analyse.evaluate import (
    HIGHLIGHT_PASS_RATE, covers, evaluate_highlights, evaluate_spoilers, load_highlight_labels, load_spoiler_cases,
)
from clipforge.analyse.highlights import candidate_windows
from clipforge.errors import ConfigError, ValidationFailed
from clipforge.providers.base import LLMResult
from clipforge.render.subject import crop_fraction, motion_focus
from clipforge.review import actions
from clipforge.types import Span

FIXTURES = Path(__file__).parent / "fixtures"


def lavfi(dest: Path, *args: str) -> Path:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args, str(dest)], check=True)
    return dest


@pytest.fixture(scope="module")
def loud_late_clip(tmp_path_factory) -> Path:
    """60 s of one static shot: quiet for 30 s, then loud."""
    return lavfi(
        tmp_path_factory.mktemp("audio") / "loud_late.mp4",
        "-f", "lavfi", "-t", "60", "-i", "color=c=gray:size=160x90:rate=10",
        "-f", "lavfi", "-t", "60", "-i", "sine=frequency=300:sample_rate=8000",
        "-af", "volume='if(lt(t,30),0.02,0.9)':eval=frame", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
    )


# -- audio energy -------------------------------------------------------------

def test_energy_tracks_loudness_per_second(loud_late_clip):
    energy = energy_per_second(loud_late_clip)
    assert len(energy) == 60
    assert max(energy[2:28]) < 0.005 < 0.05 < min(energy[32:58])  # lavfi sine peaks at 1/8
    assert mean_energy(energy, 35, 55) > 10 * mean_energy(energy, 5, 25)


def test_file_without_audio_has_no_energy(tmp_path):
    silent = lavfi(tmp_path / "silent.mp4", "-f", "lavfi", "-t", "3", "-i", "color=c=gray:size=160x90:rate=10")
    assert energy_per_second(silent) == []


def test_audio_energy_moves_the_top_window_to_the_loud_part(loud_late_clip):
    energy = energy_per_second(loud_late_clip)  # one static shot, so audio is the only signal
    without = candidate_windows(60.0, [], [], 23, 30, top_k=1)[0]
    with_audio = candidate_windows(60.0, [], [], 23, 30, top_k=1, energy=energy)[0]
    assert with_audio.span.start >= 30.0 > without.span.start
    assert with_audio.energy > 0.05


# -- dedup --------------------------------------------------------------------

def test_same_frame_hashes_identically_and_other_shots_differ(fixture_clip):
    assert dedup.frame_hash(fixture_clip, 3.0) == dedup.frame_hash(fixture_clip, 3.0)
    smpte, mandelbrot = dedup.frame_hash(fixture_clip, 9.0), dedup.frame_hash(fixture_clip, 16.0)
    assert dedup.hamming(smpte, mandelbrot) > dedup.HAMMING_THRESHOLD


def test_hash_survives_rescaling_and_recompression(fixture_clip, tmp_path):
    small = lavfi(tmp_path / "small.mp4", "-i", str(fixture_clip), "-vf", "scale=200:112", "-crf", "35", "-an")
    assert dedup.hamming(dedup.frame_hash(fixture_clip, 9.0), dedup.frame_hash(small, 9.0)) <= dedup.HAMMING_THRESHOLD


def test_duplicate_needs_most_frames_to_match():
    known = [0x0, 0xFFFF, 0xFFFF0000]
    assert dedup.is_duplicate([0x1, 0xFFFE, 0xFFFF0001], known)
    assert not dedup.is_duplicate([0x1, 0xAAAAAAAAAAAAAAAA, 0x5555555555555555], known)


def test_second_job_on_the_same_footage_is_refused(make_pipeline, db, inbox_clip):
    first = make_pipeline().make(inbox_clip, "detail-spotting", live=False)
    with pytest.raises(ValidationFailed, match="duplicates footage"):
        make_pipeline().make(inbox_clip, "detail-spotting", live=False, new=True)
    assert db.one("SELECT COUNT(*) FROM renders")[0] == 1
    assert db.one("SELECT COUNT(*) FROM keyframe_hashes WHERE job_id = ?", first)[0] == dedup.FRAMES_PER_WINDOW


def test_renamed_copy_of_used_footage_is_refused(make_pipeline, settings, inbox_clip, tmp_path):
    make_pipeline().make(inbox_clip, "detail-spotting", live=False)
    copy = lavfi(settings.inbox / "reencoded.mp4", "-i", str(inbox_clip), "-vf", "scale=240:134", "-crf", "30", "-c:a", "copy")
    with pytest.raises(ValidationFailed, match="duplicates footage"):
        make_pipeline().make(copy, "what-you-missed", live=False)


def test_rejecting_a_render_frees_its_footage_and_rotates_the_look(make_pipeline, db, inbox_clip):
    first_id = make_pipeline().make(inbox_clip, "detail-spotting", live=False)
    actions.reject(db, first_id, "weak hook")
    second_id = make_pipeline().make(inbox_clip, "detail-spotting", live=False)
    first, second = db.job(first_id), db.job(second_id)
    assert second["state"] == "pending_review" and second_id != first_id
    assert second["template"] != first["template"] and second["caption_style"] != first["caption_style"]


# -- subject-aware crop -------------------------------------------------------

@pytest.fixture(scope="module")
def mover_clip(tmp_path_factory) -> Path:
    """A small animated box near the right edge of an otherwise static 16:9 frame."""
    return lavfi(
        tmp_path_factory.mktemp("subject") / "mover.mp4",
        "-f", "lavfi", "-t", "6", "-i", "color=c=0x202020:size=320x180:rate=15",
        "-f", "lavfi", "-t", "6", "-i", "testsrc2=size=48x48:rate=15",
        "-filter_complex", "[0][1]overlay=x=250+8*sin(t*3):y=60", "-c:v", "libx264", "-preset", "ultrafast",
    )


def test_crop_follows_motion_on_one_side(mover_clip):
    fraction = crop_fraction(320, 180, 1080, 1920)
    assert fraction == pytest.approx(0.316, abs=0.001)
    focus = motion_focus(mover_clip, Span(0, 6), fraction)
    assert focus is not None and 0.78 <= focus <= 0.92  # the box sits around x = 0.86


def test_static_footage_has_no_subject(tmp_path):
    static = lavfi(tmp_path / "static.mp4", "-f", "lavfi", "-t", "4", "-i", "color=c=gray:size=320x180:rate=15")
    assert motion_focus(static, Span(0, 4), 0.316) is None


def test_motion_across_the_whole_frame_has_no_subject(fixture_clip):
    # mandelbrot zoom: everything moves, so no crop position holds most of the motion
    assert motion_focus(fixture_clip, Span(14, 20), 0.316) is None


def test_portrait_source_needs_no_horizontal_choice(mover_clip):
    assert crop_fraction(1080, 1920, 1080, 1920) == 1.0
    assert motion_focus(mover_clip, Span(0, 6), 1.0) == 0.5


def test_subject_template_falls_back_and_says_so(make_pipeline, db, inbox_clip):
    job_id = make_pipeline().make(inbox_clip, "detail-spotting", live=False, template="subject_crop")
    sidecar = json.loads(Path(db.one("SELECT sidecar_path FROM renders WHERE job_id = ?", job_id)["sidecar_path"]).read_text())
    assert sidecar["template"] == "blur_fill"
    assert any("no clear subject" in flag for flag in sidecar["flags"])


def test_subject_template_renders_a_crop_when_there_is_a_subject(make_pipeline, db, settings, tmp_path):
    clip = lavfi(
        settings.inbox / "mover_long.mp4",
        "-f", "lavfi", "-t", "30", "-i", "color=c=0x202020:size=320x180:rate=15",
        "-f", "lavfi", "-t", "30", "-i", "testsrc2=size=48x48:rate=15",
        "-filter_complex", "[0][1]overlay=x=250+8*sin(t*3):y=60", "-c:v", "libx264", "-preset", "ultrafast",
    )
    job_id = make_pipeline().make(clip, "detail-spotting", live=False, template="subject_crop")
    render = db.one("SELECT * FROM renders WHERE job_id = ?", job_id)
    sidecar = json.loads(Path(render["sidecar_path"]).read_text())
    assert sidecar["template"] == "subject_crop" and not any("no clear subject" in f for f in sidecar["flags"])
    # The crop is centred on the box, so its bright colour bars cross the middle of the output.
    proc = subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-ss", "5", "-i", render["path"], "-frames:v", "1",
         "-vf", "crop=600:80:240:1050,scale=1:1,format=gray", "-f", "rawvideo", "-"], capture_output=True, check=True,
    )
    assert proc.stdout[0] > 70  # background grey level is 32


# -- evaluation harnesses -----------------------------------------------------

def test_window_covers_a_highlight_at_half_overlap():
    assert covers(Span(10, 40), Span(30, 50))  # 10 of 20 seconds
    assert not covers(Span(10, 40), Span(31, 51))
    assert covers(Span(0, 60), Span(20, 30))


def test_highlight_evaluation_scores_labels_against_the_top_window(loud_late_clip, tmp_path):
    from clipforge.analyse.transcribe import NullTranscriber

    labels = tmp_path / "labels.yaml"
    labels.write_text(
        f"clips:\n  - file: {loud_late_clip}\n    highlights: [[32, 58]]\n"
        f"  - file: {loud_late_clip}\n    highlights: [[0, 12]]\n"
    )
    results = evaluate_highlights(load_highlight_labels(labels), tmp_path, NullTranscriber(), 23, 30)
    assert [r.matched for r in results] == [True, False]
    assert results[0].top_window.start >= 30
    assert sum(r.matched for r in results) / len(results) < HIGHLIGHT_PASS_RATE


def test_label_files_are_validated(tmp_path):
    empty = tmp_path / "empty.yaml"
    empty.write_text("clips: []\n")
    with pytest.raises(ConfigError):
        load_highlight_labels(empty)
    bad = tmp_path / "bad.yaml"
    bad.write_text("clips:\n  - file: a.mp4\n    highlights: [[5, 5]]\n")
    with pytest.raises(ConfigError):
        load_highlight_labels(bad)


def test_spoiler_fixture_set_is_well_formed_and_covers_every_label():
    cases = load_spoiler_cases(FIXTURES / "spoilers.yaml")
    labels = [case["label"] for case in cases]
    assert len(cases) >= 16 and len({case["name"] for case in cases}) == len(cases)
    for label in (spoiler.SAFE, *spoiler.BLOCKED):
        assert labels.count(label) >= 3


class KeyedLLM:
    """Answers each case with the label mapped from its description; counts spend."""

    name = "fake"

    def __init__(self, answers):
        self.answers = answers

    def complete_json(self, *, task, system, user, schema):
        label = next(label for text, label in self.answers.items() if text in user)
        return LLMResult(data={"label": label, "reason": "r"}, usd=0.01, model="fake")


def test_spoiler_evaluation_reports_accuracy_leaks_and_cost():
    cases = load_spoiler_cases(FIXTURES / "spoilers.yaml")
    perfect, usd = evaluate_spoilers(cases, KeyedLLM({c["description"]: c["label"] for c in cases}))
    assert all(r.correct for r in perfect) and not any(r.leaked for r in perfect)
    assert usd == pytest.approx(0.01 * len(cases))

    lax, _ = evaluate_spoilers(cases, KeyedLLM({c["description"]: "none" for c in cases}))
    blocked = sum(1 for c in cases if c["label"] != "none")
    assert sum(r.leaked for r in lax) == blocked

    # Mislabelling one spoiler type as another still blocks the clip, so it is wrong but not a leak.
    swapped, _ = evaluate_spoilers(
        cases, KeyedLLM({c["description"]: ("ending" if c["label"] != "none" else "none") for c in cases})
    )
    assert not any(r.leaked for r in swapped) and not all(r.correct for r in swapped)
