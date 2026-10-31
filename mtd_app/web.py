"""Shared web plumbing: templates, DB session, login and flash messages."""
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from . import categories
from .db import SessionLocal
from .models import User
from .money import format_pence

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["money"] = format_pence
templates.env.globals["category_label"] = categories.label
templates.env.globals["categories"] = categories.BY_SOURCE_TYPE


class LoginRequired(Exception):
    pass


def get_db():
    with SessionLocal() as session:
        yield session


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    user = db.get(User, user_id) if user_id else None
    if not user:
        raise LoginRequired()
    return user


DB = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(current_user)]


def flash(request: Request, message: str, kind: str = "ok"):
    request.session.setdefault("flash", []).append([kind, message])


def render(request: Request, template: str, **ctx):
    ctx["flashes"] = request.session.pop("flash", [])
    return templates.TemplateResponse(request, template, ctx)


def redirect(url: str):
    return RedirectResponse(url, status_code=303)
