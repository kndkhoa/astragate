"""add_encrypted_api_key

Revision ID: a31aab26bfb3
Revises: 0001
Create Date: 2026-06-10 05:20:02.775860

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a31aab26bfb3'
down_revision: Union[str, None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('providers', sa.Column('encrypted_api_key', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('providers', 'encrypted_api_key')
