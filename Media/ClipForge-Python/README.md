# ClipForge

A local pipeline that turns gaming footage into narrated vertical short videos (YouTube Shorts, TikTok, Instagram Reels). The full design is in [PLAN.md](PLAN.md).

## Status

| Milestone | State |
| --- | --- |
| M0 — one video from a local file | Built. Tested end to end with mocked providers and real ffmpeg. Not yet run with live providers. |
| M1 — review queue | Built and tested. |
| M3 — discovery | YouTube part built and tested against stubs. Reddit not started. |
| M4 — gated URL ingest | Built and tested against stubs. No real download has completed yet. |
| M2 — analysis quality (ranking, labelled spoiler set, dedup, subject-aware crop) | Not started. Needs 10 downloaded clips to label. |
| M5–M7 — publishing, metrics, scheduler | Not started. |

## Setup

Requirements: Python 3.11+, [uv](https://docs.astral.sh/uv/), and `ffmpeg` / `ffprobe` on `PATH` built with libx264 and libass. `clipforge ingest` also needs a JavaScript runtime (deno or node) on `PATH`, which yt-dlp uses for YouTube.

```bash
uv sync
cp .env.example .env    # then fill in the keys you need
```

Run commands from this folder, or set `CLIPFORGE_HOME` to it.

## Environment variables

| Variable | Used for | Needed for |
| --- | --- | --- |
| `LLM_API_KEY` | Anthropic key: scripts, spoiler check, window choice | `--live` |
| `VISION_API_KEY` | Anthropic key: frame descriptions (can equal `LLM_API_KEY`) | `--live` |
| `TTS_API_KEY` | ElevenLabs key: narration | `--live` |
| `MAX_COST_PER_VIDEO_USD` | Spend cap per video, default `0.50`. The job aborts when it is exceeded. | always |
| `CLIPFORGE_HOME` | Project root, if not the current folder | optional |
| `YOUTUBE_API_KEY` | YouTube Data API key: discovery and the licence check before any download | `discover`, `ingest` |
| `YOUTUBE_OAUTH_*`, `REDDIT_*`, `TIKTOK_*`, `INSTAGRAM_ACCESS_TOKEN` | Reddit discovery and publishing | later milestones |

## Make a video

Put footage in `inbox/`, then:

```bash
uv run clipforge make inbox/clip.mp4 --angle detail-spotting          # offline mocks, no spend
uv run clipforge make inbox/clip.mp4 --angle detail-spotting --live   # real script and voice
```

Without `--live` the paid providers are replaced by mocks. You get a real MP4 with a placeholder script and a tone for narration, which is enough to check cutting, framing, captions and audio. Mock renders are named `*_MOCK.mp4` and cannot be approved.

The video and its sidecar JSON (title, description, hashtags, source attribution, cost, spoiler verdict) are written to `out/`. The job then waits in the review queue.

What happens in order: source policy check → transcribe (faster-whisper) → shot detection (PySceneDetect) → candidate 20–40 s windows scored by shot-change and speech rate → vision description of sampled frames → LLM picks a window → spoiler check → script → validation → TTS → 9:16 reframe → word-level captions → audio mix → encode → output validation.

## Find topics and download allowed footage

```bash
uv run clipforge discover     # search YouTube for the keywords in config/sources.yaml; stores metadata only
uv run clipforge topics       # print stored topics, fastest-growing first, with a suggested angle
uv run clipforge ingest "https://www.youtube.com/watch?v=VIDEO_ID"
uv run clipforge make library/VIDEO_ID.mp4 --angle detail-spotting
```

`discover` never downloads. `ingest` asks the YouTube Data API about the video first and downloads only if its channel is in `allowlist_channels` or the API reports a Creative Commons licence. The download goes to `library/`, and the source row records the URL, channel, licence, the rule that allowed it and the time. Videos made from it carry a credit line in the sidecar and the description. The five highest-ranked topic titles are given to the script prompt as background.

To get a key: create a project in the Google Cloud console, enable "YouTube Data API v3", and create an API key. Each keyword uses one of the 100 daily search calls per `discover` run.

Run `ingest` from a home connection. From cloud servers YouTube refuses with a "confirm you're not a bot" check.

## Commands

| Command | What it does |
| --- | --- |
| `clipforge discover` | Search YouTube for the configured keywords, store topics, print quota used. |
| `clipforge topics [--limit N]` | Print ranked topics from stored data. No network. |
| `clipforge ingest URL` | Download one YouTube video into `library/` if the source policy allows it. |
| `clipforge make PATH --angle ANGLE [--live] [--template T] [--caption-style S] [--new]` | Make one video from a file in `inbox/` or one downloaded by `ingest`. Re-running resumes or reuses the existing job for that file and angle; `--new` starts another. |
| `clipforge jobs [--limit N]` | List recent jobs with state and failure reason. |
| `clipforge show JOB_ID` | Print a job's script and sidecar as JSON. |
| `clipforge review [--lan] [--port 8000]` | Serve the review queue. `--lan` listens on all interfaces so a phone can open `http://<your-computer-ip>:8000`. There is no login, so use it only on a network you trust. |
| `clipforge approve JOB_ID` | Approve a pending render. |
| `clipforge reject JOB_ID --reason "..."` | Reject a pending render. The reason is required and is given to later script prompts. |
| `clipforge regenerate JOB_ID [--feedback "..."]` | Reject a pending render and make a new one from the same footage with a new script. |

## Rules enforced in code

- **Source policy.** `clipforge/ingest/policy.py` denies by default. Allowed: a real file inside `inbox/` (a symlink pointing outside is refused); a YouTube video from an allowlisted channel; a YouTube video the API reports as Creative Commons. A file in `library/` is usable only if it has an ingest record and its hash still matches.
- **No auto-publish.** Every render stops at `pending_review`. Only `approve` (CLI or queue) moves it on, and there is no flag that skips this.
- **Narration.** A render fails if narration covers less than 60% of it or if any uninterrupted stretch of source footage is longer than 15 s. The limits are constants in `clipforge/rules.py`, not config.
- **Spoiler gate.** A clip classified as an ending, an isolated cutscene or a major story reveal fails before any script is written.
- **Cost cap.** Every provider call is recorded in the `costs` table. The job fails as soon as the total passes the cap, and TTS is refused up front when its known price would not fit.
- **Loud failures.** A job that cannot meet a rule ends in `failed` with the reason. The one automatic adjustment is speeding narration up by at most 1.12× to fit the clip, and that is logged and flagged in the sidecar.

## How two plan rules were interpreted

- **The 15-second segment limit.** A 20–40 s video cannot be one continuous stretch of footage. The chosen window is split at shot boundaries into segments of at most 12 s, with a 0.5 s jump cut between them, because two back-to-back segments are still one uninterrupted stretch.
- **Spoiler rejection.** The state machine only allows `rejected` from `pending_review`, so a spoiler clip ends in `failed` with a `spoiler gate` reason.

## Job states

```
discovered -> ingested -> analysed -> scripted -> voiced -> rendered
  -> pending_review -> approved -> published
                    -> rejected
any non-terminal state -> failed (with reason)
```

`clipforge/db.py` is the only place a state changes, and illegal moves raise. State lives in `state/clipforge.db`. Tables so far: `jobs`, `sources`, `moments`, `scripts`, `renders`, `costs`, `reviews`, `topics`, `quota_log`. The `posts` and `metrics` tables arrive with their milestones as new migrations.

## Configuration

| File | Contents |
| --- | --- |
| `config/sources.yaml` | Inbox and library paths, channel allowlist, Creative Commons rule, discovery keywords |
| `config/providers.yaml` | Provider, model and price per role; transcription settings |
| `config/style.yaml` | Narration angles, visual templates, caption styles, output size and length |

Templates (`blur_fill`, `center_crop`, `stack_zoom`) and caption styles (`karaoke`, `pop`) rotate per job. The caption font must be installed on the machine; if it is not, libass substitutes another one.

Transcription downloads a Whisper model from Hugging Face on first use. For footage without commentary, or on a machine that cannot reach Hugging Face, set `transcribe.provider: none`.

## Tests

```bash
uv run pytest
```

All paid providers are mocked. The end-to-end tests run real ffmpeg encodes on a generated fixture clip, so the suite takes about two minutes.

API details and the dates they were checked are in [docs/integrations.md](docs/integrations.md).
