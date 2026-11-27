"""Database models (projects, jobs, clips) and schema setup through Alembic migrations.

The schema is owned by the migrations in `clipmaker/migrations/versions`; `init_db` upgrades
the database behind DATABASE_URL to the latest revision (SQLite by default, Postgres works).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import JSON, Float, ForeignKey, Integer, String, Text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    source_file: Mapped[str] = mapped_column(String(512))  # relative to the project directory
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    owner: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)  # visitor id (D-030)
    client_ip: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    jobs: Mapped[list[Job]] = relationship(back_populates="project", order_by="Job.id")
    clips: Mapped[list[Clip]] = relationship(back_populates="project", order_by="Clip.id")


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)  # queued|running|done|failed
    stage: Mapped[str] = mapped_column(String(64), default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)

    project: Mapped[Project] = relationship(back_populates="jobs")

    def to_dict(self) -> dict:
        return {"id": self.id, "project_id": self.project_id, "status": self.status, "stage": self.stage,
                "progress": round(self.progress, 3), "error": self.error}


class Clip(Base):
    __tablename__ = "clips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"), index=True)
    key: Mapped[str] = mapped_column(String(32))  # clip_01 ...
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    title: Mapped[str] = mapped_column(String(255), default="")
    text: Mapped[str] = mapped_column(Text, default="")
    file: Mapped[str | None] = mapped_column(String(512), nullable=True)  # relative to the project directory
    data: Mapped[dict] = mapped_column(JSON, default=dict)  # the clip's full clips.json entry

    project: Mapped[Project] = relationship(back_populates="clips")

    def to_dict(self) -> dict:
        return {"id": self.id, "key": self.key, "start": self.start, "end": self.end, "score": self.score,
                "title": self.title, "text": self.text, "file": self.file,
                "duration": round(self.data.get("output_duration", self.end - self.start), 3)}


def init_db(engine: Engine) -> None:
    """Bring the database schema up to date (alembic upgrade head)."""
    from alembic import command
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, "head")


def session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(engine, expire_on_commit=False)
