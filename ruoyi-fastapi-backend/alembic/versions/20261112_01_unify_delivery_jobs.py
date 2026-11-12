"""Make forward records the outbox for both automatic and manual delivery.

Revision ID: 20261112_01
Revises: 20261111_03
"""

import sqlalchemy as sa
from alembic import op

revision = '20261112_01'
down_revision = '20261111_03'
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('tg_forward_record')}
    if 'forward_mode' not in existing:
        op.add_column(
            'tg_forward_record', sa.Column('forward_mode', sa.String(30), nullable=False, server_default='copy_clean')
        )
    if 'delivery_stage' not in existing:
        op.add_column(
            'tg_forward_record', sa.Column('delivery_stage', sa.String(20), nullable=False, server_default='source')
        )
        records = sa.table('tg_forward_record', sa.column('status'), sa.column('delivery_stage'))
        # Before this revision only local-file retries could be pending or sending.
        op.execute(
            records.update().where(records.c.status.in_(['waiting_download', 'sending'])).values(delivery_stage='local')
        )


def downgrade() -> None:
    records = sa.table('tg_forward_record', sa.column('status'), sa.column('error_message'))
    op.execute(
        records.update()
        .where(records.c.status.in_(['queued', 'sending', 'waiting_download']))
        .values(
            status='failed',
            error_message='版本回退停止了待发送任务，请检查发送记录后手动重试',
        )
    )
    op.drop_column('tg_forward_record', 'forward_mode')
    op.drop_column('tg_forward_record', 'delivery_stage')
