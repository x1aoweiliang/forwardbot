from datetime import datetime
from typing import Annotated

from fastapi import Path, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from common.aspect.db_seesion import DBSessionDependency
from common.aspect.pre_auth import CurrentUserDependency, PreAuthDependency
from common.router import APIRouterPro
from common.vo import PageResponseModel, ResponseBaseModel
from module_admin.entity.vo.user_vo import CurrentUserModel
from module_telegram.dao.telegram_dao import TelegramDao
from module_telegram.entity.do.telegram_do import TgAccount, TgListenerRule
from module_telegram.entity.vo.telegram_vo import (
    TgAccountModel,
    TgAccountPageQueryModel,
    TgAdTextModel,
    TgAdTextPageQueryModel,
    TgChatModel,
    TgChatPageQueryModel,
    TgChatSendMessageModel,
    TgContentCleanRuleModel,
    TgContentCleanRulePageQueryModel,
    TgForwardRecordModel,
    TgForwardRecordPageQueryModel,
    TgListenerRuleModel,
    TgListenerRulePageQueryModel,
    TgLoginCodeModel,
    TgLoginConfirmModel,
    TgManualForwardModel,
    TgMessageModel,
    TgMessagePageQueryModel,
    TgSensitiveWordModel,
    TgSensitiveWordPageQueryModel,
)
from module_telegram.service.telegram_client_service import TelegramClientManager
from module_telegram.service.telegram_service import TelegramCrudService
from utils.response_util import ResponseUtil

telegram_controller = APIRouterPro(prefix='/telegram', order_num=30, tags=['Telegram管理'], dependencies=[PreAuthDependency()])


def _split_ids(ids: str) -> list[int]:
    return [int(item) for item in ids.split(',') if item.strip()]


def _audit_for_create(data: object, current_user: CurrentUserModel) -> None:
    if hasattr(data, 'create_by'):
        data.create_by = current_user.user.user_name
    if hasattr(data, 'update_by'):
        data.update_by = current_user.user.user_name
    if hasattr(data, 'create_time'):
        data.create_time = datetime.now()
    if hasattr(data, 'update_time'):
        data.update_time = datetime.now()


def _audit_for_update(data: object, current_user: CurrentUserModel) -> None:
    if hasattr(data, 'update_by'):
        data.update_by = current_user.user.user_name
    if hasattr(data, 'update_time'):
        data.update_time = datetime.now()


@telegram_controller.get('/account/list', response_model=PageResponseModel[TgAccountModel], summary='获取Telegram账号列表')
async def list_accounts(
    request: Request,
    query: Annotated[TgAccountPageQueryModel, Query()],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    return ResponseUtil.success(model_content=await TelegramCrudService.list_accounts(query_db, query))


@telegram_controller.get('/account/{account_id}', summary='获取Telegram账号详情')
async def get_account(
    request: Request,
    account_id: Annotated[int, Path(description='账号ID')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    return ResponseUtil.success(data=await TelegramCrudService.account_detail(query_db, account_id))


@telegram_controller.post('/account', response_model=ResponseBaseModel, summary='新增Telegram账号')
async def add_account(
    request: Request,
    item: TgAccountModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    _audit_for_create(item, current_user)
    result = await TelegramCrudService.add_account(query_db, item)
    return ResponseUtil.success(msg=result.message)


@telegram_controller.put('/account', response_model=ResponseBaseModel, summary='修改Telegram账号')
async def edit_account(
    request: Request,
    item: TgAccountModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    _audit_for_update(item, current_user)
    result = await TelegramCrudService.edit_account(query_db, item)
    return ResponseUtil.success(msg=result.message)


@telegram_controller.delete('/account/{account_ids}', response_model=ResponseBaseModel, summary='删除Telegram账号')
async def delete_accounts(
    request: Request,
    account_ids: Annotated[str, Path(description='账号ID，逗号分隔')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    result = await TelegramCrudService.delete_accounts(query_db, _split_ids(account_ids))
    return ResponseUtil.success(msg=result.message)


@telegram_controller.post('/account/send-code', response_model=ResponseBaseModel, summary='发送Telegram登录验证码')
async def send_login_code(
    request: Request,
    item: TgLoginCodeModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    account = await TelegramDao.get_detail(query_db, TgAccount, 'account_id', item.account_id)
    if not account:
        return ResponseUtil.failure(msg='账号不存在')
    login_code_hash = await TelegramClientManager.send_login_code(account)
    await TelegramDao.update_item(
        query_db,
        TgAccount,
        {
            'account_id': item.account_id,
            'session_status': 'code_sent',
            'login_code_hash': login_code_hash,
            'session_path': str(TelegramClientManager.build_session_path(account)),
            'last_error': None,
            'update_time': datetime.now(),
        },
    )
    await query_db.commit()
    return ResponseUtil.success(msg='验证码已发送')


@telegram_controller.post('/account/confirm-login', response_model=ResponseBaseModel, summary='确认Telegram登录')
async def confirm_login(
    request: Request,
    item: TgLoginConfirmModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    account = await TelegramDao.get_detail(query_db, TgAccount, 'account_id', item.account_id)
    if not account:
        return ResponseUtil.failure(msg='账号不存在')
    status = await TelegramClientManager.confirm_login(account, item.code, item.password)
    await TelegramDao.update_item(
        query_db,
        TgAccount,
        {
            'account_id': item.account_id,
            'session_status': status,
            'login_code_hash': None if status == 'authorized' else account.login_code_hash,
            'session_path': str(TelegramClientManager.build_session_path(account)),
            'update_time': datetime.now(),
        },
    )
    await query_db.commit()
    return ResponseUtil.success(msg='登录状态已更新', data={'sessionStatus': status})


@telegram_controller.post('/account/{account_id}/start', response_model=ResponseBaseModel, summary='启动Telegram监听')
async def start_account_listener(
    request: Request,
    account_id: Annotated[int, Path(description='账号ID')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    account = await TelegramDao.get_detail(query_db, TgAccount, 'account_id', account_id)
    if not account:
        return ResponseUtil.failure(msg='账号不存在')
    await TelegramClientManager.start_listener(query_db, account)
    return ResponseUtil.success(msg='监听已启动')


@telegram_controller.post('/account/{account_id}/stop', response_model=ResponseBaseModel, summary='停止Telegram监听')
async def stop_account_listener(request: Request, account_id: Annotated[int, Path(description='账号ID')]) -> Response:
    await TelegramClientManager.disconnect(account_id)
    return ResponseUtil.success(msg='监听已停止')


@telegram_controller.get('/chat/list', response_model=PageResponseModel[TgChatModel], summary='获取频道/群组列表')
async def list_chats(
    request: Request,
    query: Annotated[TgChatPageQueryModel, Query()],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    return ResponseUtil.success(model_content=await TelegramCrudService.list_chats(query_db, query))


@telegram_controller.post('/chat', response_model=ResponseBaseModel, summary='新增频道/群组')
async def add_chat(
    request: Request,
    item: TgChatModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    _audit_for_create(item, current_user)
    result = await TelegramCrudService.save_chat(query_db, item)
    return ResponseUtil.success(msg=result.message)


@telegram_controller.put('/chat', response_model=ResponseBaseModel, summary='修改频道/群组')
async def edit_chat(
    request: Request,
    item: TgChatModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    _audit_for_update(item, current_user)
    result = await TelegramCrudService.save_chat(query_db, item)
    return ResponseUtil.success(msg=result.message)


@telegram_controller.delete('/chat/{chat_pks}', response_model=ResponseBaseModel, summary='删除频道/群组')
async def delete_chats(
    request: Request,
    chat_pks: Annotated[str, Path(description='频道主键，逗号分隔')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    result = await TelegramCrudService.delete_chats(query_db, _split_ids(chat_pks))
    return ResponseUtil.success(msg=result.message)


@telegram_controller.post('/chat/sync/{account_id}', response_model=ResponseBaseModel, summary='同步Telegram对话框')
async def sync_chats(
    request: Request,
    account_id: Annotated[int, Path(description='账号ID')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    result = await TelegramCrudService.sync_chats(query_db, account_id)
    return ResponseUtil.success(msg=result.message)


@telegram_controller.post('/chat/send-message', response_model=ResponseBaseModel, summary='按对话框发送消息')
async def send_chat_message(
    request: Request,
    item: TgChatSendMessageModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    result = await TelegramCrudService.send_chat_message(query_db, item)
    return ResponseUtil.success(msg=result.message)


@telegram_controller.get('/listener/list', response_model=PageResponseModel[TgListenerRuleModel], summary='获取监听规则列表')
async def list_rules(
    request: Request,
    query: Annotated[TgListenerRulePageQueryModel, Query()],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    return ResponseUtil.success(model_content=await TelegramCrudService.list_rules(query_db, query))


@telegram_controller.post('/listener', response_model=ResponseBaseModel, summary='新增监听规则')
async def add_rule(
    request: Request,
    item: TgListenerRuleModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    _audit_for_create(item, current_user)
    result = await TelegramCrudService.save_rule(query_db, item)
    account = await TelegramDao.get_detail(query_db, TgAccount, 'account_id', item.account_id)
    if account and account.status == '0' and account.session_status == 'authorized':
        await TelegramClientManager.reload_listener(query_db, account)
    return ResponseUtil.success(msg=result.message)


@telegram_controller.put('/listener', response_model=ResponseBaseModel, summary='修改监听规则')
async def edit_rule(
    request: Request,
    item: TgListenerRuleModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    old_rule = await TelegramDao.get_detail(query_db, TgListenerRule, 'rule_id', item.rule_id) if item.rule_id else None
    reload_account_ids = {item.account_id}
    if old_rule:
        reload_account_ids.add(old_rule.account_id)
    _audit_for_update(item, current_user)
    result = await TelegramCrudService.save_rule(query_db, item)
    for account_id in reload_account_ids:
        account = await TelegramDao.get_detail(query_db, TgAccount, 'account_id', account_id)
        if account and account.status == '0' and account.session_status == 'authorized':
            await TelegramClientManager.reload_listener(query_db, account)
    return ResponseUtil.success(msg=result.message)


@telegram_controller.delete('/listener/{rule_ids}', response_model=ResponseBaseModel, summary='删除监听规则')
async def delete_rules(
    request: Request,
    rule_ids: Annotated[str, Path(description='规则ID，逗号分隔')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    parsed_rule_ids = _split_ids(rule_ids)
    reload_account_ids = set()
    for rule_id in parsed_rule_ids:
        rule = await TelegramDao.get_detail(query_db, TgListenerRule, 'rule_id', rule_id)
        if rule:
            reload_account_ids.add(rule.account_id)
    result = await TelegramCrudService.delete_rules(query_db, parsed_rule_ids)
    for account_id in reload_account_ids:
        account = await TelegramDao.get_detail(query_db, TgAccount, 'account_id', account_id)
        if account and account.status == '0' and account.session_status == 'authorized':
            await TelegramClientManager.reload_listener(query_db, account)
    return ResponseUtil.success(msg=result.message)


@telegram_controller.get('/sensitive-word/list', response_model=PageResponseModel[TgSensitiveWordModel], summary='获取敏感词列表')
async def list_sensitive_words(
    request: Request,
    query: Annotated[TgSensitiveWordPageQueryModel, Query()],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    return ResponseUtil.success(model_content=await TelegramCrudService.list_sensitive_words(query_db, query))


@telegram_controller.post('/sensitive-word', response_model=ResponseBaseModel, summary='新增敏感词')
async def add_sensitive_word(
    request: Request,
    item: TgSensitiveWordModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    _audit_for_create(item, current_user)
    result = await TelegramCrudService.save_sensitive_word(query_db, item)
    return ResponseUtil.success(msg=result.message)


@telegram_controller.put('/sensitive-word', response_model=ResponseBaseModel, summary='修改敏感词')
async def edit_sensitive_word(
    request: Request,
    item: TgSensitiveWordModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    _audit_for_update(item, current_user)
    result = await TelegramCrudService.save_sensitive_word(query_db, item)
    return ResponseUtil.success(msg=result.message)


@telegram_controller.delete('/sensitive-word/{word_ids}', response_model=ResponseBaseModel, summary='删除敏感词')
async def delete_sensitive_words(
    request: Request,
    word_ids: Annotated[str, Path(description='敏感词ID，逗号分隔')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    result = await TelegramCrudService.delete_sensitive_words(query_db, _split_ids(word_ids))
    return ResponseUtil.success(msg=result.message)


@telegram_controller.get('/ad-text/list', response_model=PageResponseModel[TgAdTextModel], summary='获取广告词列表')
async def list_ad_texts(
    request: Request,
    query: Annotated[TgAdTextPageQueryModel, Query()],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    return ResponseUtil.success(model_content=await TelegramCrudService.list_ad_texts(query_db, query))


@telegram_controller.post('/ad-text', response_model=ResponseBaseModel, summary='新增广告词')
async def add_ad_text(
    request: Request,
    item: TgAdTextModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    _audit_for_create(item, current_user)
    result = await TelegramCrudService.save_ad_text(query_db, item)
    return ResponseUtil.success(msg=result.message)


@telegram_controller.put('/ad-text', response_model=ResponseBaseModel, summary='修改广告词')
async def edit_ad_text(
    request: Request,
    item: TgAdTextModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    _audit_for_update(item, current_user)
    result = await TelegramCrudService.save_ad_text(query_db, item)
    return ResponseUtil.success(msg=result.message)


@telegram_controller.post('/ad-text/{ad_id}/enable', response_model=ResponseBaseModel, summary='启用广告词')
async def enable_ad_text(
    request: Request,
    ad_id: Annotated[int, Path(description='广告词ID')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    result = await TelegramCrudService.enable_ad_text(query_db, ad_id)
    return ResponseUtil.success(msg=result.message)


@telegram_controller.delete('/ad-text/{ad_ids}', response_model=ResponseBaseModel, summary='删除广告词')
async def delete_ad_texts(
    request: Request,
    ad_ids: Annotated[str, Path(description='广告词ID，逗号分隔')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    result = await TelegramCrudService.delete_ad_texts(query_db, _split_ids(ad_ids))
    return ResponseUtil.success(msg=result.message)


@telegram_controller.get('/clean-rule/list', response_model=PageResponseModel[TgContentCleanRuleModel], summary='获取内容清理规则列表')
async def list_clean_rules(
    request: Request,
    query: Annotated[TgContentCleanRulePageQueryModel, Query()],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    return ResponseUtil.success(model_content=await TelegramCrudService.list_clean_rules(query_db, query))


@telegram_controller.post('/clean-rule', response_model=ResponseBaseModel, summary='新增内容清理规则')
async def add_clean_rule(
    request: Request,
    item: TgContentCleanRuleModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    _audit_for_create(item, current_user)
    result = await TelegramCrudService.save_clean_rule(query_db, item)
    return ResponseUtil.success(msg=result.message)


@telegram_controller.put('/clean-rule', response_model=ResponseBaseModel, summary='修改内容清理规则')
async def edit_clean_rule(
    request: Request,
    item: TgContentCleanRuleModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
    current_user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    _audit_for_update(item, current_user)
    result = await TelegramCrudService.save_clean_rule(query_db, item)
    return ResponseUtil.success(msg=result.message)


@telegram_controller.delete('/clean-rule/{clean_ids}', response_model=ResponseBaseModel, summary='删除内容清理规则')
async def delete_clean_rules(
    request: Request,
    clean_ids: Annotated[str, Path(description='清理规则ID，逗号分隔')],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    result = await TelegramCrudService.delete_clean_rules(query_db, _split_ids(clean_ids))
    return ResponseUtil.success(msg=result.message)


@telegram_controller.get('/message/list', response_model=PageResponseModel[TgMessageModel], summary='获取监听消息列表')
async def list_messages(
    request: Request,
    query: Annotated[TgMessagePageQueryModel, Query()],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    return ResponseUtil.success(model_content=await TelegramCrudService.list_messages(query_db, query))


@telegram_controller.post('/message/manual-forward', response_model=ResponseBaseModel, summary='手动转发消息')
async def manual_forward(
    request: Request,
    item: TgManualForwardModel,
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    result = await TelegramCrudService.manual_forward(query_db, item)
    return ResponseUtil.success(msg=result.message)


@telegram_controller.get('/forward-record/list', response_model=PageResponseModel[TgForwardRecordModel], summary='获取发送记录列表')
async def list_forward_records(
    request: Request,
    query: Annotated[TgForwardRecordPageQueryModel, Query()],
    query_db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    return ResponseUtil.success(model_content=await TelegramCrudService.list_forward_records(query_db, query))
