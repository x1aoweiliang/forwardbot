import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.vo import CrudResponseModel, PageModel
from config.env import ArchiveConfig, UploadConfig
from exceptions.exception import ServiceException
from module_telegram.dao.telegram_dao import TelegramDao
from module_telegram.entity.do.telegram_do import (
    TgAccount,
    TgAdText,
    TgChat,
    TgContentCleanRule,
    TgForwardRecord,
    TgListenerRule,
    TgMessage,
    TgSensitiveWord,
)
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
    TgForwardRecordPageQueryModel,
    TgListenerRuleModel,
    TgListenerRulePageQueryModel,
    TgManualForwardModel,
    TgMessagePageQueryModel,
    TgSensitiveWordModel,
    TgSensitiveWordPageQueryModel,
)
from module_telegram.service.media_runtime import media_runtime, run_blocking
from module_telegram.service.telegram_client_service import TelegramClientManager
from module_telegram.service.telegram_rule_service import (
    ContentCleanPolicy,
    ForwardDispatcher,
    ForwardDispatchResult,
    ListenerRulePolicy,
    SensitiveWordMatcher,
    TelegramStorageService,
)
from utils.common_util import CamelCaseUtil


class TelegramCrudService:
    """
    Telegram模块通用CRUD服务。
    """

    @staticmethod
    def _clean_payload(model_object: Any) -> dict[str, Any]:
        return model_object.model_dump(exclude_unset=True, exclude_none=True)

    @staticmethod
    def _result_model(row: Any, model_class: type) -> Any:
        return model_class(**CamelCaseUtil.transform_result(row)) if row else model_class()

    @staticmethod
    def sanitize_account_result(row: Any) -> TgAccountModel:
        result = TelegramCrudService._result_model(row, TgAccountModel)
        if result.api_hash:
            result.api_hash = '********'
        result.session_path = None
        result.login_code_hash = None
        return result

    @classmethod
    async def list_accounts(cls, db: AsyncSession, query: TgAccountPageQueryModel, is_page: bool = True) -> PageModel | list[dict[str, Any]]:
        filters = [
            TgAccount.account_name.like(f'%{query.account_name}%') if query.account_name else True,
            TgAccount.phone.like(f'%{query.phone}%') if query.phone else True,
            TgAccount.status == query.status if query.status else True,
        ]
        result = await TelegramDao.list_items(db, TgAccount, query, filters, TgAccount.account_id.desc(), is_page)
        rows = result.rows if isinstance(result, PageModel) else result
        for row in rows:
            if 'apiHash' in row:
                row['apiHash'] = '********'
            if 'sessionPath' in row:
                row['sessionPath'] = None
            if 'loginCodeHash' in row:
                row['loginCodeHash'] = None
        return result

    @classmethod
    async def account_detail(cls, db: AsyncSession, account_id: int) -> TgAccountModel:
        return cls.sanitize_account_result(await TelegramDao.get_detail(db, TgAccount, 'account_id', account_id))

    @classmethod
    async def add_account(cls, db: AsyncSession, item: TgAccountModel) -> CrudResponseModel:
        data = cls._clean_payload(item)
        data.setdefault('session_status', 'logged_out')
        try:
            await TelegramDao.add_item(db, TgAccount, data)
            await db.commit()
            return CrudResponseModel(is_success=True, message='新增成功')
        except Exception as exc:
            await db.rollback()
            raise exc

    @classmethod
    async def edit_account(cls, db: AsyncSession, item: TgAccountModel) -> CrudResponseModel:
        data = cls._clean_payload(item)
        if not data.get('account_id'):
            raise ServiceException(message='账号ID不能为空')
        if data.get('api_hash') == '********':
            del data['api_hash']
        data['update_time'] = datetime.now()
        try:
            await TelegramDao.update_item(db, TgAccount, data)
            await db.commit()
            return CrudResponseModel(is_success=True, message='修改成功')
        except Exception as exc:
            await db.rollback()
            raise exc

    @classmethod
    async def delete_accounts(cls, db: AsyncSession, ids: list[int]) -> CrudResponseModel:
        try:
            await TelegramDao.delete_items(db, TgAccount, TgAccount.account_id, ids)
            await db.commit()
            return CrudResponseModel(is_success=True, message='删除成功')
        except Exception as exc:
            await db.rollback()
            raise exc

    @classmethod
    async def list_chats(cls, db: AsyncSession, query: TgChatPageQueryModel, is_page: bool = True) -> PageModel | list[dict[str, Any]]:
        filters = [
            TgChat.account_id == query.account_id if query.account_id else True,
            TgChat.chat_title.like(f'%{query.chat_title}%') if query.chat_title else True,
            TgChat.chat_type == query.chat_type if query.chat_type else True,
            TgChat.can_listen == query.can_listen if query.can_listen else True,
            TgChat.can_send == query.can_send if query.can_send else True,
            TgChat.status == query.status if query.status else True,
        ]
        return await TelegramDao.list_items(db, TgChat, query, filters, TgChat.chat_pk.desc(), is_page)

    @classmethod
    async def chat_detail(cls, db: AsyncSession, chat_pk: int) -> TgChatModel:
        return cls._result_model(await TelegramDao.get_detail(db, TgChat, 'chat_pk', chat_pk), TgChatModel)

    @classmethod
    async def save_chat(cls, db: AsyncSession, item: TgChatModel) -> CrudResponseModel:
        data = cls._clean_payload(item)
        if 'ad_text_id' in item.model_fields_set:
            data['ad_text_id'] = item.ad_text_id
        try:
            if data.get('chat_pk'):
                data['update_time'] = datetime.now()
                await TelegramDao.update_item(db, TgChat, data)
                message = '修改成功'
            else:
                await TelegramDao.add_item(db, TgChat, data)
                message = '新增成功'
            await db.commit()
            return CrudResponseModel(is_success=True, message=message)
        except Exception as exc:
            await db.rollback()
            raise exc

    @classmethod
    async def delete_chats(cls, db: AsyncSession, ids: list[int]) -> CrudResponseModel:
        try:
            await TelegramDao.delete_items(db, TgChat, TgChat.chat_pk, ids)
            await db.commit()
            return CrudResponseModel(is_success=True, message='删除成功')
        except Exception as exc:
            await db.rollback()
            raise exc

    @classmethod
    async def send_chat_message(cls, db: AsyncSession, item: TgChatSendMessageModel) -> CrudResponseModel:
        text = str(item.text or '').strip()
        if not text:
            raise ServiceException(message='发送内容不能为空')
        chat = await TelegramDao.get_chat_by_pk(db, item.chat_pk)
        if not chat:
            raise ServiceException(message='对话框不存在')
        if chat.status != '0':
            raise ServiceException(message='对话框已停用')
        if chat.can_send != 'Y':
            raise ServiceException(message='该对话框未启用发送权限')
        account = await TelegramDao.get_detail(db, TgAccount, 'account_id', chat.account_id)
        if not account:
            raise ServiceException(message='账号不存在')
        try:
            sent_message_id: int | None = None
            error_message: str | None = None
            status = 'success'
            try:
                result = await TelegramClientManager.send_message_to_chat(account, chat.chat_id, text, chat.username)
                sent_message_id = result.get('telegram_message_id')
            except Exception as exc:
                status = 'failed'
                error_message = str(exc)
            await TelegramDao.add_forward_record(
                db,
                {
                    'message_id': None,
                    'account_id': account.account_id,
                    'target_chat_pk': chat.chat_pk,
                    'target_chat_id': chat.chat_id,
                    'target_chat_title': chat.chat_title,
                    'forward_type': 'dialog',
                    'status': status,
                    'sent_telegram_message_id': sent_message_id,
                    'error_message': error_message,
                    'create_time': datetime.now(),
                },
            )
            await db.commit()
            if status != 'success':
                raise ServiceException(message=error_message or '发送失败')
            return CrudResponseModel(is_success=True, message='发送成功')
        except ServiceException:
            raise
        except Exception as exc:
            await db.rollback()
            raise exc

    @classmethod
    async def sync_chats(cls, db: AsyncSession, account_id: int) -> CrudResponseModel:
        account = await TelegramDao.get_detail(db, TgAccount, 'account_id', account_id)
        if not account:
            raise ServiceException(message='账号不存在')
        dialogs = await TelegramClientManager.list_dialogs(account)
        try:
            synced_count = 0
            for dialog in dialogs:
                if not dialog.get('chat_id'):
                    continue
                existing_chat = await TelegramDao.get_chat_by_account_and_chat_id(db, account_id, dialog['chat_id'])
                payload = {
                    'account_id': account_id,
                    'chat_id': dialog['chat_id'],
                    'chat_title': dialog['chat_title'],
                    'username': dialog.get('username'),
                    'chat_type': dialog['chat_type'],
                    'can_listen': dialog['can_listen'],
                    'can_send': dialog['can_send'],
                    'status': dialog['status'],
                    'update_time': datetime.now(),
                }
                if existing_chat:
                    payload['chat_pk'] = existing_chat.chat_pk
                    await TelegramDao.update_item(db, TgChat, payload)
                else:
                    payload['create_time'] = datetime.now()
                    await TelegramDao.add_item(db, TgChat, payload)
                synced_count += 1
            await db.commit()
            return CrudResponseModel(is_success=True, message=f'同步成功，共{synced_count}个对话')
        except Exception as exc:
            await db.rollback()
            raise exc

    @classmethod
    async def list_rules(cls, db: AsyncSession, query: TgListenerRulePageQueryModel, is_page: bool = True) -> PageModel | list[dict[str, Any]]:
        filters = [
            TgListenerRule.account_id == query.account_id if query.account_id else True,
            TgListenerRule.rule_name.like(f'%{query.rule_name}%') if query.rule_name else True,
            TgListenerRule.status == query.status if query.status else True,
        ]
        return await TelegramDao.list_items(db, TgListenerRule, query, filters, TgListenerRule.rule_id.desc(), is_page)

    @classmethod
    async def save_rule(cls, db: AsyncSession, item: TgListenerRuleModel) -> CrudResponseModel:
        data = cls._clean_payload(item)
        source_pks = ListenerRulePolicy.parse_chat_pks(data.get('source_chat_pks') or data.get('source_chat_pk'))
        if not source_pks:
            raise ServiceException(message='来源频道不能为空')
        forward_enabled = data.get('forward_enabled', item.forward_enabled)
        download_enabled = data.get('download_enabled', item.download_enabled)
        if forward_enabled == 'N' and download_enabled == 'N':
            raise ServiceException(message='请至少开启自动转发或自动下载')
        if forward_enabled == 'Y' and not data.get('target_chat_pks'):
            raise ServiceException(message='目标频道不能为空')
        data['forward_enabled'] = forward_enabled
        data['download_enabled'] = download_enabled
        data.setdefault('target_chat_pks', '')
        data['source_chat_pk'] = source_pks[0]
        data['source_chat_pks'] = ','.join(str(pk) for pk in source_pks)
        try:
            if data.get('rule_id'):
                data['update_time'] = datetime.now()
                await TelegramDao.update_item(db, TgListenerRule, data)
                message = '修改成功'
            else:
                await TelegramDao.add_item(db, TgListenerRule, data)
                message = '新增成功'
            await db.commit()
            return CrudResponseModel(is_success=True, message=message)
        except Exception as exc:
            await db.rollback()
            raise exc

    @classmethod
    async def delete_rules(cls, db: AsyncSession, ids: list[int]) -> CrudResponseModel:
        try:
            await TelegramDao.delete_items(db, TgListenerRule, TgListenerRule.rule_id, ids)
            await db.commit()
            return CrudResponseModel(is_success=True, message='删除成功')
        except Exception as exc:
            await db.rollback()
            raise exc

    @classmethod
    async def list_sensitive_words(
        cls, db: AsyncSession, query: TgSensitiveWordPageQueryModel, is_page: bool = True
    ) -> PageModel | list[dict[str, Any]]:
        filters = [
            TgSensitiveWord.word.like(f'%{query.word}%') if query.word else True,
            TgSensitiveWord.match_case == query.match_case if query.match_case else True,
            TgSensitiveWord.status == query.status if query.status else True,
        ]
        return await TelegramDao.list_items(db, TgSensitiveWord, query, filters, TgSensitiveWord.word_id.desc(), is_page)

    @classmethod
    async def save_sensitive_word(cls, db: AsyncSession, item: TgSensitiveWordModel) -> CrudResponseModel:
        data = cls._clean_payload(item)
        try:
            if data.get('word_id'):
                data['update_time'] = datetime.now()
                await TelegramDao.update_item(db, TgSensitiveWord, data)
                message = '修改成功'
            else:
                await TelegramDao.add_item(db, TgSensitiveWord, data)
                message = '新增成功'
            await db.commit()
            return CrudResponseModel(is_success=True, message=message)
        except Exception as exc:
            await db.rollback()
            raise exc

    @classmethod
    async def delete_sensitive_words(cls, db: AsyncSession, ids: list[int]) -> CrudResponseModel:
        try:
            await TelegramDao.delete_items(db, TgSensitiveWord, TgSensitiveWord.word_id, ids)
            await db.commit()
            return CrudResponseModel(is_success=True, message='删除成功')
        except Exception as exc:
            await db.rollback()
            raise exc

    @classmethod
    async def list_ad_texts(cls, db: AsyncSession, query: TgAdTextPageQueryModel, is_page: bool = True) -> PageModel | list[dict[str, Any]]:
        filters = [
            TgAdText.ad_name.like(f'%{query.ad_name}%') if query.ad_name else True,
            TgAdText.enabled == query.enabled if query.enabled else True,
        ]
        return await TelegramDao.list_items(db, TgAdText, query, filters, TgAdText.ad_id.desc(), is_page)

    @classmethod
    async def save_ad_text(cls, db: AsyncSession, item: TgAdTextModel) -> CrudResponseModel:
        data = cls._clean_payload(item)
        try:
            if data.get('enabled') == '1':
                await TelegramDao.disable_other_ad_texts(db, data.get('ad_id'))
            if data.get('ad_id'):
                data['update_time'] = datetime.now()
                await TelegramDao.update_item(db, TgAdText, data)
                message = '修改成功'
            else:
                await TelegramDao.add_item(db, TgAdText, data)
                message = '新增成功'
            await db.commit()
            return CrudResponseModel(is_success=True, message=message)
        except Exception as exc:
            await db.rollback()
            raise exc

    @classmethod
    async def enable_ad_text(cls, db: AsyncSession, ad_id: int) -> CrudResponseModel:
        try:
            await TelegramDao.disable_other_ad_texts(db, ad_id)
            await TelegramDao.update_item(db, TgAdText, {'ad_id': ad_id, 'enabled': '1', 'update_time': datetime.now()})
            await db.commit()
            return CrudResponseModel(is_success=True, message='默认广告词设置成功')
        except Exception as exc:
            await db.rollback()
            raise exc

    @classmethod
    async def delete_ad_texts(cls, db: AsyncSession, ids: list[int]) -> CrudResponseModel:
        try:
            await TelegramDao.delete_items(db, TgAdText, TgAdText.ad_id, ids)
            await db.commit()
            return CrudResponseModel(is_success=True, message='删除成功')
        except Exception as exc:
            await db.rollback()
            raise exc

    @classmethod
    async def list_clean_rules(
        cls, db: AsyncSession, query: TgContentCleanRulePageQueryModel, is_page: bool = True
    ) -> PageModel | list[dict[str, Any]]:
        filters = [
            TgContentCleanRule.clean_name.like(f'%{query.clean_name}%') if query.clean_name else True,
            TgContentCleanRule.match_text.like(f'%{query.match_text}%') if query.match_text else True,
            TgContentCleanRule.match_case == query.match_case if query.match_case else True,
            TgContentCleanRule.status == query.status if query.status else True,
        ]
        return await TelegramDao.list_items(db, TgContentCleanRule, query, filters, TgContentCleanRule.clean_id.desc(), is_page)

    @classmethod
    async def save_clean_rule(cls, db: AsyncSession, item: TgContentCleanRuleModel) -> CrudResponseModel:
        data = cls._clean_payload(item)
        try:
            if data.get('clean_id'):
                data['update_time'] = datetime.now()
                await TelegramDao.update_item(db, TgContentCleanRule, data)
                message = '修改成功'
            else:
                await TelegramDao.add_item(db, TgContentCleanRule, data)
                message = '新增成功'
            await db.commit()
            return CrudResponseModel(is_success=True, message=message)
        except Exception as exc:
            await db.rollback()
            raise exc

    @classmethod
    async def delete_clean_rules(cls, db: AsyncSession, ids: list[int]) -> CrudResponseModel:
        try:
            await TelegramDao.delete_items(db, TgContentCleanRule, TgContentCleanRule.clean_id, ids)
            await db.commit()
            return CrudResponseModel(is_success=True, message='删除成功')
        except Exception as exc:
            await db.rollback()
            raise exc

    @classmethod
    async def list_messages(cls, db: AsyncSession, query: TgMessagePageQueryModel, is_page: bool = True) -> PageModel | list[dict[str, Any]]:
        filters = [
            TgMessage.account_id == query.account_id if query.account_id else True,
            TgMessage.source_chat_pk == query.source_chat_pk if query.source_chat_pk else True,
            TgMessage.source_chat_id == query.source_chat_id if query.source_chat_id else True,
            TgMessage.is_sensitive == query.is_sensitive if query.is_sensitive else True,
            TgMessage.auto_forward_status == query.auto_forward_status if query.auto_forward_status else True,
        ]
        return await TelegramDao.list_items(db, TgMessage, query, filters, TgMessage.message_id.desc(), is_page)

    @classmethod
    async def list_forward_records(
        cls, db: AsyncSession, query: TgForwardRecordPageQueryModel, is_page: bool = True
    ) -> PageModel | list[dict[str, Any]]:
        filters = [
            TgForwardRecord.message_id == query.message_id if query.message_id else True,
            TgForwardRecord.account_id == query.account_id if query.account_id else True,
            TgForwardRecord.target_chat_pk == query.target_chat_pk if query.target_chat_pk else True,
            TgForwardRecord.target_chat_id == query.target_chat_id if query.target_chat_id else True,
            TgForwardRecord.forward_type == query.forward_type if query.forward_type else True,
            TgForwardRecord.status == query.status if query.status else True,
        ]
        return await TelegramDao.list_items(db, TgForwardRecord, query, filters, TgForwardRecord.record_id.desc(), is_page)

    @classmethod
    async def manual_forward(cls, db: AsyncSession, item: TgManualForwardModel) -> CrudResponseModel:
        message = await TelegramDao.get_detail(db, TgMessage, 'message_id', item.message_id)
        if not message:
            raise ServiceException(message='消息不存在')
        target_chats = await TelegramDao.get_chats_by_pks(db, item.target_chat_pks)
        if not target_chats:
            raise ServiceException(message='目标频道不能为空')
        account = await TelegramDao.get_detail(db, TgAccount, 'account_id', message.account_id)
        if not account:
            raise ServiceException(message='发送账号不存在')
        try:
            medias = await TelegramDao.get_media_by_message_id(db, message.message_id)
            await TelegramForwardService.enqueue(
                db, account, message, target_chats, 'manual',
                TelegramForwardService._source_message_ids(message, medias), item.forward_mode or 'native_hidden',
            )
            await db.commit()
            return CrudResponseModel(is_success=True, message=f'已加入发送队列，共{len(target_chats)}个目标，请在发送记录查看结果')
        except Exception:
            await db.rollback()
            raise


class TelegramForwardService:
    """Transactional outbox: prepare under lock, perform I/O detached, finish fenced."""

    @staticmethod
    def _source_message_ids(message: Any, medias: list[Any]) -> list[int]:
        return [media.source_telegram_message_id for media in medias] if medias else [message.telegram_message_id]

    @classmethod
    async def enqueue(
        cls, db: AsyncSession, account: Any, message: Any, targets: list[Any],
        forward_type: str, source_ids: list[int], mode: str,
    ) -> None:
        if forward_type == 'auto':
            message.auto_forward_status = 'pending'
        for target in targets:
            await TelegramDao.add_forward_record(db, {
                'message_id': message.message_id, 'account_id': account.account_id,
                'target_chat_pk': target.chat_pk, 'target_chat_id': target.chat_id,
                'target_chat_title': target.chat_title, 'forward_type': forward_type,
                'source_message_ids': json.dumps(source_ids), 'forward_mode': mode, 'status': 'queued',
            })

    @staticmethod
    def _copy(row: Any) -> Any:
        return SimpleNamespace(**{column.name: getattr(row, column.name) for column in row.__table__.columns})

    @classmethod
    async def prepare(
        cls, db: AsyncSession, record: TgForwardRecord, message: TgMessage | None,
    ) -> dict[str, Any] | None:
        source_ids = json.loads(record.source_message_ids or '[]')
        account = await db.get(TgAccount, record.account_id)
        target = await db.get(TgChat, record.target_chat_pk) if record.target_chat_pk else None
        error = None
        if not message or (record.forward_type == 'auto' and message.is_sensitive == 'Y'):
            error = '来源消息不存在或已被敏感词拦截'
        elif not account or account.status != '0' or not target or target.status != '0' or target.can_send != 'Y':
            error = '发送账号或目标已停用'
        elif target.chat_id != record.target_chat_id:
            error = '目标频道已变更，请重新提交发送'
        elif record.forward_type == 'auto':
            rules = await TelegramDao.get_enabled_rules_for_account(db, account.account_id)
            if not any(
                rule.forward_enabled == 'Y' and rule.forward_mode == record.forward_mode
                and message.source_chat_pk in ListenerRulePolicy.source_chat_pks(rule)
                and target.chat_pk in ListenerRulePolicy.parse_chat_pks(rule.target_chat_pks)
                for rule in rules
            ):
                error = '对应的自动转发规则已停用'
        if error:
            record.status, record.error_message = 'failed', error
            record.lease_token = record.lease_until = None
            await cls.refresh_status(db, message)
            return None
        all_medias = await TelegramDao.get_media_by_message_id(db, message.message_id)
        # Legacy records without source IDs include the original message's files.
        medias = [media for media in all_medias if not source_ids or media.source_telegram_message_id in source_ids]
        if record.delivery_stage == 'local' and any(
            media.storage_kind == 'archive' and media.download_status in {'queued', 'retry', 'downloading'}
            for media in medias
        ):
            return None
        default_ad = await TelegramDao.get_enabled_ad_text(db)
        ads = await TelegramDao.get_ad_texts_by_ids(db, [target.ad_text_id] if target.ad_text_id else [])
        content = ContentCleanPolicy.apply(message.message_text, await TelegramDao.get_enabled_clean_rules(db))
        return {
            'account': cls._copy(account), 'message': cls._copy(message), 'target': cls._copy(target),
            'medias': [cls._copy(media) for media in medias], 'source_ids': source_ids,
            'mode': record.forward_mode, 'forward_type': record.forward_type,
            'content': content, 'ad': ads[0].ad_content if ads else default_ad.ad_content if default_ad else None,
            'local_only': record.delivery_stage == 'local',
        }

    @classmethod
    async def execute(cls, payload: dict[str, Any]) -> tuple[ForwardDispatchResult, dict[int, str]]:  # noqa: PLR0912
        """No database session, lock or ORM object is passed into network operations."""
        account, message, target = payload['account'], payload['message'], payload['target']
        medias, source_ids = payload['medias'], payload['source_ids']
        forward_type = payload['forward_type']
        paths = {}
        try:
            client = await TelegramClientManager.get_authorized_client(account)
            if payload['mode'] == 'native_hidden':
                ids = source_ids or cls._source_message_ids(message, medias)
                if not ids or any(source_id is None for source_id in ids):
                    raise RuntimeError('源消息不可用')
                sent = await client.forward_messages(
                    ForwardDispatcher._normalize_target_chat(target.chat_id),
                    ids,
                    from_peer=ForwardDispatcher._normalize_target_chat(message.source_chat_id),
                    drop_author=True, drop_media_captions=False,
                )
                if isinstance(sent, list):
                    sent = sent[0] if sent else None
                return ForwardDispatchResult(message.message_id, target.chat_id, forward_type, 'success', getattr(sent, 'id', None)), paths
            dispatcher = ForwardDispatcher(client)
            if not medias:
                if payload['local_only']:
                    raise RuntimeError('待补发的素材记录已缺失')
                results = await dispatcher.dispatch_text(message.message_id, [target.chat_id], payload['content'], payload['ad'], forward_type)
                return results[0], paths
            sources = []
            if not payload['local_only']:
                try:
                    ids = [media.source_telegram_message_id for media in medias]
                    if all(source_id is not None for source_id in ids):
                        sources = await client.get_messages(TelegramClientManager._normalize_chat_ref(message.source_chat_id), ids=ids)
                        sources = list(sources) if sources else []
                except Exception:
                    sources = []
            source_map = {item.id: item for item in sources if item and getattr(item, 'media', None)}
            if all(media.source_telegram_message_id in source_map for media in medias):
                results = await dispatcher.dispatch_files(
                    message.message_id, [target.chat_id], [source_map[media.source_telegram_message_id] for media in medias],
                    payload['content'], payload['ad'], forward_type,
                )
                if results[0].status == 'success':
                    return results[0], paths
            files = []
            for media in medias:
                storage = TelegramStorageService.for_media(media)
                if media.storage_kind == 'archive' and media.download_status != 'ready':
                    continue
                if media.local_path and await run_blocking((storage.base_dir / media.local_path).is_file):
                    files.append(str(storage.base_dir / media.local_path))
                elif media.storage_kind == 'cache' and media.source_telegram_message_id in source_map:
                    saved = await storage.save_message_media(source_map[media.source_telegram_message_id], account.account_id, message.message_id)
                    if saved:
                        files.append(str(saved.absolute_path))
                        paths[media.media_id] = saved.relative_path
            if len(files) == len(medias):
                results = await dispatcher.dispatch_files(message.message_id, [target.chat_id], files, payload['content'], payload['ad'], forward_type)
                return results[0], paths
            waiting = ArchiveConfig.archive_enabled and any(
                media.storage_kind == 'archive' and media.download_status in {'queued', 'retry', 'downloading'} for media in medias
            ) and all(
                media.local_path or media.media_id in paths
                or (media.storage_kind == 'archive' and media.download_status in {'queued', 'retry', 'downloading'})
                for media in medias
            )
            return ForwardDispatchResult(
                message.message_id, target.chat_id, forward_type, 'waiting_download' if waiting else 'failed',
                error_message='等待素材下载完成后补发' if waiting else '媒体文件不可用，请处理后手动转发',
            ), paths
        except Exception as exc:
            return ForwardDispatchResult(message.message_id, target.chat_id, forward_type, 'failed', error_message=str(exc)), paths

    @staticmethod
    async def refresh_status(db: AsyncSession, message: TgMessage | None) -> None:
        if not message or message.is_sensitive == 'Y':
            return
        await db.flush()
        records = await TelegramDao.get_auto_forward_records(db, message.message_id)
        if records:
            states = {record.status for record in records}
            message.auto_forward_status = (
                'partial_failed' if 'failed' in states else
                'pending' if states & {'queued', 'waiting_download', 'sending'} else 'success'
            )
            message.update_time = datetime.now()


@dataclass(frozen=True)
class TelegramMediaCleanupResult:
    scanned_count: int
    deleted_count: int
    missing_count: int
    skipped_count: int


class TelegramMediaCleanupService:
    """
    Telegram媒体本地文件清理服务。
    """

    @staticmethod
    def _resolve_media_path(base_dir: Path, local_path: str) -> Path | None:
        try:
            resolved_base = base_dir.resolve()
            resolved_path = (base_dir / local_path).resolve()
            if resolved_path == resolved_base or resolved_base not in resolved_path.parents:
                return None
            return resolved_path
        except (OSError, RuntimeError, ValueError):
            return None

    @classmethod
    async def cleanup_expired_local_files(
        cls,
        db: AsyncSession,
        base_dir: str | Path = UploadConfig.UPLOAD_PATH,
        retention_days: int = 7,
        now: datetime | None = None,
    ) -> TelegramMediaCleanupResult:
        current_time = now or datetime.now()
        cutoff_time = current_time - timedelta(days=retention_days)
        media_list = await TelegramDao.get_expired_media_with_local_path(db, cutoff_time)
        base_path = Path(base_dir)
        clear_media_ids = []
        deleted_count = 0
        missing_count = 0
        skipped_count = 0
        try:
            for media in media_list:
                if media_runtime.busy(media.message_id):
                    skipped_count += 1
                    continue
                with media_runtime.cleanup([media.message_id]):
                    if not await TelegramDao.lock_message_for_media_cleanup(db, media.message_id):
                        skipped_count += 1
                        continue
                    await db.refresh(media)
                    media_path = cls._resolve_media_path(base_path, media.local_path)
                    if not media_path:
                        skipped_count += 1
                        continue
                    if media_path.exists():
                        if media_path.is_file():
                            media_path.unlink()
                            deleted_count += 1
                            clear_media_ids.append(media.media_id)
                        else:
                            skipped_count += 1
                    else:
                        missing_count += 1
                        clear_media_ids.append(media.media_id)
            await TelegramDao.clear_media_local_paths(db, clear_media_ids)
            await db.commit()
            return TelegramMediaCleanupResult(
                scanned_count=len(media_list),
                deleted_count=deleted_count,
                missing_count=missing_count,
                skipped_count=skipped_count,
            )
        except Exception as exc:
            await db.rollback()
            raise exc


class TelegramMessageIngestService:
    """
    Telegram新消息入库、过滤、自动转发服务。
    """

    @staticmethod
    def _normalize_sent_at(sent_at: datetime | None) -> datetime | None:
        if sent_at and sent_at.tzinfo is not None:
            return sent_at.astimezone().replace(tzinfo=None)
        return sent_at

    @staticmethod
    def _message_text(message: Any) -> str | None:
        return getattr(message, 'message', None) or getattr(message, 'raw_text', None) or getattr(message, 'text', None)

    @classmethod
    def _album_text(cls, event: Any, messages: list[Any]) -> str | None:
        return (
            getattr(event, 'raw_text', None)
            or getattr(event, 'text', None)
            or next((cls._message_text(message) for message in messages if cls._message_text(message)), None)
        )

    @staticmethod
    def _message_file(message: Any) -> Any | None:
        return getattr(message, 'file', None)

    @classmethod
    async def _save_message_medias(
        cls, db: AsyncSession, db_message: TgMessage, messages: list[Any],
        collect: bool = False, existing: list[Any] | None = None,
    ) -> int:
        existing_ids = {item.source_telegram_message_id for item in (existing or [])}
        ordered_ids = sorted(existing_ids | {message.id for message in messages if getattr(message, 'media', None)})
        indexes = {source_id: index for index, source_id in enumerate(ordered_ids)}
        for item in existing or []:
            item.media_index = indexes[item.source_telegram_message_id]
        added = 0
        for message in messages:
            if not getattr(message, 'media', None) or message.id in existing_ids:
                continue
            message_file = cls._message_file(message)
            mime_type = getattr(message_file, 'mime_type', None)
            downloadable = bool(mime_type and mime_type.startswith(('image/', 'video/')))
            if getattr(message, 'photo', None) or getattr(message, 'video', None):
                downloadable = True
            media_data = {
                'message_id': db_message.message_id,
                'media_type': 'media',
                'local_path': '',
                'source_telegram_message_id': getattr(message, 'id', None),
                'media_index': indexes[message.id],
                'file_name': getattr(message_file, 'name', None),
                'mime_type': mime_type,
                'file_size': getattr(message_file, 'size', None),
            }
            if collect:
                media_data.update(
                    caption=cls._message_text(message), storage_kind='archive' if downloadable else 'cache',
                    source_media_id=getattr(getattr(message, 'photo', None) or getattr(message, 'document', None), 'id', None),
                    download_status=('blocked' if db_message.is_sensitive == 'Y' else 'queued') if downloadable else 'skipped',
                )
            await TelegramDao.add_media(db, media_data)
            added += 1
        return added

    @classmethod
    async def _enqueue_by_rules(
        cls,
        db: AsyncSession,
        account: TgAccount,
        source_chat: TgChat,
        db_message: TgMessage,
        source_messages: list[Any],
        media_source_ids: list[int] | None = None,
    ) -> None:
        rules = await TelegramDao.get_enabled_rules_for_account(db, account.account_id)
        matched_rules = [
            rule for rule in rules
            if source_chat.chat_pk in ListenerRulePolicy.source_chat_pks(rule)
            and getattr(rule, 'forward_enabled', 'Y') == 'Y'
        ]
        for rule in matched_rules:
            target_pks = ListenerRulePolicy.parse_chat_pks(rule.target_chat_pks)
            targets = await TelegramDao.get_chats_by_pks(db, target_pks)
            source_ids = media_source_ids if media_source_ids is not None else [item.id for item in source_messages]
            covered: dict[str, set[int]] = {}
            for record in await TelegramDao.get_auto_forward_records(db, db_message.message_id):
                if record.status in {'success', 'queued', 'waiting_download', 'sending'}:
                    covered.setdefault(record.target_chat_id, set()).update(json.loads(record.source_message_ids or '[]'))
            batches: dict[tuple[int, ...], list[TgChat]] = {}
            for target in targets:
                remaining = tuple(source_id for source_id in source_ids if source_id not in covered.get(target.chat_id, set()))
                if remaining:
                    batches.setdefault(remaining, []).append(target)
            for remaining, batch_targets in batches.items():
                await TelegramForwardService.enqueue(
                    db, account, db_message, batch_targets, 'auto', list(remaining),
                    getattr(rule, 'forward_mode', 'copy_clean'),
                )

    @classmethod
    async def ingest_event(cls, db: AsyncSession, account: TgAccount, source_chat: TgChat, event: Any) -> TgMessage:
        return await cls._ingest(db, account, source_chat, [event.message], cls._message_text(event.message))

    @classmethod
    async def ingest_album(cls, db: AsyncSession, account: TgAccount, source_chat: TgChat, event: Any) -> TgMessage:
        messages = sorted(getattr(event, 'messages', None) or [], key=lambda message: getattr(message, 'id', 0) or 0)
        if not messages:
            return await cls.ingest_event(db, account, source_chat, event)
        return await cls._ingest(db, account, source_chat, messages, cls._album_text(event, messages))

    @classmethod
    async def _ingest(
        cls, db: AsyncSession, account: TgAccount, source_chat: TgChat, messages: list[Any], message_text: str | None,
    ) -> TgMessage:
        first = messages[0]
        grouped_id = getattr(first, 'grouped_id', None)
        rules = await TelegramDao.get_enabled_rules_for_account(db, account.account_id)
        matched = [rule for rule in rules if source_chat.chat_pk in ListenerRulePolicy.source_chat_pks(rule)]
        collect = any(getattr(rule, 'download_enabled', 'N') == 'Y' for rule in matched)
        db_message = await TelegramDao.find_source_message(db, account.account_id, source_chat.chat_id, first.id, grouped_id)
        if db_message:
            # Catch-up and repeated events must never resend an already ingested message.
            if grouped_id:
                db_message = await db.scalar(
                    select(TgMessage).where(TgMessage.message_id == db_message.message_id)
                    .with_for_update().execution_options(populate_existing=True)
                )
                text_changed = False
                if not db_message.message_text and message_text:
                    words = await TelegramDao.get_enabled_words(db)
                    match = SensitiveWordMatcher(words, match_case=False).match(message_text)
                    db_message.message_text = message_text
                    text_changed = True
                    if match.is_blocked:
                        db_message.is_sensitive, db_message.sensitive_word = 'Y', match.hit_word
                        db_message.auto_forward_status = 'blocked'
                existing = await TelegramDao.get_media_by_message_id(db, db_message.message_id)
                existing_ids = {media.source_telegram_message_id for media in existing}
                new_messages = [item for item in messages if getattr(item, 'media', None) and item.id not in existing_ids]
                if db_message.is_sensitive == 'Y':
                    for media in existing:
                        if media.download_status in {'queued', 'retry', 'downloading', 'failed'}:
                            media.download_status = 'blocked'
                            media.lease_token = None
                            media.lease_until = None
                            media.next_retry_at = None
                added = await cls._save_message_medias(db, db_message, messages, db_message.collected == 'Y', existing)
                if added or text_changed:
                    db_message.media_revision += 1
                    db_message.update_time = datetime.now()
                if new_messages and db_message.is_sensitive != 'Y':
                    # Replays add nothing; late members must still reach each target.
                    await cls._enqueue_by_rules(
                        db, account, source_chat, db_message, new_messages,
                        media_source_ids=[item.id for item in new_messages],
                    )
            return db_message
        words = await TelegramDao.get_enabled_words(db)
        match_result = SensitiveWordMatcher(words, match_case=False).match(message_text)
        forwarding = any(getattr(rule, 'forward_enabled', 'Y') == 'Y' for rule in matched)
        db_message = await TelegramDao.add_message(db, {
            'account_id': account.account_id,
            'source_chat_pk': source_chat.chat_pk,
            'source_chat_id': source_chat.chat_id,
            'source_chat_title': source_chat.chat_title,
            'telegram_message_id': first.id,
            'grouped_id': grouped_id,
            'collected': 'Y' if collect else 'N',
            'media_revision': 1,
            'message_text': message_text,
            'sent_at': cls._normalize_sent_at(getattr(first, 'date', None)),
            'is_sensitive': 'Y' if match_result.is_blocked else 'N',
            'sensitive_word': match_result.hit_word,
            'auto_forward_status': 'blocked' if match_result.is_blocked else 'pending' if forwarding else 'disabled',
        })
        await db.flush()
        await cls._save_message_medias(db, db_message, messages, collect)
        if not match_result.is_blocked and forwarding:
            await cls._enqueue_by_rules(db, account, source_chat, db_message, messages)
        return db_message
