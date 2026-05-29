"""
Model (LLM model) and MarkupConfig ORM models.
"""
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Model(Base):
    __tablename__ = "models"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    provider_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("providers.id"), nullable=False
    )
    model_id: Mapped[str] = mapped_column(Text, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    input_price_per_1m: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    output_price_per_1m: Mapped[Decimal] = mapped_column(Numeric(10, 6), nullable=False)
    markup_rate: Mapped[Decimal | None] = mapped_column(Numeric(6, 4), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (UniqueConstraint("provider_id", "model_id", name="uq_models_provider_model"),)

    # Relationships
    provider: Mapped["Provider"] = relationship("Provider", back_populates="models")  # noqa: F821
    markup_configs: Mapped[list["MarkupConfig"]] = relationship(
        "MarkupConfig", back_populates="model", lazy="select"
    )
    usage_records: Mapped[list["UsageRecord"]] = relationship(  # noqa: F821
        "UsageRecord", back_populates="model", lazy="select"
    )


class MarkupConfig(Base):
    __tablename__ = "markup_config"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    scope: Mapped[str] = mapped_column(Text, nullable=False)
    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("providers.id"), nullable=True
    )
    model_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("models.id"), nullable=True
    )
    markup_rate: Mapped[Decimal] = mapped_column(
        Numeric(6, 4), nullable=False, default=Decimal("0.20")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("scope", "provider_id", "model_id", name="uq_markup_config_scope"),
    )

    # Relationships
    provider: Mapped["Provider | None"] = relationship(  # noqa: F821
        "Provider", back_populates="markup_configs"
    )
    model: Mapped["Model | None"] = relationship("Model", back_populates="markup_configs")
