from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from common.vo import CrudResponseModel, PageModel
from config.env import UploadConfig
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
        if not data.get('target_chat_pks'):
            raise ServiceException(message='目标频道不能为空')
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
            if getattr(item, 'forward_mode', 'native_hidden') == 'native_hidden':
                results = await TelegramForwardService.forward_native_hidden_to_chats(db, account, message, target_chats, 'manual')
            else:
                results = await TelegramForwardService.forward_to_chats(db, account, message, target_chats, 'manual')
            await db.commit()
            success_count = sum(1 for result in results if result.status == 'success')
            return CrudResponseModel(is_success=True, message=f'手动转发完成，成功{success_count}个')
        except Exception as exc:
            await db.rollback()
            raise exc


class TelegramForwardService:
    """
    Telegram转发服务。
    """

    @classmethod
    async def _load_source_media_messages(cls, client: Any, message: TgMessage, medias: list[Any]) -> list[Any]:
        source_ids = [media.source_telegram_message_id for media in medias if getattr(media, 'source_telegram_message_id', None)]
        if not source_ids:
            return []
        try:
            loaded_messages = await client.get_messages(message.source_chat_id, ids=source_ids)
        except Exception:
            return []
        if not isinstance(loaded_messages, list) and hasattr(loaded_messages, '__iter__') and not isinstance(loaded_messages, str):
            loaded_messages = list(loaded_messages)
        elif not isinstance(loaded_messages, list):
            loaded_messages = [loaded_messages] if loaded_messages else []
        loaded_by_id = {getattr(item, 'id', None): item for item in loaded_messages if item}
        return [loaded_by_id[source_id] for source_id in source_ids if source_id in loaded_by_id]

    @classmethod
    def _media_unavailable_results(cls, message_id: int, target_chat_ids: list[str], forward_type: str) -> list[ForwardDispatchResult]:
        return [
            ForwardDispatchResult(
                message_id=message_id,
                target_chat_id=target_chat_id,
                forward_type=forward_type,
                status='failed',
                error_message='媒体文件不可用',
            )
            for target_chat_id in target_chat_ids
        ]

    @classmethod
    def _source_messages_by_id(cls, source_messages: list[Any]) -> dict[int, Any]:
        return {
            source_message_id: source_message
            for source_message in source_messages
            if (source_message_id := getattr(source_message, 'id', None)) is not None
        }

    @classmethod
    def _complete_source_media_messages(cls, medias: list[Any], source_messages: list[Any]) -> list[Any]:
        source_by_id = cls._source_messages_by_id(source_messages)
        ordered_messages = []
        for media in medias:
            source_message_id = getattr(media, 'source_telegram_message_id', None)
            if source_message_id is None or source_message_id not in source_by_id:
                return []
            ordered_messages.append(source_by_id[source_message_id])
        return ordered_messages

    @classmethod
    def _source_message_ids(cls, message: TgMessage, medias: list[Any]) -> list[int]:
        if medias:
            source_ids = [
                getattr(media, 'source_telegram_message_id', None)
                for media in sorted(medias, key=lambda item: getattr(item, 'media_index', 0) or 0)
            ]
            return source_ids if all(source_id is not None for source_id in source_ids) else []
        telegram_message_id = getattr(message, 'telegram_message_id', None)
        return [telegram_message_id] if telegram_message_id is not None else []

    @classmethod
    async def _forward_native_hidden_to_chat(
        cls,
        client: Any,
        message: TgMessage,
        target_chat: TgChat,
        source_message_ids: list[int],
        forward_type: str,
    ) -> ForwardDispatchResult:
        try:
            if not source_message_ids:
                raise RuntimeError('源消息不可用')
            sent_message = await client.forward_messages(
                ForwardDispatcher._normalize_target_chat(target_chat.chat_id),
                source_message_ids,
                from_peer=ForwardDispatcher._normalize_target_chat(message.source_chat_id),
                drop_author=True,
                drop_media_captions=False,
            )
            if isinstance(sent_message, list):
                sent_message_id = getattr(sent_message[0], 'id', None) if sent_message else None
            else:
                sent_message_id = getattr(sent_message, 'id', None)
            return ForwardDispatchResult(
                message_id=message.message_id,
                target_chat_id=target_chat.chat_id,
                forward_type=forward_type,
                status='success',
                sent_telegram_message_id=sent_message_id,
            )
        except Exception as exc:
            return ForwardDispatchResult(
                message_id=message.message_id,
                target_chat_id=target_chat.chat_id,
                forward_type=forward_type,
                status='failed',
                error_message=str(exc),
            )

    @classmethod
    async def forward_native_hidden_to_chats(
        cls,
        db: AsyncSession,
        account: TgAccount,
        message: TgMessage,
        target_chats: list[TgChat],
        forward_type: str,
    ) -> list[ForwardDispatchResult]:
        client = await TelegramClientManager.get_authorized_client(account)
        medias = await TelegramDao.get_media_by_message_id(db, message.message_id)
        source_message_ids = cls._source_message_ids(message, medias)
        results = [
            await cls._forward_native_hidden_to_chat(client, message, target_chat, source_message_ids, forward_type)
            for target_chat in target_chats
        ]
        await cls._persist_forward_results(db, account, message, target_chats, results, forward_type)
        return results

    @classmethod
    async def _persist_forward_results(
        cls,
        db: AsyncSession,
        account: TgAccount,
        message: TgMessage,
        target_chats: list[TgChat],
        results: list[ForwardDispatchResult],
        forward_type: str,
    ) -> None:
        chat_map = {chat.chat_id: chat for chat in target_chats}
        for result in results:
            chat = chat_map.get(result.target_chat_id)
            await TelegramDao.add_forward_record(
                db,
                {
                    'message_id': message.message_id,
                    'account_id': account.account_id,
                    'target_chat_pk': chat.chat_pk if chat else None,
                    'target_chat_id': result.target_chat_id,
                    'target_chat_title': chat.chat_title if chat else None,
                    'forward_type': forward_type,
                    'status': result.status,
                    'sent_telegram_message_id': result.sent_telegram_message_id,
                    'error_message': result.error_message,
                    'create_time': datetime.now(),
                },
            )
        if forward_type == 'auto':
            await TelegramDao.update_message(
                db,
                {
                    'message_id': message.message_id,
                    'auto_forward_status': 'success' if all(result.status == 'success' for result in results) else 'partial_failed',
                    'update_time': datetime.now(),
                },
            )

    @classmethod
    async def _download_media_fallback(
        cls,
        db: AsyncSession,
        account: TgAccount,
        message: TgMessage,
        medias: list[Any],
        source_messages: list[Any],
    ) -> list[str]:
        source_by_id = cls._source_messages_by_id(source_messages)
        storage = TelegramStorageService()
        file_paths = []
        for media in medias:
            if getattr(media, 'local_path', None):
                file_paths.append(str(storage.base_dir / media.local_path))
                continue
            source_message_id = getattr(media, 'source_telegram_message_id', None)
            source_message = source_by_id.get(source_message_id)
            if not source_message:
                continue
            try:
                media_path = await storage.save_message_media(source_message, account.account_id, message.message_id)
            except Exception:
                continue
            if not media_path:
                continue
            await TelegramDao.update_media_local_path(db, media.media_id, media_path.relative_path)
            file_paths.append(str(media_path.absolute_path))
        return file_paths

    @classmethod
    async def _complete_fallback_file_paths(
        cls,
        db: AsyncSession,
        account: TgAccount,
        message: TgMessage,
        medias: list[Any],
        source_messages: list[Any],
    ) -> list[str]:
        file_paths = await cls._download_media_fallback(db, account, message, medias, source_messages)
        return file_paths if len(file_paths) == len(medias) else []

    @classmethod
    async def forward_to_chats(
        cls,
        db: AsyncSession,
        account: TgAccount,
        message: TgMessage,
        target_chats: list[TgChat],
        forward_type: str,
        source_messages: list[Any] | None = None,
    ) -> list[Any]:
        client = await TelegramClientManager.get_authorized_client(account)
        default_ad_text = await TelegramDao.get_enabled_ad_text(db)
        target_ad_text_ids = sorted({chat.ad_text_id for chat in target_chats if getattr(chat, 'ad_text_id', None)})
        target_ad_texts = await TelegramDao.get_ad_texts_by_ids(db, target_ad_text_ids)
        ad_text_map = {ad_text.ad_id: ad_text.ad_content for ad_text in target_ad_texts}
        clean_rules = await TelegramDao.get_enabled_clean_rules(db)
        medias = await TelegramDao.get_media_by_message_id(db, message.message_id)
        target_chat_ids = [chat.chat_id for chat in target_chats]
        ad_text_by_target_id = {
            chat.chat_id: ad_text_map.get(getattr(chat, 'ad_text_id', None), default_ad_text.ad_content if default_ad_text else None)
            for chat in target_chats
        }
        cleaned_message_text = ContentCleanPolicy.apply(message.message_text, clean_rules)
        dispatcher = ForwardDispatcher(client)
        if medias:
            source_media_messages = list(source_messages or [])
            if not source_media_messages:
                source_media_messages = await cls._load_source_media_messages(client, message, medias)
            complete_source_media_messages = cls._complete_source_media_messages(medias, source_media_messages)
            if complete_source_media_messages:
                results = await dispatcher.dispatch_files(
                    message.message_id,
                    target_chat_ids,
                    complete_source_media_messages,
                    cleaned_message_text,
                    ad_text_by_target_id,
                    forward_type,
                )
                failed_results = [result for result in results if result.status != 'success']
                if failed_results:
                    file_paths = await cls._complete_fallback_file_paths(db, account, message, medias, complete_source_media_messages)
                    if file_paths:
                        retry_results = await dispatcher.dispatch_files(
                            message.message_id,
                            [result.target_chat_id for result in failed_results],
                            file_paths,
                            cleaned_message_text,
                            ad_text_by_target_id,
                            forward_type,
                        )
                        retry_map = {result.target_chat_id: result for result in retry_results}
                        results = [retry_map.get(result.target_chat_id, result) for result in results]
            else:
                file_paths = await cls._complete_fallback_file_paths(db, account, message, medias, source_media_messages)
                if file_paths:
                    results = await dispatcher.dispatch_files(
                        message.message_id,
                        target_chat_ids,
                        file_paths,
                        cleaned_message_text,
                        ad_text_by_target_id,
                        forward_type,
                    )
                else:
                    results = cls._media_unavailable_results(message.message_id, target_chat_ids, forward_type)
        else:
            results = await dispatcher.dispatch_text(
                message.message_id,
                target_chat_ids,
                cleaned_message_text,
                ad_text_by_target_id,
                forward_type,
            )
        await cls._persist_forward_results(db, account, message, target_chats, results, forward_type)
        return results


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
    async def _save_message_medias(cls, db: AsyncSession, db_message: TgMessage, messages: list[Any]) -> None:
        for media_index, message in enumerate(messages):
            if not getattr(message, 'media', None):
                continue
            message_file = cls._message_file(message)
            await TelegramDao.add_media(
                db,
                {
                    'message_id': db_message.message_id,
                    'media_type': 'media',
                    'local_path': '',
                    'source_telegram_message_id': getattr(message, 'id', None),
                    'media_index': media_index,
                    'file_name': getattr(message_file, 'name', None),
                    'mime_type': getattr(message_file, 'mime_type', None),
                    'file_size': getattr(message_file, 'size', None),
                },
            )

    @classmethod
    async def _forward_source_message_by_rules(
        cls,
        db: AsyncSession,
        account: TgAccount,
        source_chat: TgChat,
        db_message: TgMessage,
        source_messages: list[Any],
    ) -> None:
        rules = await TelegramDao.get_enabled_rules_for_account(db, account.account_id)
        matched_rules = [rule for rule in rules if source_chat.chat_pk in ListenerRulePolicy.source_chat_pks(rule)]
        for rule in matched_rules:
            target_pks = ListenerRulePolicy.parse_chat_pks(rule.target_chat_pks)
            targets = await TelegramDao.get_chats_by_pks(db, target_pks)
            try:
                if getattr(rule, 'forward_mode', 'copy_clean') == 'native_hidden':
                    await TelegramForwardService.forward_native_hidden_to_chats(db, account, db_message, targets, 'auto')
                else:
                    await TelegramForwardService.forward_to_chats(db, account, db_message, targets, 'auto', source_messages=source_messages)
            except Exception as exc:
                for target in targets:
                    await TelegramDao.add_forward_record(
                        db,
                        {
                            'message_id': db_message.message_id,
                            'account_id': account.account_id,
                            'target_chat_pk': target.chat_pk,
                            'target_chat_id': target.chat_id,
                            'target_chat_title': target.chat_title,
                            'forward_type': 'auto',
                            'status': 'failed',
                            'sent_telegram_message_id': None,
                            'error_message': str(exc),
                            'create_time': datetime.now(),
                        },
                    )
                raise

    @classmethod
    async def ingest_event(cls, db: AsyncSession, account: TgAccount, source_chat: TgChat, event: Any) -> TgMessage:
        message_text = cls._message_text(event.message)
        sent_at = cls._normalize_sent_at(getattr(event.message, 'date', None))
        words = await TelegramDao.get_enabled_words(db)
        matcher = SensitiveWordMatcher(words, match_case=False)
        match_result = matcher.match(message_text)
        db_message = await TelegramDao.add_message(
            db,
            {
                'account_id': account.account_id,
                'source_chat_pk': source_chat.chat_pk,
                'source_chat_id': source_chat.chat_id,
                'source_chat_title': source_chat.chat_title,
                'telegram_message_id': event.message.id,
                'message_text': message_text,
                'sent_at': sent_at,
                'is_sensitive': 'Y' if match_result.is_blocked else 'N',
                'sensitive_word': match_result.hit_word,
                'auto_forward_status': 'blocked' if match_result.is_blocked else 'pending',
            },
        )
        await db.flush()
        await cls._save_message_medias(db, db_message, [event.message])
        if match_result.is_blocked:
            return db_message
        try:
            await cls._forward_source_message_by_rules(db, account, source_chat, db_message, [event.message])
        except Exception:
            await TelegramDao.update_message(
                db,
                {
                    'message_id': db_message.message_id,
                    'auto_forward_status': 'partial_failed',
                    'update_time': datetime.now(),
                },
            )
        return db_message

    @classmethod
    async def ingest_album(cls, db: AsyncSession, account: TgAccount, source_chat: TgChat, event: Any) -> TgMessage:
        messages = sorted(getattr(event, 'messages', None) or [], key=lambda message: getattr(message, 'id', 0) or 0)
        if not messages:
            return await cls.ingest_event(db, account, source_chat, event)
        first_message = messages[0]
        message_text = cls._album_text(event, messages)
        sent_at = cls._normalize_sent_at(getattr(first_message, 'date', None))
        words = await TelegramDao.get_enabled_words(db)
        matcher = SensitiveWordMatcher(words, match_case=False)
        match_result = matcher.match(message_text)
        db_message = await TelegramDao.add_message(
            db,
            {
                'account_id': account.account_id,
                'source_chat_pk': source_chat.chat_pk,
                'source_chat_id': source_chat.chat_id,
                'source_chat_title': source_chat.chat_title,
                'telegram_message_id': first_message.id,
                'message_text': message_text,
                'sent_at': sent_at,
                'is_sensitive': 'Y' if match_result.is_blocked else 'N',
                'sensitive_word': match_result.hit_word,
                'auto_forward_status': 'blocked' if match_result.is_blocked else 'pending',
            },
        )
        await db.flush()
        await cls._save_message_medias(db, db_message, messages)
        if match_result.is_blocked:
            return db_message
        try:
            await cls._forward_source_message_by_rules(db, account, source_chat, db_message, messages)
        except Exception:
            await TelegramDao.update_message(
                db,
                {
                    'message_id': db_message.message_id,
                    'auto_forward_status': 'partial_failed',
                    'update_time': datetime.now(),
                },
            )
        return db_message
