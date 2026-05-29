"""
User ORM model.
"""
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_provider: Mapped[str | None] = mapped_column(Text, nullable=True)
    oauth_sub: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(Text, nullable=False, default="customer")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    failed_login_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Relationships
    virtual_keys: Mapped[list["VirtualKey"]] = relationship(  # noqa: F821
        "VirtualKey", back_populates="user", lazy="select"
    )
    credit_account: Mapped["CreditAccount | None"] = relationship(  # noqa: F821
        "CreditAccount", back_populates="user", uselist=False, lazy="select"
    )
    credit_transactions: Mapped[list["CreditTransaction"]] = relationship(  # noqa: F821
        "CreditTransaction", back_populates="user", lazy="select"
    )
    usage_records: Mapped[list["UsageRecord"]] = relationship(  # noqa: F821
        "UsageRecord", back_populates="user", lazy="select"
    )
