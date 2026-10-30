"""HMRC fraud prevention headers for the WEB_APP_VIA_SERVER connection method.

Browser details are collected by the script in base.html into the
mtd_client cookie; network details come from the request. Headers we can't
collect are left out rather than faked, as HMRC requires.
"""
import ipaddress
import json
import os
from datetime import datetime, timezone
from urllib.parse import quote, unquote

from fastapi import Request

from .hmrc_config import PRODUCT_NAME, PRODUCT_VERSION, HmrcConfig

CLIENT_COOKIE = "mtd_client"
APP_KEY = "mtd-records"


def _enc(value) -> str:
    return quote(str(value), safe="")


def _pairs(**kv) -> str:
    return "&".join(f"{_enc(k).replace('_', '-')}={_enc(v)}" for k, v in kv.items())


def _public_ip(value: str | None) -> str | None:
    try:
        ip = ipaddress.ip_address((value or "").strip())
    except ValueError:
        return None
    return str(ip) if ip.is_global else None


def client_address(request: Request) -> tuple[str | None, int | None]:
    """The user's public IP and port. Behind a reverse proxy (set
    MTD_TRUST_PROXY=1), the first X-Forwarded-For hop is the client."""
    if os.environ.get("MTD_TRUST_PROXY") == "1":
        forwarded = request.headers.get("x-forwarded-for", "").split(",")[0]
        port = request.headers.get("x-forwarded-port")
        return _public_ip(forwarded), int(port) if port and port.isdigit() else None
    if request.client:
        return _public_ip(request.client.host), request.client.port
    return None, None


def browser_data(request: Request) -> dict:
    raw = request.cookies.get(CLIENT_COOKIE)
    if not raw:
        return {}
    try:
        data = json.loads(unquote(raw))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def build(request: Request, user_id: int, config: HmrcConfig,
          now: datetime | None = None) -> dict[str, str]:
    now = now or datetime.now(timezone.utc)
    data = browser_data(request)
    headers = {
        "Gov-Client-Connection-Method": "WEB_APP_VIA_SERVER",
        "Gov-Client-User-IDs": _pairs(**{APP_KEY: user_id}),
        "Gov-Vendor-Product-Name": _enc(PRODUCT_NAME),
        "Gov-Vendor-Version": _pairs(**{APP_KEY: PRODUCT_VERSION}),
    }

    if isinstance(data.get("ua"), str) and data["ua"]:
        headers["Gov-Client-Browser-JS-User-Agent"] = data["ua"]
    if isinstance(data.get("device"), str) and data["device"]:
        headers["Gov-Client-Device-ID"] = data["device"]
    if isinstance(data.get("tz"), str) and data["tz"].startswith("UTC"):
        headers["Gov-Client-Timezone"] = data["tz"]
    screens = [s for s in data.get("screens") or [] if isinstance(s, dict)]
    if screens:
        headers["Gov-Client-Screens"] = ",".join(
            _pairs(width=s.get("w"), height=s.get("h"),
                   scaling_factor=s.get("s"), colour_depth=s.get("d"))
            for s in screens)
    win = data.get("win")
    if isinstance(win, dict) and win.get("w") and win.get("h"):
        headers["Gov-Client-Window-Size"] = _pairs(width=win["w"], height=win["h"])

    client_ip, client_port = client_address(request)
    if client_ip:
        headers["Gov-Client-Public-IP"] = client_ip
        headers["Gov-Client-Public-IP-Timestamp"] = (
            now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z")
        if client_port:
            headers["Gov-Client-Public-Port"] = str(client_port)

    vendor_ip = _public_ip(config.vendor_public_ip)
    if vendor_ip:
        headers["Gov-Vendor-Public-IP"] = vendor_ip
        if client_ip:
            headers["Gov-Vendor-Forwarded"] = _pairs(**{"by": vendor_ip, "for": client_ip})
    return headers


REQUIRED = [
    "Gov-Client-Connection-Method", "Gov-Client-Browser-JS-User-Agent", "Gov-Client-Device-ID",
    "Gov-Client-Public-IP", "Gov-Client-Public-IP-Timestamp", "Gov-Client-Public-Port",
    "Gov-Client-Screens", "Gov-Client-Timezone", "Gov-Client-User-IDs", "Gov-Client-Window-Size",
    "Gov-Vendor-Forwarded", "Gov-Vendor-Product-Name", "Gov-Vendor-Public-IP", "Gov-Vendor-Version",
]


def missing(headers: dict[str, str]) -> list[str]:
    return [h for h in REQUIRED if h not in headers]
