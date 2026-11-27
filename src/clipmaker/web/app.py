"""FastAPI application: API + server-rendered pages."""
from __future__ import annotations

import os
import re
import shutil
import zipfile
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select

from clipmaker import __version__
from clipmaker.config import Settings, get_settings
from clipmaker.db import make_engine, ping
from clipmaker.jobs import Runner, Worker, default_runner, enqueue, project_dir
from clipmaker.store import Clip, Job, Project, init_db, session_factory, utcnow

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
    from clipmaker.cleanup import Janitor, has_room, retention_hours

    janitor = Janitor(engine, settings.data_dir)
    max_upload = int(os.environ.get("CLIPMAKER_MAX_UPLOAD_MB", "4096")) * CHUNK

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.worker:
            worker.start()
            if janitor.enabled():
                janitor.start()
        yield
        janitor.stop()
        worker.stop()

    app = FastAPI(title="ClipMaker", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.engine = engine
    app.state.worker = worker
    app.state.janitor = janitor

    from clipmaker import visitor as vis

    key = vis.secret_key(settings.data_dir)

    @app.middleware("http")
    async def visitor_cookie(request: Request, call_next):
        vid = vis.verify(key, request.cookies.get(vis.COOKIE))
        fresh = vid is None
        request.state.visitor = vid or vis.new_id()
        response = await call_next(request)
        if fresh:
            response.set_cookie(vis.COOKIE, vis.sign(key, request.state.visitor), max_age=vis.MAX_AGE,
                                httponly=True, samesite="lax", secure=request.url.scheme == "https")
        return response

    def _ip(request: Request) -> str:
        return request.client.host if request.client else ""

    def _own(s, project_id: int, request: Request) -> Project:
        """The project, or 404 if it doesn't exist or (private mode) belongs to another visitor."""
        p = s.get(Project, project_id)
        if p is None or (vis.private_mode() and p.owner != request.state.visitor):
            raise HTTPException(404, "no such project")
        return p

    def _check_limit(request: Request) -> None:
        limit = vis.daily_limit()
        if limit <= 0:
            return
        since = utcnow() - timedelta(hours=24)
        who = Project.owner == request.state.visitor
        ip = _ip(request)
        if ip:
            who = who | (Project.client_ip == ip)
        with Session() as s:
            used = s.scalar(select(func.count()).select_from(Project).where(Project.created_at >= since, who))
        if used >= limit:
            raise HTTPException(429, f"You've reached today's limit of {limit} videos. Please come back tomorrow.")

    def _need_room() -> None:
        if not has_room(engine, settings.data_dir):
            raise HTTPException(503, "The server is full right now. Please try again in a few hours.")

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__, "database": ping(engine)}

    @app.post("/api/projects", status_code=201)
    def upload(request: Request, file: UploadFile = File(...)) -> dict:
        name = _safe_name(file.filename or "video.mp4")
        ext = Path(name).suffix.lower()
        if ext not in VIDEO_EXTS:
            raise HTTPException(400, f"unsupported file type {ext or '(none)'}; use one of {sorted(VIDEO_EXTS)}")
        _check_limit(request)
        _need_room()
        with Session() as s:
            project = Project(name=Path(name).stem, source_file=f"source{ext}",
                              owner=request.state.visitor, client_ip=_ip(request))
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

    @app.post("/api/projects/link", status_code=201)
    def import_link(request: Request, url: str = Body(...), rights: bool = Body(False)) -> dict:
        from clipmaker.fetch import FetchError, check_url, filename_from_url, is_url
        from clipmaker.platforms import platform_link_ok, platform_of

        url = url.strip()
        if not is_url(url):
            raise HTTPException(400, "Paste a link that starts with http:// or https://.")
        _check_limit(request)
        _need_room()
        who = {"owner": request.state.visitor, "client_ip": _ip(request)}
        platform = platform_of(url)
        if platform:
            if not rights:
                raise HTTPException(400, "Tick “I own this video or have the rights to use it” "
                                         "to import a YouTube or Twitch link.")
            if not platform_link_ok(url):
                raise HTTPException(400, "This doesn't look like a link to one video or VOD. "
                                         "Paste the link of a single video, VOD or clip.")
            with Session() as s:
                project = Project(name=f"{platform.title()} video", source_file="download/audio", **who)
                s.add(project)
                s.commit()
                pid = project.id
            project_dir(settings.data_dir, pid).mkdir(parents=True, exist_ok=True)
            job = enqueue(engine, pid, {"source_url": url, "rights_confirmed": True})
            return {"project_id": pid, "job_id": job.id, "name": f"{platform.title()} video"}
        try:
            check_url(url)  # fail fast; the download re-checks every redirect
        except FetchError as exc:
            raise HTTPException(400, str(exc)) from exc
        from clipmaker.hls import is_hls

        name = filename_from_url(url)
        ext = Path(name).suffix.lower() if Path(name).suffix.lower() in VIDEO_EXTS else ".mp4"
        source_file = f"source{ext}"
        if is_hls(url):  # sections mode: only the audio is stored as the project's source
            name, source_file = "HLS stream", "download/audio.m4a"
        with Session() as s:
            project = Project(name=Path(name).stem, source_file=source_file, **who)
            s.add(project)
            s.commit()
            pid = project.id
        project_dir(settings.data_dir, pid).mkdir(parents=True, exist_ok=True)
        job = enqueue(engine, pid, {"source_url": url})
        return {"project_id": pid, "job_id": job.id, "name": Path(name).stem}

    @app.get("/api/jobs/{job_id}")
    def job_status(job_id: int, request: Request) -> dict:
        with Session() as s:
            job = s.get(Job, job_id)
            if job is None:
                raise HTTPException(404, "no such job")
            _own(s, job.project_id, request)
            ready = s.scalar(select(func.count()).select_from(Clip).where(Clip.job_id == job_id))
            extra = {"clips_ready": ready or 0}
            if job.status == "queued":  # jobs that will run before this one
                extra["queue_ahead"] = s.scalar(select(func.count()).select_from(Job).where(
                    (Job.status == "running") | ((Job.status == "queued") & (Job.id < job.id)))) or 0
            return job.to_dict() | extra

    @app.get("/api/projects/{project_id}")
    def project_detail(project_id: int, request: Request) -> dict:
        with Session() as s:
            p = _own(s, project_id, request)
            return {"id": p.id, "name": p.name, "created_at": p.created_at.isoformat(),
                    "jobs": [j.to_dict() for j in p.jobs],
                    "clips": [c.to_dict() | {"url": f"/media/{p.id}/{c.file}" if c.file else None}
                              for c in p.clips]}

    def _download_name(project_id: int, suffix: str) -> str:
        with Session() as s:
            p = s.get(Project, project_id)
            base = p.name if p else f"project-{project_id}"
        return f"{base}_{suffix}"

    @app.get("/media/{project_id}/clips/{name}")
    def media(project_id: int, name: str, request: Request, download: bool = False):
        if not re.fullmatch(r"[\w\-]+\.mp4", name):
            raise HTTPException(404)
        if vis.private_mode():
            with Session() as s:
                _own(s, project_id, request)
        path = project_dir(settings.data_dir, project_id) / "clips" / name
        if not path.is_file():
            raise HTTPException(404)
        if download:
            return FileResponse(path, media_type="video/mp4", filename=_download_name(project_id, name))
        return FileResponse(path, media_type="video/mp4")

    @app.get("/projects/{project_id}/clips.zip")
    def download_all(project_id: int, request: Request):
        with Session() as s:
            p = _own(s, project_id, request)
            job = p.jobs[-1] if p.jobs else None
            files = [c.file for c in sorted(p.clips, key=lambda c: -c.score)
                     if c.file and job is not None and c.job_id == job.id]
        pdir = project_dir(settings.data_dir, project_id)
        files = [f for f in files if (pdir / f).is_file()]
        if not files:
            raise HTTPException(404, "no clips to download")
        out = pdir / "clips.zip"
        tmp = out.with_suffix(".zip.part")
        with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED) as zf:  # MP4 is already compressed
            for rank, f in enumerate(files, 1):
                zf.write(pdir / f, arcname=f"{rank:02d}_{Path(f).name}")
            if (pdir / "clips" / "clips.json").is_file():
                zf.write(pdir / "clips" / "clips.json", arcname="clips.json")
        tmp.replace(out)
        return FileResponse(out, media_type="application/zip", filename=_download_name(project_id, "clips.zip"))

    @app.get("/projects/{project_id}", response_class=HTMLResponse)
    def project_page(request: Request, project_id: int):
        with Session() as s:
            p = _own(s, project_id, request)
            job = p.jobs[-1] if p.jobs else None
            clips = sorted((c for c in p.clips if job is None or c.job_id == job.id),
                           key=lambda c: -c.score)
            keep = retention_hours()
            left = (p.created_at + timedelta(hours=keep) - utcnow()).total_seconds() if keep > 0 else 0
            delete_in = (f"{int(left // 3600)} h" if left >= 3600 else f"{max(1, int(left // 60))} min") if keep else ""
            return TEMPLATES.TemplateResponse(request, "project.html",
                                              {"project": p, "job": job, "clips": clips,
                                               "keep_hours": keep, "delete_in": delete_in})

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        with Session() as s:
            q = select(Project).order_by(Project.id.desc()).limit(20)
            if vis.private_mode():  # a public server shows each visitor only their own projects
                q = q.where(Project.owner == request.state.visitor)
            projects = s.scalars(q).all()
            recent = [{"id": p.id, "name": p.name, "status": p.jobs[-1].status if p.jobs else "-",
                       "clips": len(p.clips)} for p in projects]
        return TEMPLATES.TemplateResponse(request, "index.html", {"version": __version__, "recent": recent})

    return app
