"""Persistent collection downloads and manual cloud archives.

Revision ID: 20261111_01
Revises: 20260511_01
"""

import sqlalchemy as sa
from alembic import op

revision = '20261111_01'
down_revision = '20260511_01'
branch_labels = None
depends_on = None


def _columns() -> dict[str, list[sa.Column]]:
    return {
        'tg_listener_rule': [
            sa.Column('forward_enabled', sa.CHAR(1), nullable=False, server_default='Y'),
            sa.Column('download_enabled', sa.CHAR(1), nullable=False, server_default='N'),
        ],
        'tg_chat': [sa.Column('collection_cursor', sa.BigInteger(), nullable=True)],
        'tg_message': [
            sa.Column('grouped_id', sa.BigInteger(), nullable=True),
            sa.Column('collected', sa.CHAR(1), nullable=False, server_default='N'),
            sa.Column('media_revision', sa.Integer(), nullable=False, server_default='1'),
        ],
        'tg_message_media': [
            sa.Column('source_media_id', sa.BigInteger(), nullable=True),
            sa.Column('caption', sa.Text(), nullable=True),
            sa.Column('storage_kind', sa.String(20), nullable=False, server_default='cache'),
            sa.Column('download_status', sa.String(30), nullable=False, server_default='disabled'),
            sa.Column('downloaded_bytes', sa.BigInteger(), nullable=False, server_default='0'),
            sa.Column('sha256', sa.String(64), nullable=True),
            sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('next_retry_at', sa.DateTime(), nullable=True),
            sa.Column('lease_until', sa.DateTime(), nullable=True),
            sa.Column('lease_token', sa.String(40), nullable=True),
            sa.Column('last_error', sa.Text(), nullable=True),
        ],
    }


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table, columns in _columns().items():
        existing = {column['name'] for column in inspector.get_columns(table)}
        with op.batch_alter_table(table) as batch:
            for column in columns:
                if column.name not in existing:
                    batch.add_column(column)
    for table, name, columns in [
        ('tg_message', 'uq_tg_message_album', ['account_id', 'source_chat_id', 'grouped_id']),
        ('tg_message_media', 'uq_tg_media_source', ['message_id', 'source_telegram_message_id']),
    ]:
        if name not in {item['name'] for item in sa.inspect(op.get_bind()).get_unique_constraints(table)}:
            with op.batch_alter_table(table) as batch:
                batch.create_unique_constraint(name, columns)
    if not inspector.has_table('tg_archive_task'):
        op.create_table(
            'tg_archive_task',
            sa.Column('task_id', sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column('dedupe_key', sa.String(64), nullable=False, unique=True),
            sa.Column('message_id', sa.BigInteger(), nullable=False, index=True),
            sa.Column('media_revision', sa.Integer(), nullable=False),
            sa.Column('storage_id', sa.String(64), nullable=False),
            sa.Column('target_path', sa.String(1000), nullable=False),
            sa.Column('package_name', sa.String(255), nullable=False),
            sa.Column('manifest_json', sa.Text(), nullable=False),
            sa.Column('status', sa.String(30), nullable=False, server_default='queued', index=True),
            sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('next_retry_at', sa.DateTime()),
            sa.Column('lease_until', sa.DateTime()),
            sa.Column('lease_token', sa.String(40)),
            sa.Column('last_error', sa.Text()),
            sa.Column('create_by', sa.String(64), nullable=False),
            sa.Column('create_time', sa.DateTime(), nullable=False),
            sa.Column('completed_at', sa.DateTime()),
        )
    if not inspector.has_table('tg_archive_item'):
        op.create_table(
            'tg_archive_item',
            sa.Column('item_id', sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column('task_id', sa.BigInteger(), nullable=False, index=True),
            sa.Column('media_id', sa.BigInteger()),
            sa.Column('file_name', sa.String(255), nullable=False),
            sa.Column('file_size', sa.BigInteger(), nullable=False),
            sa.Column('sha256', sa.String(64), nullable=False),
            sa.Column('status', sa.String(30), nullable=False, server_default='queued'),
            sa.Column('remote_id', sa.String(1000)),
            sa.Column('last_error', sa.Text()),
            sa.UniqueConstraint('task_id', 'file_name', name='uq_tg_archive_item_file'),
        )
    for table, column in [
        ('tg_message', 'grouped_id'),
        ('tg_message', 'collected'),
        ('tg_message_media', 'download_status'),
    ]:
        name = f'ix_{table}_{column}'
        if name not in {index['name'] for index in sa.inspect(op.get_bind()).get_indexes(table)}:
            op.create_index(name, table, [column])


def downgrade() -> None:
    op.drop_table('tg_archive_item')
    op.drop_table('tg_archive_task')
    for table, name in [('tg_message', 'uq_tg_message_album'), ('tg_message_media', 'uq_tg_media_source')]:
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(name, type_='unique')
    for table, column in [
        ('tg_message', 'grouped_id'),
        ('tg_message', 'collected'),
        ('tg_message_media', 'download_status'),
    ]:
        op.drop_index(f'ix_{table}_{column}', table_name=table)
    for table, columns in reversed(list(_columns().items())):
        with op.batch_alter_table(table) as batch:
            for column in reversed(columns):
                batch.drop_column(column.name)
