"""Thin client for HMRC's OAuth and MTD Income Tax APIs."""
from datetime import date
from urllib.parse import urlencode

import httpx

from .hmrc_config import SCOPES, HmrcConfig

# API versions, sent in the Accept header. Check these against the versions
# your Developer Hub application is subscribed to.
VERSIONS = {
    "business-details": "2.0",
    "obligations": "3.0",
    "self-employment": "5.0",
    "property": "6.0",
    "calculations": "9.0",
    "fraud-test": "1.0",
}

# The sandbox only returns test data created through the test-support APIs
# when asked for it with this header.
SANDBOX_SCENARIOS = {"business-details": "STATEFUL"}


class HmrcError(Exception):
    def __init__(self, status: int, body: dict | None, correlation_id: str | None = None):
        body = body or {}
        self.status = status
        self.code = body.get("code") or body.get("error") or f"HTTP_{status}"
        self.message = body.get("message") or body.get("error_description") or "HMRC returned an error"
        self.errors = body.get("errors") or []
        self.correlation_id = correlation_id
        super().__init__(f"{self.code}: {self.message}")

    def describe(self) -> str:
        details = "; ".join(f"{e.get('code')}: {e.get('message')}" for e in self.errors)
        return f"{self.message} ({self.code})" + (f" — {details}" if details else "")


def tax_year_param(tax_year: str) -> str:
    """'2026/27' -> '2026-27'"""
    return tax_year.replace("/", "-")


def _json(resp: httpx.Response) -> dict | None:
    try:
        data = resp.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


class HmrcClient:
    def __init__(self, config: HmrcConfig, http: httpx.Client | None = None):
        self.config = config
        self.http = http or httpx.Client(timeout=30)

    # ------------------------------------------------------------- OAuth

    def authorize_url(self, state: str) -> str:
        return self.config.authorize_url + "?" + urlencode({
            "response_type": "code",
            "client_id": self.config.client_id,
            "scope": SCOPES,
            "state": state,
            "redirect_uri": self.config.redirect_uri,
        })

    def _token(self, **form) -> dict:
        resp = self.http.post(self.config.token_url, data={
            "client_id": self.config.client_id,
            "client_secret": self.config.client_secret,
            **form,
        })
        if resp.status_code != 200:
            raise HmrcError(resp.status_code, _json(resp))
        return resp.json()

    def exchange_code(self, code: str) -> dict:
        return self._token(grant_type="authorization_code", code=code,
                           redirect_uri=self.config.redirect_uri)

    def refresh(self, refresh_token: str) -> dict:
        return self._token(grant_type="refresh_token", refresh_token=refresh_token)

    # -------------------------------------------------------------- APIs

    def _call(self, method: str, path: str, api: str, token: str, fraud: dict[str, str],
              json: dict | None = None, params: dict | None = None) -> httpx.Response:
        headers = {
            "Accept": f"application/vnd.hmrc.{VERSIONS[api]}+json",
            "Authorization": f"Bearer {token}",
            **fraud,
        }
        if self.config.env == "sandbox" and api in SANDBOX_SCENARIOS:
            headers["Gov-Test-Scenario"] = SANDBOX_SCENARIOS[api]
        resp = self.http.request(method, self.config.api_base + path, headers=headers,
                                 json=json, params=params)
        if resp.status_code >= 400:
            raise HmrcError(resp.status_code, _json(resp), resp.headers.get("X-CorrelationId"))
        return resp

    def list_businesses(self, token, fraud, nino: str) -> list[dict]:
        resp = self._call("GET", f"/individuals/business/details/{nino}/list",
                          "business-details", token, fraud)
        return resp.json().get("listOfBusinesses", [])

    def obligations(self, token, fraud, nino: str, start: date, end: date) -> list[dict]:
        try:
            resp = self._call("GET", f"/obligations/details/{nino}/income-and-expenditure",
                              "obligations", token, fraud,
                              params={"fromDate": start.isoformat(), "toDate": end.isoformat()})
        except HmrcError as e:
            if e.status == 404:  # no obligations in range
                return []
            raise
        return resp.json().get("obligations", [])

    def submit_self_employment(self, token, fraud, nino: str, business_id: str,
                               tax_year: str, body: dict) -> httpx.Response:
        return self._call(
            "PUT", f"/individuals/business/self-employment/{nino}/{business_id}"
                   f"/cumulative/{tax_year_param(tax_year)}",
            "self-employment", token, fraud, json=body)

    def submit_uk_property(self, token, fraud, nino: str, business_id: str,
                           tax_year: str, body: dict) -> httpx.Response:
        return self._call(
            "PUT", f"/individuals/business/property/uk/{nino}/{business_id}"
                   f"/cumulative/{tax_year_param(tax_year)}",
            "property", token, fraud, json=body)

    def trigger_calculation(self, token, fraud, nino: str, tax_year: str,
                            calculation_type: str = "in-year") -> str:
        resp = self._call(
            "POST", f"/individuals/calculations/{nino}/self-assessment"
                    f"/{tax_year_param(tax_year)}/trigger/{calculation_type}",
            "calculations", token, fraud)
        return resp.json()["calculationId"]

    def get_calculation(self, token, fraud, nino: str, tax_year: str, calculation_id: str) -> dict:
        resp = self._call(
            "GET", f"/individuals/calculations/{nino}/self-assessment"
                   f"/{tax_year_param(tax_year)}/{calculation_id}",
            "calculations", token, fraud)
        return resp.json()

    def validate_fraud_headers(self, token, fraud) -> dict:
        """Sandbox only: HMRC's checker for fraud prevention headers."""
        resp = self._call("GET", "/test/fraud-prevention-headers/validate",
                          "fraud-test", token, fraud)
        return resp.json()
