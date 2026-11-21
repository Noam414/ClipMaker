"""Alembic environment: uses the connection passed in by clipmaker.store.init_db,
or DATABASE_URL when run from the alembic command line."""
from alembic import context

from clipmaker.store import Base

config = context.config
target_metadata = Base.metadata


def run_migrations_online() -> None:
    conn = config.attributes.get("connection")
    if conn is not None:
        context.configure(connection=conn, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    from clipmaker.config import get_settings
    from clipmaker.db import make_engine

    engine = make_engine(get_settings().database_url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise SystemExit("offline migrations are not supported; run against a database")
run_migrations_online()
