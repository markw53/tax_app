"""Adding people, income sources and properties from the browser.

The first account is created at /setup, which only works while there are
no users. After that, any signed-in user can add people at /people, and
change their own password at /account/password.
"""
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import accounts
from .accounts import AccountError
from .models import User
from .web import DB, CurrentUser, flash, redirect, render

router = APIRouter()


def has_users(db: Session) -> bool:
    return bool(db.scalar(select(func.count()).select_from(User)))


def _create(request: Request, db: Session, back: str, **fields) -> User | None:
    try:
        user = accounts.create_user(db, **fields)
        db.commit()
        return user
    except AccountError as e:
        db.rollback()
        flash(request, str(e), "error")
        return None


# ---------------------------------------------------------- first account

@router.get("/setup")
def setup_form(request: Request, db: DB):
    if has_users(db):
        return redirect("/login")
    return render(request, "setup.html", work_types=accounts.WORK_TYPES)


@router.post("/setup")
def setup(request: Request, db: DB,
          name: Annotated[str, Form()], email: Annotated[str, Form()],
          password: Annotated[str, Form()], repeat: Annotated[str, Form()],
          work: Annotated[list[str], Form()] = [],
          calendar_quarters: Annotated[bool, Form()] = False):
    if has_users(db):
        raise HTTPException(404)
    user = _create(request, db, "/setup", email=email, name=name, password=password,
                   repeat=repeat, calendar_quarters=calendar_quarters, work=work)
    if not user:
        return redirect("/setup")
    flash(request, "Account created. Sign in to set up your authenticator app.")
    return redirect("/login")


# ---------------------------------------------------------------- password

@router.get("/account/password")
def password_form(request: Request, user: CurrentUser):
    return render(request, "password.html", user=user)


@router.post("/account/password")
def change_password(request: Request, db: DB, user: CurrentUser,
                    current: Annotated[str, Form()], password: Annotated[str, Form()],
                    repeat: Annotated[str, Form()]):
    try:
        accounts.change_password(user, current, password, repeat)
    except AccountError as e:
        flash(request, str(e), "error")
        return redirect("/account/password")
    db.commit()
    flash(request, "Your password has been changed.")
    return redirect("/")


# ------------------------------------------------------------------ people

@router.get("/people")
def people(request: Request, db: DB, user: CurrentUser):
    everyone = db.scalars(select(User).order_by(User.name)).all()
    return render(request, "people.html", user=user, people=everyone,
                  work_types=accounts.WORK_TYPES)


@router.post("/people")
def add_person(request: Request, db: DB, user: CurrentUser,
               name: Annotated[str, Form()], email: Annotated[str, Form()],
               password: Annotated[str, Form()], repeat: Annotated[str, Form()],
               work: Annotated[list[str], Form()] = [],
               calendar_quarters: Annotated[bool, Form()] = False):
    new = _create(request, db, "/people", email=email, name=name, password=password,
                  repeat=repeat, calendar_quarters=calendar_quarters, work=work)
    if new:
        flash(request, f"Added {new.name}. They sign in with {new.email} and the password you chose, "
                       "then set up their own authenticator app. They can change the password "
                       "by clicking their name at the top.")
    return redirect("/people")


@router.post("/people/work")
def add_my_work(request: Request, db: DB, user: CurrentUser, work: Annotated[str, Form()]):
    try:
        source = accounts.add_work(db, user, work)
    except AccountError as e:
        flash(request, str(e), "error")
        return redirect("/people")
    label = accounts.WORK_TYPES[work][0]
    if source is None:
        flash(request, f"You already have {label} set up.", "error")
    else:
        db.commit()
        flash(request, f"Added {label} to your account.")
    return redirect("/people")


@router.post("/people/properties")
async def add_property(request: Request, db: DB, user: CurrentUser):
    form = await request.form()
    try:
        owners = []
        for person in db.scalars(select(User).order_by(User.id)):
            text = str(form.get(f"share_{person.id}", "")).strip()
            if text:
                owners.append((person, accounts.parse_share(text)))
        if owners and user.id not in {p.id for p, _ in owners}:
            raise AccountError("You can only add a property you own a share of.")
        prop = accounts.add_property(db, str(form.get("name", "")), owners)
        db.commit()
    except AccountError as e:
        db.rollback()
        flash(request, str(e), "error")
        return redirect("/people")
    flash(request, f"Added {prop.name}.")
    return redirect("/people")
