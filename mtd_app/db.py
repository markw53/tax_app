import os
import secrets
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.environ.get("MTD_DATABASE_URL", "sqlite:///mtd.db")


def secret_key() -> str:
    """Session signing key: MTD_SECRET_KEY, or one generated once and kept
    in .secret_key (gitignored) for local use."""
    key = os.environ.get("MTD_SECRET_KEY")
    if key:
        return key
    path = Path(".secret_key")
    if not path.exists():
        path.write_text(secrets.token_urlsafe(48))
        path.chmod(0o600)
    return path.read_text().strip()


class Base(DeclarativeBase):
    pass


def make_engine(url: str = DATABASE_URL):
    engine = create_engine(url)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")
    return engine


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
