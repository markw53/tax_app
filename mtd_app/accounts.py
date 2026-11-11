"""Creating users, their income sources and properties."""
import re
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import hash_password, verify_password
from .models import IncomeSource, Property, PropertyOwner, User

MIN_PASSWORD = 10

# The kinds of work offered when adding someone:
# key -> (label, HMRC business type, source name, vehicle method)
WORK_TYPES = {
    "driver": ("Self-employed driver", "self-employment", "Delivery driving", "simplified"),
    "exam-writer": ("AQA exam writer", "self-employment", "AQA exam writing", "actual"),
    "landlord": ("Landlord", "uk-property", "UK property", None),
}


class AccountError(ValueError):
    """A problem with what was entered, worded for the person entering it."""


def add_work(db: Session, user: User, key: str) -> IncomeSource | None:
    """Give the user an income source for this kind of work. None if they
    already have it (a person has one UK property business, and one source
    per trade)."""
    if key not in WORK_TYPES:
        raise AccountError("Choose a type of work from the list.")
    _, type_, name, vehicle_method = WORK_TYPES[key]
    for s in user.sources:
        if s.type == type_ and (type_ == "uk-property" or s.name == name):
            return None
    source = IncomeSource(user=user, type=type_, name=name, vehicle_method=vehicle_method)
    db.add(source)
    return source


def create_user(db: Session, *, email: str, name: str, password: str, repeat: str,
                calendar_quarters: bool = False, work: list[str] = ()) -> User:
    email = email.strip().lower()
    name = name.strip()
    if not name:
        raise AccountError("Enter a name.")
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise AccountError("Enter a valid email address.")
    if db.scalar(select(User).where(User.email == email)):
        raise AccountError(f"{email} already has an account.")
    if len(password) < MIN_PASSWORD:
        raise AccountError(f"Use a password of at least {MIN_PASSWORD} characters.")
    if password != repeat:
        raise AccountError("The two passwords don't match.")
    if not work:
        raise AccountError("Choose at least one type of work.")
    if any(key not in WORK_TYPES for key in work):
        raise AccountError("Choose a type of work from the list.")

    user = User(email=email, name=name, password_hash=hash_password(password),
                calendar_quarters=calendar_quarters)
    db.add(user)
    for key in work:
        add_work(db, user, key)
    return user


def change_password(user: User, current: str, password: str, repeat: str):
    if not verify_password(user, current):
        raise AccountError("Your current password isn't right.")
    if len(password) < MIN_PASSWORD:
        raise AccountError(f"Use a password of at least {MIN_PASSWORD} characters.")
    if password != repeat:
        raise AccountError("The two new passwords don't match.")
    if password == current:
        raise AccountError("Choose a password different from your current one.")
    user.password_hash = hash_password(password)


def parse_share(text: str) -> int:
    """'50' or '33.33' (percent) -> basis points."""
    try:
        pct = Decimal(text.strip().rstrip("%"))
    except InvalidOperation:
        raise AccountError(f"'{text}' isn't a percentage.") from None
    bp = int(pct * 100)
    if not 0 < bp <= 10_000:
        raise AccountError("Each share must be more than 0% and no more than 100%.")
    return bp


def add_property(db: Session, name: str, owners: list[tuple[User, int]]) -> Property:
    """owners: (user, share in basis points). Owners without a UK property
    business get one."""
    name = name.strip()
    if not name:
        raise AccountError("Enter a name or address for the property.")
    if not owners:
        raise AccountError("Give at least one owner a share.")
    if sum(bp for _, bp in owners) > 10_000:
        raise AccountError("The shares add up to more than 100%.")
    prop = Property(name=name)
    db.add(prop)
    for user, bp in owners:
        db.add(PropertyOwner(property=prop, user=user, share_bp=bp))
        add_work(db, user, "landlord")
    return prop
