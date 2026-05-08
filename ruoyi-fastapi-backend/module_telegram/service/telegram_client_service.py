import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from exceptions.exception import ServiceException
from module_telegram.dao.telegram_dao import TelegramDao
from module_telegram.entity.do.telegram_do import TgAccount
from module_telegram.service.telegram_rule_service import ListenerRulePolicy
from utils.log_util import logger

try:
    from telethon import TelegramClient, events
    from telethon.errors import (
        AuthKeyNotFound,
        AuthKeyUnregisteredError,
        PhoneCodeExpiredError,
        PhoneCodeInvalidError,
        SendCodeUnavailableError,
        SessionPasswordNeededError,
    )
except ImportError:  # pragma: no cover - exercised only when dependency is missing at runtime
    TelegramClient = None
    events = None
    AuthKeyNotFound = None
    AuthKeyUnregisteredError = None
    PhoneCodeExpiredError = None
    PhoneCodeInvalidError = None
    SendCodeUnavailableError = None
    SessionPasswordNeededError = None


class TelegramClientManager:
    """
    Telethon客户端管理器。
    """

    _clients: dict[int, Any] = {}
    _handlers: dict[int, list[Any]] = {}
    _album_buffers: dict[tuple[int, int, int], dict[int, Any]] = {}
    _album_flush_tasks: dict[tuple[int, int, int], asyncio.Task] = {}
    _album_flush_delay = 5.0
    _session_dir = Path('vf_admin/telegram_sessions')

    @classmethod
    def _ensure_dependency(cls) -> None:
        if TelegramClient is None:
            raise ServiceException(message='缺少telethon依赖，请先安装后重启服务')

    @classmethod
    def build_session_path(cls, account: TgAccount) -> Path:
        cls._session_dir.mkdir(parents=True, exist_ok=True)
        return cls._session_dir / f'account_{account.account_id}'

    @classmethod
    def _normalize_chat_ref(cls, chat_id: str) -> int | str:
        chat_ref = str(chat_id).strip()
        if chat_ref.lstrip('-').isdigit():
            return int(chat_ref)
        return chat_ref

    @classmethod
    async def get_client(cls, account: TgAccount) -> Any:
        cls._ensure_dependency()
        if account.account_id in cls._clients:
            return cls._clients[account.account_id]
        session_path = account.session_path or str(cls.build_session_path(account))
        client = TelegramClient(session_path, account.api_id, account.api_hash, sequential_updates=True)
        await client.connect()
        cls._clients[account.account_id] = client
        return client

    @classmethod
    async def get_authorized_client(cls, account: TgAccount) -> Any:
        client = await cls.get_client(account)
        if not await client.is_user_authorized():
            raise ServiceException(message='Telegram账号未登录')
        return client

    @classmethod
    async def send_login_code(cls, account: TgAccount) -> str:
        await cls.disconnect(account.account_id)
        client = await cls.get_client(account)
        try:
            result = await client.send_code_request(account.phone)
            return getattr(result, 'phone_code_hash', '')
        except Exception as exc:
            if SendCodeUnavailableError and isinstance(exc, SendCodeUnavailableError):
                raise ServiceException(message='验证码发送方式已用尽，请稍后再试，或直接输入最近一次收到的验证码') from exc
            raise exc

    @classmethod
    async def list_dialogs(cls, account: TgAccount) -> list[dict[str, Any]]:
        client = await cls.get_authorized_client(account)
        dialogs = []
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            chat_type = 'channel' if getattr(dialog, 'is_channel', False) else 'group' if getattr(dialog, 'is_group', False) else 'private'
            chat_id = getattr(dialog, 'id', None) or getattr(entity, 'id', '')
            dialogs.append(
                {
                    'chat_id': str(chat_id),
                    'chat_title': getattr(dialog, 'name', '') or str(getattr(entity, 'id', '')),
                    'username': getattr(entity, 'username', None),
                    'chat_type': chat_type,
                    'can_listen': 'Y' if chat_type in {'channel', 'group'} else 'N',
                    'can_send': 'Y',
                    'status': '0',
                }
            )
        return dialogs

    @classmethod
    async def _resolve_send_entity(cls, client: Any, chat_id: str | None, username: str | None = None) -> Any:
        normalized_username = str(username or '').strip().lstrip('@') or None
        normalized_chat_id: int | None = None
        if chat_id is not None and str(chat_id).strip():
            try:
                normalized_chat_id = int(str(chat_id).strip())
            except (TypeError, ValueError):
                normalized_chat_id = None
        if normalized_chat_id:
            try:
                return await client.get_input_entity(normalized_chat_id)
            except Exception:
                pass
        if normalized_username:
            try:
                return await client.get_input_entity(f'@{normalized_username}')
            except Exception:
                pass
        if normalized_chat_id:
            async for dialog in client.iter_dialogs(limit=200):
                entity = dialog.entity
                entity_username = str(getattr(entity, 'username', '') or '').strip().lower()
                if int(getattr(dialog, 'id', 0) or 0) == normalized_chat_id:
                    return entity
                if normalized_username and entity_username == normalized_username.lower():
                    return entity
            return normalized_chat_id
        if normalized_username:
            return f'@{normalized_username}'
        raise ServiceException(message='发送目标不能为空')

    @classmethod
    async def send_message_to_chat(cls, account: TgAccount, chat_id: str, text: str, username: str | None = None) -> dict[str, Any]:
        if not text or not text.strip():
            raise ServiceException(message='发送内容不能为空')
        client = await cls.get_authorized_client(account)
        entity = await cls._resolve_send_entity(client, chat_id, username)
        message = await client.send_message(entity=entity, message=text.strip())
        return {'success': True, 'telegram_message_id': getattr(message, 'id', None)}

    @classmethod
    async def confirm_login(cls, account: TgAccount, code: str | None = None, password: str | None = None) -> str:
        client = await cls.get_client(account)
        try:
            if code:
                await client.sign_in(account.phone, code, phone_code_hash=account.login_code_hash)
            elif password and account.session_status == 'password_required':
                await client.sign_in(password=password)
            else:
                raise ServiceException(message='请输入Telegram验证码')
        except Exception as exc:
            if SessionPasswordNeededError and isinstance(exc, SessionPasswordNeededError):
                if password:
                    await client.sign_in(password=password)
                    return 'authorized' if await client.is_user_authorized() else 'logged_out'
                return 'password_required'
            if PhoneCodeInvalidError and isinstance(exc, PhoneCodeInvalidError):
                raise ServiceException(message='验证码错误，请检查后重新输入') from exc
            if PhoneCodeExpiredError and isinstance(exc, PhoneCodeExpiredError):
                raise ServiceException(message='验证码已过期，请重新发送验证码') from exc
            if (
                (AuthKeyNotFound and isinstance(exc, AuthKeyNotFound))
                or (AuthKeyUnregisteredError and isinstance(exc, AuthKeyUnregisteredError))
            ):
                await cls.disconnect(account.account_id)
                raise ServiceException(message='Telegram登录会话已失效，请重新发送验证码') from exc
            raise exc
        return 'authorized' if await client.is_user_authorized() else 'logged_out'

    @classmethod
    async def disconnect(cls, account_id: int) -> None:
        await cls._cancel_album_flush_tasks(account_id)
        client = cls._clients.pop(account_id, None)
        if client:
            await client.disconnect()
        cls._handlers.pop(account_id, None)

    @classmethod
    def _album_buffer_key(cls, account: TgAccount, source_chat: Any, grouped_id: Any) -> tuple[int, int, int]:
        return (int(account.account_id), int(source_chat.chat_pk), int(grouped_id))

    @classmethod
    def _queue_grouped_message(cls, account: TgAccount, source_chat: Any, event: Any) -> None:
        grouped_id = getattr(event.message, 'grouped_id', None)
        if grouped_id is None:
            return
        key = cls._album_buffer_key(account, source_chat, grouped_id)
        message_id = getattr(event.message, 'id', None)
        if message_id is None:
            return
        cls._album_buffers.setdefault(key, {})[int(message_id)] = event.message
        existing_task = cls._album_flush_tasks.get(key)
        if existing_task and not existing_task.done():
            existing_task.cancel()
        cls._album_flush_tasks[key] = asyncio.create_task(cls._flush_grouped_album_after_delay(key, account, source_chat))

    @classmethod
    async def _flush_grouped_album_after_delay(cls, key: tuple[int, int, int], account: TgAccount, source_chat: Any) -> None:
        try:
            await asyncio.sleep(cls._album_flush_delay)
            await cls._flush_grouped_album(key, account, source_chat)
        except asyncio.CancelledError:
            raise

    @classmethod
    async def _flush_grouped_album(cls, key: tuple[int, int, int], account: TgAccount, source_chat: Any) -> None:
        pending_task = cls._album_flush_tasks.pop(key, None)
        if pending_task and pending_task is not asyncio.current_task() and not pending_task.done():
            pending_task.cancel()
        buffered_messages = cls._album_buffers.pop(key, {})
        messages = sorted(buffered_messages.values(), key=lambda message: getattr(message, 'id', 0) or 0)
        if not messages:
            return

        from config.database import AsyncSessionLocal  # noqa: PLC0415
        from module_telegram.service.telegram_service import TelegramMessageIngestService  # noqa: PLC0415

        async with AsyncSessionLocal() as handler_db:
            try:
                album_event = SimpleNamespace(
                    messages=messages,
                    raw_text=next((getattr(message, 'raw_text', None) for message in messages if getattr(message, 'raw_text', None)), None),
                    text=next((getattr(message, 'text', None) for message in messages if getattr(message, 'text', None)), None),
                )
                await TelegramMessageIngestService.ingest_album(handler_db, account, source_chat, album_event)
                await handler_db.commit()
            except Exception as exc:
                await handler_db.rollback()
                logger.exception(
                    f'Telegram聚合相册消息处理失败: account_id={account.account_id}, chat_id={source_chat.chat_id}, '
                    f'grouped_id={key[2]}, error={exc}'
                )

    @classmethod
    async def _cancel_album_flush_tasks(cls, account_id: int | None = None) -> None:
        tasks = []
        for key, task in list(cls._album_flush_tasks.items()):
            if account_id is not None and key[0] != account_id:
                continue
            cls._album_flush_tasks.pop(key, None)
            cls._album_buffers.pop(key, None)
            if not task.done():
                task.cancel()
                tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    @classmethod
    async def reload_listener(cls, db: AsyncSession, account: TgAccount) -> None:
        await cls.disconnect(account.account_id)
        await cls.start_listener(db, account)

    @classmethod
    async def start_listener(cls, db: AsyncSession, account: TgAccount) -> None:
        if events is None:
            cls._ensure_dependency()
        if cls._handlers.get(account.account_id):
            logger.info(f'Telegram监听已存在: account_id={account.account_id}')
            return
        client = await cls.get_authorized_client(account)
        rules = await TelegramDao.get_enabled_rules_for_account(db, account.account_id)
        source_pks = sorted({source_pk for rule in rules for source_pk in ListenerRulePolicy.source_chat_pks(rule)})
        source_chats = await TelegramDao.get_chats_by_pks(db, source_pks)
        cls._handlers.setdefault(account.account_id, [])

        for source_chat in source_chats:
            if source_chat.can_listen != 'Y' or source_chat.status != '0':
                continue

            async def handler(event: Any, chat: Any = source_chat) -> None:
                if getattr(event.message, 'grouped_id', None):
                    cls._queue_grouped_message(account, chat, event)
                    return
                from config.database import AsyncSessionLocal  # noqa: PLC0415
                from module_telegram.service.telegram_service import TelegramMessageIngestService  # noqa: PLC0415

                async with AsyncSessionLocal() as handler_db:
                    try:
                        await TelegramMessageIngestService.ingest_event(handler_db, account, chat, event)
                        await handler_db.commit()
                    except Exception as exc:
                        await handler_db.rollback()
                        logger.exception(f'Telegram消息处理失败: account_id={account.account_id}, chat_id={chat.chat_id}, error={exc}')

            builder = events.NewMessage(chats=cls._normalize_chat_ref(source_chat.chat_id))
            client.add_event_handler(handler, builder)
            cls._handlers[account.account_id].append((handler, builder))
            logger.info(f'Telegram监听已启动: account_id={account.account_id}, chat_id={source_chat.chat_id}')

    @classmethod
    async def restore_enabled_listeners(cls, db: AsyncSession) -> None:
        accounts = await TelegramDao.get_enabled_accounts(db)
        for account in accounts:
            try:
                await cls.start_listener(db, account)
            except Exception as exc:  # noqa: PERF203
                logger.exception(f'Telegram监听恢复失败: account_id={account.account_id}, error={exc}')

    @classmethod
    async def stop_all(cls) -> None:
        await cls._cancel_album_flush_tasks()
        await asyncio.gather(*(client.disconnect() for client in cls._clients.values()), return_exceptions=True)
        cls._clients.clear()
        cls._handlers.clear()
