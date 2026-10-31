"""Set up users, income sources and properties.

    python -m mtd_app.cli create-user --email you@example.com --name "Your Name"
    python -m mtd_app.cli add-source --email you@example.com --type self-employment \\
        --name "Delivery driving" --vehicle-method simplified
    python -m mtd_app.cli add-property --name "1 High St" \\
        --owner you@example.com:50 --owner partner@example.com:50
    python -m mtd_app.cli list
"""
import argparse
import getpass
import sys
from decimal import Decimal

from sqlalchemy import select

from .auth import hash_password
from .db import SessionLocal
from .models import IncomeSource, Property, PropertyOwner, User


def _user(db, email: str) -> User:
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if not user:
        sys.exit(f"No user with email {email}")
    return user


def create_user(db, args):
    email = args.email.strip().lower()
    if db.scalar(select(User).where(User.email == email)):
        sys.exit(f"{email} already exists")
    password = getpass.getpass("Password: ")
    if len(password) < 10:
        sys.exit("Use at least 10 characters")
    if password != getpass.getpass("Repeat password: "):
        sys.exit("Passwords don't match")
    db.add(User(email=email, name=args.name, password_hash=hash_password(password),
                calendar_quarters=args.calendar_quarters))
    db.commit()
    print(f"Created {email}")


def set_password(db, args):
    user = _user(db, args.email)
    password = getpass.getpass("New password: ")
    if len(password) < 10:
        sys.exit("Use at least 10 characters")
    if password != getpass.getpass("Repeat password: "):
        sys.exit("Passwords don't match")
    user.password_hash = hash_password(password)
    db.commit()
    print(f"Password updated for {user.email}")


def reset_mfa(db, args):
    user = _user(db, args.email)
    user.totp_secret = None
    user.totp_last_step = None
    db.commit()
    print(f"Two-step sign-in reset for {user.email}: they'll set it up again next time they sign in")


def add_source(db, args):
    user = _user(db, args.email)
    if args.type == "uk-property" and any(s.type == "uk-property" for s in user.sources):
        sys.exit(f"{user.email} already has a UK property business")
    method = args.vehicle_method if args.type == "self-employment" else None
    db.add(IncomeSource(user=user, type=args.type, name=args.name, vehicle_method=method))
    db.commit()
    print(f"Added {args.type} '{args.name}' for {user.email}")


def add_property(db, args):
    owners = []
    for spec in args.owner:
        email, _, pct = spec.rpartition(":")
        if not email:
            sys.exit(f"--owner must look like email:percent, not '{spec}'")
        owners.append((_user(db, email), int(Decimal(pct) * 100)))
    if sum(bp for _, bp in owners) > 10_000:
        sys.exit("Shares add up to more than 100%")

    prop = Property(name=args.name)
    db.add(prop)
    for user, bp in owners:
        db.add(PropertyOwner(property=prop, user=user, share_bp=bp))
        if not any(s.type == "uk-property" for s in user.sources):
            db.add(IncomeSource(user=user, type="uk-property", name="UK property"))
    db.commit()
    print(f"Added property '{args.name}'")


def list_all(db, args):
    for user in db.scalars(select(User).order_by(User.id)):
        print(f"{user.name} <{user.email}>")
        for s in user.sources:
            extra = f", {s.vehicle_method} vehicle costs" if s.vehicle_method else ""
            print(f"  [{s.id}] {s.type}: {s.name}{extra}")
        for h in user.property_shares:
            print(f"  property [{h.property.id}] {h.property.name}: {h.share_bp / 100:g}%")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m mtd_app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("create-user")
    p.add_argument("--email", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--calendar-quarters", action="store_true",
                   help="Use calendar quarters (to 30 Jun, 30 Sep, 31 Dec, 5 Apr)")
    p.set_defaults(func=create_user)

    p = sub.add_parser("set-password")
    p.add_argument("--email", required=True)
    p.set_defaults(func=set_password)

    p = sub.add_parser("reset-mfa", help="For a lost phone: set up two-step sign-in again")
    p.add_argument("--email", required=True)
    p.set_defaults(func=reset_mfa)

    p = sub.add_parser("add-source")
    p.add_argument("--email", required=True)
    p.add_argument("--type", required=True, choices=["self-employment", "uk-property"])
    p.add_argument("--name", required=True)
    p.add_argument("--vehicle-method", choices=["actual", "simplified"], default="actual")
    p.set_defaults(func=add_source)

    p = sub.add_parser("add-property")
    p.add_argument("--name", required=True)
    p.add_argument("--owner", action="append", required=True, metavar="EMAIL:PERCENT")
    p.set_defaults(func=add_property)

    sub.add_parser("list").set_defaults(func=list_all)

    args = parser.parse_args(argv)
    with SessionLocal() as db:
        args.func(db, args)


if __name__ == "__main__":
    main()
