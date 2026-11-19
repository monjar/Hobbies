"""Review queue: a small server-rendered site that works in a phone browser."""

import json
import threading
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..config import Settings
from ..db import DB
from ..errors import ClipforgeError
from ..log import log
from ..pipeline import Pipeline, build_providers
from . import actions

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(
    settings: Settings,
    pipeline_factory: Callable[[bool], Pipeline] | None = None,
    background: bool = True,
) -> FastAPI:
    """`pipeline_factory(live)` builds the pipeline used by Regenerate; tests inject mocks.

    With background=False a regenerated job runs inside the request instead of a thread.
    """
    app = FastAPI(title="clipforge review", docs_url=None, redoc_url=None, openapi_url=None)

    def db() -> DB:
        return DB(settings.db_path)  # one connection per request or worker thread

    def make_pipeline(live: bool) -> Pipeline:
        if pipeline_factory:
            return pipeline_factory(live)
        return Pipeline(settings, db(), build_providers(settings, live))

    def same_origin(request: Request) -> None:
        # Blocks other websites from submitting these forms through the reviewer's browser.
        origin = request.headers.get("origin")
        if origin and urlsplit(origin).netloc != request.headers.get("host"):
            raise HTTPException(403, "cross-site request refused")

    def job_context(database: DB, job_id: str) -> dict:
        try:
            job = database.job(job_id)
        except ClipforgeError as exc:
            raise HTTPException(404, str(exc)) from exc
        script = database.one("SELECT * FROM scripts WHERE job_id = ?", job_id)
        render = database.one("SELECT * FROM renders WHERE job_id = ?", job_id)
        sidecar = None
        if render and Path(render["sidecar_path"]).is_file():
            sidecar = json.loads(Path(render["sidecar_path"]).read_text())
        return {
            "job": job,
            "script": json.loads(script["body"]) if script else None,
            "moment": database.one("SELECT * FROM moments WHERE job_id = ?", job_id),
            "source": database.one("SELECT * FROM sources WHERE id = ?", job["source_id"]),
            "render": render,
            "sidecar": sidecar,
            "costs": database.all("SELECT * FROM costs WHERE job_id = ? ORDER BY id", job_id),
            "total_cost": database.total_cost(job_id),
            "cap": settings.max_cost_usd,
            "reviews": database.all("SELECT * FROM reviews WHERE job_id = ? ORDER BY id", job_id),
            "source_name": Path(job["source_path"]).name,
        }

    def detail(request: Request, job_id: str, error: str | None = None, status: int = 200):
        context = job_context(db(), job_id)
        return TEMPLATES.TemplateResponse(request, "job.html", {**context, "error": error}, status_code=status)

    @app.get("/")
    def queue(request: Request):
        database = db()
        pending = database.all("SELECT * FROM jobs WHERE state = 'pending_review' ORDER BY created_at")
        others = database.all("SELECT * FROM jobs WHERE state != 'pending_review' ORDER BY updated_at DESC LIMIT 30")
        name = lambda job: Path(job["source_path"]).name  # noqa: E731
        return TEMPLATES.TemplateResponse(request, "queue.html", {"pending": pending, "others": others, "name": name})

    @app.get("/job/{job_id}")
    def job_page(request: Request, job_id: str):
        return detail(request, job_id)

    @app.get("/media/{job_id}")
    def media(job_id: str):
        render = db().one("SELECT path FROM renders WHERE job_id = ?", job_id)
        if not render or not Path(render["path"]).is_file():
            raise HTTPException(404, "no render for this job")
        return FileResponse(render["path"], media_type="video/mp4")

    @app.post("/job/{job_id}/approve")
    def approve(request: Request, job_id: str):
        same_origin(request)
        try:
            actions.approve(db(), job_id)
        except ClipforgeError as exc:
            return detail(request, job_id, str(exc), 400)
        return RedirectResponse("/", status_code=303)

    @app.post("/job/{job_id}/reject")
    def reject(request: Request, job_id: str, reason: str = Form("")):
        same_origin(request)
        try:
            actions.reject(db(), job_id, reason)
        except ClipforgeError as exc:
            return detail(request, job_id, str(exc), 400)
        return RedirectResponse("/", status_code=303)

    @app.post("/job/{job_id}/regenerate")
    def regenerate(request: Request, job_id: str, feedback: str = Form("")):
        same_origin(request)
        try:
            pipeline = make_pipeline(bool(db().job(job_id)["live"]))
            new_id = pipeline.regenerate(job_id, feedback)
        except ClipforgeError as exc:
            return detail(request, job_id, str(exc), 400)

        def work() -> None:
            try:
                pipeline.run(new_id)
            except ClipforgeError:
                pass  # run() already marked the job failed and logged the reason
            except Exception as exc:  # keep the worker thread from dying silently
                log("regenerate_crashed", new_id, level="error", reason=repr(exc))

        if background:
            threading.Thread(target=work, daemon=True).start()
        else:
            work()
        return RedirectResponse(f"/job/{new_id}", status_code=303)

    return app
