import asyncio
import json
import shutil
import time
import uuid
from contextlib import AsyncExitStack, suppress
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from sqlalchemy import and_, or_, select, update

from config.database import AsyncSessionLocal
from config.env import ArchiveConfig
from module_telegram.dao.telegram_dao import TelegramDao
from module_telegram.entity.do.archive_do import TgArchiveItem, TgArchiveTask
from module_telegram.entity.do.telegram_do import TgAccount, TgChat, TgForwardRecord, TgMessage, TgMessageMedia
from module_telegram.service.archive_service import file_sha256, manifest_files, media_filename, private_path
from module_telegram.service.archive_storage import ArchiveStorageError, child_path, get_storage
from module_telegram.service.media_runtime import media_runtime, run_blocking
from module_telegram.service.telegram_client_service import TelegramClientManager
from module_telegram.service.telegram_rule_service import ListenerRulePolicy, TelegramStorageService
from module_telegram.service.telegram_service import TelegramForwardService
from utils.log_util import logger


class DiskSpaceError(Exception):
    pass


PROGRESS_INTERVAL_SECONDS = 2
CATCHUP_PAGE_SIZE = 200
ALBUM_SETTLE_SECONDS = 10


def row_values(row: Any) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


class TelegramArchiveWorker:
    """Persistent delivery, collection and archive lanes sharing media ownership."""

    def __init__(self) -> None:
        self.tasks: list[asyncio.Task] = []

    def start(self) -> None:
        if not self.tasks:
            self.tasks = [asyncio.create_task(self._forward_loop())]
            if ArchiveConfig.archive_enabled:
                Path(ArchiveConfig.archive_media_dir).mkdir(parents=True, exist_ok=True)
                self.tasks.extend(asyncio.create_task(self._loop(kind)) for kind in ('download', 'archive'))
                self.tasks.append(asyncio.create_task(self._catchup_loop()))

    async def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()

    @staticmethod
    def _model(kind: str) -> tuple[type, str, str]:
        if kind == 'forward':
            return TgForwardRecord, 'record_id', 'status'
        return (
            (TgMessageMedia, 'media_id', 'download_status')
            if kind == 'download'
            else (TgArchiveTask, 'task_id', 'status')
        )

    async def claim(self, kind: str) -> dict[str, Any] | None:
        model, pk, state_name = self._model(kind)
        state = getattr(model, state_name)
        now = datetime.now()
        ready = or_(
            and_(state.in_(['queued', 'retry']), or_(model.next_retry_at.is_(None), model.next_retry_at <= now)),
            and_(state == ('downloading' if kind == 'download' else 'uploading'), model.lease_until < now),
        )
        async with AsyncSessionLocal() as db:
            candidate = (
                await db.execute(
                    select(getattr(model, pk), model.message_id)
                    .join(TgMessage, TgMessage.message_id == model.message_id)
                    .where(
                        ready,
                        getattr(model, pk).not_in([job_id for (active_kind, job_id) in media_runtime.active if active_kind == kind]),
                        model.message_id.not_in(
                            select(TgMessageMedia.message_id).where(TgMessageMedia.download_status == 'deleting')
                        ),
                    )
                    .order_by(getattr(model, pk))
                    .limit(1)
                    .with_for_update(of=TgMessage, skip_locked=True)
                )
            ).first()
            if candidate is None:
                return None
            candidate, message_id = candidate
            if await db.scalar(
                select(TgMessageMedia.media_id)
                .where(TgMessageMedia.message_id == message_id, TgMessageMedia.download_status == 'deleting')
                .with_for_update()
                .limit(1)
            ):
                return None
            token = uuid.uuid4().hex
            result = await db.execute(
                update(model)
                .where(getattr(model, pk) == candidate, ready)
                .values(
                    **{
                        state_name: 'downloading' if kind == 'download' else 'uploading',
                        'lease_until': now + timedelta(seconds=ArchiveConfig.archive_lease_seconds),
                        'lease_token': token,
                        'attempts': model.attempts + 1,
                        'last_error': None,
                    }
                )
            )
            if result.rowcount != 1:
                await db.rollback()
                return None
            row = await db.get(model, candidate)
            values = row_values(row)
            await db.commit()
            return values

    async def _update(self, kind: str, job: dict[str, Any], **values: Any) -> bool:
        model, pk, _ = self._model(kind)
        conditions = [getattr(model, pk) == job[pk], model.lease_token == job['lease_token']]
        if kind == 'forward':
            conditions.extend([model.status == 'sending', model.lease_until > datetime.now()])
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                update(model)
                .where(*conditions)
                .values(**values)
            )
            await db.commit()
            return result.rowcount == 1

    async def _heartbeat(self, kind: str, job: dict[str, Any], work: asyncio.Task) -> None:
        try:
            while True:
                await asyncio.sleep(max(1, ArchiveConfig.archive_lease_seconds // 3))
                if not await self._update(
                    kind, job, lease_until=datetime.now() + timedelta(seconds=ArchiveConfig.archive_lease_seconds)
                ):
                    work.cancel()
                    return
        except Exception:
            # Stop using a lease that could not be renewed; another worker may recover it.
            work.cancel()

    async def _run_with_heartbeat(self, kind: str, job: dict[str, Any], work: asyncio.Task) -> None:
        heartbeat = asyncio.create_task(self._heartbeat(kind, job, work))
        try:
            await work
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    async def _loop(self, kind: str) -> None:
        while True:
            try:
                job = await self.claim(kind)
                if not job:
                    await asyncio.sleep(ArchiveConfig.archive_poll_seconds)
                    continue
                work = asyncio.create_task(self.download(job) if kind == 'download' else self.archive(job))
                try:
                    await self._run_with_heartbeat(kind, job, work)
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise
                except Exception as exc:
                    await self._failed(kind, job, exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error('归档后台任务暂时不可用: kind={}, error_type={}', kind, type(exc).__name__)
                await asyncio.sleep(10)

    async def _failed(self, kind: str, job: dict[str, Any], exc: Exception) -> None:
        _, _, state_name = self._model(kind)
        delay = min(3600, 15 * 2 ** min(job['attempts'], 8))
        attempts = job['attempts']
        if isinstance(exc, DiskSpaceError):
            message, delay, attempts = '磁盘空间不足，释放空间后将自动继续', 60, max(0, attempts - 1)
        elif type(exc).__name__ == 'FloodWaitError':
            message, delay, attempts = (
                'Telegram 请求限流，等待后自动重试',
                max(1, int(getattr(exc, 'seconds', 60))),
                max(0, attempts - 1),
            )
        elif isinstance(exc, ArchiveStorageError):
            message = str(exc)
        else:
            # Third-party exceptions can include signed URLs or credential values.
            message = f'处理失败（{type(exc).__name__}），请检查网络、登录状态和存储配置'
        await self._update(
            kind,
            job,
            **{
                state_name: 'failed' if attempts >= ArchiveConfig.archive_max_attempts else 'retry',
                'attempts': attempts,
                'last_error': message[:500],
                'next_retry_at': datetime.now() + timedelta(seconds=delay),
                'lease_token': None,
                'lease_until': None,
            },
        )

    async def download(self, job: dict[str, Any]) -> None:  # noqa: PLR0912, PLR0915
        with media_runtime.operation('download', job['media_id'], job['message_id']):
            async with AsyncSessionLocal() as db:
                message = await db.get(TgMessage, job['message_id'])
                account = await db.get(TgAccount, message.account_id) if message else None
                if not message or not account or account.status != '0':
                    raise ArchiveStorageError('来源消息或启用的 Telegram 账号不存在')
                if message.is_sensitive == 'Y':
                    await self._update('download', job, download_status='blocked', lease_token=None, lease_until=None)
                    return
                source_chat_id = message.source_chat_id
                account_data = SimpleNamespace(**row_values(account))
            client = await TelegramClientManager.get_authorized_client(account_data)
            source = await client.get_messages(
                TelegramClientManager._normalize_chat_ref(source_chat_id), ids=job['source_telegram_message_id']
            )
            if not source or not getattr(source, 'media', None):
                raise ArchiveStorageError('源消息媒体已删除或当前账号无权访问')
            media_id = getattr(getattr(source, 'photo', None) or getattr(source, 'document', None), 'id', None)
            if job.get('source_media_id') and media_id != job['source_media_id']:
                raise ArchiveStorageError('源消息的素材已被替换，无法恢复原始素材')
            filename = media_filename(job['source_telegram_message_id'], job['mime_type'])
            storage = TelegramStorageService(ArchiveConfig.archive_media_dir)
            path = storage.build_media_path(account_data.account_id, job['message_id'], filename)
            partial = path.absolute_path.with_name(path.absolute_path.name + '.part')
            expected = getattr(getattr(source, 'file', None), 'size', None) or job['file_size']
            async with AsyncExitStack() as stack:
                async with AsyncSessionLocal() as db:
                    await db.scalar(
                        select(TgMessage.message_id).where(TgMessage.message_id == job['message_id']).with_for_update()
                    )
                    active = await db.scalar(
                        select(TgMessageMedia.media_id)
                        .where(
                            TgMessageMedia.media_id == job['media_id'],
                            TgMessageMedia.lease_token == job['lease_token'],
                            TgMessageMedia.download_status == 'downloading',
                        )
                        .with_for_update()
                    )
                    if active is None:
                        raise asyncio.CancelledError
                    path.absolute_path.parent.mkdir(parents=True, exist_ok=True)
                    written = partial.stat().st_size if partial.exists() else 0
                    if expected is not None and written > expected:
                        partial.unlink()
                        written = 0
                    free = shutil.disk_usage(storage.base_dir).free
                    if free < ArchiveConfig.archive_min_free_bytes + max(0, (expected or 0) - written):
                        raise DiskSpaceError
                    output = await run_blocking(partial.open, 'ab')
                    stack.push_async_callback(run_blocking, output.close)
                    await db.commit()
                last_progress = time.monotonic()
                stream = client.iter_download(source.media, offset=written)
                try:
                    async for chunk in stream:
                        await run_blocking(output.write, chunk)
                        written += len(chunk)
                        if time.monotonic() - last_progress >= PROGRESS_INTERVAL_SECONDS:
                            await run_blocking(output.flush)
                            if shutil.disk_usage(storage.base_dir).free < ArchiveConfig.archive_min_free_bytes:
                                raise DiskSpaceError
                            if not await self._update('download', job, downloaded_bytes=written):
                                raise asyncio.CancelledError
                            last_progress = time.monotonic()
                finally:
                    await stream.close()
            if expected is not None and written != expected:
                raise ArchiveStorageError('文件未完整下载，等待重试')
            checksum = await run_blocking(file_sha256, partial)
            async with AsyncSessionLocal() as db:
                # Cleanup and late sensitive captions share this lock. A revoked
                # downloader must not recreate files after cleanup has finished.
                await db.scalar(
                    select(TgMessage.message_id).where(TgMessage.message_id == job['message_id']).with_for_update()
                )
                active = await db.scalar(
                    select(TgMessageMedia.media_id)
                    .where(
                        TgMessageMedia.media_id == job['media_id'],
                        TgMessageMedia.lease_token == job['lease_token'],
                        TgMessageMedia.download_status == 'downloading',
                    )
                    .with_for_update()
                )
                if active is None:
                    raise asyncio.CancelledError
                partial.replace(path.absolute_path)
                await db.execute(
                    update(TgMessageMedia)
                    .where(TgMessageMedia.media_id == active)
                    .values(
                        download_status='ready',
                        local_path=path.relative_path,
                        downloaded_bytes=written,
                        file_size=written,
                        sha256=checksum,
                        next_retry_at=None,
                        lease_token=None,
                        lease_until=None,
                        last_error=None,
                    )
                )
                await db.commit()

    async def archive(self, job: dict[str, Any]) -> None:
        with media_runtime.operation('archive', job['task_id'], job['message_id']):
            async with AsyncSessionLocal() as db:
                message = await db.get(TgMessage, job['message_id'])
                if not message or message.is_sensitive == 'Y':
                    raise ArchiveStorageError('来源消息不存在或已被敏感词拦截')
            storage = await run_blocking(get_storage, job['storage_id'])
            destination = child_path(job['target_path'], job['package_name'])
            await run_blocking(storage.ensure_dir, destination)
            manifest = json.loads(job['manifest_json'])
            workdir = private_path(f'.tasks/{job["task_id"]}')
            workdir.mkdir(parents=True, exist_ok=True)
            for filename, content in manifest_files(manifest).items():
                (workdir / filename).write_bytes(content)
            async with AsyncSessionLocal() as db:
                items = [
                    row_values(item)
                    for item in (
                        await db.execute(
                            select(TgArchiveItem)
                            .where(TgArchiveItem.task_id == job['task_id'], TgArchiveItem.status != 'completed')
                            .order_by(TgArchiveItem.item_id)
                        )
                    ).scalars()
                ]
            for item in items:
                source = workdir / item['file_name']
                if item['media_id']:
                    async with AsyncSessionLocal() as db:
                        media = await db.get(TgMessageMedia, item['media_id'])
                        relative_path = media.local_path if media and media.download_status == 'ready' else ''
                    if not relative_path:
                        raise ArchiveStorageError('待上传的本地素材不可用')
                    source = private_path(relative_path)
                if (
                    not source.is_file()
                    or source.stat().st_size != item['file_size']
                    or await run_blocking(file_sha256, source) != item['sha256']
                ):
                    raise ArchiveStorageError('本地文件已缺失或发生变化，归档已暂停')
                try:
                    remote = await run_blocking(storage.upload, source, destination, item['file_name'], item['sha256'])
                except Exception as exc:
                    async with AsyncSessionLocal() as db:
                        await db.execute(
                            update(TgArchiveItem)
                            .where(TgArchiveItem.item_id == item['item_id'])
                            .values(
                                status='failed',
                                last_error=str(exc)[:400] if isinstance(exc, ArchiveStorageError) else type(exc).__name__,
                            )
                        )
                        await db.commit()
                    raise
                async with AsyncSessionLocal() as db:
                    # Fence completion against an expired/stolen parent lease.
                    task = await db.scalar(
                        select(TgArchiveTask.task_id)
                        .where(TgArchiveTask.task_id == job['task_id'], TgArchiveTask.lease_token == job['lease_token'])
                        .with_for_update()
                    )
                    if task is None:
                        raise asyncio.CancelledError
                    await db.execute(
                        update(TgArchiveItem)
                        .where(TgArchiveItem.item_id == item['item_id'])
                        .values(status='completed', remote_id=remote, last_error=None)
                    )
                    await db.commit()
            await self._update(
                'archive',
                job,
                status='completed',
                completed_at=datetime.now(),
                lease_token=None,
                lease_until=None,
                next_retry_at=None,
                last_error=None,
            )

    async def _catchup_loop(self) -> None:
        while True:
            try:
                await self.catch_up()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning('采集补偿扫描暂时失败: error_type={}', type(exc).__name__)
            await asyncio.sleep(60)

    async def _forward_loop(self) -> None:
        while True:
            try:
                await self.deliver_pending()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning('转发补偿扫描暂时失败: error_type={}', type(exc).__name__)
            await asyncio.sleep(ArchiveConfig.archive_poll_seconds)

    async def deliver_pending(self) -> None:
        ready = or_(
            TgForwardRecord.status.in_(['queued', 'waiting_download']),
            and_(TgForwardRecord.status == 'sending', TgForwardRecord.lease_until <= datetime.now()),
        )
        async with AsyncSessionLocal() as db:
            pending = (await db.execute(
                select(TgForwardRecord.record_id, TgForwardRecord.message_id, TgForwardRecord.account_id, TgForwardRecord.forward_type)
                .where(ready)
                .order_by(TgForwardRecord.record_id)
            )).all()
        for record_id, message_id, account_id, forward_type in pending:
            if ('forward', record_id) in media_runtime.active:
                continue
            if forward_type == 'auto' and not TelegramClientManager._handlers.get(account_id):
                continue
            job = None
            try:
                async with AsyncSessionLocal() as db:
                    # Claim in a short transaction, in the same lock order as cleanup.
                    message = await db.scalar(select(TgMessage).where(TgMessage.message_id == message_id).with_for_update(skip_locked=True))
                    if message is None:
                        continue
                    record = await db.scalar(select(TgForwardRecord).where(TgForwardRecord.record_id == record_id).with_for_update())
                    if not record or not (
                        record.status in {'queued', 'waiting_download'}
                        or (record.status == 'sending' and record.lease_until and record.lease_until <= datetime.now())
                    ):
                        continue
                    payload = await TelegramForwardService.prepare(db, record, message)
                    if payload:
                        resume_status = 'waiting_download' if record.delivery_stage == 'local' else 'queued'
                        record.status = 'sending'
                        record.lease_token = uuid.uuid4().hex
                        record.lease_until = datetime.now() + timedelta(seconds=ArchiveConfig.archive_lease_seconds)
                        job = row_values(record)
                        job['resume_status'] = resume_status
                    await db.commit()
                if job:
                    await self._run_with_heartbeat(
                        'forward', job, asyncio.create_task(self._deliver(job, payload)),
                    )
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
            except Exception as exc:
                if job:
                    await self._update(
                        'forward', job, status=job['resume_status'], lease_token=None, lease_until=None,
                    )
                logger.warning('转发补偿暂时失败: record_id={}, error_type={}', record_id, type(exc).__name__)

    async def _deliver(self, job: dict[str, Any], payload: dict[str, Any]) -> None:
        with media_runtime.operation('forward', job['record_id'], job['message_id']):
            result, paths = await TelegramForwardService.execute(payload)
            await self._complete_delivery(job, result, paths)

    async def _complete_delivery(
        self, job: dict[str, Any], result: Any, paths: dict[int, str],
    ) -> None:
        async with AsyncSessionLocal() as db:
            message = await db.scalar(
                select(TgMessage).where(TgMessage.message_id == job['message_id']).with_for_update()
            )
            record = await db.scalar(
                select(TgForwardRecord).where(TgForwardRecord.record_id == job['record_id']).with_for_update()
            )
            if (
                not message or not record or record.status != 'sending'
                or record.lease_token != job['lease_token']
                or not record.lease_until or record.lease_until <= datetime.now()
            ):
                return
            for media_id, path in paths.items():
                await TelegramDao.update_media_local_path(db, media_id, path)
            record.status = result.status
            if result.status == 'waiting_download':
                record.delivery_stage = 'local'
            record.sent_telegram_message_id = result.sent_telegram_message_id
            record.error_message = result.error_message
            record.lease_token = record.lease_until = None
            if record.forward_type == 'auto':
                await TelegramForwardService.refresh_status(db, message)
            await db.commit()

    async def catch_up(self) -> None:
        async with AsyncSessionLocal() as db:
            accounts = [
                SimpleNamespace(**row_values(account))
                for account in await TelegramDao.get_enabled_accounts(db)
                if account.session_status == 'authorized'
            ]
        for account in accounts:
            if not TelegramClientManager._handlers.get(account.account_id):
                # Respect the existing account-level stop-listening action.
                continue
            try:
                async with AsyncSessionLocal() as db:
                    rules = await TelegramDao.get_enabled_rules_for_account(db, account.account_id)
                    source_pks = {
                        pk
                        for rule in rules
                        if rule.download_enabled == 'Y'
                        for pk in ListenerRulePolicy.source_chat_pks(rule)
                    }
                    chats = [
                        SimpleNamespace(**row_values(chat))
                        for chat in await TelegramDao.get_chats_by_pks(db, list(source_pks))
                        if chat.status == '0' and chat.can_listen == 'Y'
                    ]
                client = await TelegramClientManager.get_authorized_client(account)
            except Exception as exc:
                logger.warning('采集账号补偿暂时失败: account_id={}, error_type={}', account.account_id, type(exc).__name__)
                continue
            for chat in chats:
                try:
                    await self._catch_up_chat(account, chat, client)
                except Exception as exc:  # noqa: PERF203 - isolate failures by source, not by scan
                    logger.warning(
                        '采集群补偿暂时失败: account_id={}, chat_pk={}, error_type={}',
                        account.account_id, chat.chat_pk, type(exc).__name__,
                    )

    async def _catch_up_chat(self, account: Any, chat: Any, client: Any) -> None:
        entity = TelegramClientManager._normalize_chat_ref(chat.chat_id)
        if chat.collection_cursor is None:
            latest = await client.get_messages(entity, limit=1)
            cursor = latest[0].id if latest else 0
        else:
            messages = [
                message
                async for message in client.iter_messages(
                    entity, min_id=chat.collection_cursor, reverse=True, limit=CATCHUP_PAGE_SIZE + 1
                )
            ]
            # Do not archive an album still arriving, or split one at a page boundary.
            messages = [
                message
                for message in messages
                if not getattr(message, 'date', None)
                or message.date.timestamp() <= time.time() - ALBUM_SETTLE_SECONDS
            ]
            if len(messages) > CATCHUP_PAGE_SIZE:
                overflow_group = getattr(messages[-1], 'grouped_id', None)
                messages = messages[:-1]
                if overflow_group:
                    messages = [message for message in messages if getattr(message, 'grouped_id', None) != overflow_group]
            groups: dict[tuple[str, int], list[Any]] = {}
            for message in messages:
                grouped_id = getattr(message, 'grouped_id', None)
                key = ('album', grouped_id) if grouped_id else ('message', message.id)
                groups.setdefault(key, []).append(message)
            for group in groups.values():
                await TelegramClientManager.persist_messages(account, chat, group)
            cursor = max((message.id for message in messages), default=chat.collection_cursor)
        async with AsyncSessionLocal() as db:
            await db.execute(update(TgChat).where(TgChat.chat_pk == chat.chat_pk).values(collection_cursor=cursor))
            await db.commit()
