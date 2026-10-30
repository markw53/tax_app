import os

os.environ.setdefault("MTD_SECRET_KEY", "test-secret")
# Fixed test-only Fernet key so tests never write .encryption_key
os.environ.setdefault("MTD_ENCRYPTION_KEY", "q2PQ0j9f8wGqJ3yQ0V9bXo0y6wq1m3Jt7r5l2YJ6c0E=")

import pytest  # noqa: E402
from sqlalchemy import create_engine, event  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from mtd_app.db import Base  # noqa: E402


@pytest.fixture
def session_factory():
    # One shared in-memory connection, so the app and the test see the same data
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    event.listen(engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def session(session_factory):
    with session_factory() as s:
        yield s
