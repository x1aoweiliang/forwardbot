"""add tg chat ad text id

Revision ID: 20260508_01
Revises: 20260507_01
Create Date: 2026-05-08 00:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '20260508_01'
down_revision: str | Sequence[str] | None = '20260507_01'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return inspector.has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table(table_name):
        return False
    return any(column['name'] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    """Upgrade schema."""
    if _has_table('tg_chat') and not _has_column('tg_chat', 'ad_text_id'):
        op.add_column('tg_chat', sa.Column('ad_text_id', sa.BigInteger(), nullable=True, comment='目标群广告词ID'))


def downgrade() -> None:
    """Downgrade schema."""
    if _has_table('tg_chat') and _has_column('tg_chat', 'ad_text_id'):
        op.drop_column('tg_chat', 'ad_text_id')
