from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class TelegramBaseModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, from_attributes=True, populate_by_name=True)


class TgAccountModel(TelegramBaseModel):
    account_id: int | None = Field(default=None, description='账号主键')
    account_name: str | None = Field(default=None, description='账号名称')
    phone: str | None = Field(default=None, description='手机号')
    api_id: int | None = Field(default=None, description='Telegram API ID')
    api_hash: str | None = Field(default=None, description='Telegram API Hash')
    session_path: str | None = Field(default=None, description='Session文件路径')
    session_status: str | None = Field(default='logged_out', description='Session状态')
    login_code_hash: str | None = Field(default=None, description='登录验证码Hash')
    last_error: str | None = Field(default=None, description='最后错误')
    status: Literal['0', '1'] | None = Field(default='0', description='状态')
    create_by: str | None = Field(default=None, description='创建者')
    create_time: datetime | None = Field(default=None, description='创建时间')
    update_by: str | None = Field(default=None, description='更新者')
    update_time: datetime | None = Field(default=None, description='更新时间')
    remark: str | None = Field(default=None, description='备注')


class TgAccountPageQueryModel(TgAccountModel):
    session_status: str | None = Field(default=None, description='Session状态')
    status: Literal['0', '1'] | None = Field(default=None, description='状态')
    page_num: int = Field(default=1, description='当前页码')
    page_size: int = Field(default=10, description='每页记录数')


class TgLoginCodeModel(TelegramBaseModel):
    account_id: int = Field(description='账号ID')


class TgLoginConfirmModel(TelegramBaseModel):
    account_id: int = Field(description='账号ID')
    code: str | None = Field(default=None, description='验证码')
    password: str | None = Field(default=None, description='二次密码')


class TgChatModel(TelegramBaseModel):
    chat_pk: int | None = Field(default=None, description='频道主键')
    account_id: int | None = Field(default=None, description='账号ID')
    chat_id: str | None = Field(default=None, description='Telegram Chat ID')
    chat_title: str | None = Field(default=None, description='频道/群组标题')
    username: str | None = Field(default=None, description='用户名')
    chat_type: str | None = Field(default=None, description='类型')
    can_listen: Literal['Y', 'N'] | None = Field(default='N', description='是否可监听')
    can_send: Literal['Y', 'N'] | None = Field(default='N', description='是否可发送')
    status: Literal['0', '1'] | None = Field(default='0', description='状态')
    create_by: str | None = Field(default=None, description='创建者')
    create_time: datetime | None = Field(default=None, description='创建时间')
    update_by: str | None = Field(default=None, description='更新者')
    update_time: datetime | None = Field(default=None, description='更新时间')
    remark: str | None = Field(default=None, description='备注')


class TgChatPageQueryModel(TgChatModel):
    account_id: int | None = Field(default=None, description='账号ID')
    chat_title: str | None = Field(default=None, description='频道/群组标题')
    chat_type: str | None = Field(default=None, description='类型')
    can_listen: Literal['Y', 'N'] | None = Field(default=None, description='是否可监听')
    can_send: Literal['Y', 'N'] | None = Field(default=None, description='是否可发送')
    status: Literal['0', '1'] | None = Field(default=None, description='状态')
    page_num: int = Field(default=1, description='当前页码')
    page_size: int = Field(default=10, description='每页记录数')


class TgListenerRuleModel(TelegramBaseModel):
    rule_id: int | None = Field(default=None, description='规则主键')
    account_id: int | None = Field(default=None, description='账号ID')
    source_chat_pk: int | None = Field(default=None, description='来源频道主键')
    source_chat_pks: str | None = Field(default=None, description='来源频道主键，逗号分隔')
    target_chat_pks: str | None = Field(default=None, description='目标频道主键，逗号分隔')
    rule_name: str | None = Field(default=None, description='规则名称')
    status: Literal['0', '1'] | None = Field(default='0', description='状态')
    create_by: str | None = Field(default=None, description='创建者')
    create_time: datetime | None = Field(default=None, description='创建时间')
    update_by: str | None = Field(default=None, description='更新者')
    update_time: datetime | None = Field(default=None, description='更新时间')
    remark: str | None = Field(default=None, description='备注')


class TgListenerRulePageQueryModel(TgListenerRuleModel):
    account_id: int | None = Field(default=None, description='账号ID')
    source_chat_pk: int | None = Field(default=None, description='来源频道主键')
    source_chat_pks: str | None = Field(default=None, description='来源频道主键，逗号分隔')
    rule_name: str | None = Field(default=None, description='规则名称')
    status: Literal['0', '1'] | None = Field(default=None, description='状态')
    page_num: int = Field(default=1, description='当前页码')
    page_size: int = Field(default=10, description='每页记录数')


class TgSensitiveWordModel(TelegramBaseModel):
    word_id: int | None = Field(default=None, description='敏感词主键')
    word: str | None = Field(default=None, description='敏感词')
    match_case: Literal['Y', 'N'] | None = Field(default='N', description='是否区分大小写')
    status: Literal['0', '1'] | None = Field(default='0', description='状态')
    create_by: str | None = Field(default=None, description='创建者')
    create_time: datetime | None = Field(default=None, description='创建时间')
    update_by: str | None = Field(default=None, description='更新者')
    update_time: datetime | None = Field(default=None, description='更新时间')
    remark: str | None = Field(default=None, description='备注')


class TgSensitiveWordPageQueryModel(TgSensitiveWordModel):
    word: str | None = Field(default=None, description='敏感词')
    match_case: Literal['Y', 'N'] | None = Field(default=None, description='是否区分大小写')
    status: Literal['0', '1'] | None = Field(default=None, description='状态')
    page_num: int = Field(default=1, description='当前页码')
    page_size: int = Field(default=10, description='每页记录数')


class TgAdTextModel(TelegramBaseModel):
    ad_id: int | None = Field(default=None, description='广告词主键')
    ad_name: str | None = Field(default=None, description='广告词名称')
    ad_content: str | None = Field(default=None, description='广告词内容')
    enabled: Literal['0', '1'] | None = Field(default='0', description='是否启用')
    create_by: str | None = Field(default=None, description='创建者')
    create_time: datetime | None = Field(default=None, description='创建时间')
    update_by: str | None = Field(default=None, description='更新者')
    update_time: datetime | None = Field(default=None, description='更新时间')
    remark: str | None = Field(default=None, description='备注')


class TgAdTextPageQueryModel(TgAdTextModel):
    ad_name: str | None = Field(default=None, description='广告词名称')
    enabled: Literal['0', '1'] | None = Field(default=None, description='是否启用')
    page_num: int = Field(default=1, description='当前页码')
    page_size: int = Field(default=10, description='每页记录数')


class TgContentCleanRuleModel(TelegramBaseModel):
    clean_id: int | None = Field(default=None, description='清理规则主键')
    clean_name: str | None = Field(default=None, description='规则名称')
    match_text: str | None = Field(default=None, description='匹配文本')
    replacement: str | None = Field(default=None, description='替换文本')
    match_case: Literal['Y', 'N'] | None = Field(default='Y', description='是否区分大小写')
    status: Literal['0', '1'] | None = Field(default='0', description='状态')
    create_by: str | None = Field(default=None, description='创建者')
    create_time: datetime | None = Field(default=None, description='创建时间')
    update_by: str | None = Field(default=None, description='更新者')
    update_time: datetime | None = Field(default=None, description='更新时间')
    remark: str | None = Field(default=None, description='备注')


class TgContentCleanRulePageQueryModel(TgContentCleanRuleModel):
    clean_name: str | None = Field(default=None, description='规则名称')
    match_text: str | None = Field(default=None, description='匹配文本')
    match_case: Literal['Y', 'N'] | None = Field(default=None, description='是否区分大小写')
    status: Literal['0', '1'] | None = Field(default=None, description='状态')
    page_num: int = Field(default=1, description='当前页码')
    page_size: int = Field(default=10, description='每页记录数')


class TgMessageModel(TelegramBaseModel):
    message_id: int | None = Field(default=None, description='消息主键')
    account_id: int | None = Field(default=None, description='账号ID')
    source_chat_pk: int | None = Field(default=None, description='来源频道主键')
    source_chat_id: str | None = Field(default=None, description='来源Telegram Chat ID')
    source_chat_title: str | None = Field(default=None, description='来源标题')
    telegram_message_id: int | None = Field(default=None, description='Telegram消息ID')
    message_text: str | None = Field(default=None, description='消息文本')
    sent_at: datetime | None = Field(default=None, description='发送时间')
    is_sensitive: Literal['Y', 'N'] | None = Field(default='N', description='是否命中敏感词')
    sensitive_word: str | None = Field(default=None, description='命中的敏感词')
    auto_forward_status: str | None = Field(default='pending', description='自动转发状态')
    create_time: datetime | None = Field(default=None, description='创建时间')
    update_time: datetime | None = Field(default=None, description='更新时间')


class TgMessagePageQueryModel(TgMessageModel):
    account_id: int | None = Field(default=None, description='账号ID')
    source_chat_pk: int | None = Field(default=None, description='来源频道主键')
    source_chat_id: str | None = Field(default=None, description='来源Telegram Chat ID')
    message_text: str | None = Field(default=None, description='消息文本')
    is_sensitive: Literal['Y', 'N'] | None = Field(default=None, description='是否命中敏感词')
    auto_forward_status: str | None = Field(default=None, description='自动转发状态')
    page_num: int = Field(default=1, description='当前页码')
    page_size: int = Field(default=10, description='每页记录数')


class TgMessageMediaModel(TelegramBaseModel):
    media_id: int | None = Field(default=None, description='媒体主键')
    message_id: int | None = Field(default=None, description='消息ID')
    media_type: str | None = Field(default=None, description='媒体类型')
    local_path: str | None = Field(default=None, description='本地相对路径')
    source_telegram_message_id: int | None = Field(default=None, description='源Telegram媒体消息ID')
    media_index: int | None = Field(default=None, description='媒体顺序')
    file_name: str | None = Field(default=None, description='文件名')
    mime_type: str | None = Field(default=None, description='MIME类型')
    file_size: int | None = Field(default=None, description='文件大小')
    create_time: datetime | None = Field(default=None, description='创建时间')


class TgForwardRecordModel(TelegramBaseModel):
    record_id: int | None = Field(default=None, description='发送记录主键')
    message_id: int | None = Field(default=None, description='消息ID')
    account_id: int | None = Field(default=None, description='账号ID')
    target_chat_pk: int | None = Field(default=None, description='目标频道主键')
    target_chat_id: str | None = Field(default=None, description='目标Telegram Chat ID')
    target_chat_title: str | None = Field(default=None, description='目标标题')
    forward_type: Literal['auto', 'manual', 'dialog'] | None = Field(default=None, description='发送类型')
    status: str | None = Field(default=None, description='发送状态')
    sent_telegram_message_id: int | None = Field(default=None, description='发送后的Telegram消息ID')
    error_message: str | None = Field(default=None, description='错误原因')
    create_time: datetime | None = Field(default=None, description='创建时间')


class TgForwardRecordPageQueryModel(TgForwardRecordModel):
    page_num: int = Field(default=1, description='当前页码')
    page_size: int = Field(default=10, description='每页记录数')


class TgManualForwardModel(TelegramBaseModel):
    message_id: int = Field(description='消息ID')
    target_chat_pks: list[int] = Field(description='目标频道主键列表')


class TgChatSendMessageModel(TelegramBaseModel):
    chat_pk: int = Field(description='对话框主键')
    text: str = Field(description='发送文本')
