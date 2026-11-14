# ruff: noqa: PLR2004, PLC0415, ASYNC240
# Literal counts and local imports keep isolated regression fixtures readable.
import asyncio
import hashlib
import importlib.util
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from zipfile import ZipFile

import pytest
from sqlalchemy import BigInteger, create_engine, func, inspect, select, update
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.ext.compiler import compiles

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from config.database import Base, create_async_session_local
from config.env import ArchiveConfig
from exceptions.exception import ServiceException
from module_telegram.entity.do.archive_do import TgArchiveItem, TgArchiveMessage, TgArchiveTask
from module_telegram.entity.do.telegram_do import (
    TgAccount,
    TgChat,
    TgListenerRule,
    TgMessage,
    TgMessageMedia,
    TgSensitiveWord,
)
from module_telegram.entity.vo.archive_vo import ArchiveLibraryQuery, ArchiveSubmitModel
from module_telegram.entity.vo.telegram_vo import TgListenerRuleModel
from module_telegram.service.archive_service import TelegramArchiveService, media_filename, private_path
from module_telegram.service.archive_storage import (
    ArchiveStorageError,
    QuarkArchiveStorage,
    S3ArchiveStorage,
    StorageSpec,
    normalize_path,
)
from module_telegram.service.archive_worker import DiskSpaceError, TelegramArchiveWorker
from module_telegram.service.telegram_client_service import TelegramClientManager
from module_telegram.service.telegram_service import (
    TelegramCrudService,
    TelegramMediaCleanupService,
    TelegramMessageIngestService,
)


@compiles(BigInteger, 'sqlite')
def sqlite_bigint(_type: Any, compiler: Any, **kwargs: Any) -> str:
    return 'INTEGER'


@asynccontextmanager
async def database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    engine = create_async_engine(f'sqlite+aiosqlite:///{tmp_path / "test.db"}')
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda connection: Base.metadata.create_all(
                connection, tables=[table for name, table in Base.metadata.tables.items() if name.startswith('tg_')]
            )
        )
    sessions = create_async_session_local(engine)
    monkeypatch.setattr('module_telegram.service.archive_worker.AsyncSessionLocal', sessions)
    monkeypatch.setattr('config.database.AsyncSessionLocal', sessions)
    monkeypatch.setattr(TelegramClientManager, '_handlers', {1: [('test-handler', None)]})
    monkeypatch.setattr(ArchiveConfig, 'archive_media_dir', str(tmp_path / 'private'))
    monkeypatch.setattr(ArchiveConfig, 'archive_min_free_bytes', 0)
    storages = tmp_path / 'storages.json'
    storages.write_text(json.dumps([{'id': 'test', 'type': 's3', 'name': 'Test', 'bucket': 'test'}]))
    monkeypatch.setattr(ArchiveConfig, 'archive_storages_file', str(storages))
    Path(ArchiveConfig.archive_media_dir).mkdir()
    async with sessions() as db:
        db.add(
            TgAccount(
                account_id=1, account_name='test', phone='test', api_id=1, api_hash='test', session_status='authorized'
            )
        )
        db.add(TgChat(chat_pk=1, account_id=1, chat_id='-1001', chat_title='Source', chat_type='group', can_listen='Y'))
        db.add(
            TgListenerRule(
                rule_id=1,
                account_id=1,
                source_chat_pk=1,
                source_chat_pks='1',
                target_chat_pks='',
                rule_name='Collect',
                forward_enabled='N',
                download_enabled='Y',
            )
        )
        await db.commit()
    try:
        yield sessions
    finally:
        await engine.dispose()
        TelegramClientManager._ingest_locks.clear()


def source_message(message_id: int = 10, text: str = '原文', grouped_id: int | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        id=message_id,
        message=text,
        media=object(),
        photo=SimpleNamespace(id=message_id * 100),
        file=SimpleNamespace(name='photo.jpg', mime_type='image/jpeg', size=6),
        date=datetime(2026, 11, 11),
        grouped_id=grouped_id,
    )


async def ingest(db: Any, messages: list[Any]) -> int:
    account, chat = await db.get(TgAccount, 1), await db.get(TgChat, 1)
    result = await TelegramMessageIngestService.ingest_album(db, account, chat, SimpleNamespace(messages=messages))
    message_id = result.message_id
    await db.commit()
    return message_id


async def ingest_and_dispatch(db: Any, messages: list[Any]) -> int:
    """Simulate the independent consumer only after the ingest transaction commits."""
    message_id = await ingest(db, messages)
    await TelegramArchiveWorker().deliver_pending()
    db.expire_all()
    return message_id


async def ready_media(db: Any, message_id: int) -> None:
    medias = (await db.execute(select(TgMessageMedia).where(TgMessageMedia.message_id == message_id))).scalars().all()
    for media in medias:
        relative = f'tg/1/{message_id}/{media.source_telegram_message_id}.jpg'
        path = private_path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'abcdef')
        media.local_path, media.download_status = relative, 'ready'
        media.downloaded_bytes = 6
        media.sha256 = hashlib.sha256(b'abcdef').hexdigest()
    await db.commit()


def test_album_collects_captions_deduplicates_and_preserves_late_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions, sessions() as db:
            message_id = await ingest(db, [source_message(10, '第一张', 777), source_message(11, '第二张', 777)])
            assert await ingest(db, [source_message(10, '第一张', 777)]) == message_id
            assert await db.scalar(select(func.count()).select_from(TgMessageMedia)) == 2
            await ingest(db, [source_message(12, '晚到视频', 777)])
            row = await db.get(TgMessage, message_id)
            assert row.media_revision == 2
            assert row.auto_forward_status == 'disabled'
            media = (
                (await db.execute(select(TgMessageMedia).order_by(TgMessageMedia.source_telegram_message_id)))
                .scalars()
                .all()
            )
            assert [item.caption for item in media] == ['第一张', '第二张', '晚到视频']
            assert all(
                item.download_status == 'queued' and item.local_path == '' and item.storage_kind == 'archive'
                for item in media
            )
            assert await db.scalar(select(func.count()).select_from(TgMessage)) == 1

    asyncio.run(run())


def test_sensitive_and_other_file_types_do_not_start_downloads(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions, sessions() as db:
            db.add(TgSensitiveWord(word='spam', match_case='N'))
            await db.commit()
            blocked = await ingest(db, [source_message(10, 'spam')])
            assert (
                await db.scalar(select(TgMessageMedia.download_status).where(TgMessageMedia.message_id == blocked))
                == 'blocked'
            )
            with pytest.raises(ServiceException):
                await TelegramArchiveService.submit(
                    db, ArchiveSubmitModel(message_ids=[blocked], storage_id='test', archive_name='test'), 'test'
                )
            document = source_message(11)
            document.photo = None
            document.file.mime_type = 'application/pdf'
            doc_id = await ingest(db, [document])
            assert (
                await db.scalar(select(TgMessageMedia.download_status).where(TgMessageMedia.message_id == doc_id))
                == 'skipped'
            )

    asyncio.run(run())


def test_collect_only_rule_and_commit_response_are_safe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions, sessions() as db:
            response = await TelegramCrudService.save_rule(
                db,
                TgListenerRuleModel(
                    account_id=1,
                    source_chat_pks='1',
                    rule_name='Collect only',
                    forward_enabled='N',
                    download_enabled='Y',
                ),
            )
            assert response.is_success
            assert sessions.kw['expire_on_commit'] is False
            with pytest.raises(ServiceException) as error:
                await TelegramCrudService.save_rule(
                    db,
                    TgListenerRuleModel(account_id=1, source_chat_pks='1', forward_enabled='N', download_enabled='N'),
                )
            assert '至少开启' in error.value.message

    asyncio.run(run())


def test_late_album_caption_blocks_queued_and_inflight_downloads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions:
            async with sessions() as db:
                db.add(TgSensitiveWord(word='spam', match_case='N'))
                await db.commit()
                message_id = await ingest(db, [source_message(11, '', 777), source_message(12, '', 777)])
            worker = TelegramArchiveWorker()
            job = await worker.claim('download')
            async with sessions() as db:
                await ingest(db, [source_message(10, 'spam', 777)])
                message = await db.get(TgMessage, message_id)
                assert message.message_text == 'spam'
                assert message.is_sensitive == 'Y'
                assert message.media_revision == 2
                medias = (
                    (await db.execute(select(TgMessageMedia).order_by(TgMessageMedia.source_telegram_message_id)))
                    .scalars()
                    .all()
                )
                assert [item.media_index for item in medias] == [0, 1, 2]
                assert all(item.download_status == 'blocked' and item.lease_token is None for item in medias)
            assert not await worker._update('download', job, download_status='ready')
            assert await worker.claim('download') is None

    asyncio.run(run())


def test_catchup_respects_stopped_listener(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def unexpected_client(*args: Any) -> None:
        pytest.fail('stopped listener must not connect for catch-up')

    async def run() -> None:
        async with database(monkeypatch, tmp_path):
            monkeypatch.setattr(TelegramClientManager, '_handlers', {})
            monkeypatch.setattr(TelegramClientManager, 'get_authorized_client', unexpected_client)
            await TelegramArchiveWorker().catch_up()

    asyncio.run(run())


def test_download_resumes_partial_file_and_only_marks_complete_after_verification(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    offsets = []

    class Stream:
        def __init__(self, offset: int) -> None:
            self.offset, self.sent = offset, False

        def __aiter__(self) -> Any:
            return self

        async def __anext__(self) -> bytes:
            if self.sent:
                raise StopAsyncIteration
            self.sent = True
            return b'abcdef'[self.offset :]

        async def close(self) -> None:
            pass

    class Client:
        async def get_messages(self, *args: Any, **kwargs: Any) -> Any:
            return source_message()

        def iter_download(self, media: Any, offset: int = 0) -> Any:
            offsets.append(offset)
            return Stream(offset)

    async def get_client(*args: Any) -> Client:
        return Client()

    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions:
            monkeypatch.setattr(TelegramClientManager, 'get_authorized_client', get_client)
            async with sessions() as db:
                message_id = await ingest(db, [source_message()])
            partial = private_path(f'tg/1/{message_id}/10.jpg.part')
            partial.parent.mkdir(parents=True)
            partial.write_bytes(b'abc')
            worker = TelegramArchiveWorker()
            job = await worker.claim('download')
            assert job and job['download_status'] == 'downloading'
            assert await worker.claim('download') is None
            await worker.download(job)
            async with sessions() as db:
                media = await db.get(TgMessageMedia, job['media_id'])
                assert media.download_status == 'ready'
                assert Path(media.local_path).name == '原文.jpg'
                assert private_path(media.local_path).read_bytes() == b'abcdef'
                assert media.sha256 == hashlib.sha256(b'abcdef').hexdigest()
                assert not partial.exists()
            assert offsets == [3]

    asyncio.run(run())


def test_expired_job_lease_can_be_reclaimed_but_old_worker_cannot_complete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions:
            async with sessions() as db:
                await ingest(db, [source_message()])
            worker = TelegramArchiveWorker()
            old = await worker.claim('download')
            async with sessions() as db:
                await db.execute(update(TgMessageMedia).values(lease_until=datetime.now() - timedelta(seconds=1)))
                await db.commit()
            new = await worker.claim('download')
            assert new['lease_token'] != old['lease_token']
            assert not await worker._update('download', old, download_status='ready')
            assert await worker._update('download', new, downloaded_bytes=3)
            await worker._failed('download', new, DiskSpaceError())
            async with sessions() as db:
                row = await db.get(TgMessageMedia, new['media_id'])
                assert row.download_status == 'retry'
                assert '磁盘' in row.last_error

    asyncio.run(run())


def test_zip_archive_is_idempotent_and_retries_the_same_package(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    uploads = []

    class Storage:
        def ensure_dir(self, path: str) -> str:
            assert path == '旅行'
            return path

        def upload(self, source: Path, directory: str, filename: str, checksum: str) -> str:
            assert filename == 'test.zip' and directory == '旅行'
            contents = source.read_bytes()
            assert hashlib.sha256(contents).hexdigest() == checksum
            with ZipFile(source) as package:
                assert package.namelist() == ['原文_01.jpg', '原文_02.jpg']
                assert package.testzip() is None
                assert all(package.read(name) == b'abcdef' for name in package.namelist())
            uploads.append(contents)
            if len(uploads) == 1:
                raise ArchiveStorageError('网络中断')
            return 'remote/test.zip'

    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions:
            monkeypatch.setattr('module_telegram.service.archive_worker.get_storage', lambda _id: Storage())
            async with sessions() as db:
                message_id = await ingest(db, [source_message(10, grouped_id=777), source_message(11, '', 777)])
                request = ArchiveSubmitModel(message_ids=[message_id], storage_id='test', archive_name='test', target_path='旅行')
                with pytest.raises(ServiceException) as error:
                    await TelegramArchiveService.submit(db, request, 'operator')
                assert '尚未全部下载' in error.value.message
                await ready_media(db, message_id)
                ids = await TelegramArchiveService.submit(db, request, 'operator')
                assert await TelegramArchiveService.submit(db, request, 'operator') == ids
                assert (await TelegramArchiveService.list_library(db, ArchiveLibraryQuery()))['rows'][0]['archived'] is False
            worker = TelegramArchiveWorker()
            first = await worker.claim('archive')
            with pytest.raises(ArchiveStorageError) as error:
                await worker.archive(first)
            await worker._failed('archive', first, error.value)
            assert not private_path(f'.tasks/{ids[0]}').exists()
            async with sessions() as db:
                assert await db.scalar(select(func.count()).select_from(TgArchiveItem).where(TgArchiveItem.status == 'completed')) == 0
                await TelegramArchiveService.retry_archive(db, ids[0])
            await worker.archive(await worker.claim('archive'))
            async with sessions() as db:
                assert (await db.get(TgArchiveTask, ids[0])).status == 'completed'
                assert (await TelegramArchiveService.list_library(db, ArchiveLibraryQuery(state='archived')))['total'] == 1
                record = (await TelegramArchiveService.detail(db, message_id))['archives'][0]
                assert record['files'] == [{'name': 'test.zip', 'status': 'completed', 'error': None}]
                assert record['mediaCount'] == 2
            assert len(uploads) == 2 and uploads[0] == uploads[1]
            assert not private_path(f'.tasks/{ids[0]}').exists()

    asyncio.run(run())


@pytest.mark.parametrize(('text', 'sequence', 'expected'), [
    ('第二集', None, '第二集.mp4'),
    ('第一集.mp4', None, '第一集.mp4'),
    ('  ', None, '986.mp4'),
    ('第/一集\n预告', 2, '第_一集 预告_02.mp4'),
])
def test_media_filename_uses_caption_and_safe_album_index(text: str, sequence: int | None, expected: str) -> None:
    assert media_filename(986, 'video/mp4', text, sequence) == expected
    assert len(media_filename(986, 'video/mp4', '第' * 500, 1).encode()) < 200


def test_batch_zip_follows_source_order_and_marks_every_member_archived(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    uploads = []

    class Storage:
        def ensure_dir(self, path: str) -> str:
            assert path == '飞机/20261114'
            return path

        def upload(self, source: Path, directory: str, filename: str, checksum: str) -> str:
            with ZipFile(source) as package:
                uploads.append(package.namelist())
            assert filename == '剧名.zip' and directory == '飞机/20261114'
            return filename

    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions, sessions() as db:
            monkeypatch.setattr('module_telegram.service.archive_worker.get_storage', lambda _: Storage())
            last = await ingest(db, [source_message(30, '第二集')])
            first = await ingest(db, [source_message(10, '第一集')])
            second = await ingest(db, [source_message(20, '第一集')])
            for message_id in [last, first, second]:
                await ready_media(db, message_id)
            request = ArchiveSubmitModel(message_ids=[last, second, first], storage_id='test', archive_name='剧名', target_path='飞机/20261114')
            # Responses must not touch expired ORM fields after commit.
            db.sync_session.expire_on_commit = True
            ids = await TelegramArchiveService.submit(db, request, 'test')
            assert len(ids) == 1
            assert await TelegramArchiveService.submit(db, request, 'test') == ids
            task = await db.get(TgArchiveTask, ids[0])
            assert task.package_name == '剧名.zip' and task.archive_format == 'zip'
            assert len(list(await db.scalars(select(TgArchiveMessage)))) == 3
            for member_id in [first, second, last]:
                with pytest.raises(ServiceException):
                    await TelegramArchiveService.cleanup_local(db, [member_id])
                await db.rollback()
                assert (await TelegramArchiveService.detail(db, member_id))['archives'][0]['taskId'] == ids[0]
            await db.rollback()
            worker = TelegramArchiveWorker()
            await worker.archive(await worker.claim('archive'))
            assert len(uploads) == 1
            names = uploads[0]
            assert names[0] == '第一集.jpg' and names[2] == '第二集.jpg'
            assert names[1].startswith('第一集_20_') and len(set(names)) == 3
            assert (await TelegramArchiveService.list_library(db, ArchiveLibraryQuery(state='archived')))['total'] == 3
            for member_id in [first, second, last]:
                assert (await TelegramArchiveService.detail(db, member_id))['archives'][0]['files'][0]['name'] == '剧名.zip'
            assert (await TelegramArchiveService.cleanup_local(db, [first, second, last]))['freedBytes'] == 18

    asyncio.run(run())


def test_text_only_message_does_not_create_an_archive(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions, sessions() as db:
            text = source_message()
            text.media = text.photo = None
            message_id = await ingest(db, [text])
            with pytest.raises(ServiceException) as error:
                await TelegramArchiveService.submit(db, ArchiveSubmitModel(message_ids=[message_id], storage_id='test', archive_name='test'), 'test')
            assert '没有可归档' in error.value.message
            assert await db.scalar(select(func.count()).select_from(TgArchiveTask)) == 0

    asyncio.run(run())


def test_existing_media_rename_preserves_partial_files_preview_and_cleanup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from rename_archive_media import migrate

    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions, sessions() as db:
            message_id = await ingest(db, [source_message(10, '第一集', 777), source_message(11, '', 777)])
            await ready_media(db, message_id)
            pending = await ingest(db, [source_message(20, '第二集')])
            partial = private_path(f'tg/1/{pending}/20.jpg.part')
            partial.parent.mkdir(parents=True)
            partial.write_bytes(b'abc')
            assert await migrate(db) == {'planned': 2, 'unchanged': 0}
            assert private_path(f'tg/1/{message_id}/10.jpg').exists()
            journal = tmp_path / 'rename.json'
            assert await migrate(db, apply=True, backup=journal) == {'renamed': 2, 'unchanged': 0}
            files = list(await db.scalars(select(TgMessageMedia).where(TgMessageMedia.message_id == message_id).order_by(TgMessageMedia.media_id)))
            assert [Path(media.local_path).name for media in files] == ['第一集_01.jpg', '第一集_02.jpg']
            for media in files:
                path, mime = await TelegramArchiveService.media_file(db, media.media_id)
                assert path.read_bytes() == b'abcdef' and mime == 'image/jpeg'
            assert partial.read_bytes() == b'abc'
            assert await migrate(db) == {'planned': 0, 'unchanged': 2}
            assert len(json.loads(journal.read_text())) == 2
            assert (await TelegramArchiveService.cleanup_local(db, [message_id]))['freedBytes'] == 12
            assert not private_path(f'tg/1/{message_id}/第一集_01.jpg').exists()
            assert partial.exists()

    asyncio.run(run())


def test_cleanup_never_removes_unarchived_collection(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions, sessions() as db:
            message_id = await ingest(db, [source_message()])
            await ready_media(db, message_id)
            media = await db.scalar(select(TgMessageMedia))
            media.create_time = datetime.now() - timedelta(days=30)
            await db.commit()
            result = await TelegramMediaCleanupService.cleanup_expired_local_files(
                db, base_dir=ArchiveConfig.archive_media_dir
            )
            assert result.deleted_count == 0
            assert private_path(media.local_path).is_file()

    asyncio.run(run())


def test_preview_tickets_are_media_scoped_and_paths_cannot_escape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(ArchiveConfig, 'archive_media_dir', str(tmp_path))
    ticket = TelegramArchiveService.preview_ticket(1)
    TelegramArchiveService.verify_ticket(1, ticket)
    with pytest.raises(ServiceException):
        TelegramArchiveService.verify_ticket(2, ticket)
    with pytest.raises(ServiceException):
        TelegramArchiveService.verify_ticket(1, ticket + 'invalid')
    for path in ['../outside', '/etc/passwd']:
        with pytest.raises(ServiceException):
            private_path(path)
    for path in ['a/../b', 'a//b', 'a\\b', 'a/\x00b']:
        with pytest.raises(ArchiveStorageError):
            normalize_path(path)


def test_quark_browse_uses_full_cli_list(monkeypatch: pytest.MonkeyPatch) -> None:
    storage = QuarkArchiveStorage(StorageSpec(id='q', name='Quark', type='quark', prefix='素材'), cookie='test')

    def run(args: list[str]) -> dict:
        assert args == ['list', '/素材']
        return {'list': [{'fid': str(i), 'file_name': f'folder-{i}', 'dir': True} for i in range(80)]}

    monkeypatch.setattr(storage, '_run', run)
    assert len(storage.list_dirs('')) == 80


def test_s3_upload_reconciles_remote_object_without_overwrite(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    content = tmp_path / 'asset.jpg'
    content.write_bytes(b'abc')
    checksum = hashlib.sha256(b'abc').hexdigest()
    client = SimpleNamespace(head_object=lambda **kwargs: {'ContentLength': 3, 'Metadata': {'sha256': checksum}})
    monkeypatch.setattr('module_telegram.service.archive_storage.boto3.client', lambda *args, **kwargs: client)
    storage = S3ArchiveStorage(StorageSpec(id='s', name='S3', type='s3', bucket='test', prefix='archive'))
    assert storage.upload(content, 'album', 'asset.jpg', checksum) == 'archive/album/asset.jpg'
    with pytest.raises(ArchiveStorageError, match='不同内容'):
        storage.upload(content, 'album', 'asset.jpg', 'different')


def test_migration_creates_archive_schema_and_can_roundtrip(tmp_path: Path) -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = create_engine(f'sqlite:///{tmp_path / "migration.db"}')
    versions = Path(__file__).parents[1] / 'alembic' / 'versions'
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        for path in [
            versions / '20260430_01_create_telegram_forwardbot_tables.py',
            versions / '20261111_01_add_media_archive.py',
        ]:
            spec = importlib.util.spec_from_file_location(path.stem, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.upgrade()
        assert 'tg_archive_task' in inspect(conn).get_table_names()
        assert 'download_status' in {column['name'] for column in inspect(conn).get_columns('tg_message_media')}
        module.downgrade()
        assert 'tg_archive_task' not in inspect(conn).get_table_names()
        module.upgrade()
    engine.dispose()


def test_catchup_keeps_album_together_at_page_boundary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class Client:
        async def iter_messages(self, entity: str, min_id: int, reverse: bool, limit: int) -> Any:
            assert reverse
            for i in range(min_id + 1, min(204, min_id + limit + 1)):
                message = source_message(i, grouped_id=777 if i >= 199 else None)
                message.date = datetime.now() - timedelta(seconds=60)
                yield message

    async def get_client(*args: Any) -> Client:
        return Client()

    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions:
            monkeypatch.setattr(TelegramClientManager, 'get_authorized_client', get_client)
            async with sessions() as db:
                await db.execute(update(TgChat).values(collection_cursor=0))
                await db.commit()
            worker = TelegramArchiveWorker()
            await worker.catch_up()
            async with sessions() as db:
                assert (await db.get(TgChat, 1)).collection_cursor == 198
            await worker.catch_up()
            async with sessions() as db:
                assert await db.scalar(select(func.count()).select_from(TgMessage)) == 199
                assert await db.scalar(select(func.count()).select_from(TgMessageMedia)) == 203
                assert (await db.get(TgChat, 1)).collection_cursor == 203

    asyncio.run(run())


def test_late_album_media_marks_previous_archive_outdated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions, sessions() as db:
            message_id = await ingest(db, [source_message(10, grouped_id=777)])
            await ready_media(db, message_id)
            ids = await TelegramArchiveService.submit(
                db, ArchiveSubmitModel(message_ids=[message_id], storage_id='test', archive_name='test'), 'operator'
            )
            await db.execute(update(TgArchiveTask).where(TgArchiveTask.task_id == ids[0]).values(status='completed'))
            await db.commit()
            assert (await TelegramArchiveService.list_library(db, ArchiveLibraryQuery(state='archived')))['total'] == 1
            await ingest(db, [source_message(11, grouped_id=777)])
            assert (await TelegramArchiveService.list_library(db, ArchiveLibraryQuery(state='archived')))['total'] == 0
            assert (await TelegramArchiveService.list_library(db, ArchiveLibraryQuery(state='unarchived')))[
                'total'
            ] == 1

    asyncio.run(run())


def test_preview_http_requires_permission_and_supports_range(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import httpx
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse

    from common.context import RequestContext
    from config.get_db import get_db
    from exceptions.exception import AuthException, PermissionException
    from module_admin.service.login_service import LoginService
    from module_telegram.controller.archive_controller import archive_controller

    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions:
            async with sessions() as db:
                message_id = await ingest(db, [source_message()])
                await ready_media(db, message_id)
                media_id = await db.scalar(select(TgMessageMedia.media_id))
            app = FastAPI()
            app.include_router(archive_controller)

            async def db_dependency() -> Any:
                async with sessions() as db:
                    yield db

            async def get_user(request: Request, token: str, db: Any) -> Any:
                user = SimpleNamespace(permissions=['tg:archive:list'] if token == 'Bearer reader' else [])
                RequestContext.set_current_user(user)
                return user

            async def denied(request: Request, exc: Any) -> JSONResponse:
                return JSONResponse({'message': exc.message}, status_code=403)

            app.dependency_overrides[get_db] = db_dependency
            app.add_exception_handler(AuthException, denied)
            app.add_exception_handler(PermissionException, denied)
            app.add_exception_handler(ServiceException, denied)
            monkeypatch.setattr(LoginService, 'get_current_user', get_user)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
                assert (await client.get('/telegram/archive/library')).status_code == 403
                for method, url, body in (
                    ('post', '/telegram/archive/local/cleanup', {'messageIds': [message_id]}),
                    ('post', '/telegram/archive/quark/quark/login', {}),
                    ('get', '/telegram/archive/quark/quark/status', None),
                ):
                    denied_response = await client.request(
                        method, url, json=body, headers={'Authorization': 'Bearer reader'}
                    )
                    assert denied_response.status_code == 403

                assert (
                    await client.get(
                        f'/telegram/archive/media/{media_id}/ticket', headers={'Authorization': 'Bearer no-permission'}
                    )
                ).status_code == 403
                response = await client.get(
                    f'/telegram/archive/media/{media_id}/ticket', headers={'Authorization': 'Bearer reader'}
                )
                assert response.status_code == 200
                ticket = response.json()['data']['ticket']
                preview = await client.get(
                    f'/telegram/archive/media/{media_id}/content',
                    params={'ticket': ticket},
                    headers={'Range': 'bytes=1-3'},
                )
                assert preview.status_code == 206
                assert preview.content == b'bcd'
                assert preview.headers['cache-control'] == 'private, no-store'
                invalid = await client.get(f'/telegram/archive/media/{media_id + 1}/content', params={'ticket': ticket})
                assert invalid.status_code == 403
                unsigned = await client.get(f'/telegram/archive/media/{media_id}/content')
                assert unsigned.status_code == 422

    asyncio.run(run())


def test_independent_transport_keys_persist_without_replacing_other_deployment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from archive_entrypoint import configure_transport_keys

    monkeypatch.delenv('TRANSPORT_CRYPTO_PRIVATE_KEY', raising=False)
    monkeypatch.delenv('TRANSPORT_CRYPTO_PUBLIC_KEY', raising=False)
    path = tmp_path / 'archive' / 'transport.pem'
    configure_transport_keys(path)
    first = path.read_bytes()
    assert path.stat().st_mode & 0o777 == 0o600
    monkeypatch.delenv('TRANSPORT_CRYPTO_PRIVATE_KEY')
    monkeypatch.delenv('TRANSPORT_CRYPTO_PUBLIC_KEY')
    configure_transport_keys(path)
    assert path.read_bytes() == first


@pytest.mark.parametrize('archive_enabled', [True, False])
def test_archive_worker_starts_after_restart_with_stale_logging_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, archive_enabled: bool,
) -> None:
    from unittest.mock import AsyncMock, Mock

    import server

    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions:
            restore, start = AsyncMock(), Mock()
            monkeypatch.setattr(ArchiveConfig, 'archive_enabled', archive_enabled)
            monkeypatch.setattr(server.AppConfig, 'app_workers', 1)
            monkeypatch.setattr(server, 'AsyncSessionLocal', sessions)
            monkeypatch.setattr(server.SchedulerUtil, 'init_system_scheduler', AsyncMock())
            monkeypatch.setattr(server.LogAggregatorService, 'consume_stream', AsyncMock())
            monkeypatch.setattr(TelegramClientManager, 'restore_enabled_listeners', restore)
            monkeypatch.setattr(TelegramArchiveWorker, 'start', start)
            app = SimpleNamespace(state=SimpleNamespace(redis=object(), startup_log_enabled=False))
            await server._start_background_tasks(app)
            restore.assert_awaited_once()
            start.assert_called_once()
            await app.state.log_aggregator_task

    asyncio.run(run())


def test_local_cleanup_preserves_text_archives_and_requires_explicit_restore(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions, sessions() as db:
            message_id = await ingest(db, [source_message(10, grouped_id=777), source_message(11, grouped_id=777)])
            await ready_media(db, message_id)
            task_id = (
                await TelegramArchiveService.submit(
                    db, ArchiveSubmitModel(message_ids=[message_id], storage_id='test', archive_name='test'), 'test'
                )
            )[0]
            await db.execute(update(TgArchiveTask).values(status='completed'))
            await db.commit()
            partial = private_path(f'tg/1/{message_id}/10.jpg.part')
            partial.write_bytes(b'abc')
            db.sync_session.expire_on_commit = True
            result = await TelegramArchiveService.cleanup_local(db, [message_id, message_id])
            assert result == {'cleanedMessages': 1, 'freedBytes': 15, 'errors': []}
            assert not partial.exists()
            assert not private_path(f'tg/1/{message_id}/10.jpg').exists()
            assert (await TelegramArchiveService.detail(db, message_id))['messageText'] == '原文'
            assert (await db.get(TgArchiveTask, task_id)).status == 'completed'
            archived = await TelegramArchiveService.list_library(db, ArchiveLibraryQuery(state='archived'))
            assert archived['rows'][0]['downloadState'] == 'deleted'
            assert (await TelegramArchiveService.list_library(db, ArchiveLibraryQuery(state='ready')))['total'] == 0
            assert (await TelegramArchiveService.list_library(db, ArchiveLibraryQuery(state='deleted')))['total'] == 1
            # Replaying the album does not recreate or requeue its files.
            await ingest(db, [source_message(10, grouped_id=777), source_message(11, grouped_id=777)])
            assert set(await db.scalars(select(TgMessageMedia.download_status))) == {'deleted'}
            await db.rollback()
            assert await TelegramArchiveWorker().claim('download') is None
            assert await TelegramArchiveService.retry_download(db, message_id) == 2
            assert (await TelegramArchiveWorker().claim('download'))['message_id'] == message_id

    asyncio.run(run())


@pytest.mark.parametrize('state', ['queued', 'uploading', 'retry', 'downloading'])
def test_cleanup_blocks_active_work_without_deleting_any_files(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, state: str
) -> None:
    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions, sessions() as db:
            first = await ingest(db, [source_message(10)])
            second = await ingest(db, [source_message(11)])
            await ready_media(db, first)
            await ready_media(db, second)
            if state == 'downloading':
                await db.execute(
                    update(TgMessageMedia).where(TgMessageMedia.message_id == second).values(download_status=state)
                )
            else:
                await TelegramArchiveService.submit(
                    db, ArchiveSubmitModel(message_ids=[second], storage_id='test', archive_name='test'), 'test'
                )
                await db.execute(update(TgArchiveTask).values(status=state))
            await db.commit()
            with pytest.raises(ServiceException, match='') as error:
                await TelegramArchiveService.cleanup_local(db, [first, second])
            assert '暂不能清理' in error.value.message
            await db.rollback()
            assert private_path(f'tg/1/{first}/10.jpg').exists()
            assert private_path(f'tg/1/{second}/11.jpg').exists()
            assert (
                await db.scalar(select(TgMessageMedia.download_status).where(TgMessageMedia.message_id == first))
                == 'ready'
            )

    asyncio.run(run())


def test_partial_cleanup_can_resume_and_does_not_stall_other_messages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions, sessions() as db:
            message_id = await ingest(db, [source_message(10, grouped_id=777)])
            partial = private_path(f'tg/1/{message_id}/10.jpg.part')
            partial.parent.mkdir(parents=True)
            partial.write_bytes(b'abc')
            original = Path.unlink
            with monkeypatch.context() as patch:

                def denied(path: Path, *args: Any, **kwargs: Any) -> None:
                    if path == partial:
                        raise PermissionError
                    original(path, *args, **kwargs)

                patch.setattr(Path, 'unlink', denied)
                result = await TelegramArchiveService.cleanup_local(db, [message_id])
            assert result['freedBytes'] == 0 and result['errors']
            assert await db.scalar(select(TgMessageMedia.download_status)) == 'deleting'
            with pytest.raises(ServiceException):
                await TelegramArchiveService.retry_download(db, message_id)
            await db.rollback()
            # Late arrivals are distinct files, and cannot starve the global queue.
            await ingest(db, [source_message(11, grouped_id=777)])
            other = await ingest(db, [source_message(20)])
            assert (await TelegramArchiveWorker().claim('download'))['message_id'] == other
            result = await TelegramArchiveService.cleanup_local(db, [message_id])
            assert result['freedBytes'] == 3 and not result['errors']
            assert not partial.exists()
            assert (await TelegramArchiveService.cleanup_local(db, [message_id]))['freedBytes'] == 0

    asyncio.run(run())


def test_cleanup_rejects_paths_from_other_messages_and_symlinks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions, sessions() as db:
            message_id = await ingest(db, [source_message()])
            outside = tmp_path / 'important.jpg'
            outside.write_bytes(b'keep')
            for stored in ('../important.jpg', 'tg/1/999/10.jpg'):
                await db.execute(update(TgMessageMedia).values(local_path=stored, download_status='ready'))
                await db.commit()
                with pytest.raises(ServiceException):
                    await TelegramArchiveService.cleanup_local(db, [message_id])
                await db.rollback()
            await db.execute(update(TgMessageMedia).values(local_path=''))
            await db.commit()
            link = Path(ArchiveConfig.archive_media_dir) / f'tg/1/{message_id}/10.jpg'
            link.parent.mkdir(parents=True)
            link.symlink_to(outside)
            with pytest.raises(ServiceException):
                await TelegramArchiveService.cleanup_local(db, [message_id])
            assert outside.read_bytes() == b'keep'

    asyncio.run(run())


def test_failed_archive_requires_missing_local_media_to_be_restored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions, sessions() as db:
            message_id = await ingest(db, [source_message()])
            await ready_media(db, message_id)
            task_id = (
                await TelegramArchiveService.submit(
                    db, ArchiveSubmitModel(message_ids=[message_id], storage_id='test', archive_name='test'), 'test'
                )
            )[0]
            await db.execute(update(TgArchiveTask).values(status='failed'))
            await db.commit()
            await TelegramArchiveService.cleanup_local(db, [message_id])
            with pytest.raises(ServiceException) as error:
                await TelegramArchiveService.retry_archive(db, task_id)
            assert '重新下载' in error.value.message
            await db.rollback()
            await ready_media(db, message_id)
            await TelegramArchiveService.retry_archive(db, task_id)
            assert (await db.get(TgArchiveTask, task_id)).status == 'queued'

    asyncio.run(run())


def test_revoked_download_cannot_recreate_cleaned_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions:
            async with sessions() as db:
                message_id = await ingest(db, [source_message(10, text='', grouped_id=777)])
            worker = TelegramArchiveWorker()
            job = await worker.claim('download')

            class Client:
                async def get_messages(self, *_args: Any, **_kwargs: Any) -> Any:
                    async with sessions() as db:
                        db.add(TgSensitiveWord(word='spam', match_case='N'))
                        await db.commit()
                        await ingest(db, [source_message(9, 'spam', 777)])
                        with pytest.raises(ServiceException):
                            await TelegramArchiveService.cleanup_local(db, [message_id])
                    return source_message(10, grouped_id=777)

                def iter_download(self, *_args: Any, **_kwargs: Any) -> None:
                    pytest.fail('revoked job must not open a download stream')

            async def client(_account: Any) -> Client:
                return Client()

            monkeypatch.setattr(TelegramClientManager, 'get_authorized_client', client)
            with pytest.raises(asyncio.CancelledError):
                await worker.download(job)
            async with sessions() as db:
                await TelegramArchiveService.cleanup_local(db, [message_id])
            assert not private_path(f'tg/1/{message_id}/10.jpg.part').exists()
            async with sessions() as db:
                assert set(await db.scalars(select(TgMessageMedia.download_status))) == {'deleted'}

    asyncio.run(run())
