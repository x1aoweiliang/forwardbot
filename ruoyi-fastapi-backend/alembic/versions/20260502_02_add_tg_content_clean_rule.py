"""add tg content clean rule

Revision ID: 20260502_02
Revises: 20260502_01
Create Date: 2026-05-02 00:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '20260502_02'
down_revision: str | Sequence[str] | None = '20260502_01'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return inspector.has_table(table_name)


def upgrade() -> None:
    """Upgrade schema."""
    if not _has_table('tg_content_clean_rule'):
        op.create_table(
            'tg_content_clean_rule',
            sa.Column('clean_id', sa.BigInteger(), autoincrement=True, nullable=False, comment='清理规则主键'),
            sa.Column('clean_name', sa.String(length=100), nullable=False, comment='规则名称'),
            sa.Column('match_text', sa.Text(), nullable=False, comment='匹配文本'),
            sa.Column('replacement', sa.Text(), nullable=True, comment='替换文本'),
            sa.Column('match_case', sa.CHAR(length=1), server_default='Y', nullable=False, comment='是否区分大小写'),
            sa.Column('status', sa.CHAR(length=1), server_default='0', nullable=False, comment='状态（0启用 1停用）'),
            sa.Column('create_by', sa.String(length=64), server_default='', nullable=True, comment='创建者'),
            sa.Column('create_time', sa.DateTime(), nullable=True, comment='创建时间'),
            sa.Column('update_by', sa.String(length=64), server_default='', nullable=True, comment='更新者'),
            sa.Column('update_time', sa.DateTime(), nullable=True, comment='更新时间'),
            sa.Column('remark', sa.String(length=500), nullable=True, comment='备注'),
            sa.PrimaryKeyConstraint('clean_id'),
            comment='Telegram内容清理规则表',
        )
    op.execute(
        """
        insert into tg_content_clean_rule(clean_name, match_text, replacement, match_case, status, create_by, create_time, remark)
        select
            '移除大事件频道投稿尾巴',
            '关注大事件频道➡️ @bx666 投稿：@tx188',
            '',
            'Y',
            '0',
            'admin',
            current_timestamp,
            '发送前移除固定渠道推广文案'
        where not exists (
            select 1 from tg_content_clean_rule where match_text = '关注大事件频道➡️ @bx666 投稿：@tx188'
        )
        """
    )
    if _has_table('sys_menu'):
        menu_rows = [
            (1906, '内容清理', 1900, 6, 'clean-rule', 'tg/clean-rule/index', 'tg:clean-rule:list', 'textarea', 'TG内容清理'),
            (1907, '消息中心', 1900, 7, 'message', 'tg/message/index', 'tg:message:list', 'message', 'TG消息中心'),
            (1908, '发送记录', 1900, 8, 'forward-record', 'tg/forward-record/index', 'tg:forward-record:list', 'log', 'TG发送记录'),
        ]
        for menu_id, menu_name, parent_id, order_num, path, component, perms, icon, remark in menu_rows:
            op.execute(
                f"""
                insert into sys_menu values({menu_id}, '{menu_name}', {parent_id}, {order_num}, '{path}', '{component}', '', '', 1, 0, 'C', '0', '0', '{perms}', '{icon}', 'admin', current_timestamp, '', null, '{remark}')
                on conflict (menu_id) do nothing
                """
            )
        op.execute(
            """
            update sys_menu m
            set
                menu_name = v.menu_name,
                parent_id = v.parent_id,
                order_num = v.order_num,
                path = v.path,
                component = v.component,
                query = v.query,
                route_name = v.route_name,
                is_frame = v.is_frame,
                is_cache = v.is_cache,
                menu_type = v.menu_type,
                visible = v.visible,
                status = v.status,
                perms = v.perms,
                icon = v.icon,
                update_by = 'admin',
                update_time = current_timestamp,
                remark = v.remark
            from (
                values
                (1906, '内容清理', 1900, 6, 'clean-rule', 'tg/clean-rule/index', '', '', 1, 0, 'C', '0', '0', 'tg:clean-rule:list', 'textarea', 'TG内容清理'),
                (1907, '消息中心', 1900, 7, 'message', 'tg/message/index', '', '', 1, 0, 'C', '0', '0', 'tg:message:list', 'message', 'TG消息中心'),
                (1908, '发送记录', 1900, 8, 'forward-record', 'tg/forward-record/index', '', '', 1, 0, 'C', '0', '0', 'tg:forward-record:list', 'log', 'TG发送记录')
            ) as v(menu_id, menu_name, parent_id, order_num, path, component, query, route_name, is_frame, is_cache, menu_type, visible, status, perms, icon, remark)
            where m.menu_id = v.menu_id
            """
        )


def downgrade() -> None:
    """Downgrade schema."""
    if _has_table('tg_content_clean_rule'):
        op.drop_table('tg_content_clean_rule')
