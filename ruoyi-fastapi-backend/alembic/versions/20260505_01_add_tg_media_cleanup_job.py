"""add tg media cleanup job

Revision ID: 20260505_01
Revises: 20260502_02
Create Date: 2026-05-05 00:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '20260505_01'
down_revision: str | Sequence[str] | None = '20260502_02'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

INVOKE_TARGET = 'module_task.telegram_media_cleanup.cleanup_expired_local_files'


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return inspector.has_table(table_name)


def upgrade() -> None:
    """Upgrade schema."""
    if not _has_table('sys_job'):
        return
    op.execute(
        f"""
        insert into sys_job(
            job_name,
            job_group,
            job_executor,
            invoke_target,
            job_args,
            job_kwargs,
            cron_expression,
            misfire_policy,
            concurrent,
            status,
            create_by,
            create_time,
            update_by,
            update_time,
            remark
        )
        select
            'TG媒体本地文件清理',
            'default',
            'default',
            '{INVOKE_TARGET}',
            null,
            null,
            '0 0 3 * * ?',
            '3',
            '1',
            '0',
            'admin',
            current_timestamp,
            '',
            null,
            '自动删除7天前的Telegram媒体本地文件'
        where not exists (
            select 1 from sys_job where invoke_target = '{INVOKE_TARGET}'
        )
        """
    )


def downgrade() -> None:
    """Downgrade schema."""
    if not _has_table('sys_job'):
        return
    op.execute(f"delete from sys_job where invoke_target = '{INVOKE_TARGET}'")
