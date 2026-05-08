"""add tg listener rule source chat pks

Revision ID: 20260507_01
Revises: 20260506_01
Create Date: 2026-05-07 00:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '20260507_01'
down_revision: str | Sequence[str] | None = '20260506_01'
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
    if not _has_table('tg_listener_rule'):
        return
    if not _has_column('tg_listener_rule', 'source_chat_pks'):
        op.add_column(
            'tg_listener_rule',
            sa.Column('source_chat_pks', sa.String(length=1000), nullable=True, comment='来源频道主键，逗号分隔'),
        )
    listener_rule = sa.table(
        'tg_listener_rule',
        sa.column('source_chat_pk', sa.BigInteger()),
        sa.column('source_chat_pks', sa.String(length=1000)),
    )
    op.execute(
        listener_rule.update()
        .where(sa.or_(listener_rule.c.source_chat_pks.is_(None), listener_rule.c.source_chat_pks == ''))
        .values(source_chat_pks=sa.cast(listener_rule.c.source_chat_pk, sa.String(length=1000)))
    )


def downgrade() -> None:
    """Downgrade schema."""
    if _has_table('tg_listener_rule') and _has_column('tg_listener_rule', 'source_chat_pks'):
        op.drop_column('tg_listener_rule', 'source_chat_pks')
