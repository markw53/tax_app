"""Time-based one-time codes (RFC 6238) from an authenticator app."""
import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

import segno

from .hmrc_config import PRODUCT_NAME

STEP = 30
DIGITS = 6
# Accept the code before and after the current one, for clock drift
WINDOW = 1


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode()


def code_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def current_step(now: float | None = None) -> int:
    return int((time.time() if now is None else now) // STEP)


def verify(secret: str, code: str, last_step: int | None, now: float | None = None) -> int | None:
    """The time step the code belongs to, or None if it's wrong or was
    already used (a step at or before last_step)."""
    code = "".join(code.split())
    if not (len(code) == DIGITS and code.isdigit()):
        return None
    step = current_step(now)
    for candidate in range(step - WINDOW, step + WINDOW + 1):
        if last_step is not None and candidate <= last_step:
            continue
        if hmac.compare_digest(code_at(secret, candidate), code):
            return candidate
    return None


def otpauth_uri(secret: str, email: str) -> str:
    label = quote(f"{PRODUCT_NAME}:{email}")
    return f"otpauth://totp/{label}?" + urlencode({"secret": secret, "issuer": PRODUCT_NAME})


def qr_svg(uri: str) -> str:
    return segno.make(uri, error="m").svg_inline(scale=5, border=2, dark="#000", light="#fff")


def reference(secret: str) -> str:
    """Identifies the authenticator for HMRC without revealing the secret."""
    return hashlib.sha256(secret.encode()).hexdigest()
