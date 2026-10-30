"""HMRC API settings, read from the environment or a local .env.hmrc file.

    HMRC_CLIENT_ID=...
    HMRC_CLIENT_SECRET=...
    HMRC_ENV=sandbox                 # or production
    HMRC_REDIRECT_URI=http://localhost:8000/hmrc/callback
    HMRC_VENDOR_PUBLIC_IP=           # the server's public IP, once hosted
"""
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet

ENV_FILE = Path(".env.hmrc")

BASES = {
    "sandbox": ("https://test-api.service.hmrc.gov.uk", "https://test-www.tax.service.gov.uk"),
    "production": ("https://api.service.hmrc.gov.uk", "https://www.tax.service.gov.uk"),
}

SCOPES = "read:self-assessment write:self-assessment"
PRODUCT_NAME = "MTD records"
PRODUCT_VERSION = "0.3.0"


def _read_env_file(path: Path) -> dict[str, str]:
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class HmrcConfig:
    client_id: str
    client_secret: str
    env: str = "sandbox"
    redirect_uri: str = "http://localhost:8000/hmrc/callback"
    vendor_public_ip: str = ""

    @property
    def api_base(self) -> str:
        return BASES[self.env][0]

    @property
    def authorize_url(self) -> str:
        return BASES[self.env][1] + "/oauth/authorize"

    @property
    def token_url(self) -> str:
        return self.api_base + "/oauth/token"


def load_config() -> HmrcConfig | None:
    """None until the client ID and secret have been set up."""
    values = {**_read_env_file(ENV_FILE), **os.environ}
    if not values.get("HMRC_CLIENT_ID") or not values.get("HMRC_CLIENT_SECRET"):
        return None
    env = values.get("HMRC_ENV", "sandbox")
    if env not in BASES:
        raise ValueError(f"HMRC_ENV must be sandbox or production, not {env!r}")
    return HmrcConfig(
        client_id=values["HMRC_CLIENT_ID"],
        client_secret=values["HMRC_CLIENT_SECRET"],
        env=env,
        redirect_uri=values.get("HMRC_REDIRECT_URI", HmrcConfig.redirect_uri),
        vendor_public_ip=values.get("HMRC_VENDOR_PUBLIC_IP", ""),
    )


def _fernet() -> Fernet:
    """Key for encrypting stored HMRC tokens: MTD_ENCRYPTION_KEY, or one
    generated once into .encryption_key (gitignored)."""
    key = os.environ.get("MTD_ENCRYPTION_KEY")
    if not key:
        path = Path(".encryption_key")
        if not path.exists():
            path.write_text(Fernet.generate_key().decode())
            path.chmod(0o600)
        key = path.read_text().strip()
    return Fernet(key.encode())


def encrypt(text: str) -> str:
    return _fernet().encrypt(text.encode()).decode()


def decrypt(token: str) -> str:
    return _fernet().decrypt(token.encode()).decode()


def new_state() -> str:
    return secrets.token_urlsafe(24)
