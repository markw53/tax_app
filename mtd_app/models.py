"""Digital records. Money is stored as integer pence."""
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    # Elected to use calendar quarters (to 30 Jun, 30 Sep, 31 Dec) for updates
    calendar_quarters: Mapped[bool] = mapped_column(default=False)
    nino: Mapped[str | None] = mapped_column(String(9))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    sources: Mapped[list["IncomeSource"]] = relationship(back_populates="user")
    property_shares: Mapped[list["PropertyOwner"]] = relationship(back_populates="user")
    hmrc: Mapped["HmrcConnection | None"] = relationship(back_populates="user")


class IncomeSource(Base):
    """An HMRC 'business': a sole trade, or the person's UK property business."""
    __tablename__ = "income_sources"
    __table_args__ = (
        CheckConstraint("type IN ('self-employment', 'uk-property')"),
        CheckConstraint("vehicle_method IS NULL OR vehicle_method IN ('actual', 'simplified')"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    type: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(100))
    hmrc_business_id: Mapped[str | None] = mapped_column(String(20))
    # Self-employment only: claim actual vehicle costs or simplified mileage
    vehicle_method: Mapped[str | None] = mapped_column(String(10))

    user: Mapped[User] = relationship(back_populates="sources")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="source")
    trips: Mapped[list["MileageTrip"]] = relationship(back_populates="source")


class Property(Base):
    __tablename__ = "properties"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))

    owners: Mapped[list["PropertyOwner"]] = relationship(back_populates="property")
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="property")


class PropertyOwner(Base):
    """Each owner reports their share of a property's income and expenses.
    Spouses default to 50/50 unless they've sent HMRC a Form 17."""
    __tablename__ = "property_owners"
    __table_args__ = (
        UniqueConstraint("property_id", "user_id"),
        CheckConstraint("share_bp > 0 AND share_bp <= 10000"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    property_id: Mapped[int] = mapped_column(ForeignKey("properties.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    share_bp: Mapped[int] = mapped_column(Integer)  # basis points: 5000 = 50%

    property: Mapped[Property] = relationship(back_populates="owners")
    user: Mapped[User] = relationship(back_populates="property_shares")


class Transaction(Base):
    """A self-employment transaction belongs to a source; a property
    transaction belongs to a property and is split between its owners."""
    __tablename__ = "transactions"
    __table_args__ = (
        CheckConstraint("kind IN ('income', 'expense')"),
        CheckConstraint("amount_pence >= 0"),
        CheckConstraint("(source_id IS NULL) != (property_id IS NULL)"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    kind: Mapped[str] = mapped_column(String(10))
    category: Mapped[str] = mapped_column(String(40))
    amount_pence: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(String(255), default="")
    source_id: Mapped[int | None] = mapped_column(ForeignKey("income_sources.id"), index=True)
    property_id: Mapped[int | None] = mapped_column(ForeignKey("properties.id"), index=True)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    source: Mapped[IncomeSource | None] = relationship(back_populates="transactions")
    property: Mapped[Property | None] = relationship(back_populates="transactions")


class MileageTrip(Base):
    __tablename__ = "mileage_trips"
    __table_args__ = (
        CheckConstraint("vehicle IN ('car_van', 'motorcycle')"),
        CheckConstraint("miles_tenths > 0"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("income_sources.id"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    miles_tenths: Mapped[int] = mapped_column(Integer)
    vehicle: Mapped[str] = mapped_column(String(10), default="car_van")
    description: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    source: Mapped[IncomeSource] = relationship(back_populates="trips")


class HmrcConnection(Base):
    """A user's HMRC authorisation. Tokens are encrypted (hmrc_config.encrypt)."""
    __tablename__ = "hmrc_connections"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    env: Mapped[str] = mapped_column(String(10))
    access_token: Mapped[str] = mapped_column(String(2000))
    refresh_token: Mapped[str] = mapped_column(String(2000))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    connected_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped[User] = relationship(back_populates="hmrc")


class Submission(Base):
    """Audit trail of every update sent to HMRC, successful or not."""
    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("income_sources.id"), index=True)
    env: Mapped[str] = mapped_column(String(10))
    tax_year: Mapped[str] = mapped_column(String(7))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    payload: Mapped[str] = mapped_column(Text)
    status_code: Mapped[int | None] = mapped_column(Integer)
    correlation_id: Mapped[str | None] = mapped_column(String(100))
    error: Mapped[str | None] = mapped_column(Text)
    succeeded: Mapped[bool] = mapped_column(default=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    source: Mapped[IncomeSource] = relationship()
