"""Save custom media names and retain ZIPs before cleaning their sources.

Revision ID: 20261114_02
Revises: 20261114_01
"""
import sqlalchemy as sa
from alembic import op

revision = '20261114_02'
down_revision = '20261114_01'
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if 'media_name' not in {column['name'] for column in inspector.get_columns('tg_message')}:
        op.add_column('tg_message', sa.Column('media_name', sa.String(200), nullable=True))
    existing = {column['name'] for column in inspector.get_columns('tg_archive_task')}
    for column in [
        sa.Column('local_path', sa.String(500), nullable=False, server_default=''),
        # Historical tasks never opt in to automatic source deletion.
        sa.Column('source_cleanup', sa.String(20), nullable=False, server_default='none'),
        sa.Column('cleanup_error', sa.Text(), nullable=True),
    ]:
        if column.name not in existing:
            op.add_column('tg_archive_task', column)


def downgrade() -> None:
    tasks = sa.table('tg_archive_task', sa.column('local_path'))
    if op.get_bind().scalar(sa.select(sa.func.count()).select_from(tasks).where(tasks.c.local_path != '')):
        raise RuntimeError('仍有保留的本地 ZIP，不能回退并丢失文件记录')
    for name in ['cleanup_error', 'source_cleanup', 'local_path']:
        op.drop_column('tg_archive_task', name)
    op.drop_column('tg_message', 'media_name')
