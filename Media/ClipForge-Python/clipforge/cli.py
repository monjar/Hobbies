"""clipforge command line."""

import json
from pathlib import Path
from typing import Annotated, Optional

import typer

from .config import load_settings
from .db import DB
from .errors import ClipforgeError
from .pipeline import Pipeline, build_providers
from .review import actions

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Gaming footage -> narrated vertical shorts.")

LiveOpt = Annotated[bool, typer.Option("--live", help="Use the real paid providers. Without it, offline mocks run.")]


def _fail(exc: ClipforgeError) -> None:
    typer.secho(f"error: {exc}", fg=typer.colors.RED, err=True)
    raise typer.Exit(1)


def _pipeline(live: bool) -> Pipeline:
    settings = load_settings()
    return Pipeline(settings, DB(settings.db_path), build_providers(settings, live))


def _report(pipeline: Pipeline, job_id: str) -> None:
    job = pipeline.db.job(job_id)
    render = pipeline.db.one("SELECT * FROM renders WHERE job_id = ?", job_id)
    typer.echo(f"job {job_id}: {job['state']}")
    if render:
        typer.echo(f"video:   {render['path']}")
        typer.echo(f"sidecar: {render['sidecar_path']}")
        typer.echo(f"cost:    ${pipeline.db.total_cost(job_id):.4f}")


@app.command()
def make(
    path: Annotated[Path, typer.Argument(help="Footage file in the inbox, or one downloaded by `clipforge ingest`.")],
    angle: Annotated[str, typer.Option("--angle", help="Narration angle from config/style.yaml.")],
    live: LiveOpt = False,
    template: Annotated[Optional[str], typer.Option(help="Visual template; rotated when omitted.")] = None,
    caption_style: Annotated[Optional[str], typer.Option(help="Caption style; rotated when omitted.")] = None,
    new: Annotated[bool, typer.Option("--new", help="Start a new job even if one exists for this file and angle.")] = False,
):
    """Turn one local file into a narrated vertical video waiting in the review queue."""
    try:
        pipeline = _pipeline(live)
        if not live:
            typer.secho(
                "Running with MOCK providers (placeholder script and tone narration, no spend). "
                "Pass --live for a real video.",
                fg=typer.colors.YELLOW,
                err=True,
            )
        job_id = pipeline.make(path, angle, live, template, caption_style, new)
        _report(pipeline, job_id)
    except ClipforgeError as exc:
        _fail(exc)


def _youtube(settings, on_call=None):
    from .discover.youtube import YouTubeClient

    return YouTubeClient(settings.secret("YOUTUBE_API_KEY"), on_call=on_call)


@app.command()
def discover():
    """Search YouTube for the configured keywords and store the results as topics. Metadata only."""
    from .discover.rank import discover as run_discover

    try:
        settings = load_settings()
        result = run_discover(DB(settings.db_path), settings, lambda on_call: _youtube(settings, on_call))
        quota = ", ".join(f"{bucket}: {units}" for bucket, units in result["quota"].items()) or "none"
        typer.echo(f"stored {result['topics']} topics; YouTube quota used this run - {quota}")
    except ClipforgeError as exc:
        _fail(exc)


@app.command()
def topics(
    limit: Annotated[int, typer.Option(help="How many topics to print.")] = 20,
    cc: Annotated[bool, typer.Option("--cc", help="Only Creative Commons uploads, which `ingest` may download.")] = False,
):
    """Print stored topics, fastest-growing first, with a suggested narration angle. No network."""
    from .discover.rank import ranked_topics, suggest_angle

    try:
        settings = load_settings()
        rows = ranked_topics(DB(settings.db_path), limit, creative_commons_only=cc)
        if not rows:
            typer.echo("no topics stored; run `clipforge discover` first")
        for rank, topic in enumerate(rows, 1):
            licence = "CC" if topic["licence"] == "creativeCommon" else "std"
            angle = suggest_angle(topic["title"], list(settings.style.angles))
            typer.echo(
                f"{rank:>2}. {topic['views_per_hour']:>9.0f} views/h  [{licence}] {topic['duration_s'] // 60:>3}m  {angle:<19} "
                f"{topic['title']}  https://www.youtube.com/watch?v={topic['video_id']}"
            )
    except ClipforgeError as exc:
        _fail(exc)


@app.command()
def ingest(url: Annotated[str, typer.Argument(help="A YouTube video URL.")]):
    """Download one video into the library, if the source policy allows it."""
    from .ingest.url import YtDlpDownloader, ingest_url

    try:
        settings = load_settings()
        db = DB(settings.db_path)
        source_id = ingest_url(db, settings, url, _youtube(settings).video, YtDlpDownloader())
        row = db.one("SELECT * FROM sources WHERE id = ?", source_id)
        typer.echo(f"allowed by: {row['policy_rule']}\nfile:       {row['path']}")
    except ClipforgeError as exc:
        _fail(exc)


@app.command("eval-highlights")
def eval_highlights(labels: Annotated[Path, typer.Argument(help="YAML file of clips and hand-labelled highlight spans.")]):
    """Check highlight ranking against hand labels: does the top window cover a labelled highlight?"""
    from .analyse.evaluate import HIGHLIGHT_PASS_RATE, evaluate_highlights, load_highlight_labels
    from .analyse.transcribe import build_transcriber
    from .pipeline import WINDOW_MARGIN_S

    try:
        settings = load_settings()
        out = settings.style.output
        results = evaluate_highlights(
            load_highlight_labels(labels), settings.root, build_transcriber(settings.providers.transcribe),
            out.min_s + WINDOW_MARGIN_S, out.max_s,
        )
    except ClipforgeError as exc:
        _fail(exc)
    for result in results:
        mark = "match" if result.matched else "MISS "
        typer.echo(f"{mark}  {result.top_window.start:7.1f}-{result.top_window.end:7.1f}s  {result.file}")
    matched = sum(r.matched for r in results)
    passed = matched >= HIGHLIGHT_PASS_RATE * len(results)
    typer.echo(f"{matched}/{len(results)} matched; needs {HIGHLIGHT_PASS_RATE:.0%} - {'PASS' if passed else 'FAIL'}")
    raise typer.Exit(0 if passed else 1)


@app.command("eval-spoilers")
def eval_spoilers(
    cases: Annotated[Path, typer.Argument(help="YAML file of labelled cases.")] = Path("tests/fixtures/spoilers.yaml"),
    live: LiveOpt = False,
):
    """Run the spoiler classifier over labelled cases. Fails if any spoiler would get through."""
    from .analyse.evaluate import evaluate_spoilers, load_spoiler_cases

    try:
        settings = load_settings()
        if not live:
            typer.secho("Mock provider labels everything 'none'; pass --live to test the real classifier.", fg=typer.colors.YELLOW, err=True)
        results, usd = evaluate_spoilers(load_spoiler_cases(cases), build_providers(settings, live).llm)
    except ClipforgeError as exc:
        _fail(exc)
    for result in results:
        mark = "LEAK " if result.leaked else ("ok   " if result.correct else "wrong")
        typer.echo(f"{mark}  expected {result.expected:<20} got {result.predicted:<20} {result.name}")
    leaks = sum(r.leaked for r in results)
    typer.echo(f"{sum(r.correct for r in results)}/{len(results)} correct, {leaks} spoilers let through, cost ${usd:.4f}")
    raise typer.Exit(1 if leaks else 0)


def _google_auth(settings):
    from .publish.google_auth import GoogleAuth

    return GoogleAuth(
        settings.secret("YOUTUBE_OAUTH_CLIENT_ID"), settings.secret("YOUTUBE_OAUTH_CLIENT_SECRET"),
        settings.root / "state" / "google_token.json",
    )


def _publisher(settings, platform: str):
    from .publish.export import ExportPublisher
    from .publish.instagram import InstagramPublisher
    from .publish.youtube import YouTubePublisher

    cfg = getattr(settings.publish, platform)
    if cfg.mode == "export":
        return ExportPublisher(platform, settings.out, getattr(cfg, "ai_disclosure", False))
    if platform == "youtube":
        # The token is fetched only when a request is really sent, so --dry-run needs no login.
        return YouTubePublisher(cfg, lambda: _google_auth(settings).access_token())
    return InstagramPublisher(cfg, settings.secret("INSTAGRAM_ACCESS_TOKEN"), settings.secret("INSTAGRAM_USER_ID"))


@app.command()
def auth(service: Annotated[str, typer.Argument(help="Only 'youtube' is supported.")]):
    """Connect a YouTube channel for uploads and analytics. Opens a Google consent page."""
    import webbrowser

    if service != "youtube":
        typer.secho("error: only `clipforge auth youtube` is supported", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    def show(url: str) -> None:
        typer.echo(f"Open this page and approve access:\n\n{url}\n")
        webbrowser.open(url)

    try:
        _google_auth(load_settings()).login(show)
        typer.echo("YouTube connected.")
    except ClipforgeError as exc:
        _fail(exc)


@app.command()
def publish(
    job_id: str,
    platform: Annotated[str, typer.Option("--platform", help="youtube, tiktok or instagram.")],
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Print the exact requests without sending anything.")] = False,
):
    """Publish an approved render to one platform, or export it for manual upload."""
    from .publish.base import PLATFORMS, publish_job

    try:
        if platform not in PLATFORMS:
            raise ClipforgeError(f"unknown platform {platform!r}; choose from {', '.join(PLATFORMS)}")
        settings = load_settings()
        result = publish_job(DB(settings.db_path), job_id, _publisher(settings, platform), dry_run)
    except ClipforgeError as exc:
        _fail(exc)
    if dry_run:
        typer.echo(json.dumps(result, indent=2))
    elif result.status == "exported":
        typer.echo(f"exported for manual upload:\n  {result.detail.get('video')}\n  {result.detail.get('caption')}")
    else:
        typer.echo(f"published to {platform}: {result.url or result.post_id}")


@app.command("metrics-pull")
def metrics_pull():
    """Store today's numbers for every post published to YouTube through the API."""
    from datetime import date

    from .metrics.pull import pull_youtube

    try:
        settings = load_settings()
        stored = pull_youtube(DB(settings.db_path), lambda: _google_auth(settings).access_token(), date.today())
        typer.echo(f"stored metrics for {stored} YouTube posts")
    except ClipforgeError as exc:
        _fail(exc)


@app.command("metrics-add")
def metrics_add(
    job_id: str,
    platform: Annotated[str, typer.Option("--platform")],
    views: Annotated[int, typer.Option("--views")],
    likes: Annotated[int, typer.Option("--likes")] = 0,
    shares: Annotated[int, typer.Option("--shares")] = 0,
    avg_view_s: Annotated[Optional[float], typer.Option("--avg-view-s", help="Average view duration in seconds.")] = None,
):
    """Enter numbers by hand for a post uploaded manually (TikTok, Instagram)."""
    from datetime import date

    from .metrics.pull import add_manual

    try:
        add_manual(DB(load_settings().db_path), job_id, platform, date.today(), views, likes, shares, avg_view_s)
        typer.echo("stored")
    except ClipforgeError as exc:
        _fail(exc)


@app.command()
def report(days: Annotated[int, typer.Option(help="How many days back to cover.")] = 7):
    """Print a markdown performance report from stored metrics and save it under out/reports/."""
    from datetime import date

    from .metrics.report import build_report

    try:
        settings = load_settings()
        text = build_report(DB(settings.db_path), date.today(), days)
    except ClipforgeError as exc:
        _fail(exc)
    dest = settings.out / "reports" / f"{date.today().isoformat()}.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text)
    typer.echo(text)


@app.command()
def run(
    jobs: Annotated[int, typer.Option("--jobs", help="How many videos to make at most.")] = 3,
    live: LiveOpt = False,
):
    """Unattended batch: discover, ingest allowed footage, make videos. Stops at the review queue."""
    from .discover.rank import discover as run_discover
    from .ingest.url import YtDlpDownloader, ingest_url
    from .notify import send_webhook
    from .scheduler import run_batch

    try:
        settings = load_settings()
        db = DB(settings.db_path)
        pipeline = Pipeline(settings, db, build_providers(settings, live))
        has_key = bool(settings.env.get("YOUTUBE_API_KEY"))
        webhook = settings.env.get("NOTIFY_WEBHOOK_URL")

        def ingest(url: str) -> Path:
            source_id = ingest_url(db, settings, url, _youtube(settings).video, YtDlpDownloader())
            return Path(db.one("SELECT path FROM sources WHERE id = ?", source_id)["path"])

        summary = run_batch(
            db, settings, pipeline, jobs, live,
            discover=(lambda: run_discover(db, settings, lambda on_call: _youtube(settings, on_call))) if has_key else None,
            ingest=ingest if has_key else None,
            notify=(lambda text: send_webhook(webhook, text)) if webhook else None,
        )
    except ClipforgeError as exc:
        _fail(exc)
    typer.echo(f"made {len(summary['made'])}, failed {len(summary['failed'])}, waiting for review {summary['pending_review']}")
    for item in summary["failed"]:
        typer.echo(f"  failed: {item['target']} - {item['reason']}")


@app.command()
def jobs(limit: Annotated[int, typer.Option(help="How many recent jobs to list.")] = 20):
    """List recent jobs and their states."""
    try:
        settings = load_settings()
        db = DB(settings.db_path)
        for job in db.all("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", limit):
            line = f"{job['id']}  {job['state']:<15} {'live' if job['live'] else 'mock'}  {job['angle']:<20} {Path(job['source_path']).name}"
            if job["fail_reason"]:
                line += f"  [{job['fail_reason']}]"
            typer.echo(line)
    except ClipforgeError as exc:
        _fail(exc)


@app.command()
def show(job_id: str):
    """Print a job's script, sources, spoiler verdict and cost as JSON."""
    try:
        settings = load_settings()
        db = DB(settings.db_path)
        render = db.one("SELECT sidecar_path FROM renders WHERE job_id = ?", job_id)
        script = db.one("SELECT body FROM scripts WHERE job_id = ?", job_id)
        typer.echo(
            json.dumps(
                {
                    "job": dict(db.job(job_id)),
                    "script": json.loads(script["body"]) if script else None,
                    "sidecar": json.loads(Path(render["sidecar_path"]).read_text()) if render else None,
                },
                indent=2,
            )
        )
    except ClipforgeError as exc:
        _fail(exc)


@app.command()
def approve(job_id: str):
    """Approve a render that is pending review."""
    try:
        actions.approve(DB(load_settings().db_path), job_id)
        typer.echo(f"job {job_id}: approved")
    except ClipforgeError as exc:
        _fail(exc)


@app.command()
def reject(job_id: str, reason: Annotated[str, typer.Option("--reason", help="Why; fed to later script prompts.")]):
    """Reject a render that is pending review."""
    try:
        actions.reject(DB(load_settings().db_path), job_id, reason)
        typer.echo(f"job {job_id}: rejected")
    except ClipforgeError as exc:
        _fail(exc)


@app.command()
def regenerate(
    job_id: str,
    feedback: Annotated[str, typer.Option("--feedback", help="What the new script should do differently.")] = "",
):
    """Reject a pending render and make a new one from the same moment with a fresh script."""
    try:
        settings = load_settings()
        db = DB(settings.db_path)
        pipeline = Pipeline(settings, db, build_providers(settings, bool(db.job(job_id)["live"])))
        new_id = pipeline.regenerate(job_id, feedback)
        pipeline.run(new_id)
        _report(pipeline, new_id)
    except ClipforgeError as exc:
        _fail(exc)


@app.command()
def review(
    lan: Annotated[bool, typer.Option("--lan", help="Listen on all interfaces so a phone on the network can connect.")] = False,
    port: Annotated[int, typer.Option(help="Port to listen on.")] = 8000,
):
    """Serve the review queue."""
    import uvicorn

    from .review.app import create_app

    try:
        settings = load_settings()
    except ClipforgeError as exc:
        _fail(exc)
    host = "0.0.0.0" if lan else "127.0.0.1"
    if lan:
        typer.secho("Listening on all interfaces with no login: use only on a network you trust.", fg=typer.colors.YELLOW, err=True)
    uvicorn.run(create_app(settings), host=host, port=port, log_level="warning")
