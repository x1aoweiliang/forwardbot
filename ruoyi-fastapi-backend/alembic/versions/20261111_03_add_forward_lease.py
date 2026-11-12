"""Lease deferred sends without holding message locks across network uploads.

Revision ID: 20261111_03
Revises: 20261111_02
"""

import sqlalchemy as sa
from alembic import op

revision = '20261111_03'
down_revision = '20261111_02'
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('tg_forward_record')}
    for column in (sa.Column('lease_token', sa.String(40)), sa.Column('lease_until', sa.DateTime())):
        if column.name not in existing:
            op.add_column('tg_forward_record', column)
    # These files were never collected. Keep actual archive paths untouched.
    media = sa.table(
        'tg_message_media', sa.column('storage_kind'), sa.column('download_status'), sa.column('local_path')
    )
    op.execute(
        media.update()
        .where(media.c.storage_kind == 'archive', media.c.download_status == 'skipped', media.c.local_path == '')
        .values(storage_kind='cache')
    )


def downgrade() -> None:
    records = sa.table('tg_forward_record', sa.column('status'))
    op.execute(records.update().where(records.c.status == 'sending').values(status='waiting_download'))
    with op.batch_alter_table('tg_forward_record') as batch:
        batch.drop_column('lease_until')
        batch.drop_column('lease_token')
    # Cache paths remain valid with the preceding version; do not relabel files.
