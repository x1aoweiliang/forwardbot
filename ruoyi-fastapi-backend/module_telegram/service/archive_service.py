import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import jwt
from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config.env import ArchiveConfig, JwtConfig
from exceptions.exception import ServiceException
from module_telegram.entity.do.archive_do import TgArchiveItem, TgArchiveTask
from module_telegram.entity.do.telegram_do import TgMessage, TgMessageMedia
from module_telegram.entity.vo.archive_vo import ArchiveLibraryQuery, ArchiveSubmitModel
from module_telegram.service.archive_storage import load_storages, normalize_path
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


def manifest_files(manifest: dict[str, Any]) -> dict[str, bytes]:
    return {
        'message.txt': manifest['text'].encode('utf-8'),
        'metadata.json': json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode('utf-8'),
    }


class TelegramArchiveService:
    @staticmethod
    def _archived() -> Any:
        return exists().where(
            TgArchiveTask.message_id == TgMessage.message_id,
            TgArchiveTask.media_revision == TgMessage.media_revision,
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
        if query.state == 'archived':
            filters.append(cls._archived())
        elif query.state == 'unarchived':
            filters.append(~cls._archived())
        elif query.state == 'ready':
            filters.extend([~pending, ~failed, TgMessage.is_sensitive == 'N'])
        elif query.state == 'pending':
            filters.append(pending)
        elif query.state == 'failed':
            filters.append(failed)
        elif query.state == 'blocked':
            filters.append(TgMessage.is_sensitive == 'Y')
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
                    .where(TgArchiveTask.message_id == message_id)
                    .order_by(TgArchiveTask.task_id.desc())
                )
            )
            .scalars()
            .all()
        )
        task_ids = [task.task_id for task in tasks]
        items = (
            (await db.execute(select(TgArchiveItem).where(TgArchiveItem.task_id.in_(task_ids)))).scalars().all()
            if tasks
            else []
        )
        data = CamelCaseUtil.transform_result(message)
        data['medias'] = [
            {
                'mediaId': media.media_id,
                'fileName': media.file_name or f'素材 {index + 1}',
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
                'status': task.status,
                'lastError': task.last_error,
                'mediaRevision': task.media_revision,
                'createTime': task.create_time,
                'completedAt': task.completed_at,
                'files': [
                    {'name': item.file_name, 'status': item.status, 'error': item.last_error}
                    for item in items
                    if item.task_id == task.task_id
                ],
            }
            for task in tasks
        ]
        return data

    @staticmethod
    async def retry_download(db: AsyncSession, message_id: int) -> int:
        message = await db.get(TgMessage, message_id)
        if not message or message.collected != 'Y' or message.is_sensitive == 'Y':
            raise ServiceException(message='该消息不可重试下载')
        result = await db.execute(
            update(TgMessageMedia)
            .where(TgMessageMedia.message_id == message_id, TgMessageMedia.download_status.in_(['failed', 'retry']))
            .values(download_status='queued', attempts=0, next_retry_at=None, last_error=None)
        )
        count = result.rowcount
        await db.commit()
        return count

    @classmethod
    async def submit(cls, db: AsyncSession, request: ArchiveSubmitModel, username: str) -> list[int]:
        target_path = normalize_path(request.target_path)
        if request.storage_id not in {item.id for item in load_storages()}:
            raise ServiceException(message='存储目标尚未配置')
        result = []
        for message_id in sorted(set(request.message_ids)):
            message = (
                (await db.execute(select(TgMessage).where(TgMessage.message_id == message_id).with_for_update()))
                .scalars()
                .first()
            )
            if not message or message.collected != 'Y' or message.is_sensitive == 'Y':
                raise ServiceException(message=f'消息 {message_id} 不可归档')
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
            if any(media.download_status not in {'ready', 'skipped'} for media in medias):
                raise ServiceException(message=f'消息 {message_id} 的素材尚未全部下载完成')
            selected = [media for media in medias if media.download_status == 'ready']
            for media in selected:
                if not media.local_path or not private_path(media.local_path).is_file():
                    raise ServiceException(message=f'消息 {message_id} 的本地素材缺失')
            manifest = {
                'text': message.message_text or '',
                'source_chat_id': message.source_chat_id,
                'source_chat_title': message.source_chat_title,
                'telegram_message_id': message.telegram_message_id,
                'sent_at': message.sent_at.isoformat() if message.sent_at else None,
                'grouped_id': message.grouped_id,
                'revision': message.media_revision,
                'files': [
                    {
                        'media_id': media.media_id,
                        'name': Path(media.local_path).name,
                        'original_name': media.file_name,
                        'mime_type': media.mime_type,
                        'size': media.file_size,
                        'sha256': media.sha256,
                        'source_message_id': media.source_telegram_message_id,
                        'caption': media.caption,
                    }
                    for media in selected
                ],
                'skipped_files': [
                    {'name': media.file_name, 'mime_type': media.mime_type}
                    for media in medias
                    if media.download_status == 'skipped'
                ],
            }
            manifest_json = json.dumps(manifest, ensure_ascii=False, sort_keys=True)
            fingerprint = hashlib.sha256(manifest_json.encode()).hexdigest()
            dedupe_key = hashlib.sha256(
                json.dumps([message_id, request.storage_id, target_path, fingerprint]).encode()
            ).hexdigest()
            existing = (
                (await db.execute(select(TgArchiveTask).where(TgArchiveTask.dedupe_key == dedupe_key)))
                .scalars()
                .first()
            )
            if existing:
                result.append(existing.task_id)
                continue
            source_label = re.sub(r'[^a-zA-Z0-9_-]', '_', message.source_chat_id)
            date_label = (message.sent_at or message.create_time or datetime.now()).strftime('%Y-%m-%d')
            package = f'{date_label}_{source_label}_{message.telegram_message_id}_{fingerprint[:8]}'
            task = TgArchiveTask(
                dedupe_key=dedupe_key,
                message_id=message_id,
                media_revision=message.media_revision,
                storage_id=request.storage_id,
                target_path=target_path,
                package_name=package,
                manifest_json=manifest_json,
                status='queued',
                create_by=username,
            )
            db.add(task)
            await db.flush()
            for file in manifest['files']:
                db.add(
                    TgArchiveItem(
                        task_id=task.task_id,
                        media_id=file['media_id'],
                        file_name=file['name'],
                        file_size=file['size'],
                        sha256=file['sha256'],
                        status='queued',
                    )
                )
            for name, content in manifest_files(manifest).items():
                db.add(
                    TgArchiveItem(
                        task_id=task.task_id,
                        file_name=name,
                        file_size=len(content),
                        sha256=hashlib.sha256(content).hexdigest(),
                        status='queued',
                    )
                )
            result.append(task.task_id)
        # Return copied IDs rather than ORM attributes after committing.
        await db.commit()
        return result

    @staticmethod
    async def retry_archive(db: AsyncSession, task_id: int) -> None:
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
