"""FastAPI application: API + server-rendered pages."""
from __future__ import annotations

import os
import re
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from clipmaker import __version__
from clipmaker.config import Settings, get_settings
from clipmaker.db import make_engine, ping
from clipmaker.jobs import Runner, Worker, default_runner, enqueue, project_dir
from clipmaker.store import Job, Project, init_db, session_factory

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}
CHUNK = 1024 * 1024


def _safe_name(name: str) -> str:
    stem = re.sub(r"[^\w.\- ]+", "_", Path(name or "video").name, flags=re.UNICODE).strip(" .")
    return stem[:200] or "video"


def create_app(settings: Settings | None = None, runner: Runner = default_runner) -> FastAPI:
    settings = settings or get_settings()
    engine = make_engine(settings.database_url)
    init_db(engine)
    Session = session_factory(engine)
    worker = Worker(engine, settings.data_dir, runner)
    max_upload = int(os.environ.get("CLIPMAKER_MAX_UPLOAD_MB", "4096")) * CHUNK

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.worker:
            worker.start()
        yield
        worker.stop()

    app = FastAPI(title="ClipMaker", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.worker = worker

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__, "database": ping(engine)}

    @app.post("/api/projects", status_code=201)
    def upload(file: UploadFile = File(...)) -> dict:
        name = _safe_name(file.filename or "video.mp4")
        ext = Path(name).suffix.lower()
        if ext not in VIDEO_EXTS:
            raise HTTPException(400, f"unsupported file type {ext or '(none)'}; use one of {sorted(VIDEO_EXTS)}")
        with Session() as s:
            project = Project(name=Path(name).stem, source_file=f"source{ext}")
            s.add(project)
            s.commit()
            pid = project.id
        pdir = project_dir(settings.data_dir, pid)
        pdir.mkdir(parents=True, exist_ok=True)
        size = 0
        with open(pdir / f"source{ext}", "wb") as out:
            while chunk := file.file.read(CHUNK):
                size += len(chunk)
                if size > max_upload:
                    out.close()
                    shutil.rmtree(pdir, ignore_errors=True)
                    with Session() as s:
                        s.delete(s.get(Project, pid))
                        s.commit()
                    raise HTTPException(413, "file too large")
                out.write(chunk)
        job = enqueue(engine, pid)
        return {"project_id": pid, "job_id": job.id, "name": Path(name).stem, "bytes": size}

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: int) -> dict:
        with Session() as s:
            job = s.get(Job, job_id)
            if job is None:
                raise HTTPException(404, "no such job")
            return job.to_dict()

    @app.get("/api/projects/{project_id}")
    def project_detail(project_id: int) -> dict:
        with Session() as s:
            p = s.get(Project, project_id)
            if p is None:
                raise HTTPException(404, "no such project")
            return {"id": p.id, "name": p.name, "created_at": p.created_at.isoformat(),
                    "jobs": [j.to_dict() for j in p.jobs],
                    "clips": [c.to_dict() | {"url": f"/media/{p.id}/{c.file}" if c.file else None}
                              for c in p.clips]}

    @app.get("/media/{project_id}/clips/{name}")
    def media(project_id: int, name: str):
        if not re.fullmatch(r"[\w\-]+\.mp4", name):
            raise HTTPException(404)
        path = project_dir(settings.data_dir, project_id) / "clips" / name
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="video/mp4")

    @app.get("/projects/{project_id}", response_class=HTMLResponse)
    def project_page(request: Request, project_id: int):
        with Session() as s:
            p = s.get(Project, project_id)
            if p is None:
                raise HTTPException(404, "no such project")
            job = p.jobs[-1] if p.jobs else None
            clips = sorted((c for c in p.clips if job is None or c.job_id == job.id),
                           key=lambda c: -c.score)
            return TEMPLATES.TemplateResponse(request, "project.html",
                                              {"project": p, "job": job, "clips": clips})

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        with Session() as s:
            projects = s.scalars(select(Project).order_by(Project.id.desc()).limit(20)).all()
            recent = [{"id": p.id, "name": p.name, "status": p.jobs[-1].status if p.jobs else "-",
                       "clips": len(p.clips)} for p in projects]
        return TEMPLATES.TemplateResponse(request, "index.html", {"version": __version__, "recent": recent})

    return app
