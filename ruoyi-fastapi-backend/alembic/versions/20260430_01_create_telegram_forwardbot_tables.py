"""create telegram forwardbot tables

Revision ID: 20260430_01
Revises:
Create Date: 2026-04-30 00:00:00

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = '20260430_01'
down_revision: str | Sequence[str] | None = None
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


def upgrade() -> None:  # noqa: PLR0912
    """Upgrade schema."""
    if not _has_table('tg_account'):
        op.create_table(
            'tg_account',
            sa.Column('account_id', sa.BigInteger(), autoincrement=True, nullable=False, comment='账号主键'),
            sa.Column('account_name', sa.String(length=100), nullable=False, comment='账号名称'),
            sa.Column('phone', sa.String(length=50), nullable=False, comment='手机号'),
            sa.Column('api_id', sa.Integer(), nullable=False, comment='Telegram API ID'),
            sa.Column('api_hash', sa.String(length=255), nullable=False, comment='Telegram API Hash'),
            sa.Column('session_path', sa.String(length=500), nullable=True, comment='Session文件路径'),
            sa.Column('session_status', sa.String(length=30), server_default='logged_out', nullable=False, comment='Session状态'),
            sa.Column('login_code_hash', sa.String(length=255), nullable=True, comment='登录验证码Hash'),
            sa.Column('last_error', sa.Text(), nullable=True, comment='最后错误'),
            sa.Column('status', sa.CHAR(length=1), server_default='0', nullable=False, comment='状态（0启用 1停用）'),
            sa.Column('create_by', sa.String(length=64), server_default='', nullable=True, comment='创建者'),
            sa.Column('create_time', sa.DateTime(), nullable=True, comment='创建时间'),
            sa.Column('update_by', sa.String(length=64), server_default='', nullable=True, comment='更新者'),
            sa.Column('update_time', sa.DateTime(), nullable=True, comment='更新时间'),
            sa.Column('remark', sa.String(length=500), nullable=True, comment='备注'),
            sa.PrimaryKeyConstraint('account_id'),
            comment='Telegram账号表',
        )

    if not _has_table('tg_chat'):
        op.create_table(
            'tg_chat',
            sa.Column('chat_pk', sa.BigInteger(), autoincrement=True, nullable=False, comment='频道主键'),
            sa.Column('account_id', sa.BigInteger(), nullable=False, comment='账号ID'),
            sa.Column('chat_id', sa.String(length=100), nullable=False, comment='Telegram Chat ID'),
            sa.Column('chat_title', sa.String(length=255), nullable=False, comment='频道/群组标题'),
            sa.Column('username', sa.String(length=255), nullable=True, comment='用户名'),
            sa.Column('chat_type', sa.String(length=30), nullable=False, comment='类型：group/channel/private'),
            sa.Column('can_listen', sa.CHAR(length=1), server_default='N', nullable=False, comment='是否可监听'),
            sa.Column('can_send', sa.CHAR(length=1), server_default='N', nullable=False, comment='是否可发送'),
            sa.Column('ad_text_id', sa.BigInteger(), nullable=True, comment='目标群广告词ID'),
            sa.Column('status', sa.CHAR(length=1), server_default='0', nullable=False, comment='状态（0启用 1停用）'),
            sa.Column('create_by', sa.String(length=64), server_default='', nullable=True, comment='创建者'),
            sa.Column('create_time', sa.DateTime(), nullable=True, comment='创建时间'),
            sa.Column('update_by', sa.String(length=64), server_default='', nullable=True, comment='更新者'),
            sa.Column('update_time', sa.DateTime(), nullable=True, comment='更新时间'),
            sa.Column('remark', sa.String(length=500), nullable=True, comment='备注'),
            sa.PrimaryKeyConstraint('chat_pk'),
            sa.UniqueConstraint('account_id', 'chat_id', name='uq_tg_chat_account_chat'),
            comment='Telegram频道/群组表',
        )

    if not _has_table('tg_listener_rule'):
        op.create_table(
            'tg_listener_rule',
            sa.Column('rule_id', sa.BigInteger(), autoincrement=True, nullable=False, comment='规则主键'),
            sa.Column('account_id', sa.BigInteger(), nullable=False, comment='监听账号ID'),
            sa.Column('source_chat_pk', sa.BigInteger(), nullable=False, comment='来源频道主键'),
            sa.Column('source_chat_pks', sa.String(length=1000), nullable=True, comment='来源频道主键，逗号分隔'),
            sa.Column('target_chat_pks', sa.String(length=1000), nullable=False, comment='目标频道主键，逗号分隔'),
            sa.Column('rule_name', sa.String(length=100), nullable=False, comment='规则名称'),
            sa.Column('status', sa.CHAR(length=1), server_default='0', nullable=False, comment='状态（0启用 1停用）'),
            sa.Column('create_by', sa.String(length=64), server_default='', nullable=True, comment='创建者'),
            sa.Column('create_time', sa.DateTime(), nullable=True, comment='创建时间'),
            sa.Column('update_by', sa.String(length=64), server_default='', nullable=True, comment='更新者'),
            sa.Column('update_time', sa.DateTime(), nullable=True, comment='更新时间'),
            sa.Column('remark', sa.String(length=500), nullable=True, comment='备注'),
            sa.PrimaryKeyConstraint('rule_id'),
            comment='Telegram监听规则表',
        )
    elif not _has_column('tg_listener_rule', 'source_chat_pks'):
        op.add_column(
            'tg_listener_rule',
            sa.Column('source_chat_pks', sa.String(length=1000), nullable=True, comment='来源频道主键，逗号分隔'),
        )
    if _has_table('tg_chat') and not _has_column('tg_chat', 'ad_text_id'):
        op.add_column('tg_chat', sa.Column('ad_text_id', sa.BigInteger(), nullable=True, comment='目标群广告词ID'))

    if not _has_table('tg_sensitive_word'):
        op.create_table(
            'tg_sensitive_word',
            sa.Column('word_id', sa.BigInteger(), autoincrement=True, nullable=False, comment='敏感词主键'),
            sa.Column('word', sa.String(length=255), nullable=False, comment='敏感词'),
            sa.Column('match_case', sa.CHAR(length=1), server_default='N', nullable=False, comment='是否区分大小写'),
            sa.Column('status', sa.CHAR(length=1), server_default='0', nullable=False, comment='状态（0启用 1停用）'),
            sa.Column('create_by', sa.String(length=64), server_default='', nullable=True, comment='创建者'),
            sa.Column('create_time', sa.DateTime(), nullable=True, comment='创建时间'),
            sa.Column('update_by', sa.String(length=64), server_default='', nullable=True, comment='更新者'),
            sa.Column('update_time', sa.DateTime(), nullable=True, comment='更新时间'),
            sa.Column('remark', sa.String(length=500), nullable=True, comment='备注'),
            sa.PrimaryKeyConstraint('word_id'),
            comment='Telegram敏感词表',
        )

    if not _has_table('tg_ad_text'):
        op.create_table(
            'tg_ad_text',
            sa.Column('ad_id', sa.BigInteger(), autoincrement=True, nullable=False, comment='广告词主键'),
            sa.Column('ad_name', sa.String(length=100), nullable=False, comment='广告词名称'),
            sa.Column('ad_content', sa.Text(), nullable=False, comment='广告词内容'),
            sa.Column('enabled', sa.CHAR(length=1), server_default='0', nullable=False, comment='是否启用（1启用 0停用）'),
            sa.Column('create_by', sa.String(length=64), server_default='', nullable=True, comment='创建者'),
            sa.Column('create_time', sa.DateTime(), nullable=True, comment='创建时间'),
            sa.Column('update_by', sa.String(length=64), server_default='', nullable=True, comment='更新者'),
            sa.Column('update_time', sa.DateTime(), nullable=True, comment='更新时间'),
            sa.Column('remark', sa.String(length=500), nullable=True, comment='备注'),
            sa.PrimaryKeyConstraint('ad_id'),
            comment='Telegram广告词表',
        )

    if not _has_table('tg_message'):
        op.create_table(
            'tg_message',
            sa.Column('message_id', sa.BigInteger(), autoincrement=True, nullable=False, comment='消息主键'),
            sa.Column('account_id', sa.BigInteger(), nullable=False, comment='账号ID'),
            sa.Column('source_chat_pk', sa.BigInteger(), nullable=True, comment='来源频道主键'),
            sa.Column('source_chat_id', sa.String(length=100), nullable=False, comment='来源Telegram Chat ID'),
            sa.Column('source_chat_title', sa.String(length=255), nullable=True, comment='来源标题'),
            sa.Column('telegram_message_id', sa.BigInteger(), nullable=False, comment='Telegram消息ID'),
            sa.Column('message_text', sa.Text(), nullable=True, comment='消息文本'),
            sa.Column('sent_at', sa.DateTime(), nullable=True, comment='Telegram发送时间'),
            sa.Column('is_sensitive', sa.CHAR(length=1), server_default='N', nullable=False, comment='是否命中敏感词'),
            sa.Column('sensitive_word', sa.String(length=255), nullable=True, comment='命中的敏感词'),
            sa.Column('auto_forward_status', sa.String(length=30), server_default='pending', nullable=False, comment='自动转发状态'),
            sa.Column('create_time', sa.DateTime(), nullable=True, comment='创建时间'),
            sa.Column('update_time', sa.DateTime(), nullable=True, comment='更新时间'),
            sa.PrimaryKeyConstraint('message_id'),
            sa.UniqueConstraint('account_id', 'source_chat_id', 'telegram_message_id', name='uq_tg_message_source'),
            comment='Telegram监听消息表',
        )

    if not _has_table('tg_message_media'):
        op.create_table(
            'tg_message_media',
            sa.Column('media_id', sa.BigInteger(), autoincrement=True, nullable=False, comment='媒体主键'),
            sa.Column('message_id', sa.BigInteger(), nullable=False, comment='消息ID'),
            sa.Column('media_type', sa.String(length=30), nullable=False, comment='媒体类型'),
            sa.Column('local_path', sa.String(length=500), server_default='', nullable=False, comment='本地相对路径'),
            sa.Column('source_telegram_message_id', sa.BigInteger(), nullable=True, comment='源Telegram媒体消息ID'),
            sa.Column('media_index', sa.Integer(), server_default='0', nullable=False, comment='媒体顺序'),
            sa.Column('file_name', sa.String(length=255), nullable=True, comment='文件名'),
            sa.Column('mime_type', sa.String(length=100), nullable=True, comment='MIME类型'),
            sa.Column('file_size', sa.BigInteger(), nullable=True, comment='文件大小'),
            sa.Column('create_time', sa.DateTime(), nullable=True, comment='创建时间'),
            sa.PrimaryKeyConstraint('media_id'),
            comment='Telegram消息媒体表',
        )
    else:
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

    if not _has_table('tg_forward_record'):
        op.create_table(
            'tg_forward_record',
            sa.Column('record_id', sa.BigInteger(), autoincrement=True, nullable=False, comment='发送记录主键'),
            sa.Column('message_id', sa.BigInteger(), nullable=True, comment='消息ID；主动对话框发送为空'),
            sa.Column('account_id', sa.BigInteger(), nullable=False, comment='发送账号ID'),
            sa.Column('target_chat_pk', sa.BigInteger(), nullable=True, comment='目标频道主键'),
            sa.Column('target_chat_id', sa.String(length=100), nullable=False, comment='目标Telegram Chat ID'),
            sa.Column('target_chat_title', sa.String(length=255), nullable=True, comment='目标标题'),
            sa.Column('forward_type', sa.String(length=20), nullable=False, comment='发送类型：auto/manual/dialog'),
            sa.Column('status', sa.String(length=30), nullable=False, comment='发送状态'),
            sa.Column('sent_telegram_message_id', sa.BigInteger(), nullable=True, comment='发送后的Telegram消息ID'),
            sa.Column('error_message', sa.Text(), nullable=True, comment='错误原因'),
            sa.Column('create_time', sa.DateTime(), nullable=True, comment='创建时间'),
            sa.PrimaryKeyConstraint('record_id'),
            comment='Telegram发送记录表',
        )
    elif _has_column('tg_forward_record', 'message_id'):
        op.alter_column('tg_forward_record', 'message_id', existing_type=sa.BigInteger(), nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    for table_name in (
        'tg_forward_record',
        'tg_message_media',
        'tg_message',
        'tg_ad_text',
        'tg_sensitive_word',
        'tg_listener_rule',
        'tg_chat',
        'tg_account',
    ):
        if _has_table(table_name):
            op.drop_table(table_name)
