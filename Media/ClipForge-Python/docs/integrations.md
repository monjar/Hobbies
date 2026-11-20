# Integrations

Each integration is checked against current official documentation before it is implemented (PLAN.md constraint 5). Re-check before changing a call, and update the date.

## Anthropic Messages API — `LLMProvider`, `VisionProvider`

Checked: 2026-11-19. Code: `clipforge/providers/anthropic_provider.py`.

| What | Value | Source |
| --- | --- | --- |
| SDK | `anthropic` Python SDK 2.11.0, `client.beta.messages.create` | https://github.com/anthropics/anthropic-sdk-python |
| Model | `claude-opus-5-5` | https://platform.claude.com/docs/en/about-claude/models/overview |
| Price | $4.00 input / $20.00 output per million tokens | https://platform.claude.com/docs/en/about-claude/pricing |
| JSON output | `output_config.format = {type: json_schema, schema}` | https://platform.claude.com/docs/en/build-with-claude/structured-outputs |
| Effort | `output_config.effort`; default on this model is `medium` | https://platform.claude.com/docs/en/build-with-claude/effort |
| Thinking | Always adaptive on this model; the `thinking` parameter is not sent | https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking |
| Images | base64 `image` content blocks placed before the text block | https://platform.claude.com/docs/en/build-with-claude/vision |
| Refusals | `stop_reason == "refusal"` is checked before content is read | https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback |
| Fallback | `fallbacks: "default"` with beta `server-side-fallback-2026-07-01` | same page |

Notes:

- Sampling parameters (`temperature` and so on) are rejected by this model and are not sent.
- When a fallback model serves a request, its rates differ from the configured ones. The cost record is then marked `cost_is_estimate`.
- Model facts were taken from Anthropic's Claude API reference bundled with Claude Code (cached 2026-11-03), not fetched live. No live call has been made from this repo yet.

## ElevenLabs text to speech — `TTSProvider`

Checked: 2026-11-19. Code: `clipforge/voice/providers/elevenlabs.py`.

| What | Value | Source |
| --- | --- | --- |
| Endpoint | `POST https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps` | https://elevenlabs.io/docs/api-reference/text-to-speech/convert-with-timestamps |
| Auth | `xi-api-key` header | same page |
| Query | `output_format`, default `mp3_44100_128` | same page |
| Body | `text` (required), `model_id` (default `eleven_multilingual_v2`), `previous_text` | same page |
| Response | `audio_base64`, `alignment.characters`, `alignment.character_start_times_seconds`, `alignment.character_end_times_seconds` | same page |
| Model limit | `eleven_multilingual_v2`: 10,000 characters per request | https://elevenlabs.io/docs/overview/models |
| Price | $0.08 per 1,000 characters for multilingual models; $0.04 for Flash/Turbo | https://elevenlabs.io/pricing/api |

Notes:

- One request is made per narration line. `previous_text` carries the line before it for continuity.
- PCM and WAV output at 44.1 kHz need the Pro tier, and 192 kbps MP3 needs Creator. The default MP3 format has no tier requirement.
- No live call has been made from this repo yet.

## YouTube Data API v3 — discovery and the source policy lookup

Checked: 2026-11-20. Code: `clipforge/discover/youtube.py`.

| What | Value | Source |
| --- | --- | --- |
| Auth | API key in the `key` query parameter (read-only calls need no OAuth) | https://developers.google.com/youtube/v3/docs |
| Search | `GET https://www.googleapis.com/youtube/v3/search` with `part=snippet`, `type=video`, `q`, `order`, `publishedAfter`, `maxResults` (max 50), `videoLicense` (`any`, `creativeCommon`, `youtube`; needs `type=video`) | https://developers.google.com/youtube/v3/docs/search/list |
| Videos | `GET https://www.googleapis.com/youtube/v3/videos` with `part=snippet,contentDetails,statistics,status` and comma-separated `id`; `maxResults` is not supported together with `id` | https://developers.google.com/youtube/v3/docs/videos/list |
| Licence field | `status.license` is `creativeCommon` or `youtube` | https://developers.google.com/youtube/v3/docs/videos |
| Quota | `search.list` has its own bucket: 100 calls a day, 1 per call, each extra page counts. `videos.list` costs 1 unit from the 10,000-unit daily pool. Invalid requests also cost at least 1. Resets at midnight Pacific. | https://developers.google.com/youtube/data/determine_quota_cost |

Notes:

- The search quota changed from the long-standing "100 units per call out of 10,000". It is now a separate 100-calls-a-day bucket. Each configured keyword uses one search call per `clipforge discover` run.
- The limit of 50 IDs per `videos.list` call is not stated on the reference page; the client batches at 50, the usual value. Not confirmed against a live call.
- The key travels in the URL, so request URLs are never logged or put in error messages.
- Live status: requests from this repo reach the API and its error responses are handled (checked with an invalid key on 2026-11-20). No call with a valid key has been made yet.

## yt-dlp — downloads for policy-approved URLs

Checked: 2026-11-20, version 2026.10.26. Code: `clipforge/ingest/url.py`.

- Python API: `yt_dlp.YoutubeDL(options).download([url])` with `format`, `merge_output_format`, `outtmpl`, `noplaylist`, `js_runtimes`.
- YouTube extraction needs a JavaScript runtime on `PATH`. yt-dlp enables only deno by default; this project also enables node.
- yt-dlp is not an official YouTube API, and the Data API has no download endpoint. Downloads are limited by `ingest/policy.py` to allowlisted channels and uploads the API reports as Creative Commons. Whether that use fits YouTube's Terms of Service for your case is your call to make.
- From a cloud or datacentre IP, YouTube answers with "Sign in to confirm you're not a bot" (seen 2026-11-20). Run `clipforge ingest` from a home connection. This project does not pass account cookies to get around that check.
- Live status: no download has completed from this repo yet.

## Local libraries (no network API)

| Library | Version checked | Use |
| --- | --- | --- |
| faster-whisper | 1.2.1 | `WhisperModel.transcribe(path, word_timestamps=True)`. Downloads the model from Hugging Face on first use. |
| PySceneDetect | 0.7.3 | `detect(path, ContentDetector())`; timecodes read through `.seconds`. |
| ffmpeg | 6.1.1 | Filters used: `concat`, `ass`, `loudnorm`, `sidechaincompress`, `amix` with `normalize=0`, `alimiter`. |

## Not yet integrated

Reddit API, YouTube uploads, TikTok and Instagram publishing belong to M3–M5. Each needs its own entry here (scopes, audit status, private-only restrictions for unaudited apps, rate limits, synthetic-content disclosure fields) before any code is written.
