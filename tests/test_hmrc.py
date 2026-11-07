import json
from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs, quote, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from mtd_app import fraud_headers, hmrc_payloads
from mtd_app.auth import hash_password
from mtd_app.hmrc_client import HmrcClient
from mtd_app.hmrc_config import HmrcConfig
from mtd_app.hmrc_routes import get_hmrc_client
from mtd_app.main import app, get_db
from mtd_app.models import HmrcConnection, IncomeSource, Submission, Transaction, User
from mtd_app.records import Totals

from .conftest import sign_in, totp_secret

PASSWORD = "correct horse battery"
NINO = "AA123456A"
CONFIG = HmrcConfig(client_id="cid", client_secret="csecret")


class FakeHmrc:
    """Just enough of HMRC's sandbox to exercise the app."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.issued = 0

    def tokens(self):
        self.issued += 1
        return httpx.Response(
            200,
            json={
                "access_token": f"access-{self.issued}",
                "refresh_token": f"refresh-{self.issued}",
                "expires_in": 14400,
                "token_type": "bearer",
            },
        )

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        path = req.url.path
        if path == "/oauth/token":
            form = parse_qs(req.content.decode())
            if form.get("code") == ["good"] or form.get("refresh_token", [""])[
                0
            ].startswith("refresh-"):
                return self.tokens()
            return httpx.Response(
                400, json={"error": "invalid_grant", "error_description": "bad"}
            )
        if req.headers.get("Authorization", "").startswith("Bearer access-") is False:
            return httpx.Response(
                401, json={"code": "INVALID_CREDENTIALS", "message": "no"}
            )
        if path == f"/individuals/business/details/{NINO}/list":
            return httpx.Response(
                200,
                json={
                    "listOfBusinesses": [
                        {
                            "typeOfBusiness": "self-employment",
                            "businessId": "XAIS00000000001",
                            "tradingName": "Deliveries",
                        },
                        {
                            "typeOfBusiness": "uk-property",
                            "businessId": "XPIS00000000002",
                        },
                    ]
                },
            )
        if path == f"/obligations/details/{NINO}/income-and-expenditure":
            return httpx.Response(
                200,
                json={
                    "obligations": [
                        {
                            "typeOfBusiness": "self-employment",
                            "businessId": "XAIS00000000001",
                            "obligationDetails": [
                                {
                                    "periodStartDate": "2026-04-06",
                                    "periodEndDate": "2026-07-05",
                                    "dueDate": "2026-08-07",
                                    "status": "open",
                                }
                            ],
                        }
                    ]
                },
            )
        if "/cumulative/2026-27" in path and req.method == "PUT":
            if "XREJECT" in path:
                return httpx.Response(
                    400,
                    headers={"X-CorrelationId": "corr-bad"},
                    json={
                        "code": "RULE_BOTH_EXPENSES_SUPPLIED",
                        "message": "Both expenses supplied",
                    },
                )
            return httpx.Response(204, headers={"X-CorrelationId": "corr-ok"})
        if path.endswith("/self-assessment/2026-27/trigger/in-year"):
            return httpx.Response(202, json={"calculationId": "calc-1"})
        if path.endswith("/self-assessment/2026-27/calc-1"):
            return httpx.Response(
                200,
                json={
                    "calculation": {
                        "taxCalculation": {
                            "incomeTax": {"totalIncomeTaxDue": 1234.5},
                            "nics": {"totalNic": 321},
                            "totalIncomeTaxAndNicsDue": 1555.5,
                        }
                    }
                },
            )
        if path.endswith("/self-assessment/2026-27/calc-pending"):
            return httpx.Response(
                404, json={"code": "MATTER_NOT_FOUND", "message": "Not found"}
            )
        return httpx.Response(
            404, json={"code": "NOT_FOUND", "message": f"fake has no {path}"}
        )


@pytest.fixture
def hmrc():
    return FakeHmrc()


@pytest.fixture
def env(session_factory, hmrc):
    def _db():
        with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_hmrc_client] = lambda: HmrcClient(
        CONFIG, http=httpx.Client(transport=httpx.MockTransport(hmrc))
    )
    with session_factory() as s:
        u = User(
            email="a@example.com",
            name="Alex",
            password_hash=hash_password(PASSWORD),
            totp_secret=totp_secret(),
        )
        s.add(u)
        s.flush()
        src = IncomeSource(
            user_id=u.id,
            type="self-employment",
            name="Delivery driving",
            vehicle_method="actual",
        )
        s.add(src)
        s.flush()
        s.add_all(
            [
                Transaction(
                    date=date(2026, 5, 1),
                    kind="income",
                    category="turnover",
                    amount_pence=250_000,
                    source_id=src.id,
                    created_by_id=u.id,
                ),
                Transaction(
                    date=date(2026, 9, 1),
                    kind="income",
                    category="turnover",
                    amount_pence=99_900,
                    source_id=src.id,
                    created_by_id=u.id,
                ),
                Transaction(
                    date=date(2026, 5, 2),
                    kind="expense",
                    category="adminCosts",
                    amount_pence=1_999,
                    source_id=src.id,
                    created_by_id=u.id,
                ),
            ]
        )
        s.commit()
        ids = {"user": u.id, "source": src.id}
    yield ids
    app.dependency_overrides.clear()


def logged_in():
    c = TestClient(app)
    sign_in(c, "a@example.com", PASSWORD)
    return c


def connect(c):
    r = c.get("/hmrc/connect", follow_redirects=False)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    c.get(f"/hmrc/callback?code=good&state={state}")


# ----------------------------------------------------------- payloads


def test_self_employment_payload_adds_back_disallowable():
    t = Totals()
    t.income["turnover"] = 123_456
    t.expense["adminCosts"] = 1_000
    t.expense["depreciation"] = 50_000
    body = hmrc_payloads.self_employment(t, date(2026, 4, 6), date(2026, 7, 5))
    assert body == {
        "periodDates": {"periodStartDate": "2026-04-06", "periodEndDate": "2026-07-05"},
        "periodIncome": {"turnover": 1234.56, "other": 0.0},
        "periodExpenses": {"adminCosts": 10.0, "depreciation": 500.0},
        "periodDisallowableExpenses": {"depreciationDisallowable": 500.0},
    }


def test_property_payload():
    t = Totals()
    t.income["periodAmount"] = 47_500
    t.expense["residentialFinancialCost"] = 20_000
    assert hmrc_payloads.uk_property(t, date(2026, 4, 6), date(2026, 7, 5)) == {
        "fromDate": "2026-04-06",
        "toDate": "2026-07-05",
        "ukProperty": {
            "income": {"periodAmount": 475.0},
            "expenses": {"residentialFinancialCost": 200.0},
        },
    }


# ------------------------------------------------------ fraud headers


def _request(client=("81.2.69.160", 51234), cookie=None, headers=(), session=None):
    raw = (
        [(b"cookie", f"{fraud_headers.CLIENT_COOKIE}={cookie}".encode())]
        if cookie
        else []
    )
    raw += [(k.encode(), v.encode()) for k, v in headers]
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": raw,
            "client": client,
            "session": session or {},
        }
    )


def test_fraud_headers_from_browser_and_request():
    cookie = quote(
        json.dumps(
            {
                "ua": "Mozilla/5.0 (X11)",
                "device": "beec798b-b366-47fa-b1f8-92cede14a1ce",
                "tz": "UTC+01:00",
                "screens": [{"w": 1920, "h": 1080, "s": 1.25, "d": 24}],
                "win": {"w": 1256, "h": 803},
            }
        )
    )
    mfa = {"type": "TOTP", "at": "2026-10-30T14:29Z", "ref": "ab12"}
    h = fraud_headers.build(
        _request(cookie=cookie, session={"mfa": mfa}),
        7,
        HmrcConfig("a", "b", vendor_public_ip="81.2.69.200"),
        now=datetime(2026, 10, 30, 14, 30, 5, 123000, tzinfo=UTC),
    )
    assert h == {
        "Gov-Client-Connection-Method": "WEB_APP_VIA_SERVER",
        "Gov-Client-User-IDs": "mtd-records=7",
        "Gov-Vendor-Product-Name": "MTD%20records",
        "Gov-Vendor-Version": "mtd-records=0.3.0",
        "Gov-Vendor-License-IDs": "mtd-records=" + fraud_headers.licence_id(7),
        "Gov-Client-Browser-JS-User-Agent": "Mozilla/5.0 (X11)",
        "Gov-Client-Device-ID": "beec798b-b366-47fa-b1f8-92cede14a1ce",
        "Gov-Client-Timezone": "UTC+01:00",
        "Gov-Client-Screens": "width=1920&height=1080&scaling-factor=1.25&colour-depth=24",
        "Gov-Client-Window-Size": "width=1256&height=803",
        "Gov-Client-Public-IP": "81.2.69.160",
        "Gov-Client-Public-IP-Timestamp": "2026-10-30T14:30:05.123Z",
        "Gov-Client-Public-Port": "51234",
        "Gov-Vendor-Public-IP": "81.2.69.200",
        "Gov-Vendor-Forwarded": "by=81.2.69.200&for=81.2.69.160",
        "Gov-Client-Multi-Factor": "type=TOTP&timestamp=2026-10-30T14%3A29Z&unique-reference=ab12",
    }
    assert fraud_headers.missing(h) == []


def test_private_and_documentation_ips_are_left_out():
    h = fraud_headers.build(_request(client=("192.168.1.10", 5000)), 1, CONFIG)
    assert "Gov-Client-Public-IP" not in h and "Gov-Client-Public-Port" not in h
    assert "Gov-Client-Public-IP-Timestamp" in h
    assert {"Gov-Client-Public-IP", "Gov-Client-Device-ID"} <= set(
        fraud_headers.missing(h)
    )


def test_licence_id_is_stable_per_user_and_hides_the_id():
    assert fraud_headers.licence_id(1) == fraud_headers.licence_id(1)
    assert fraud_headers.licence_id(1) != fraud_headers.licence_id(2)
    assert len(fraud_headers.licence_id(1)) == 64


def test_garbage_cookie_ignored():
    h = fraud_headers.build(_request(cookie="%7Bnot-json"), 1, CONFIG)
    assert "Gov-Client-Device-ID" not in h


def test_proxy_header_only_trusted_when_enabled(monkeypatch):
    req = _request(
        client=("10.0.0.2", 80), headers=[("x-forwarded-for", "81.2.69.160, 10.0.0.1")]
    )
    assert "Gov-Client-Public-IP" not in fraud_headers.build(req, 1, CONFIG)
    monkeypatch.setenv("MTD_TRUST_PROXY", "1")
    assert fraud_headers.build(req, 1, CONFIG)["Gov-Client-Public-IP"] == "81.2.69.160"


# ------------------------------------------------------------- OAuth


def test_hmrc_page_without_credentials(env):
    app.dependency_overrides[get_hmrc_client] = lambda: None
    assert ".env.hmrc" in logged_in().get("/hmrc").text


def test_connect_stores_encrypted_tokens(env, session_factory):
    c = logged_in()
    r = c.get("/hmrc/connect", follow_redirects=False)
    url = urlparse(r.headers["location"])
    q = parse_qs(url.query)
    assert (
        url.netloc == "test-www.tax.service.gov.uk" and url.path == "/oauth/authorize"
    )
    assert q["client_id"] == ["cid"] and q["scope"] == [
        "read:self-assessment write:self-assessment"
    ]
    page = c.get(f"/hmrc/callback?code=good&state={q['state'][0]}").text
    assert "Connected to HMRC" in page
    with session_factory() as s:
        conn = s.query(HmrcConnection).one()
        assert (
            "access-1" not in conn.access_token
            and "refresh-1" not in conn.refresh_token
        )


def test_callback_rejects_wrong_state(env, session_factory):
    c = logged_in()
    c.get("/hmrc/connect", follow_redirects=False)
    assert (
        "couldn&#39;t be verified"
        in c.get("/hmrc/callback?code=good&state=forged").text
    )
    with session_factory() as s:
        assert s.query(HmrcConnection).count() == 0


def test_callback_reports_denied_access(env):
    c = logged_in()
    c.get("/hmrc/connect", follow_redirects=False)
    assert "didn&#39;t authorise" in c.get("/hmrc/callback?error=access_denied").text


def test_expired_token_is_refreshed(env, session_factory, hmrc):
    c = logged_in()
    connect(c)
    c.post("/hmrc/nino", data={"nino": "aa 12 34 56 a"})
    with session_factory() as s:
        conn = s.query(HmrcConnection).one()
        conn.expires_at -= timedelta(hours=5)
        s.commit()
    c.post("/hmrc/businesses")
    assert hmrc.issued == 2
    assert hmrc.requests[-1].headers["Authorization"] == "Bearer access-2"


def test_nino_validation(env, session_factory):
    c = logged_in()
    assert "doesn&#39;t look like" in c.post("/hmrc/nino", data={"nino": "AB12"}).text
    c.post("/hmrc/nino", data={"nino": "aa 12 34 56 a"})
    with session_factory() as s:
        assert s.query(User).one().nino == NINO


# ---------------------------------------------------- full submission


def test_link_review_and_send_update(env, session_factory, hmrc):
    c = logged_in()
    connect(c)
    c.post("/hmrc/nino", data={"nino": NINO})
    c.post("/hmrc/businesses")
    # Can't link to a business of the wrong type
    c.post(
        "/hmrc/link",
        data={"source_id": env["source"], "business_id": "XPIS00000000002"},
    )
    c.post(
        "/hmrc/link",
        data={"source_id": env["source"], "business_id": "XAIS00000000001"},
    )

    page = c.get("/hmrc?year=2026/27").text
    assert "Review and send" in page and "7 Aug 2026" in page

    review = c.get(f"/hmrc/update/{env['source']}?year=2026/27&end=2026-07-05").text
    assert "£2,500.00" in review and "£19.99" in review and "£999.00" not in review

    page = c.post(
        f"/hmrc/update/{env['source']}", data={"year": "2026/27", "end": "2026-07-05"}
    ).text
    assert "HMRC accepted" in page

    put = next(r for r in hmrc.requests if r.method == "PUT")
    assert put.method == "PUT"
    assert (
        put.url.path
        == f"/individuals/business/self-employment/{NINO}/XAIS00000000001/cumulative/2026-27"
    )
    assert put.headers["Accept"] == "application/vnd.hmrc.5.0+json"
    assert put.headers["Gov-Client-Connection-Method"] == "WEB_APP_VIA_SERVER"
    body = json.loads(put.content)
    assert body["periodDates"] == {
        "periodStartDate": "2026-04-06",
        "periodEndDate": "2026-07-05",
    }
    assert body["periodIncome"]["turnover"] == 2500.0

    with session_factory() as s:
        sub = s.query(Submission).one()
        assert (
            sub.succeeded and sub.status_code == 204 and sub.correlation_id == "corr-ok"
        )
        assert json.loads(sub.payload) == body


def test_rejected_update_is_recorded(env, session_factory):
    c = logged_in()
    connect(c)
    c.post("/hmrc/nino", data={"nino": NINO})
    with session_factory() as s:
        s.get(IncomeSource, env["source"]).hmrc_business_id = "XREJECT"
        s.commit()
    page = c.post(
        f"/hmrc/update/{env['source']}", data={"year": "2026/27", "end": "2026-07-05"}
    ).text
    assert "HMRC rejected" in page and "RULE_BOTH_EXPENSES_SUPPLIED" in page
    with session_factory() as s:
        sub = s.query(Submission).one()
        assert (
            not sub.succeeded
            and sub.status_code == 400
            and sub.correlation_id == "corr-bad"
        )


def test_send_requires_link_and_nino(env, session_factory):
    c = logged_in()
    connect(c)
    page = c.post(
        f"/hmrc/update/{env['source']}", data={"year": "2026/27", "end": "2026-07-05"}
    ).text
    assert "National Insurance number first" in page
    with session_factory() as s:
        assert s.query(Submission).count() == 0


def test_period_outside_tax_year_rejected(env):
    c = logged_in()
    page = c.get(f"/hmrc/update/{env['source']}?year=2026/27&end=2027-07-05").text
    assert "isn&#39;t in tax year" in page


def test_calculation(env):
    c = logged_in()
    connect(c)
    c.post("/hmrc/nino", data={"nino": NINO})
    page = c.post("/hmrc/calculation", data={"year": "2026/27"}).text
    assert "£1,555.50" in page and "£1,234.50" in page
    assert (
        "still working" in c.get("/hmrc/calculation?year=2026/27&id=calc-pending").text
    )


def test_other_users_source_not_accessible(env, session_factory):
    with session_factory() as s:
        s.add(
            User(
                email="b@example.com",
                name="Sam",
                password_hash=hash_password(PASSWORD),
                totp_secret=totp_secret(),
            )
        )
        s.commit()
    c = TestClient(app)
    sign_in(c, "b@example.com", PASSWORD)
    assert (
        c.get(f"/hmrc/update/{env['source']}?year=2026/27&end=2026-07-05").status_code
        == 404
    )


def test_sandbox_scenario_header_only_on_business_details(env, hmrc):
    c = logged_in()
    connect(c)
    c.post("/hmrc/nino", data={"nino": NINO})
    c.post("/hmrc/businesses")
    c.post("/hmrc/calculation", data={"year": "2026/27"})
    details = next(r for r in hmrc.requests if r.url.path.endswith("/list"))
    trigger = next(r for r in hmrc.requests if "/trigger/" in r.url.path)
    assert details.headers["Gov-Test-Scenario"] == "STATEFUL"
    assert "Gov-Test-Scenario" not in trigger.headers
    assert trigger.headers["Accept"] == "application/vnd.hmrc.9.0+json"


def test_quarters_listed_even_when_hmrc_has_no_obligation(env, session_factory, hmrc):
    c = logged_in()
    connect(c)
    c.post("/hmrc/nino", data={"nino": NINO})
    with session_factory() as s:
        s.get(IncomeSource, env["source"]).hmrc_business_id = "XNOTLISTED00001"
        s.commit()
    page = c.get("/hmrc?year=2026/27").text
    assert page.count("Delivery driving") >= 4
    assert f"/hmrc/update/{env['source']}?year=2026/27&end=2026-07-05" in page

    c.post(
        f"/hmrc/update/{env['source']}", data={"year": "2026/27", "end": "2026-07-05"}
    )
    assert "Sent " in c.get("/hmrc?year=2026/27").text
