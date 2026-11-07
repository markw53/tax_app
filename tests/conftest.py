import os

os.environ.setdefault("MTD_SECRET_KEY", "test-secret")
# Fixed test-only Fernet key so tests never write .encryption_key
os.environ.setdefault(
    "MTD_ENCRYPTION_KEY", "q2PQ0j9f8wGqJ3yQ0V9bXo0y6wq1m3Jt7r5l2YJ6c0E="
)

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from mtd_app.db import Base


@pytest.fixture
def session_factory():
    # One shared in-memory connection, so the app and the test see the same data
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    event.listen(
        engine, "connect", lambda conn, _: conn.execute("PRAGMA foreign_keys=ON")
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def session(session_factory):
    with session_factory() as s:
        yield s


# Authenticator secret shared by the test users
TOTP_SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"


def totp_secret():
    from mtd_app.hmrc_config import encrypt

    return encrypt(TOTP_SECRET)


def sign_in(client, email, password):
    """Password, then the authenticator code. A code can only be used once,
    so if this user already signed in this time step, use the next one."""
    from mtd_app import mfa

    r = client.post(
        "/login", data={"email": email, "password": password}, follow_redirects=False
    )
    assert r.headers["location"] == "/login/code"
    step = mfa.current_step()
    for s in (step, step + 1):
        r = client.post(
            "/login/code",
            data={"code": mfa.code_at(TOTP_SECRET, s)},
            follow_redirects=False,
        )
        if r.headers["location"] == "/":
            return r
    raise AssertionError("authenticator code rejected")
