"""The store's database tables. Money is always stored as integer cents."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from .db import now


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True)
    name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(default=now)


class LoginSession(Base):
    """A signed-in browser. Only a hash of the token is stored."""

    __tablename__ = "login_sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=now)
    expires_at: Mapped[datetime]
    revoked: Mapped[bool] = mapped_column(default=False)


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    slug: Mapped[str] = mapped_column(String(100), unique=True)

    products: Mapped[list[Product]] = relationship(back_populates="category")


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("price_cents >= 0", name="product_price_not_negative"),
        CheckConstraint("stock >= 0", name="product_stock_not_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(String(40), unique=True)
    title: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), index=True)
    price_cents: Mapped[int]
    stock: Mapped[int] = mapped_column(default=0)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    category: Mapped[Category] = relationship(back_populates="products")


class CartLine(Base):
    """A line in a shopper's own cart, or in the separate cart of one agent run."""

    __tablename__ = "cart_lines"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="cart_quantity_positive"),
        Index(
            "one_line_per_product_in_own_cart",
            "user_id",
            "product_id",
            unique=True,
            sqlite_where=text("run_id IS NULL"),
            postgresql_where=text("run_id IS NULL"),
        ),
        UniqueConstraint("run_id", "product_id", name="one_line_per_product_in_run_cart"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"))
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    quantity: Mapped[int]

    product: Mapped[Product] = relationship()


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("total_cents >= 0", name="order_total_not_negative"),
        UniqueConstraint("user_id", "idempotency_key", name="one_order_per_checkout_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("agent_runs.id"))
    status: Mapped[str] = mapped_column(String(20), default="paid")
    total_cents: Mapped[int]
    idempotency_key: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=now)

    lines: Mapped[list[OrderLine]] = relationship(back_populates="order", cascade="all")
    payment: Mapped[Payment] = relationship(back_populates="order", cascade="all", uselist=False)


class OrderLine(Base):
    """What was bought, kept as it was at the time even if the product changes later."""

    __tablename__ = "order_lines"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="order_quantity_positive"),
        CheckConstraint("unit_price_cents >= 0", name="order_price_not_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"))
    sku: Mapped[str] = mapped_column(String(40))
    title: Mapped[str] = mapped_column(String(200))
    unit_price_cents: Mapped[int]
    quantity: Mapped[int]

    order: Mapped[Order] = relationship(back_populates="lines")


class Payment(Base):
    """A simulated charge. Card numbers are never stored, only the last four digits."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), unique=True)
    reference: Mapped[str] = mapped_column(String(40))
    amount_cents: Mapped[int]
    card_last4: Mapped[str] = mapped_column(String(4))
    created_at: Mapped[datetime] = mapped_column(default=now)

    order: Mapped[Order] = relationship(back_populates="payment")


class AgentRun(Base):
    """One request handed to the agent team."""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    request: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="running")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    created_at: Mapped[datetime] = mapped_column(default=now)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)

    events: Mapped[list[AgentEvent]] = relationship(cascade="all", order_by="AgentEvent.seq")
    questions: Mapped[list[AgentQuestion]] = relationship(
        cascade="all", order_by="AgentQuestion.id"
    )


class AgentEvent(Base):
    """One entry in a run's activity record."""

    __tablename__ = "agent_events"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="one_event_per_sequence"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int]
    type: Mapped[str] = mapped_column(String(20))
    agent: Mapped[str] = mapped_column(String(40))
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=now)


class AgentQuestion(Base):
    """Something a run needs from its shopper: an answer, or approval of a purchase."""

    __tablename__ = "agent_questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="question")
    text: Mapped[str] = mapped_column(Text)
    data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    answer: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(default=now)
    answered_at: Mapped[datetime | None] = mapped_column(default=None)
