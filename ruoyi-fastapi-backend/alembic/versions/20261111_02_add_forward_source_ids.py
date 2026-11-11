"""Keep the media subset of a delivery across late albums and download retries.

Revision ID: 20261111_02
Revises: 20261111_01
"""

import sqlalchemy as sa
from alembic import op

revision = '20261111_02'
down_revision = '20261111_01'
branch_labels = None
depends_on = None


def upgrade() -> None:
    if 'source_message_ids' not in {
        column['name'] for column in sa.inspect(op.get_bind()).get_columns('tg_forward_record')
    }:
        op.add_column('tg_forward_record', sa.Column('source_message_ids', sa.Text(), nullable=True))
    indexes = {
        index['name'] for index in sa.inspect(op.get_bind()).get_indexes('tg_forward_record')
    }
    for column in ('status', 'message_id'):
        name = f'ix_tg_forward_record_{column}'
        if name not in indexes:
            op.create_index(name, 'tg_forward_record', [column])


def downgrade() -> None:
    op.drop_index('ix_tg_forward_record_status', table_name='tg_forward_record')
    op.drop_index('ix_tg_forward_record_message_id', table_name='tg_forward_record')
    with op.batch_alter_table('tg_forward_record') as batch:
        batch.drop_column('source_message_ids')
