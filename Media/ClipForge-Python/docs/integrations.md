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

- The search quota changed from the long-standing "100 units per call out of 10,000". It is now a separate 100-calls-a-day bucket. Each configured keyword uses two search calls per `clipforge discover` run (trending, then Creative Commons only).
- The limit of 50 IDs per `videos.list` call is not stated on the reference page; the client batches at 50, the usual value. Not confirmed against a live call.
- The key travels in the URL, so request URLs are never logged or put in error messages.
- Live status: `search.list` and `videos.list` work with a real key (2026-11-21). The `videoLicense=creativeCommon` filter and `status.license` agreed on all 50 results checked. No batch above 50 IDs has been sent.

## yt-dlp — downloads for policy-approved URLs

Checked: 2026-11-20, version 2026.10.26. Code: `clipforge/ingest/url.py`.

- Python API: `yt_dlp.YoutubeDL(options).download([url])` with `format`, `merge_output_format`, `outtmpl`, `noplaylist`, `js_runtimes`.
- YouTube extraction needs a JavaScript runtime on `PATH`. yt-dlp enables only deno by default; this project also enables node.
- yt-dlp is not an official YouTube API, and the Data API has no download endpoint. Downloads are limited by `ingest/policy.py` to allowlisted channels and uploads the API reports as Creative Commons. Whether that use fits YouTube's Terms of Service for your case is your call to make.
- From a cloud or datacentre IP, YouTube answers with "Sign in to confirm you're not a bot" (seen 2026-11-20). Run `clipforge ingest` from a home connection. This project does not pass account cookies to get around that check.
- Live status: the policy lookup works against the real API. No download has completed: every attempt from the cloud build machine hit the bot check (2026-11-21).

## Local libraries (no network API)

| Library | Version checked | Use |
| --- | --- | --- |
| faster-whisper | 1.2.1 | `WhisperModel.transcribe(samples, word_timestamps=True)` on audio decoded by ffmpeg (16 kHz mono float). Its own file decoding fails with PyAV 19 (`metadata_errors` argument removed). Downloads the model on first use from `huggingface.co`, `cas-server.xethub.hf.co` and `us.aws.cdn.hf.co`. Ran for real on 2026-11-21 with the `small` model. |
| PySceneDetect | 0.7.3 | `detect(path, ContentDetector())`; timecodes read through `.seconds`. |
| ffmpeg | 6.1.1 | Filters used: `concat`, `ass`, `loudnorm`, `sidechaincompress`, `amix` with `normalize=0`, `alimiter`. |

## Google OAuth for installed apps — YouTube uploads and analytics

Checked: 2026-11-21. Code: `clipforge/publish/google_auth.py`.

| What | Value | Source |
| --- | --- | --- |
| Consent page | `https://accounts.google.com/o/oauth2/v2/auth` with `client_id`, `redirect_uri`, `response_type=code`, `scope`, `code_challenge`, `code_challenge_method=S256`, `state` | https://developers.google.com/youtube/v3/guides/auth/installed-apps |
| Redirect | Loopback: `http://127.0.0.1:<port>` on a random free port (recommended for desktop apps) | same page |
| Token exchange | `POST https://oauth2.googleapis.com/token` with `code`, `code_verifier`, `grant_type=authorization_code`, `redirect_uri`, `client_id`, `client_secret` | same page |
| Refresh | Same endpoint with `refresh_token` and `grant_type=refresh_token` | same page |

Notes:

- `access_type=offline` and `prompt=consent` are sent to get a refresh token. These two parameters come from Google's general OAuth documentation, not the excerpt checked here.
- The refresh token is stored in `state/google_token.json` with mode 600. DPoP, which Google describes as optional, is not used.
- Scopes requested: `youtube.upload`, `youtube.readonly`, `yt-analytics.readonly`.
- Live status: the flow has only run against a stub. No real consent has been completed.

## YouTube uploads — `videos.insert`

Checked: 2026-11-21. Code: `clipforge/publish/youtube.py`.

| What | Value | Source |
| --- | --- | --- |
| Scope | `https://www.googleapis.com/auth/youtube.upload` | https://developers.google.com/youtube/v3/docs/videos/insert |
| Start | `POST https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status` with a JSON video resource and `X-Upload-Content-Length`, `X-Upload-Content-Type` headers; the session URL comes back in `Location` | https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol |
| Transfer | `PUT <session URL>` with the file bytes; success is `201` with the video resource | same page |
| Audit status | Since 2 November 2026, uploads from unverified API projects are **not** locked to private. Before that they were. | https://developers.google.com/youtube/data/revision_history |
| Default privacy | `public` when `status.privacyStatus` is omitted; this project always sends it | `videos.insert` page |
| Daily cap | 100 `videos.insert` calls a day in their own quota bucket | https://developers.google.com/youtube/data/determine_quota_cost |
| Disclosure | `status.containsSyntheticMedia` discloses *realistic* altered or synthetic content | https://developers.google.com/youtube/v3/docs/videos |
| Audience | `status.selfDeclaredMadeForKids`, sent as `false` | same page |

Notes:

- `containsSyntheticMedia` defaults to `false` in `config/publish.yaml`. YouTube's examples are a real person made to say something, altered footage of a real event, or a realistic scene that did not happen. A synthetic narrator over game footage does not obviously fit those, but it is your call; set it to `true` if you clone a real person's voice.
- One older reference page still says unverified projects are locked to private. The revision history and the `videos.insert` page both say the lock is gone.
- The file is sent in one `PUT`. Renders are a few megabytes, so chunked resume is not implemented.
- Live status: tested against a stub only. No real upload has been made.

## YouTube Analytics API — metrics

Checked: 2026-11-21. Code: `clipforge/metrics/pull.py`.

| What | Value | Source |
| --- | --- | --- |
| Endpoint | `GET https://youtubeanalytics.googleapis.com/v2/reports` | https://developers.google.com/youtube/analytics/reference |
| Scope | `https://www.googleapis.com/auth/yt-analytics.readonly` | https://developers.google.com/youtube/analytics/reference/reports/query |
| Parameters | `ids=channel==MINE`, `startDate`, `endDate`, `metrics`, `dimensions=video`, `filters=video==id1,id2` (up to 500 IDs) | same page and https://developers.google.com/youtube/analytics/dimensions |
| Metrics used | `views`, `likes`, `shares`, `averageViewDuration` | https://developers.google.com/youtube/analytics/metrics |

Notes:

- `sort=-views` and `maxResults=200` are sent because per-video reports have required a sort and a row limit. That requirement and the response shape (`columnHeaders[].name`, `rows`) were not in the pages checked; confirm on the first real pull.
- Live status: tested against a stub only.

## Instagram content publishing — Reels

Checked: 2026-11-21. Code: `clipforge/publish/instagram.py`. Off by default (`mode: export`).

| What | Value | Source |
| --- | --- | --- |
| Create container | `POST https://graph.facebook.com/<version>/<IG_USER_ID>/media` with `media_type=REELS`, `upload_type=resumable`, `caption`; returns `id` and `uri` | https://developers.facebook.com/docs/instagram-platform/content-publishing/resumable-uploads |
| Upload | `POST https://rupload.facebook.com/ig-api-upload/<version>/<container>` with headers `Authorization: OAuth <token>`, `offset: 0`, `file_size`, and the file bytes | same page |
| Status | `GET /<container>?fields=status_code` until `FINISHED` (other values: `IN_PROGRESS`, `ERROR`, `EXPIRED`, `PUBLISHED`) | same page |
| Publish | `POST /<IG_USER_ID>/media_publish` with `creation_id` | same page |
| Permissions | `instagram_basic`, `instagram_content_publish`, `pages_read_engagement`; a professional account linked to a Facebook Page | https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media |
| Limits | 100 API-published posts per 24 hours (one page says 50); containers expire after 24 hours | https://developers.facebook.com/docs/instagram-platform/instagram-api-with-facebook-login/content-publishing |
| Disclosure | `is_ai_generated=true` on the container | listed on one version of the resumable-uploads page and not the other |

Notes:

- The resumable upload is documented as "only for apps that have implemented Facebook Login for Business". The alternative is `video_url`, which needs the file on a public server.
- App review is needed for accounts without a role on your app. Publishing to your own account from your own app in development mode normally does not need it; that is from general Meta platform rules, not the pages checked here.
- `permalink` is requested after publishing for the post URL. If that lookup fails the post is still recorded, without a URL.
- Live status: tested against a stub only.

## TikTok Content Posting API — not used; export instead

Checked: 2026-11-21. Sources: https://developers.tiktok.com/docs/en/content-sharing-guidelines and https://developers.tiktok.com/docs/en/content-posting-api-reference-direct-post

- Unaudited API clients can post only as `SELF_ONLY`, the posting account must be set to private, and at most 5 users may post per 24 hours.
- Passing the audit is not realistic for this project. TikTok's guidelines list as not acceptable: "A utility tool to help upload contents to the account(s) you or your team manages", and "An app that copies arbitrary contents from other platforms to TikTok".
- So `clipforge publish --platform tiktok` writes the MP4 and a caption file to `out/export/<date>/tiktok/` for manual upload, as PLAN.md M5 prescribes when an audit blocks public posting.
- For reference if this changes: endpoint `/v2/post/publish/video/init/`, scope `video.publish`, 6 requests a minute per user token, about 15 posts a day per creator, and `post_info.is_aigc` for the AI-generated label.

## Reddit Data API — not used

Checked: 2026-11-21. Sources: https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy and https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki

- Self-service API access ended in November 2025. Access now needs explicit approval, and unauthenticated traffic is blocked.
- A third-party summary of a Reddit announcement (not confirmed on a Reddit page here) says new public API access requests stopped on 31 October 2026 and remaining public access closes in March 2027.
- Reddit discovery from PLAN.md M3 is therefore not built. If you already hold approved credentials, the calls would be an app-only OAuth token, then `GET https://oauth.reddit.com/r/<subreddit>/hot`, with a descriptive User-Agent and a limit of 100 requests a minute.
