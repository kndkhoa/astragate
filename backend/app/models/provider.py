"""
Provider ORM model.
"""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, Text, func
from app.database import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Provider(Base):
    __tablename__ = "providers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    balance_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), nullable=False, default=Decimal("0")
    )
    warning_threshold: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("10.00")
    )
    hard_stop_threshold: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("2.00")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="normal")
    fallback_provider_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("providers.id"), nullable=True
    )
    last_warning_alert_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    hard_stop_activated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    encrypted_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    # Self-referential relationship for fallback
    fallback_provider: Mapped["Provider | None"] = relationship(
        "Provider",
        foreign_keys=[fallback_provider_id],
        remote_side="Provider.id",
        lazy="select",
    )

    # Relationships
    models: Mapped[list["Model"]] = relationship(  # noqa: F821
        "Model", back_populates="provider", lazy="select"
    )
    markup_configs: Mapped[list["MarkupConfig"]] = relationship(  # noqa: F821
        "MarkupConfig", back_populates="provider", lazy="select"
    )
    usage_records: Mapped[list["UsageRecord"]] = relationship(  # noqa: F821
        "UsageRecord", back_populates="provider", lazy="select"
    )
    balance_logs: Mapped[list["ProviderBalanceLog"]] = relationship(  # noqa: F821
        "ProviderBalanceLog", back_populates="provider", lazy="select"
    )
