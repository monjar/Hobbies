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
    path: Annotated[Path, typer.Argument(help="Footage file inside the inbox folder.")],
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
