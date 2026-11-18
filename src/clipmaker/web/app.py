"""FastAPI application: API + server-rendered pages."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from clipmaker import __version__
from clipmaker.config import Settings, get_settings
from clipmaker.db import make_engine, ping

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = make_engine(settings.database_url)
    app = FastAPI(title="ClipMaker", version=__version__)
    app.state.settings = settings
    app.state.engine = engine

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__, "database": ping(engine)}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        return TEMPLATES.TemplateResponse(request, "index.html", {"version": __version__})

    return app
