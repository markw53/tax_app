import pytest
from fastapi.testclient import TestClient

from mtd_app import mfa
from mtd_app.auth import hash_password
from mtd_app.main import app, get_db
from mtd_app.models import User

from .conftest import TOTP_SECRET, sign_in, totp_secret

PASSWORD = "correct horse battery"


def test_rfc6238_vector():
    # RFC 6238 appendix B, SHA-1, T=59s, last 6 digits of 94287082
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # base32 of "12345678901234567890"
    assert mfa.code_at(secret, mfa.current_step(59)) == "287082"


def test_verify_window_and_replay():
    now = 1_000_000.0
    step = mfa.current_step(now)
    assert mfa.verify(TOTP_SECRET, mfa.code_at(TOTP_SECRET, step - 1), None, now) == step - 1
    assert mfa.verify(TOTP_SECRET, mfa.code_at(TOTP_SECRET, step - 2), None, now) is None
    assert mfa.verify(TOTP_SECRET, mfa.code_at(TOTP_SECRET, step), step, now) is None
    assert mfa.verify(TOTP_SECRET, "12345", None, now) is None


@pytest.fixture
def users(session_factory):
    def _db():
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_db] = _db
    with session_factory() as s:
        s.add_all([User(email="a@example.com", name="Alex", password_hash=hash_password(PASSWORD),
                        totp_secret=totp_secret()),
                   User(email="new@example.com", name="New", password_hash=hash_password(PASSWORD))])
        s.commit()
    yield session_factory
    app.dependency_overrides.clear()


def _password(c, email):
    return c.post("/login", data={"email": email, "password": PASSWORD}, follow_redirects=False)


def test_password_alone_does_not_sign_in(users):
    c = TestClient(app)
    assert _password(c, "a@example.com").headers["location"] == "/login/code"
    assert c.get("/", follow_redirects=False).headers["location"] == "/login"


def test_code_signs_in_and_cannot_be_reused(users):
    c = TestClient(app)
    sign_in(c, "a@example.com", PASSWORD)
    assert c.get("/", follow_redirects=False).status_code == 200
    with users() as s:
        used = s.query(User).filter_by(email="a@example.com").one().totp_last_step
    c2 = TestClient(app)
    _password(c2, "a@example.com")
    r = c2.post("/login/code", data={"code": mfa.code_at(TOTP_SECRET, used)}, follow_redirects=False)
    assert r.headers["location"] == "/login/code"


def test_too_many_wrong_codes(users):
    c = TestClient(app)
    _password(c, "a@example.com")
    for _ in range(4):
        assert c.post("/login/code", data={"code": "000000"}, follow_redirects=False).headers["location"] == "/login/code"
    assert c.post("/login/code", data={"code": "000000"}, follow_redirects=False).headers["location"] == "/login"
    assert c.get("/login/code", follow_redirects=False).headers["location"] == "/login"


def test_code_page_needs_password_first(users):
    assert TestClient(app).get("/login/code", follow_redirects=False).headers["location"] == "/login"


def test_new_user_sets_up_authenticator(users):
    c = TestClient(app)
    assert _password(c, "new@example.com").headers["location"] == "/login/setup"
    page = c.get("/login/setup").text
    assert "<svg" in page
    secret = page.split('<code style="word-break: break-all;">')[1].split("<")[0]
    assert c.get("/login/setup").text.count(secret) == 1  # same secret on reload
    r = c.post("/login/setup", data={"code": mfa.code_at(secret, mfa.current_step())}, follow_redirects=False)
    assert r.headers["location"] == "/"
    assert c.get("/", follow_redirects=False).status_code == 200
    with users() as s:
        stored = s.query(User).filter_by(email="new@example.com").one().totp_secret
    assert stored and secret not in stored  # encrypted at rest

