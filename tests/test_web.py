from datetime import date

import pytest
from fastapi.testclient import TestClient

from mtd_app.auth import hash_password
from mtd_app.main import app, get_db
from mtd_app.models import IncomeSource, Property, PropertyOwner, Transaction, User

PASSWORD = "correct horse battery"


@pytest.fixture
def setup(session_factory):
    def _db():
        with session_factory() as s:
            yield s
    app.dependency_overrides[get_db] = _db

    with session_factory() as s:
        a = User(email="a@example.com", name="Alex", password_hash=hash_password(PASSWORD))
        b = User(email="b@example.com", name="Sam", password_hash=hash_password(PASSWORD))
        s.add_all([a, b])
        s.flush()
        driving = IncomeSource(user_id=a.id, type="self-employment", name="Delivery driving",
                               vehicle_method="simplified")
        examining = IncomeSource(user_id=b.id, type="self-employment", name="Examining")
        s.add_all([driving, examining,
                   IncomeSource(user_id=a.id, type="uk-property", name="UK property"),
                   IncomeSource(user_id=b.id, type="uk-property", name="UK property")])
        prop = Property(name="1 High St")
        s.add(prop)
        s.flush()
        s.add_all([PropertyOwner(property_id=prop.id, user_id=a.id, share_bp=5000),
                   PropertyOwner(property_id=prop.id, user_id=b.id, share_bp=5000)])
        s.commit()
        ids = {"driving": driving.id, "examining": examining.id, "prop": prop.id}
    yield ids
    app.dependency_overrides.clear()


def client_for(email=None):
    c = TestClient(app)
    if email:
        r = c.post("/login", data={"email": email, "password": PASSWORD}, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/"
    return c


def test_requires_login(setup):
    r = client_for().get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_wrong_password_rejected(setup):
    c = client_for()
    c.post("/login", data={"email": "a@example.com", "password": "nope"}, follow_redirects=False)
    assert "not recognised" in c.get("/login").text
    assert c.get("/", follow_redirects=False).status_code == 303


def test_dashboard_lists_own_sources_only(setup):
    page = client_for("a@example.com").get("/?year=2026/27").text
    assert "Delivery driving" in page and "Examining" not in page
    assert "1 High St" in page


def test_add_transaction_and_validation(setup, session_factory):
    c = client_for("a@example.com")
    url = f"/sources/{setup['driving']}/transactions"
    c.post(url, data={"date": "2026-05-01", "entry": "income:turnover", "amount": "£1,234.50"})
    c.post(url, data={"date": "2026-05-01", "entry": "income:turnover", "amount": "12.345"})
    c.post(url, data={"date": "2026-05-01", "entry": "income:residentialFinancialCost", "amount": "5"})
    page = c.get(f"/sources/{setup['driving']}?year=2026/27").text
    assert "£1,234.50" in page
    with session_factory() as s:
        assert [t.amount_pence for t in s.query(Transaction)] == [123450]


def test_cannot_see_or_change_other_users_records(setup, session_factory):
    a = client_for("a@example.com")
    b = client_for("b@example.com")
    assert b.get(f"/sources/{setup['driving']}").status_code == 404
    r = b.post(f"/sources/{setup['driving']}/transactions",
               data={"date": "2026-05-01", "entry": "income:turnover", "amount": "1"})
    assert r.status_code == 404

    a.post(f"/sources/{setup['driving']}/transactions",
           data={"date": "2026-05-01", "entry": "income:turnover", "amount": "10"})
    with session_factory() as s:
        txn_id = s.query(Transaction).one().id
    assert b.post(f"/transactions/{txn_id}/delete").status_code == 404
    with session_factory() as s:
        assert s.query(Transaction).count() == 1


def test_joint_property_shared_between_owners(setup):
    a = client_for("a@example.com")
    b = client_for("b@example.com")
    a.post(f"/properties/{setup['prop']}/transactions",
           data={"date": "2026-05-01", "entry": "income:periodAmount", "amount": "1000"})
    assert "£1,000.00" in b.get(f"/properties/{setup['prop']}?year=2026/27").text
    # Each owner's dashboard shows their £500 share
    for c in (a, b):
        assert "£500.00" in c.get("/?year=2026/27").text


def test_mileage_trip_adds_simplified_allowance(setup):
    c = client_for("a@example.com")
    c.post(f"/sources/{setup['driving']}/trips",
           data={"date": "2026-05-01", "miles": "100", "vehicle": "car_van"})
    page = c.get(f"/sources/{setup['driving']}?year=2026/27").text
    assert "£45.00" in page


def test_csv_import_all_or_nothing(setup, session_factory):
    c = client_for("b@example.com")
    url = f"/sources/{setup['examining']}/import"
    bad = b"date,type,category,amount\n2026-05-01,income,turnover,100\n2026-05-02,expense,Nonsense,5\n"
    page = c.post(url, files={"file": ("x.csv", bad, "text/csv")}).text
    assert "Row 3" in page and "Nothing was imported" in page
    with session_factory() as s:
        assert s.query(Transaction).count() == 0

    good = (b"Date,Type,Category,Amount,Description\n"
            b"01/05/2026,income,\"Turnover (takings, fees, sales)\",\"1,000.00\",May fees\n"
            b"2026-05-02,expense,adminCosts,12.99,Printer ink\n")
    c.post(url, files={"file": ("x.csv", good, "text/csv")})
    with session_factory() as s:
        rows = s.query(Transaction).order_by(Transaction.date).all()
        assert [(r.date, r.category, r.amount_pence) for r in rows] == [
            (date(2026, 5, 1), "turnover", 100_000),
            (date(2026, 5, 2), "adminCosts", 1_299),
        ]
