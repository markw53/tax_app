from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import User

_hasher = PasswordHasher()
# Verified against when the email is unknown, so timing doesn't reveal it
_DUMMY_HASH = _hasher.hash("not-a-real-password")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(user: User, password: str) -> bool:
    try:
        return _hasher.verify(user.password_hash, password)
    except VerifyMismatchError:
        return False


def authenticate(session: Session, email: str, password: str) -> User | None:
    user = session.scalar(select(User).where(User.email == email.strip().lower()))
    try:
        _hasher.verify(user.password_hash if user else _DUMMY_HASH, password)
    except VerifyMismatchError:
        return None
    return user
