"""Track one archive submission containing multiple message revisions.

Revision ID: 20261114_01
Revises: 20261112_01
"""
import sqlalchemy as sa
from alembic import op

revision = '20261114_01'
down_revision = '20261112_01'
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if 'archive_format' not in {column['name'] for column in inspector.get_columns('tg_archive_task')}:
        op.add_column('tg_archive_task', sa.Column('archive_format', sa.String(20), nullable=False, server_default='files'))
    if not inspector.has_table('tg_archive_message'):
        op.create_table(
            'tg_archive_message',
            sa.Column('task_id', sa.BigInteger(), primary_key=True),
            sa.Column('message_id', sa.BigInteger(), primary_key=True),
            sa.Column('media_revision', sa.Integer(), nullable=False),
        )
        op.create_index('ix_tg_archive_message_message_id', 'tg_archive_message', ['message_id'])
    tasks = sa.table('tg_archive_task', sa.column('task_id'), sa.column('message_id'), sa.column('media_revision'))
    members = sa.table('tg_archive_message', sa.column('task_id'), sa.column('message_id'), sa.column('media_revision'))
    op.execute(members.insert().from_select(
        ['task_id', 'message_id', 'media_revision'],
        sa.select(tasks.c.task_id, tasks.c.message_id, tasks.c.media_revision).where(
            ~sa.exists().where(members.c.task_id == tasks.c.task_id)
        ),
    ))


def downgrade() -> None:
    tasks = sa.table('tg_archive_task', sa.column('archive_format'))
    if op.get_bind().scalar(sa.select(sa.func.count()).select_from(tasks).where(tasks.c.archive_format == 'zip')):
        raise RuntimeError('已有 ZIP 归档，不能回退到不支持批量 ZIP 的版本')
    op.drop_table('tg_archive_message')
    op.drop_column('tg_archive_task', 'archive_format')
