"""Initial schema — all tables

Revision ID: 0001
Revises:
Create Date: 2024-01-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── users ─────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("oauth_provider", sa.Text(), nullable=True),
        sa.Column("oauth_sub", sa.Text(), nullable=True),
        sa.Column("role", sa.Text(), nullable=False, server_default="customer"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    # ── virtual_keys ──────────────────────────────────────────────────────────
    op.create_table(
        "virtual_keys",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("key_hash", sa.Text(), nullable=False),
        sa.Column("key_prefix", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("rate_limit_rpm", sa.Integer(), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_requests", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_virtual_keys_user_id"),
        sa.UniqueConstraint("key_hash", name="uq_virtual_keys_key_hash"),
    )
    op.create_index("idx_virtual_keys_hash", "virtual_keys", ["key_hash"])
    op.create_index("idx_virtual_keys_user", "virtual_keys", ["user_id"])

    # ── credit_accounts ───────────────────────────────────────────────────────
    op.create_table(
        "credit_accounts",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "balance_usd", sa.Numeric(12, 6), nullable=False, server_default="0"
        ),
        sa.Column("last_topup_amount", sa.Numeric(10, 2), nullable=True),
        sa.Column("last_topup_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("low_balance_alert_sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_credit_accounts_user_id"
        ),
        sa.UniqueConstraint("user_id", name="uq_credit_accounts_user_id"),
    )

    # ── credit_transactions ───────────────────────────────────────────────────
    op.create_table(
        "credit_transactions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.Text(), nullable=False),
        sa.Column("amount_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("balance_after", sa.Numeric(12, 6), nullable=False),
        sa.Column("stripe_payment_intent_id", sa.Text(), nullable=True),
        sa.Column("usage_record_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_credit_transactions_user_id"
        ),
    )
    op.create_index(
        "idx_credit_tx_user",
        "credit_transactions",
        ["user_id", sa.text("created_at DESC")],
    )

    # ── providers ─────────────────────────────────────────────────────────────
    op.create_table(
        "providers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column(
            "balance_usd", sa.Numeric(12, 4), nullable=False, server_default="0"
        ),
        sa.Column(
            "warning_threshold",
            sa.Numeric(10, 2),
            nullable=False,
            server_default="10.00",
        ),
        sa.Column(
            "hard_stop_threshold",
            sa.Numeric(10, 2),
            nullable=False,
            server_default="2.00",
        ),
        sa.Column("status", sa.Text(), nullable=False, server_default="normal"),
        sa.Column("fallback_provider_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("last_warning_alert_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("hard_stop_activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["fallback_provider_id"],
            ["providers.id"],
            name="fk_providers_fallback_provider_id",
        ),
        sa.UniqueConstraint("name", name="uq_providers_name"),
    )

    # ── models ────────────────────────────────────────────────────────────────
    op.create_table(
        "models",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("input_price_per_1m", sa.Numeric(10, 6), nullable=False),
        sa.Column("output_price_per_1m", sa.Numeric(10, 6), nullable=False),
        sa.Column("markup_rate", sa.Numeric(6, 4), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"], ["providers.id"], name="fk_models_provider_id"
        ),
        sa.UniqueConstraint(
            "provider_id", "model_id", name="uq_models_provider_model"
        ),
    )

    # ── markup_config ─────────────────────────────────────────────────────────
    op.create_table(
        "markup_config",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "markup_rate", sa.Numeric(6, 4), nullable=False, server_default="0.20"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["model_id"], ["models.id"], name="fk_markup_config_model_id"
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"], ["providers.id"], name="fk_markup_config_provider_id"
        ),
        sa.UniqueConstraint(
            "scope", "provider_id", "model_id", name="uq_markup_config_scope"
        ),
    )

    # ── usage_records ─────────────────────────────────────────────────────────
    op.create_table(
        "usage_records",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("virtual_key_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("model_name", sa.Text(), nullable=False),
        sa.Column("provider_name", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "base_cost_usd", sa.Numeric(12, 6), nullable=False, server_default="0"
        ),
        sa.Column(
            "markup_rate", sa.Numeric(6, 4), nullable=False, server_default="0"
        ),
        sa.Column(
            "billed_amount_usd", sa.Numeric(12, 6), nullable=False, server_default="0"
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("cache_hit", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_fallback", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.Text(), nullable=False, server_default="success"),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["model_id"], ["models.id"], name="fk_usage_records_model_id"
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"], ["providers.id"], name="fk_usage_records_provider_id"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_usage_records_user_id"
        ),
        sa.ForeignKeyConstraint(
            ["virtual_key_id"],
            ["virtual_keys.id"],
            name="fk_usage_records_virtual_key_id",
        ),
    )
    op.create_index(
        "idx_usage_user_time",
        "usage_records",
        ["user_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_usage_key_time",
        "usage_records",
        ["virtual_key_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "idx_usage_provider",
        "usage_records",
        ["provider_id", sa.text("created_at DESC")],
    )

    # ── provider_balance_log ──────────────────────────────────────────────────
    op.create_table(
        "provider_balance_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("provider_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("change_type", sa.Text(), nullable=False),
        sa.Column("amount_usd", sa.Numeric(12, 6), nullable=False),
        sa.Column("balance_before", sa.Numeric(12, 4), nullable=False),
        sa.Column("balance_after", sa.Numeric(12, 4), nullable=False),
        sa.Column("usage_record_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["providers.id"],
            name="fk_provider_balance_log_provider_id",
        ),
        sa.ForeignKeyConstraint(
            ["usage_record_id"],
            ["usage_records.id"],
            name="fk_provider_balance_log_usage_record_id",
        ),
    )
    op.create_index(
        "idx_pbl_provider",
        "provider_balance_log",
        ["provider_id", sa.text("created_at DESC")],
    )

    # ── guardrail_keywords ────────────────────────────────────────────────────
    op.create_table(
        "guardrail_keywords",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("keyword", sa.Text(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False, server_default="both"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    # ── guardrail_events ──────────────────────────────────────────────────────
    op.create_table(
        "guardrail_events",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("virtual_key_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("keyword_matched", sa.Text(), nullable=False),
        sa.Column("content_snippet", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_guardrail_events_user_id"
        ),
        sa.ForeignKeyConstraint(
            ["virtual_key_id"],
            ["virtual_keys.id"],
            name="fk_guardrail_events_virtual_key_id",
        ),
    )


def downgrade() -> None:
    op.drop_table("guardrail_events")
    op.drop_table("guardrail_keywords")
    op.drop_index("idx_pbl_provider", table_name="provider_balance_log")
    op.drop_table("provider_balance_log")
    op.drop_index("idx_usage_provider", table_name="usage_records")
    op.drop_index("idx_usage_key_time", table_name="usage_records")
    op.drop_index("idx_usage_user_time", table_name="usage_records")
    op.drop_table("usage_records")
    op.drop_table("markup_config")
    op.drop_table("models")
    op.drop_table("providers")
    op.drop_table("credit_transactions")
    op.drop_table("credit_accounts")
    op.drop_index("idx_virtual_keys_user", table_name="virtual_keys")
    op.drop_index("idx_virtual_keys_hash", table_name="virtual_keys")
    op.drop_table("virtual_keys")
    op.drop_table("users")
