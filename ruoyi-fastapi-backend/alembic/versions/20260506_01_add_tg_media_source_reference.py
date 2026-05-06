"""add tg media source reference

Revision ID: 20260506_01
Revises: 20260505_01
Create Date: 2026-05-06 00:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '20260506_01'
down_revision: str | Sequence[str] | None = '20260505_01'
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
    if not _has_table('tg_message_media'):
        return
    if _has_column('tg_message_media', 'local_path'):
        op.alter_column(
            'tg_message_media',
            'local_path',
            existing_type=sa.String(length=500),
            server_default='',
            existing_nullable=False,
        )
    if not _has_column('tg_message_media', 'source_telegram_message_id'):
        op.add_column(
            'tg_message_media',
            sa.Column('source_telegram_message_id', sa.BigInteger(), nullable=True, comment='源Telegram媒体消息ID'),
        )
    if not _has_column('tg_message_media', 'media_index'):
        op.add_column(
            'tg_message_media',
            sa.Column('media_index', sa.Integer(), server_default='0', nullable=False, comment='媒体顺序'),
        )


def downgrade() -> None:
    """Downgrade schema."""
    if not _has_table('tg_message_media'):
        return
    if _has_column('tg_message_media', 'local_path'):
        op.alter_column(
            'tg_message_media',
            'local_path',
            existing_type=sa.String(length=500),
            server_default=None,
            existing_nullable=False,
        )
    if _has_column('tg_message_media', 'media_index'):
        op.drop_column('tg_message_media', 'media_index')
    if _has_column('tg_message_media', 'source_telegram_message_id'):
        op.drop_column('tg_message_media', 'source_telegram_message_id')
