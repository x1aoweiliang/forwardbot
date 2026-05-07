from datetime import datetime

from sqlalchemy import CHAR, BigInteger, Column, DateTime, Integer, String, Text, UniqueConstraint

from config.database import Base
from config.env import DataBaseConfig
from utils.common_util import SqlalchemyUtil


class TgAccount(Base):
    """
    Telegram账号表
    """

    __tablename__ = 'tg_account'
    __table_args__ = {'comment': 'Telegram账号表'}

    account_id = Column(BigInteger, primary_key=True, nullable=False, autoincrement=True, comment='账号主键')
    account_name = Column(String(100), nullable=False, comment='账号名称')
    phone = Column(String(50), nullable=False, comment='手机号')
    api_id = Column(Integer, nullable=False, comment='Telegram API ID')
    api_hash = Column(String(255), nullable=False, comment='Telegram API Hash')
    session_path = Column(String(500), nullable=True, comment='Session文件路径')
    session_status = Column(String(30), nullable=False, server_default='logged_out', comment='Session状态')
    login_code_hash = Column(String(255), nullable=True, comment='登录验证码Hash')
    last_error = Column(Text, nullable=True, comment='最后错误')
    status = Column(CHAR(1), nullable=False, server_default='0', comment='状态（0启用 1停用）')
    create_by = Column(String(64), nullable=True, server_default="''", comment='创建者')
    create_time = Column(DateTime, nullable=True, default=datetime.now, comment='创建时间')
    update_by = Column(String(64), nullable=True, server_default="''", comment='更新者')
    update_time = Column(DateTime, nullable=True, default=datetime.now, comment='更新时间')
    remark = Column(
        String(500),
        nullable=True,
        server_default=SqlalchemyUtil.get_server_default_null(DataBaseConfig.db_type),
        comment='备注',
    )


class TgChat(Base):
    """
    Telegram频道/群组表
    """

    __tablename__ = 'tg_chat'
    __table_args__ = (UniqueConstraint('account_id', 'chat_id', name='uq_tg_chat_account_chat'), {'comment': 'Telegram频道/群组表'})

    chat_pk = Column(BigInteger, primary_key=True, nullable=False, autoincrement=True, comment='频道主键')
    account_id = Column(BigInteger, nullable=False, comment='账号ID')
    chat_id = Column(String(100), nullable=False, comment='Telegram Chat ID')
    chat_title = Column(String(255), nullable=False, comment='频道/群组标题')
    username = Column(String(255), nullable=True, comment='用户名')
    chat_type = Column(String(30), nullable=False, comment='类型：group/channel/private')
    can_listen = Column(CHAR(1), nullable=False, server_default='N', comment='是否可监听')
    can_send = Column(CHAR(1), nullable=False, server_default='N', comment='是否可发送')
    status = Column(CHAR(1), nullable=False, server_default='0', comment='状态（0启用 1停用）')
    create_by = Column(String(64), nullable=True, server_default="''", comment='创建者')
    create_time = Column(DateTime, nullable=True, default=datetime.now, comment='创建时间')
    update_by = Column(String(64), nullable=True, server_default="''", comment='更新者')
    update_time = Column(DateTime, nullable=True, default=datetime.now, comment='更新时间')
    remark = Column(String(500), nullable=True, comment='备注')


class TgListenerRule(Base):
    """
    Telegram监听规则表
    """

    __tablename__ = 'tg_listener_rule'
    __table_args__ = {'comment': 'Telegram监听规则表'}

    rule_id = Column(BigInteger, primary_key=True, nullable=False, autoincrement=True, comment='规则主键')
    account_id = Column(BigInteger, nullable=False, comment='监听账号ID')
    source_chat_pk = Column(BigInteger, nullable=False, comment='来源频道主键')
    source_chat_pks = Column(String(1000), nullable=True, comment='来源频道主键，逗号分隔')
    target_chat_pks = Column(String(1000), nullable=False, comment='目标频道主键，逗号分隔')
    rule_name = Column(String(100), nullable=False, comment='规则名称')
    status = Column(CHAR(1), nullable=False, server_default='0', comment='状态（0启用 1停用）')
    create_by = Column(String(64), nullable=True, server_default="''", comment='创建者')
    create_time = Column(DateTime, nullable=True, default=datetime.now, comment='创建时间')
    update_by = Column(String(64), nullable=True, server_default="''", comment='更新者')
    update_time = Column(DateTime, nullable=True, default=datetime.now, comment='更新时间')
    remark = Column(String(500), nullable=True, comment='备注')


class TgSensitiveWord(Base):
    """
    Telegram敏感词表
    """

    __tablename__ = 'tg_sensitive_word'
    __table_args__ = {'comment': 'Telegram敏感词表'}

    word_id = Column(BigInteger, primary_key=True, nullable=False, autoincrement=True, comment='敏感词主键')
    word = Column(String(255), nullable=False, comment='敏感词')
    match_case = Column(CHAR(1), nullable=False, server_default='N', comment='是否区分大小写')
    status = Column(CHAR(1), nullable=False, server_default='0', comment='状态（0启用 1停用）')
    create_by = Column(String(64), nullable=True, server_default="''", comment='创建者')
    create_time = Column(DateTime, nullable=True, default=datetime.now, comment='创建时间')
    update_by = Column(String(64), nullable=True, server_default="''", comment='更新者')
    update_time = Column(DateTime, nullable=True, default=datetime.now, comment='更新时间')
    remark = Column(String(500), nullable=True, comment='备注')


class TgAdText(Base):
    """
    Telegram广告词表
    """

    __tablename__ = 'tg_ad_text'
    __table_args__ = {'comment': 'Telegram广告词表'}

    ad_id = Column(BigInteger, primary_key=True, nullable=False, autoincrement=True, comment='广告词主键')
    ad_name = Column(String(100), nullable=False, comment='广告词名称')
    ad_content = Column(Text, nullable=False, comment='广告词内容')
    enabled = Column(CHAR(1), nullable=False, server_default='0', comment='是否启用（1启用 0停用）')
    create_by = Column(String(64), nullable=True, server_default="''", comment='创建者')
    create_time = Column(DateTime, nullable=True, default=datetime.now, comment='创建时间')
    update_by = Column(String(64), nullable=True, server_default="''", comment='更新者')
    update_time = Column(DateTime, nullable=True, default=datetime.now, comment='更新时间')
    remark = Column(String(500), nullable=True, comment='备注')


class TgContentCleanRule(Base):
    """
    Telegram内容清理规则表
    """

    __tablename__ = 'tg_content_clean_rule'
    __table_args__ = {'comment': 'Telegram内容清理规则表'}

    clean_id = Column(BigInteger, primary_key=True, nullable=False, autoincrement=True, comment='清理规则主键')
    clean_name = Column(String(100), nullable=False, comment='规则名称')
    match_text = Column(Text, nullable=False, comment='匹配文本')
    replacement = Column(Text, nullable=True, comment='替换文本')
    match_case = Column(CHAR(1), nullable=False, server_default='Y', comment='是否区分大小写')
    status = Column(CHAR(1), nullable=False, server_default='0', comment='状态（0启用 1停用）')
    create_by = Column(String(64), nullable=True, server_default="''", comment='创建者')
    create_time = Column(DateTime, nullable=True, default=datetime.now, comment='创建时间')
    update_by = Column(String(64), nullable=True, server_default="''", comment='更新者')
    update_time = Column(DateTime, nullable=True, default=datetime.now, comment='更新时间')
    remark = Column(String(500), nullable=True, comment='备注')


class TgMessage(Base):
    """
    Telegram监听消息表
    """

    __tablename__ = 'tg_message'
    __table_args__ = (
        UniqueConstraint('account_id', 'source_chat_id', 'telegram_message_id', name='uq_tg_message_source'),
        {'comment': 'Telegram监听消息表'},
    )

    message_id = Column(BigInteger, primary_key=True, nullable=False, autoincrement=True, comment='消息主键')
    account_id = Column(BigInteger, nullable=False, comment='账号ID')
    source_chat_pk = Column(BigInteger, nullable=True, comment='来源频道主键')
    source_chat_id = Column(String(100), nullable=False, comment='来源Telegram Chat ID')
    source_chat_title = Column(String(255), nullable=True, comment='来源标题')
    telegram_message_id = Column(BigInteger, nullable=False, comment='Telegram消息ID')
    message_text = Column(Text, nullable=True, comment='消息文本')
    sent_at = Column(DateTime, nullable=True, comment='Telegram发送时间')
    is_sensitive = Column(CHAR(1), nullable=False, server_default='N', comment='是否命中敏感词')
    sensitive_word = Column(String(255), nullable=True, comment='命中的敏感词')
    auto_forward_status = Column(String(30), nullable=False, server_default='pending', comment='自动转发状态')
    create_time = Column(DateTime, nullable=True, default=datetime.now, comment='创建时间')
    update_time = Column(DateTime, nullable=True, default=datetime.now, comment='更新时间')


class TgMessageMedia(Base):
    """
    Telegram消息媒体表
    """

    __tablename__ = 'tg_message_media'
    __table_args__ = {'comment': 'Telegram消息媒体表'}

    media_id = Column(BigInteger, primary_key=True, nullable=False, autoincrement=True, comment='媒体主键')
    message_id = Column(BigInteger, nullable=False, comment='消息ID')
    media_type = Column(String(30), nullable=False, comment='媒体类型')
    local_path = Column(String(500), nullable=False, server_default='', comment='本地相对路径')
    source_telegram_message_id = Column(BigInteger, nullable=True, comment='源Telegram媒体消息ID')
    media_index = Column(Integer, nullable=False, server_default='0', comment='媒体顺序')
    file_name = Column(String(255), nullable=True, comment='文件名')
    mime_type = Column(String(100), nullable=True, comment='MIME类型')
    file_size = Column(BigInteger, nullable=True, comment='文件大小')
    create_time = Column(DateTime, nullable=True, default=datetime.now, comment='创建时间')


class TgForwardRecord(Base):
    """
    Telegram发送记录表
    """

    __tablename__ = 'tg_forward_record'
    __table_args__ = {'comment': 'Telegram发送记录表'}

    record_id = Column(BigInteger, primary_key=True, nullable=False, autoincrement=True, comment='发送记录主键')
    message_id = Column(BigInteger, nullable=True, comment='消息ID；主动对话框发送为空')
    account_id = Column(BigInteger, nullable=False, comment='发送账号ID')
    target_chat_pk = Column(BigInteger, nullable=True, comment='目标频道主键')
    target_chat_id = Column(String(100), nullable=False, comment='目标Telegram Chat ID')
    target_chat_title = Column(String(255), nullable=True, comment='目标标题')
    forward_type = Column(String(20), nullable=False, comment='发送类型：auto/manual/dialog')
    status = Column(String(30), nullable=False, comment='发送状态')
    sent_telegram_message_id = Column(BigInteger, nullable=True, comment='发送后的Telegram消息ID')
    error_message = Column(Text, nullable=True, comment='错误原因')
    create_time = Column(DateTime, nullable=True, default=datetime.now, comment='创建时间')
