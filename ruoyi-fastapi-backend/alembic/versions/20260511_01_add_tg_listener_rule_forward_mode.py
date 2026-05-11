"""add tg listener rule forward mode

Revision ID: 20260511_01
Revises: 20260508_01
Create Date: 2026-05-11 00:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '20260511_01'
down_revision: str | Sequence[str] | None = '20260508_01'
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
    if _has_table('tg_listener_rule') and not _has_column('tg_listener_rule', 'forward_mode'):
        op.add_column(
            'tg_listener_rule',
            sa.Column(
                'forward_mode',
                sa.String(length=30),
                nullable=False,
                server_default='copy_clean',
                comment='转发方式：copy_clean清洗复制 native_hidden原生隐藏',
            ),
        )


def downgrade() -> None:
    """Downgrade schema."""
    if _has_table('tg_listener_rule') and _has_column('tg_listener_rule', 'forward_mode'):
        op.drop_column('tg_listener_rule', 'forward_mode')
