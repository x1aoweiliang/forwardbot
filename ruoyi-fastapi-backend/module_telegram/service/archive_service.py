import hashlib
import json
import mimetypes
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jwt
from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config.env import ArchiveConfig, JwtConfig
from exceptions.exception import ServiceException
from module_telegram.entity.do.archive_do import TgArchiveItem, TgArchiveMessage, TgArchiveTask
from module_telegram.entity.do.telegram_do import TgForwardRecord, TgMessage, TgMessageMedia
from module_telegram.entity.vo.archive_vo import ArchiveLibraryQuery, ArchiveSubmitModel
from module_telegram.service.archive_storage import load_storages, normalize_path
from module_telegram.service.media_runtime import media_runtime
from utils.common_util import CamelCaseUtil


def private_path(relative_path: str) -> Path:
    root = Path(ArchiveConfig.archive_media_dir).resolve()
    path = (root / relative_path).resolve()
    if path == root or not path.is_relative_to(root):
        raise ServiceException(message='素材文件路径不合法')
    return path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def media_filename(
    source_message_id: int, mime_type: str | None, text: str | None = None, sequence: int | None = None,
) -> str:
    suffix = mimetypes.guess_extension(mime_type or '') or '.bin'
    suffix = '.jpg' if suffix == '.jpe' else suffix
    stem = ' '.join((text or '').split())
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', '_', stem).strip(' .')
    if stem.lower().endswith(suffix):
        stem = stem[:-len(suffix)].strip(' .')
    # Leave space for the extension, album index and archive collision suffix.
    stem = stem.encode('utf-8')[:140].decode('utf-8', errors='ignore').rstrip(' .') or str(source_message_id)
    return f'{stem}{f"_{sequence:02d}" if sequence is not None else ""}{suffix}'


def message_media_filenames(message: TgMessage, medias: list[TgMessageMedia]) -> dict[int, str]:
    files = sorted(
        (media for media in medias if media.storage_kind == 'archive'),
        key=lambda media: media.source_telegram_message_id,
    )
    return {
        media.media_id: media_filename(
            media.source_telegram_message_id, media.mime_type,
            (media.caption or '').strip() or message.message_text,
            index if len(files) > 1 else None,
        )
        for index, media in enumerate(files, 1)
    }


def cleanup_paths(account_id: int, message_id: int, media: TgMessageMedia) -> list[Path]:
    # Delete exact files only: late album arrivals can create other files here.
    root = Path(ArchiveConfig.archive_media_dir).resolve()
    directory = root / f'tg/{account_id}/{message_id}'
    path = directory / media_filename(media.source_telegram_message_id, media.mime_type)
    paths = {path, path.with_name(path.name + '.part')}
    if media.local_path:
        stored = root / media.local_path
        if stored.parent != directory:
            raise ServiceException(message='素材文件不属于该消息，已停止清理')
        paths.update([stored, stored.with_name(stored.name + '.part')])
    if any(path.resolve() != path for path in paths):
        raise ServiceException(message='素材路径含符号链接，已停止清理')
    return sorted(paths)


class TelegramArchiveService:
    @staticmethod
    def _archived() -> Any:
        return exists().where(
            TgArchiveMessage.message_id == TgMessage.message_id,
            TgArchiveMessage.media_revision == TgMessage.media_revision,
            TgArchiveTask.task_id == TgArchiveMessage.task_id,
            TgArchiveTask.status == 'completed',
        )

    @classmethod
    async def list_library(cls, db: AsyncSession, query: ArchiveLibraryQuery) -> dict[str, Any]:
        filters = [TgMessage.collected == 'Y']
        if query.account_id:
            filters.append(TgMessage.account_id == query.account_id)
        if query.source_chat_pk:
            filters.append(TgMessage.source_chat_pk == query.source_chat_pk)
        if query.keyword:
            filters.append(TgMessage.message_text.contains(query.keyword, autoescape=True))
        pending = exists().where(
            TgMessageMedia.message_id == TgMessage.message_id,
            TgMessageMedia.download_status.in_(['queued', 'downloading', 'retry']),
        )
        failed = exists().where(
            TgMessageMedia.message_id == TgMessage.message_id, TgMessageMedia.download_status == 'failed'
        )
        unavailable = exists().where(
            TgMessageMedia.message_id == TgMessage.message_id,
            TgMessageMedia.download_status.not_in(['ready', 'skipped']),
        )
        if query.state == 'archived':
            filters.append(cls._archived())
        elif query.state == 'unarchived':
            filters.append(~cls._archived())
        elif query.state == 'ready':
            filters.extend([~unavailable, TgMessage.is_sensitive == 'N'])
        elif query.state == 'pending':
            filters.append(pending)
        elif query.state == 'failed':
            filters.append(failed)
        elif query.state == 'blocked':
            filters.append(TgMessage.is_sensitive == 'Y')
        elif query.state in {'deleted', 'deleting'}:
            filters.append(
                exists().where(
                    TgMessageMedia.message_id == TgMessage.message_id,
                    TgMessageMedia.download_status == query.state,
                )
            )
        total = await db.scalar(select(func.count()).select_from(TgMessage).where(*filters))
        messages = (
            await db.execute(
                select(TgMessage, cls._archived().label('archived'))
                .where(*filters)
                .order_by(TgMessage.message_id.desc())
                .offset((query.page_num - 1) * query.page_size)
                .limit(query.page_size)
            )
        ).all()
        ids = [message.message_id for message, _ in messages]
        medias = (
            (await db.execute(select(TgMessageMedia).where(TgMessageMedia.message_id.in_(ids)))).scalars().all()
            if ids
            else []
        )
        grouped: dict[int, list[Any]] = {}
        for media in medias:
            grouped.setdefault(media.message_id, []).append(media)
        rows = []
        for message, archived in messages:
            files = grouped.get(message.message_id, [])
            states = {file.download_status for file in files}
            state = (
                'blocked'
                if message.is_sensitive == 'Y'
                else 'deleting'
                if 'deleting' in states
                else 'deleted'
                if 'deleted' in states
                else 'failed'
                if 'failed' in states
                else 'pending'
                if states & {'queued', 'downloading', 'retry'}
                else 'ready'
            )
            row = CamelCaseUtil.transform_result(message)
            row.update(
                downloadState=state,
                archived=bool(archived),
                mediaCount=len(files),
                readyCount=sum(file.download_status == 'ready' for file in files),
                totalBytes=sum(file.file_size or 0 for file in files),
                downloadedBytes=sum(file.downloaded_bytes or 0 for file in files),
            )
            rows.append(row)
        return {'rows': rows, 'total': total, 'pageNum': query.page_num, 'pageSize': query.page_size}

    @staticmethod
    async def detail(db: AsyncSession, message_id: int) -> dict[str, Any]:
        message = await db.get(TgMessage, message_id)
        if not message or message.collected != 'Y':
            raise ServiceException(message='采集记录不存在')
        medias = (
            (
                await db.execute(
                    select(TgMessageMedia)
                    .where(TgMessageMedia.message_id == message_id)
                    .order_by(TgMessageMedia.source_telegram_message_id)
                )
            )
            .scalars()
            .all()
        )
        tasks = (
            (
                await db.execute(
                    select(TgArchiveTask)
                    .join(TgArchiveMessage, TgArchiveMessage.task_id == TgArchiveTask.task_id)
                    .where(TgArchiveMessage.message_id == message_id)
                    .order_by(TgArchiveTask.task_id.desc())
                )
            )
            .scalars()
            .all()
        )
        task_ids = [task.task_id for task in tasks]
        revisions = dict((await db.execute(
            select(TgArchiveMessage.task_id, TgArchiveMessage.media_revision)
            .where(TgArchiveMessage.message_id == message_id)
        )).all())
        items = (
            (await db.execute(select(TgArchiveItem).where(TgArchiveItem.task_id.in_(task_ids)))).scalars().all()
            if tasks
            else []
        )
        data = CamelCaseUtil.transform_result(message)
        filenames = message_media_filenames(message, medias)
        data['medias'] = [
            {
                'mediaId': media.media_id,
                'fileName': filenames.get(media.media_id) or media.file_name or f'素材 {index + 1}',
                'mimeType': media.mime_type,
                'fileSize': media.file_size,
                'downloadStatus': media.download_status,
                'downloadedBytes': media.downloaded_bytes,
                'lastError': media.last_error,
                'caption': media.caption,
            }
            for index, media in enumerate(medias)
        ]
        data['archives'] = [
            {
                'taskId': task.task_id,
                'storageId': task.storage_id,
                'targetPath': task.target_path,
                'packageName': task.package_name,
                'archiveFormat': task.archive_format,
                'status': task.status,
                'lastError': task.last_error,
                'mediaRevision': revisions[task.task_id],
                'createTime': task.create_time,
                'completedAt': task.completed_at,
                'files': [
                    {
                        'name': item.file_name,
                        'status': task.status if task.archive_format == 'zip' else item.status,
                        'error': task.last_error if task.archive_format == 'zip' else item.last_error,
                    }
                    for item in items
                    if item.task_id == task.task_id
                    and (task.archive_format != 'zip' or item.media_id is None)
                ],
                'mediaCount': sum(item.task_id == task.task_id and item.media_id is not None for item in items),
            }
            for task in tasks
        ]
        return data

    @staticmethod
    async def retry_download(db: AsyncSession, message_id: int) -> int:
        message = await db.scalar(select(TgMessage).where(TgMessage.message_id == message_id).with_for_update())
        if not message or message.collected != 'Y' or message.is_sensitive == 'Y':
            raise ServiceException(message='该消息不可重试下载')
        if await db.scalar(
            select(TgMessageMedia.media_id)
            .where(TgMessageMedia.message_id == message_id, TgMessageMedia.download_status == 'deleting')
            .with_for_update()
            .limit(1)
        ):
            raise ServiceException(message='请先完成本地清理，再重新下载')
        result = await db.execute(
            update(TgMessageMedia)
            .where(
                TgMessageMedia.message_id == message_id,
                TgMessageMedia.download_status.in_(['failed', 'retry', 'deleted']),
            )
            .values(download_status='queued', attempts=0, next_retry_at=None, last_error=None)
        )
        count = result.rowcount
        await db.commit()
        return count

    @staticmethod
    async def cleanup_local(db: AsyncSession, message_ids: list[int]) -> dict[str, Any]:  # noqa: PLR0912
        ids = sorted(set(message_ids))
        with media_runtime.cleanup(ids):
            selected = []
            for message_id in ids:
                message = await db.scalar(select(TgMessage).where(TgMessage.message_id == message_id).with_for_update())
                if not message or message.collected != 'Y':
                    raise ServiceException(message=f'采集记录 {message_id} 不存在')
                active_forward = await db.scalar(
                    select(TgForwardRecord.record_id).where(
                        TgForwardRecord.message_id == message_id,
                        TgForwardRecord.status == 'sending',
                        TgForwardRecord.lease_until > datetime.now(),
                    ).limit(1)
                )
                if active_forward:
                    raise ServiceException(message=f'消息 {message_id} 正在补发，暂不能清理本地素材')
                medias = (
                    await db.scalars(
                        select(TgMessageMedia)
                        .where(TgMessageMedia.message_id == message_id)
                        .with_for_update()
                        .execution_options(populate_existing=True)
                    )
                ).all()
                active_task = await db.scalar(
                    select(TgArchiveTask.task_id)
                    .join(TgArchiveMessage, TgArchiveMessage.task_id == TgArchiveTask.task_id)
                    .where(
                        TgArchiveMessage.message_id == message_id,
                        TgArchiveTask.status.in_(['queued', 'uploading', 'retry']),
                    )
                    .with_for_update(of=TgArchiveTask)
                    .limit(1)
                )
                if active_task or any(media.download_status == 'downloading' for media in medias):
                    raise ServiceException(message=f'消息 {message_id} 正在下载或等待归档完成，暂不能清理')
                for media in medias:
                    if media.storage_kind != 'archive' or media.download_status in {'skipped', 'disabled', 'deleted'}:
                        continue
                    paths = cleanup_paths(message.account_id, message_id, media)
                    selected.append((message_id, media.media_id, paths))
                    media.download_status = 'deleting'
                    media.lease_token = media.lease_until = media.next_retry_at = None
                    media.last_error = None
            # Persist intent before touching disk. A crash leaves retryable cleanup,
            # never a ready record pointing at a file that was already removed.
            await db.commit()
            freed, cleaned, errors = 0, set(), []
            for message_id in ids:
                await db.scalar(select(TgMessage.message_id).where(TgMessage.message_id == message_id).with_for_update())
            for message_id, media_id, paths in selected:
                media = await db.scalar(
                    select(TgMessageMedia)
                    .where(TgMessageMedia.media_id == media_id)
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                if media.download_status != 'deleting':
                    continue
                try:
                    for path in paths:
                        if path.resolve() != path:
                            raise OSError('symbolic link')
                        try:
                            size = path.stat().st_size
                            path.unlink()
                            freed += size
                        except FileNotFoundError:
                            pass
                except OSError:
                    media.last_error = '本地文件清理失败，请检查目录权限后再次清理'
                    errors.append({'messageId': message_id, 'mediaId': media_id, 'message': media.last_error})
                    continue
                media.download_status, media.local_path = 'deleted', ''
                media.downloaded_bytes, media.attempts = 0, 0
                media.sha256 = media.last_error = None
                cleaned.add(message_id)
            result = {'cleanedMessages': len(cleaned), 'freedBytes': freed, 'errors': errors}
            await db.commit()
            return result

    @classmethod
    async def submit(cls, db: AsyncSession, request: ArchiveSubmitModel, username: str) -> list[int]:  # noqa: PLR0912
        target_path = normalize_path(request.target_path)
        if request.storage_id not in {item.id for item in load_storages()}:
            raise ServiceException(message='存储目标尚未配置')
        ids = sorted(set(request.message_ids))
        # Lock every member in the same order used by cleanup and retry.
        messages = list(await db.scalars(
            select(TgMessage).where(TgMessage.message_id.in_(ids))
            .order_by(TgMessage.message_id).with_for_update()
        ))
        if len(messages) != len(ids):
            raise ServiceException(message='部分采集记录不存在')
        messages.sort(key=lambda message: (
            message.sent_at or message.create_time or datetime.min,
            message.telegram_message_id, message.message_id,
        ))
        files, members, reserved = [], [], set()
        for message in messages:
            message_id = message.message_id
            if message.collected != 'Y' or message.is_sensitive == 'Y':
                raise ServiceException(message=f'消息 {message_id} 不可归档')
            medias = list(await db.scalars(
                select(TgMessageMedia).where(TgMessageMedia.message_id == message_id)
                .order_by(TgMessageMedia.source_telegram_message_id)
            ))
            if any(media.download_status not in {'ready', 'skipped'} for media in medias):
                raise ServiceException(message=f'消息 {message_id} 的素材尚未全部下载完成')
            selected = [media for media in medias if media.download_status == 'ready']
            if not selected:
                raise ServiceException(message=f'消息 {message_id} 没有可归档的图片或视频')
            filenames = message_media_filenames(message, medias)
            for media in selected:
                if not media.local_path or not private_path(media.local_path).is_file():
                    raise ServiceException(message=f'消息 {message_id} 的本地素材缺失')
                original = Path(filenames[media.media_id])
                name, number = original.name, 1
                while name.casefold() in reserved:
                    tail = f'_{message.telegram_message_id}_{media.media_id}'
                    if number > 1:
                        tail += f'_{number}'
                    name = f'{original.stem}{tail}{original.suffix}'
                    number += 1
                reserved.add(name.casefold())
                files.append({
                    'media_id': media.media_id, 'name': name,
                    'size': media.file_size, 'sha256': media.sha256,
                })
            members.append({'message_id': message_id, 'media_revision': message.media_revision})
        manifest_json = json.dumps(
            {'layout': 'zip-v1', 'messages': members, 'files': files}, ensure_ascii=False, sort_keys=True,
        )
        dedupe_key = hashlib.sha256(json.dumps([
            request.storage_id, target_path, request.archive_name, manifest_json,
        ]).encode()).hexdigest()
        existing = await db.scalar(select(TgArchiveTask.task_id).where(TgArchiveTask.dedupe_key == dedupe_key))
        if existing:
            await db.commit()
            return [existing]
        conflict = await db.scalar(select(TgArchiveTask.task_id).where(
            TgArchiveTask.storage_id == request.storage_id,
            TgArchiveTask.target_path == target_path,
            TgArchiveTask.package_name == request.archive_name,
            TgArchiveTask.archive_format == 'zip',
        ).limit(1))
        if conflict:
            raise ServiceException(message='该目录已提交过不同内容的同名 ZIP，请修改压缩包名称')
        task = TgArchiveTask(
            dedupe_key=dedupe_key,
            message_id=min(ids),
            media_revision=next(member['media_revision'] for member in members if member['message_id'] == min(ids)),
            storage_id=request.storage_id, target_path=target_path,
            package_name=request.archive_name, archive_format='zip',
            manifest_json=manifest_json, status='queued', create_by=username,
        )
        db.add(task)
        await db.flush()
        task_id = task.task_id
        for member in members:
            db.add(TgArchiveMessage(task_id=task_id, **member))
        for file in files:
            db.add(TgArchiveItem(
                task_id=task_id, media_id=file['media_id'], file_name=file['name'],
                file_size=file['size'], sha256=file['sha256'], status='queued',
            ))
        # Source items stay immutable; this output item records the one uploaded ZIP.
        db.add(TgArchiveItem(
            task_id=task_id, file_name=request.archive_name, file_size=0, sha256='', status='queued',
        ))
        await db.commit()
        return [task_id]

    @staticmethod
    async def retry_archive(db: AsyncSession, task_id: int) -> None:
        message_ids = select(TgArchiveMessage.message_id).where(TgArchiveMessage.task_id == task_id)
        await db.execute(select(TgMessage.message_id).where(TgMessage.message_id.in_(message_ids))
                         .order_by(TgMessage.message_id).with_for_update())
        medias = (
            await db.scalars(
                select(TgMessageMedia)
                .join(TgArchiveItem, TgArchiveItem.media_id == TgMessageMedia.media_id)
                .where(TgArchiveItem.task_id == task_id, TgArchiveItem.status != 'completed')
                .with_for_update()
            )
        ).all()
        if any(
            media.download_status != 'ready' or not media.local_path or not private_path(media.local_path).is_file()
            for media in medias
        ):
            raise ServiceException(message='本地素材尚未就绪，请先重新下载再重试归档')
        result = await db.execute(
            update(TgArchiveTask)
            .where(TgArchiveTask.task_id == task_id, TgArchiveTask.status.in_(['failed', 'retry']))
            .values(status='queued', attempts=0, next_retry_at=None, last_error=None)
        )
        if result.rowcount != 1:
            raise ServiceException(message='该归档任务当前不可重试')
        await db.commit()

    @staticmethod
    async def media_file(db: AsyncSession, media_id: int) -> tuple[Path, str]:
        media = await db.get(TgMessageMedia, media_id)
        if not media or media.storage_kind != 'archive' or media.download_status != 'ready' or not media.local_path:
            raise ServiceException(message='素材尚未准备好')
        path = private_path(media.local_path)
        if not path.is_file():
            raise ServiceException(message='本地素材不存在')
        mime = media.mime_type or 'application/octet-stream'
        if not mime.startswith(('image/', 'video/')) or mime == 'image/svg+xml':
            mime = 'application/octet-stream'
        return path, mime

    @staticmethod
    def preview_ticket(media_id: int) -> str:
        return jwt.encode(
            {
                'sub': str(media_id),
                'aud': 'media-preview',
                'iss': 'media-archive',
                'exp': datetime.now(timezone.utc) + timedelta(minutes=15),
            },
            JwtConfig.jwt_secret_key,
            algorithm=JwtConfig.jwt_algorithm,
        )

    @staticmethod
    def verify_ticket(media_id: int, ticket: str) -> None:
        try:
            payload = jwt.decode(
                ticket,
                JwtConfig.jwt_secret_key,
                algorithms=[JwtConfig.jwt_algorithm],
                audience='media-preview',
                issuer='media-archive',
                options={'require': ['exp', 'sub', 'aud', 'iss']},
            )
            if payload['sub'] != str(media_id):
                raise ValueError('different media')
        except (jwt.PyJWTError, ValueError) as exc:
            raise ServiceException(message='预览链接无效或已过期，请重新打开素材') from exc
