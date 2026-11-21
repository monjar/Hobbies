"""Stage orchestration. Each stage is resumable: it runs only when the job is in its input state."""

import json
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from .analyse import dedup, spoiler
from .analyse.audio import energy_per_second
from .analyse.highlights import candidate_windows, pick_window
from .analyse.scenes import detect_cuts
from .analyse.transcribe import Transcriber, build_transcriber
from .config import Settings
from .costs import CostTracker
from .db import DB
from .errors import ClipforgeError, ConfigError, SpoilerRejected, ValidationFailed
from .ingest.local import ingest_local, sha256_file
from .discover.rank import topic_context
from .ingest.policy import check_library, check_local
from .log import log
from .media import probe
from .metrics.report import top_scripts
from .providers.base import LLMProvider, VisionProvider
from .render.captions import render_ass
from .render.cut import plan_segments
from .render.mix import build_narration
from .render.reframe import render_video
from .render.subject import crop_fraction, motion_focus
from .render.templates import FALLBACK_TEMPLATE, SUBJECT_AWARE
from .render.validate import check_output, check_plan
from .script.validate import estimate_speech_s, narration_lines
from .script.writer import write_script
from .types import Span, Word
from .voice.tts import TTSProvider, layout_narration, timeline_words

# Jump cuts and dropped fragments shorten a window, so candidates are picked this much longer
# than the minimum render length.
WINDOW_MARGIN_S = 3.0
PAST_REJECTIONS = 5


@dataclass
class Providers:
    llm: LLMProvider
    vision: VisionProvider
    tts: TTSProvider
    transcriber: Transcriber


def build_providers(settings: Settings, live: bool) -> Providers:
    """Real paid providers only with live=True; otherwise offline mocks."""
    transcriber = build_transcriber(settings.providers.transcribe)
    if not live:
        from .providers.mock import MockLLM, MockTTS, MockVision

        wps = settings.style.speech_wps
        return Providers(MockLLM(wps), MockVision(), MockTTS(wps), transcriber)

    from .providers.anthropic_provider import AnthropicProvider
    from .voice.providers.elevenlabs import ElevenLabsTTS

    cfg = settings.providers
    return Providers(
        llm=AnthropicProvider(settings.secret("LLM_API_KEY"), cfg.llm),
        vision=AnthropicProvider(settings.secret("VISION_API_KEY"), cfg.vision),
        tts=ElevenLabsTTS(settings.secret("TTS_API_KEY"), cfg.tts),
        transcriber=transcriber,
    )


class Pipeline:
    def __init__(self, settings: Settings, db: DB, providers: Providers):
        self.settings = settings
        self.db = db
        self.providers = providers
        self.stages = {
            "discovered": self._ingest,
            "ingested": self._analyse,
            "analysed": self._script,
            "scripted": self._voice,
            "voiced": self._render,
            "rendered": self._queue,
        }

    # -- entry points ------------------------------------------------------
    def make(
        self,
        path: Path | str,
        angle: str,
        live: bool,
        template: str | None = None,
        caption_style: str | None = None,
        new: bool = False,
    ) -> str:
        """Create (or resume) the job for a local file and run it to pending_review."""
        style = self.settings.style
        if angle not in style.angles:
            raise ConfigError(f"unknown angle {angle!r}; configured angles: {', '.join(style.angles)}")
        caption_names = [c.name for c in style.caption_styles]
        if template and template not in style.templates:
            raise ConfigError(f"unknown template {template!r}; configured: {', '.join(style.templates)}")
        if caption_style and caption_style not in caption_names:
            raise ConfigError(f"unknown caption style {caption_style!r}; configured: {', '.join(caption_names)}")

        self._check_source(path)  # nothing is written for a denied source
        source = Path(path).expanduser().resolve()
        sha = sha256_file(source)

        existing = None
        if not new:
            existing = self.db.one(
                "SELECT id FROM jobs WHERE source_sha256 = ? AND angle = ? AND live = ? "
                "AND state NOT IN ('failed', 'rejected') ORDER BY created_at DESC LIMIT 1",
                sha, angle, int(live),
            )
        if existing:
            job_id = existing["id"]
            log("job_resumed", job_id, state=self.db.job(job_id)["state"])
        else:
            job_id = self._create_job(source, sha, angle, live, template, caption_style)
        self.run(job_id)
        return job_id

    def _check_source(self, path: Path | str) -> None:
        """Allow a file in the inbox, or a library file recorded by a policy-approved URL ingest."""
        local = check_local(path, self.settings)
        if local.allowed:
            return
        resolved = Path(path).expanduser().resolve()
        if resolved.is_file() and resolved.is_relative_to(self.settings.library):
            check_library(resolved, self.settings, self.db, sha256_file(resolved)).require()
            return
        local.require()

    def _create_job(self, source, sha, angle, live, template=None, caption_style=None, parent=None) -> str:
        style = self.settings.style
        rotation = self.db.one("SELECT COUNT(*) FROM jobs")[0]  # rotates templates and caption styles
        job_id = self.db.create_job(
            angle=angle,
            live=int(live),
            source_path=str(source),
            source_sha256=sha,
            template=template or style.templates[rotation % len(style.templates)],
            caption_style=caption_style or style.caption_styles[rotation % len(style.caption_styles)].name,
            parent_job_id=parent,
        )
        log("job_created", job_id, source=str(source), angle=angle, live=live)
        return job_id

    def run(self, job_id: str) -> None:
        """Advance a job stage by stage until it reaches a state no stage handles."""
        while True:
            job = self.db.job(job_id)
            stage = self.stages.get(job["state"])
            if stage is None:
                return
            log("stage_start", job_id, stage=stage.__name__.lstrip("_"), state=job["state"])
            try:
                stage(job)
            except Exception as exc:
                reason = f"{type(exc).__name__}: {exc}"
                self.db.transition(job_id, "failed", reason)
                log("job_failed", job_id, level="error", reason=reason)
                if isinstance(exc, ClipforgeError):
                    raise
                raise ClipforgeError(reason) from exc

    def regenerate(self, job_id: str, feedback: str = "") -> str:
        """Reject a pending render and start a new job that reuses its moment with a new script."""
        from .review.actions import reject

        parent = self.db.job(job_id)
        reason = "regenerate script" + (f": {feedback.strip()}" if feedback.strip() else "")
        reject(self.db, job_id, reason)
        new_id = self._create_job(
            Path(parent["source_path"]), parent["source_sha256"], parent["angle"], bool(parent["live"]), parent=job_id
        )
        moment = dict(self.db.one("SELECT * FROM moments WHERE job_id = ?", job_id))
        moment.pop("id")
        moment["job_id"] = new_id
        self.db.insert("moments", **moment)
        for row in self.db.all("SELECT hash FROM keyframe_hashes WHERE job_id = ? ORDER BY id", job_id):
            self.db.insert("keyframe_hashes", job_id=new_id, hash=row["hash"])
        self.db.set_job(new_id, source_id=parent["source_id"])
        self.db.transition(new_id, "ingested")
        self.db.transition(new_id, "analysed")
        return new_id

    # -- helpers -----------------------------------------------------------
    def _work(self, job_id: str) -> Path:
        path = self.settings.work / job_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _costs(self, job_id: str) -> CostTracker:
        return CostTracker(self.db, job_id, self.settings.max_cost_usd)

    def _replace(self, table: str, job_id: str, **cols) -> None:
        self.db.conn.execute(f"DELETE FROM {table} WHERE job_id = ?", (job_id,))
        self.db.insert(table, job_id=job_id, **cols)

    # -- stages ------------------------------------------------------------
    def _ingest(self, job) -> None:
        source = Path(job["source_path"])
        if source.is_relative_to(self.settings.library):
            # Already downloaded under the URL policy; re-check that the record still matches.
            check_library(source, self.settings, self.db, job["source_sha256"]).require()
            source_id = self.db.one("SELECT id FROM sources WHERE kind = 'url' AND path = ?", str(source))["id"]
        else:
            source_id = ingest_local(self.db, self.settings, source, job["source_sha256"])
        self.db.set_job(job["id"], source_id=source_id)
        self.db.transition(job["id"], "ingested")

    def _analyse(self, job) -> None:
        job_id, source, out = job["id"], Path(job["source_path"]), self.settings.style.output
        work, costs = self._work(job_id), self._costs(job_id)
        info = probe(source)
        words = self.providers.transcriber.transcribe(source)
        cuts = detect_cuts(source)
        (work / "analysis.json").write_text(json.dumps({"cuts": cuts, "words": [w.to_dict() for w in words]}))
        log("analysed_source", job_id, duration_s=info.duration, cuts=len(cuts), words=len(words))

        energy = energy_per_second(source)
        ranked = candidate_windows(
            info.duration, cuts, words, out.min_s + WINDOW_MARGIN_S, out.max_s, energy=energy
        )
        # Refuse footage that is already in another render; fall through to the next-best window.
        candidates, hashes = [], {}
        for candidate in ranked:
            window = dedup.window_hashes(source, candidate.span)
            twin = dedup.duplicate_of(self.db, window, exclude_job=job_id)
            if twin:
                log("duplicate_window", job_id, level="warning", start_s=candidate.span.start, duplicate_of=twin)
            else:
                candidates.append(candidate)
                hashes[candidate.span] = window
        if not candidates:
            raise ValidationFailed(["every candidate window duplicates footage already used in another render"])
        pick = pick_window(
            source=source,
            candidates=candidates,
            words=words,
            angle=job["angle"],
            llm=self.providers.llm,
            vision=self.providers.vision,
            costs=costs,
            frames_dir=work / "frames",
        )
        verdict = spoiler.classify(
            description=pick.description, transcript=pick.transcript, llm=self.providers.llm, costs=costs
        )
        segments = plan_segments(pick.candidate.span, cuts)
        dedup.store_hashes(self.db, job_id, hashes[pick.candidate.span])
        self._replace(
            "moments",
            job_id,
            start_s=pick.candidate.span.start,
            end_s=pick.candidate.span.end,
            segments=json.dumps([s.to_dict() for s in segments]),
            description=pick.description,
            transcript=pick.transcript,
            pick_reason=pick.reason,
            spoiler_label=verdict.label,
            spoiler_reason=verdict.reason,
        )
        if verdict.blocked:
            raise SpoilerRejected(f"spoiler gate: {verdict.label} ({verdict.reason})")
        render_s = sum(s.length for s in segments)
        if not out.min_s <= render_s <= out.max_s:  # caught here, before any script or voice spend
            raise ValidationFailed(
                [f"planned render is {render_s:.1f}s; allowed range is {out.min_s:.0f}-{out.max_s:.0f}s"]
            )
        self.db.transition(job_id, "analysed")

    def _moment(self, job_id: str) -> tuple[dict, list[Span]]:
        moment = dict(self.db.one("SELECT * FROM moments WHERE job_id = ?", job_id))
        return moment, [Span(**s) for s in json.loads(moment["segments"])]

    def _script(self, job) -> None:
        job_id, style = job["id"], self.settings.style
        moment, segments = self._moment(job_id)
        rejections = [
            row["reason"]
            for row in self.db.all(
                "SELECT reason FROM reviews WHERE action = 'reject' AND reason IS NOT NULL ORDER BY id DESC LIMIT ?",
                PAST_REJECTIONS,
            )
        ]
        script, attempts = write_script(
            job_id=job_id,
            angle=job["angle"],
            angle_brief=style.angles[job["angle"]],
            description=moment["description"],
            transcript=moment["transcript"],
            render_s=sum(s.length for s in segments),
            wps=style.speech_wps,
            llm=self.providers.llm,
            costs=self._costs(job_id),
            rejections=rejections,
            topic_context=topic_context(self.db),
            winners=top_scripts(self.db),
        )
        self._replace(
            "scripts",
            job_id,
            body=json.dumps(script),
            est_speech_s=estimate_speech_s(script, style.speech_wps),
            attempts=attempts,
            created_at=time.time(),
        )
        self.db.transition(job_id, "scripted")

    def _voice(self, job) -> None:
        job_id, tts = job["id"], self.providers.tts
        costs, voice_dir = self._costs(job_id), self._work(job_id) / "voice"
        script = json.loads(self.db.one("SELECT body FROM scripts WHERE job_id = ?", job_id)["body"])
        lines = narration_lines(script)
        costs.reserve(sum(tts.estimate_usd(text) for text, _ in lines), "narration")
        voiced, previous = [], None
        for index, (text, target) in enumerate(lines):
            result = tts.synthesize(text, voice_dir / f"line_{index:02d}", previous_text=previous)
            costs.add(tts.name, "narration", tts.model, result.usd, {"chars": result.chars})
            voiced.append(
                {
                    "text": text,
                    "target": target,
                    "path": str(result.path),
                    "duration": result.duration,
                    "words": [w.to_dict() for w in result.words],
                }
            )
            previous = text
        (self._work(job_id) / "voice.json").write_text(json.dumps(voiced))
        self.db.transition(job_id, "voiced")

    def _render(self, job) -> None:
        job_id, style = job["id"], self.settings.style
        out, work = style.output, self._work(job_id)
        moment, segments = self._moment(job_id)
        script = json.loads(self.db.one("SELECT body FROM scripts WHERE job_id = ?", job_id)["body"])
        voiced = json.loads((work / "voice.json").read_text())
        source = Path(job["source_path"])

        render_s = sum(s.length for s in segments)
        durations = [line["duration"] for line in voiced]
        layout = layout_narration(durations, [line["target"] for line in voiced], render_s)
        spans = [layout.span(i, d) for i, d in enumerate(durations)]
        report = check_plan(segments, spans, out)  # constraint 3, before any encoding

        flags = []
        if layout.packed:
            flags.append(f"narration sped up {layout.tempo:.2f}x and packed back to back to fit the clip")
            log("narration_tempo", job_id, level="warning", tempo=round(layout.tempo, 3))
        if not job["live"]:
            flags.append("produced with mock providers; cannot be approved or published")

        words = timeline_words([[Word(**w) for w in line["words"]] for line in voiced], layout)
        caption_style = next(c for c in style.caption_styles if c.name == job["caption_style"])
        (work / "captions.ass").write_text(render_ass(words, caption_style, out.width, out.height))
        narration, lufs = build_narration(
            [(Path(line["path"]), start) for line, start in zip(voiced, layout.starts)], layout.tempo, render_s, work
        )
        source_info = probe(source)
        template, crop_centres = job["template"], None
        if template in SUBJECT_AWARE:
            fraction = crop_fraction(source_info.width, source_info.height, out.width, out.height)
            crop_centres = [motion_focus(source, segment, fraction) for segment in segments]
            if any(centre is None for centre in crop_centres):
                flags.append(f"no clear subject to follow in every segment; used {FALLBACK_TEMPLATE} instead of {template}")
                log("subject_crop_fallback", job_id, level="warning", template=FALLBACK_TEMPLATE)
                template, crop_centres = FALLBACK_TEMPLATE, None
        staged = work / "render.mp4"
        render_video(
            source=source,
            source_has_audio=source_info.has_audio,
            segments=segments,
            narration=narration,
            captions_name="captions.ass",
            template=template,
            out=out,
            game_gain_db=style.game_gain_db,
            dest=staged,
            work_dir=work,
            crop_centres=crop_centres,
        )
        check_output(staged, out, render_s)

        stem = f"{time.strftime('%Y%m%d')}_{job_id}" + ("" if job["live"] else "_MOCK")
        final = self.settings.out / f"{stem}.mp4"
        sidecar_path = self.settings.out / f"{stem}.json"
        final.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(staged, final)

        source_row = dict(self.db.one("SELECT * FROM sources WHERE id = ?", job["source_id"]))
        description, credit = script["description"], None
        if source_row["kind"] == "url":
            credit = f"Footage: {source_row['channel']} - {source_row['url']}"
            if source_row["licence"] == "creativeCommon":
                credit += " (Creative Commons Attribution)"
            description = f"{description}\n\n{credit}"
        cost_rows = self.db.all("SELECT provider, task, model, usd FROM costs WHERE job_id = ? ORDER BY id", job_id)
        sidecar = {
            "job_id": job_id,
            "title": script["title"],
            "description": description,
            "hashtags": script["hashtags"],
            "source_attribution": {
                "credit": credit,
                "kind": source_row["kind"],
                "path": source_row["path"],
                "url": source_row["url"],
                "channel": source_row["channel"],
                "licence": source_row["licence"],
                "policy_rule": source_row["policy_rule"],
                "sha256": source_row["sha256"],
                "window_s": [moment["start_s"], moment["end_s"]],
                "segments_s": [[s.start, s.end] for s in segments],
            },
            "cost": {
                "total_usd": round(self.db.total_cost(job_id), 6),
                "cap_usd": self.settings.max_cost_usd,
                "items": [dict(row) for row in cost_rows],
            },
            "spoiler": {"label": moment["spoiler_label"], "reason": moment["spoiler_reason"]},
            "angle": job["angle"],
            "template": template,
            "caption_style": job["caption_style"],
            "duration_s": round(report.render_s, 3),
            "narration_coverage": round(report.narration_coverage, 4),
            "max_source_segment_s": round(report.max_source_segment_s, 3),
            "narration_lufs": round(lufs, 2),
            "live_providers": bool(job["live"]),
            "flags": flags,
        }
        sidecar_path.write_text(json.dumps(sidecar, indent=2))
        self._replace(
            "renders",
            job_id,
            path=str(final),
            sidecar_path=str(sidecar_path),
            duration_s=report.render_s,
            narration_coverage=report.narration_coverage,
            max_source_segment_s=report.max_source_segment_s,
            created_at=time.time(),
        )
        self.db.transition(job_id, "rendered")

    def _queue(self, job) -> None:
        # Every render stops here. Only an explicit approve action moves it on.
        self.db.transition(job["id"], "pending_review")
