"""Hard constraints from PLAN.md section 2. Constants, not config: nothing may loosen them."""

MIN_NARRATION_COVERAGE = 0.60  # narration must cover at least this share of the render
MAX_SOURCE_SEGMENT_S = 15.0  # longest uninterrupted stretch of source footage
NARRATION_LUFS = -14.0
HOOK_MAX_WORDS = 12
SPEECH_TOLERANCE = 0.10  # estimated speech time must be within clip length +/- 10%
CAPTION_SAFE_TOP_PX = 220
CAPTION_SAFE_BOTTOM_PX = 420
MAX_NARRATION_TEMPO = 1.12  # narration may be sped up at most this much to fit, and it is flagged
