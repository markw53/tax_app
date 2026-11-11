import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from mtd_app.auth import authenticate, hash_password
from mtd_app.main import app, get_db
from mtd_app.models import Property, User

from .conftest import sign_in, totp_secret

PASSWORD = "correct horse battery"
NEW = {"name": "Sam", "email": "Sam@Example.com", "password": PASSWORD, "repeat": PASSWORD}


@pytest.fixture
def client(session_factory):
    def _db():
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_db] = _db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def alex(session_factory, client):
    """A signed-in existing user."""
    with session_factory() as s:
        s.add(User(email="a@example.com", name="Alex", password_hash=hash_password(PASSWORD),
                   totp_secret=totp_secret()))
        s.commit()
    sign_in(client, "a@example.com", PASSWORD)
    return client


def _user(session, email):
    return session.scalar(select(User).where(User.email == email))


def test_login_goes_to_setup_when_there_are_no_users(client):
    r = client.get("/login", follow_redirects=False)
    assert r.headers["location"] == "/setup"
    assert "Create the first account" in client.get("/setup").text


def test_setup_creates_first_account_with_chosen_work(client, session):
    r = client.post("/setup", data={**NEW, "work": ["driver", "landlord"]}, follow_redirects=False)
    assert r.headers["location"] == "/login"
    user = _user(session, "sam@example.com")
    assert {(s.type, s.name, s.vehicle_method) for s in user.sources} == {
        ("self-employment", "Delivery driving", "simplified"),
        ("uk-property", "UK property", None),
    }
    assert authenticate(session, "sam@example.com", PASSWORD)
    # First sign-in goes on to set up the authenticator app
    r = client.post("/login", data={"email": "sam@example.com", "password": PASSWORD},
                    follow_redirects=False)
    assert r.headers["location"] == "/login/setup"


def test_setup_is_closed_once_someone_has_an_account(alex, session):
    client = TestClient(app)  # not signed in
    assert client.get("/setup", follow_redirects=False).headers["location"] == "/login"
    assert client.post("/setup", data={**NEW, "work": "driver"}).status_code == 404
    assert _user(session, "sam@example.com") is None


def test_adding_people_needs_a_login(alex, session):
    client = TestClient(app)
    r = client.post("/people", data={**NEW, "work": "driver"}, follow_redirects=False)
    assert r.headers["location"] == "/login"
    assert _user(session, "sam@example.com") is None


def test_add_person_as_exam_writer(alex, session):
    r = alex.post("/people", data={**NEW, "work": "exam-writer", "calendar_quarters": "true"})
    assert "Added Sam" in r.text
    user = _user(session, "sam@example.com")
    assert user.calendar_quarters
    assert [(s.type, s.name, s.vehicle_method) for s in user.sources] == [
        ("self-employment", "AQA exam writing", "actual")]


@pytest.mark.parametrize("change, message", [
    ({"email": "a@example.com"}, "already has an account"),
    ({"email": "not-an-email"}, "valid email"),
    ({"password": "short", "repeat": "short"}, "at least 10"),
    ({"repeat": "something else entirely"}, "match"),
    ({"work": []}, "at least one type of work"),
    ({"work": "astronaut"}, "from the list"),
])
def test_add_person_rejects_bad_input(alex, session, change, message):
    r = alex.post("/people", data={**NEW, "work": "driver", **change})
    assert message in r.text
    assert len(session.scalars(select(User)).all()) == 1


def test_add_work_to_own_account_once(alex, session):
    assert "Added Landlord" in alex.post("/people/work", data={"work": "landlord"}).text
    assert "already have" in alex.post("/people/work", data={"work": "landlord"}).text
    assert [s.type for s in _user(session, "a@example.com").sources] == ["uk-property"]


def test_add_joint_property(alex, session):
    alex.post("/people", data={**NEW, "work": "exam-writer"})
    a, sam = _user(session, "a@example.com"), _user(session, "sam@example.com")
    r = alex.post("/people/properties",
                  data={"name": "1 High St", f"share_{a.id}": "50", f"share_{sam.id}": "50"})
    assert "Added 1 High St" in r.text
    prop = session.scalar(select(Property))
    assert {(o.user_id, o.share_bp) for o in prop.owners} == {(a.id, 5000), (sam.id, 5000)}
    # Both owners now have a UK property business to report their share under
    session.expire_all()
    for person in (a, sam):
        assert "uk-property" in [s.type for s in session.get(User, person.id).sources]


@pytest.mark.parametrize("shares, message", [
    ({"me": "60", "other": "50"}, "more than 100%"),
    ({"me": "", "other": "100"}, "you own a share of"),
    ({"me": "", "other": ""}, "at least one owner"),
    ({"me": "half", "other": ""}, "a percentage"),
    ({"me": "0", "other": ""}, "more than 0%"),
])
def test_add_property_rejects_bad_shares(alex, session, shares, message):
    alex.post("/people", data={**NEW, "work": "exam-writer"})
    a, sam = _user(session, "a@example.com"), _user(session, "sam@example.com")
    r = alex.post("/people/properties", data={
        "name": "1 High St", f"share_{a.id}": shares["me"], f"share_{sam.id}": shares["other"]})
    assert message in r.text
    assert session.scalar(select(Property)) is None


NEW_PASSWORD = "a much better password"


def test_change_password_needs_a_login(client):
    r = client.get("/account/password", follow_redirects=False)
    assert r.headers["location"] in ("/login", "/setup")


def test_change_password(alex, session):
    r = alex.post("/account/password",
                  data={"current": PASSWORD, "password": NEW_PASSWORD, "repeat": NEW_PASSWORD})
    assert "Your password has been changed" in r.text
    assert authenticate(session, "a@example.com", NEW_PASSWORD)
    session.expire_all()
    assert not authenticate(session, "a@example.com", PASSWORD)


@pytest.mark.parametrize("data, message", [
    ({"current": "wrong password!", "password": NEW_PASSWORD, "repeat": NEW_PASSWORD}, "current password"),
    ({"current": PASSWORD, "password": "short", "repeat": "short"}, "at least 10"),
    ({"current": PASSWORD, "password": NEW_PASSWORD, "repeat": NEW_PASSWORD + "x"}, "match"),
    ({"current": PASSWORD, "password": PASSWORD, "repeat": PASSWORD}, "different from"),
])
def test_change_password_rejects_bad_input(alex, session, data, message):
    r = alex.post("/account/password", data=data)
    assert message in r.text
    assert authenticate(session, "a@example.com", PASSWORD)
