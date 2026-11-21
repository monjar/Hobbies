# PLAN.md — Gaming Clip Pipeline (GTA 6 first)

Instructions for Claude Code. Build in milestone order. Do not start a milestone until the previous one passes its acceptance checks.

## 1. Goal

A local pipeline that turns gaming footage into narrated vertical short videos (YouTube Shorts, TikTok, Instagram Reels):

discover trending topics -> ingest footage -> find highlight moments -> cut -> write narration -> synthesise voice -> render -> human review -> publish -> measure.

Priority is speed to first video. Milestone 0 must produce a finished video from a local file on day one. Everything else is automation around that core.

## 2. Hard constraints

1. **Source policy is enforced in code.** Footage may only come from: (a) the local `inbox/` folder, (b) channels listed in `config/sources.yaml` allowlist, (c) uploads the YouTube API reports as Creative Commons. Anything else is discovery metadata only, never downloaded.
2. **No auto-publish without approval.** Every render goes to `pending_review`. Publishing requires an explicit approve action. No flag bypasses this.
3. **Narration is mandatory.** A render fails validation if narration covers less than 60% of its duration, or if any single uninterrupted source segment exceeds 15 seconds.
4. **Spoiler gate.** Clips classified as ending, isolated cutscene, or major story reveal are auto-rejected.
5. **Verify APIs against current official docs before implementing each integration.** Do not rely on memory for endpoints, quotas, scopes, model names, or audit rules. Record the doc URL and date checked in `docs/integrations.md`.
6. **No secrets in the repo.** `.env` only, with `.env.example` committed.
7. **Cost cap.** Each video has a configurable max spend (LLM + TTS). Abort the job if exceeded.

## 3. Stack

- Python 3.11+, `uv` for dependency management
- `ffmpeg` / `ffprobe` (system binary) for all media operations
- PySceneDetect for shot boundaries
- faster-whisper for transcription and word-level timestamps
- SQLite (via SQLModel or plain `sqlite3`) for state
- Typer for the CLI
- FastAPI + server-rendered HTML for the review queue (must work on a phone browser)
- pytest for tests
- Provider interfaces (swappable, configured in `config/providers.yaml`):
  - `LLMProvider` — scripting, classification, highlight ranking
  - `VisionProvider` — frame/video understanding
  - `TTSProvider` — narration audio
  - `Publisher` — one implementation per platform

## 4. Repo layout

```
clipforge/
  cli.py                 # Typer entry point
  config.py              # loads yaml + env, validates
  db.py                  # schema, migrations, state transitions
  discover/              # youtube.py, reddit.py, rank.py
  ingest/                # local.py, url.py (allowlist-gated), policy.py
  analyse/               # transcribe.py, scenes.py, highlights.py, spoiler.py, dedup.py
  script/                # writer.py, prompts/, validate.py
  voice/                 # tts.py (interface), providers/
  render/                # cut.py, reframe.py, captions.py, mix.py, templates/
  review/                # app.py, templates/
  publish/               # youtube.py, tiktok.py, instagram.py
  metrics/               # pull.py, report.py
config/
  sources.yaml           # allowlisted channels, CC rule, inbox path
  providers.yaml         # provider + model per role
  style.yaml             # narration angles, caption style, templates
inbox/                   # drop local footage here
work/                    # intermediate files, gitignored
out/                     # final renders, gitignored
tests/
docs/integrations.md
```

## 5. Data model

Tables: `topics`, `sources`, `moments`, `scripts`, `renders`, `posts`, `metrics`, `costs`.

Job state machine (single source of truth in `db.py`, illegal transitions raise):

```
discovered -> ingested -> analysed -> scripted -> voiced -> rendered
  -> pending_review -> approved -> published
                    -> rejected
any state -> failed (with reason)
```

Every stage is idempotent and resumable: re-running a stage on a finished job is a no-op.

## 6. Output spec

- 1080x1920, 30 fps, H.264 + AAC, MP4
- Length 20–40 s (configurable)
- Burned-in word-level captions, safe margins for platform UI (top 220 px, bottom 420 px clear)
- Audio: narration normalised to -14 LUFS; game audio ducked under narration
- Hook in the first 2 seconds (script validator checks first sentence length <= 12 words)
- Sidecar JSON per render: title, description, hashtags, source attribution, cost, spoiler verdict

## 7. Milestones

### M0 — One video from a local file (target: day 1)

`clipforge make inbox/clip.mp4 --angle "detail-spotting"`

Steps: transcribe -> scene detect -> pick best 20–40 s window (LLM over transcript + sampled frames) -> write script -> TTS -> reframe to 9:16 -> captions -> mix -> render to `out/`.

Acceptance:
- Produces a valid MP4 matching section 6 from a single command
- Narration coverage and segment-length rules (constraint 3) enforced by `render/validate`
- Cost written to `costs`
- Unit tests for window selection, script validation, caption timing; one end-to-end test using a short fixture clip with providers mocked

### M1 — Review queue

`clipforge review` serves a mobile-friendly page: video player, script, sources, spoiler verdict, cost; buttons Approve / Reject / Regenerate script.

Acceptance:
- State transitions only via the queue or CLI equivalents
- Reject requires a reason (stored, later fed to the script prompt)
- Works on a phone on the local network

### M2 — Analysis quality

- Highlight ranking: score candidate windows on action density (scene-cut rate, audio energy), transcript interest, and vision-model description
- Spoiler classifier (constraint 4) with a labelled fixture set in `tests/`
- Dedup: perceptual hash of keyframes; refuse moments within threshold of anything already rendered
- Reframing: subject-aware crop where possible, fallback to centre crop with blurred fill

Acceptance: on a fixture set of 10 clips, top-ranked window matches a hand-labelled highlight in at least 7.

### M3 — Discovery

- YouTube Data API search/trending for configured keywords; store metadata, compute views-per-hour
- Reddit API for configured subreddits
- `clipforge topics` prints ranked topics with suggested narration angles
- Discovery output feeds the script prompt as context; it does not trigger downloads

Acceptance: quota usage logged per run; ranking is deterministic given stored data.

### M4 — Gated URL ingest

- `ingest/policy.py` decides allow/deny from `sources.yaml` + API licence field; deny is the default
- Every ingested source stores: URL, channel, licence, policy rule that allowed it, timestamp
- Attribution carried through to the sidecar JSON and post description

Acceptance: tests prove a non-allowlisted, non-CC URL is refused and nothing is written to disk.

### M5 — Publishing

Order: YouTube -> TikTok -> Instagram. One `Publisher` each, approved renders only.

Before coding each one, confirm in current docs and record in `docs/integrations.md`:
- required scopes and app review/audit status
- whether unaudited apps have uploads restricted to private
- rate limits and daily caps
- AI/synthetic-content disclosure fields, and set them where policy requires

If an audit blocks public posting, implement `export` instead: write the MP4 + caption text to a dated folder for manual upload or a scheduler tool.

Acceptance: dry-run mode prints the exact request without sending; real publish stores platform post ID and URL.

### M6 — Metrics loop

- Pull views, average view duration, likes, shares per post daily
- Weekly report: best/worst by retention, grouped by angle, template, clip length, hook wording
- Top 5 scripts and rejection reasons injected into the script prompt as examples

Acceptance: `clipforge report` produces a markdown summary from stored data.

### M7 — Scheduler

`clipforge run` executes discover -> ingest -> analyse -> script -> voice -> render for N jobs and stops at `pending_review`. Cron/systemd example in the README. Optional notification when the queue has items.

## 8. Script prompt requirements

- Input: transcript excerpt, vision description of the moment, topic context, chosen angle, past winners, past rejection reasons
- Angles live in `config/style.yaml` (e.g. detail-spotting, GTA V comparison, mechanic explained, "what you missed")
- Output: JSON with `hook`, `lines[]` (each with target start time), `title`, `description`, `hashtags`
- Validator rejects: unverifiable factual claims about the game not grounded in the inputs, hook > 12 words, total speech time outside clip length ±10%
- At least 3 visual templates and 2 caption styles, rotated, so output is not uniform

## 9. Working rules for Claude Code

- Small commits per stage; tests alongside code
- Mock all paid providers in tests; real calls only behind `--live`
- Structured logs (JSON) with job ID on every line
- Fail loudly: no silent fallbacks that produce a worse video without flagging it
- Ask before adding any dependency that needs a system install beyond ffmpeg
- Update the README with setup, env vars, and every CLI command as they are added

## 10. Env vars (`.env.example`)

```
LLM_API_KEY=
VISION_API_KEY=
TTS_API_KEY=
YOUTUBE_API_KEY=
YOUTUBE_OAUTH_CLIENT_ID=
YOUTUBE_OAUTH_CLIENT_SECRET=
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
TIKTOK_CLIENT_KEY=
TIKTOK_CLIENT_SECRET=
INSTAGRAM_ACCESS_TOKEN=
MAX_COST_PER_VIDEO_USD=0.50
```

## 11. Out of scope

- Bulk downloading from non-allowlisted sources
- Unattended publishing
- Scraping any platform outside its official API
- Watermark removal or any re-encoding intended to evade duplicate detection
