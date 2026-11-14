# ruff: noqa: PLC0415
import asyncio
import hashlib
import importlib.util
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, inspect, select, text, update
from test_media_archive import database, ingest, ready_media, source_message

from exceptions.exception import ServiceException
from module_telegram.entity.do.archive_do import TgArchiveItem, TgArchiveMessage, TgArchiveTask
from module_telegram.entity.do.telegram_do import TgMessageMedia
from module_telegram.entity.vo.archive_vo import ArchiveSubmitModel
from module_telegram.service.archive_service import TelegramArchiveService
from module_telegram.service.archive_storage import ArchiveStorageError
from module_telegram.service.archive_worker import TelegramArchiveWorker
from module_telegram.service.archive_zip import DiskSpaceError, build_media_zip


@pytest.mark.parametrize(('name', 'expected'), [('abc', 'abc.zip'), (' 剧名.ZIP ', '剧名.zip')])
def test_zip_name_normalizes_suffix(name: str, expected: str) -> None:
    request = ArchiveSubmitModel(message_ids=[1], storage_id='test', archive_name=name)
    assert request.archive_name == expected


@pytest.mark.parametrize('name', ['', ' ', '.zip', '..', '../剧名', 'a/b', 'a\\b', 'a\nb.zip', '剧' * 61])
def test_zip_name_rejects_invalid_paths(name: str) -> None:
    with pytest.raises(ValidationError):
        ArchiveSubmitModel(message_ids=[1], storage_id='test', archive_name=name)


def test_zip_rejects_changed_content_and_checks_disk_space(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    file = tmp_path / 'episode.mp4'
    file.write_bytes(b'abcdef')
    files = [{'source': file, 'file_name': '第一集.mp4', 'file_size': 6, 'sha256': hashlib.sha256(b'xxxxxx').hexdigest()}]
    with pytest.raises(ArchiveStorageError, match='校验'):
        build_media_zip(tmp_path / 'series.zip', files)
    files[0]['sha256'] = hashlib.sha256(file.read_bytes()).hexdigest()
    monkeypatch.setattr('module_telegram.service.archive_zip.shutil.disk_usage', lambda _: SimpleNamespace(free=0))
    with pytest.raises(DiskSpaceError):
        build_media_zip(tmp_path / 'series.zip', files)


def test_batch_retry_validates_all_members_and_rejects_other_content_with_same_zip_name(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions, sessions() as db:
            first = await ingest(db, [source_message(10)])
            second = await ingest(db, [source_message(11)])
            for message_id in [first, second]:
                await ready_media(db, message_id)
            request = ArchiveSubmitModel(message_ids=[first, second], storage_id='test', archive_name='剧名')
            ids = await TelegramArchiveService.submit(db, request, 'test')
            with pytest.raises(ServiceException) as error:
                await TelegramArchiveService.submit(db, request.model_copy(update={'message_ids': [first]}), 'test')
            assert '同名 ZIP' in error.value.message
            await db.rollback()
            await db.execute(update(TgArchiveTask).values(status='failed'))
            await db.commit()
            await TelegramArchiveService.cleanup_local(db, [second])
            with pytest.raises(ServiceException) as error:
                await TelegramArchiveService.retry_archive(db, ids[0])
            assert '重新下载' in error.value.message
            await db.rollback()
            await ready_media(db, second)
            await TelegramArchiveService.retry_archive(db, ids[0])
            assert (await db.get(TgArchiveTask, ids[0])).status == 'queued'

    asyncio.run(run())


def test_old_file_archive_continues_without_creating_zip(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    uploads = []

    class Storage:
        def ensure_dir(self, path: str) -> str:
            return path

        def upload(self, source: Path, directory: str, filename: str, checksum: str) -> str:
            uploads.append((directory, filename))
            return filename

    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions, sessions() as db:
            monkeypatch.setattr('module_telegram.service.archive_worker.get_storage', lambda _: Storage())
            message_id = await ingest(db, [source_message()])
            await ready_media(db, message_id)
            media = await db.scalar(select(TgMessageMedia))
            task = TgArchiveTask(
                dedupe_key='old', message_id=message_id, media_revision=1, storage_id='test',
                target_path='历史目录', package_name='旧消息目录', manifest_json='{}', create_by='test',
            )
            db.add(task)
            await db.flush()
            task_id = task.task_id
            db.add(TgArchiveMessage(task_id=task_id, message_id=message_id, media_revision=1))
            db.add(TgArchiveItem(task_id=task_id, media_id=media.media_id, file_name='10.jpg', file_size=6, sha256=media.sha256))
            await db.commit()
            worker = TelegramArchiveWorker()
            await worker.archive(await worker.claim('archive'))
            assert uploads == [('历史目录/旧消息目录', '10.jpg')]
            db.expire_all()
            assert (await db.get(TgArchiveTask, task_id)).status == 'completed'

    asyncio.run(run())


def test_zip_migration_backfills_history_and_is_repeatable(tmp_path: Path) -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = create_engine(f'sqlite:///{tmp_path / "migration.db"}')
    versions = Path(__file__).parents[1] / 'alembic' / 'versions'
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        for name in ['20260430_01_create_telegram_forwardbot_tables', '20261111_01_add_media_archive']:
            spec = importlib.util.spec_from_file_location(name, versions / f'{name}.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            module.upgrade()
        conn.execute(text('''INSERT INTO tg_archive_task
            (task_id, dedupe_key, message_id, media_revision, storage_id, target_path, package_name,
             manifest_json, status, create_by, create_time)
            VALUES (1, 'old', 12, 3, 'quark', '飞机/20261113', '', '{}', 'completed', 'test', :created)
        '''), {'created': datetime.now()})
        spec = importlib.util.spec_from_file_location('zip_migration', versions / '20261114_01_add_zip_archives.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.upgrade()
        module.upgrade()
        assert conn.execute(text('SELECT task_id, message_id, media_revision FROM tg_archive_message')).all() == [(1, 12, 3)]
        assert conn.scalar(text('SELECT archive_format FROM tg_archive_task')) == 'files'
        module.downgrade()
        assert 'tg_archive_message' not in inspect(conn).get_table_names()
        module.upgrade()
        conn.execute(text("UPDATE tg_archive_task SET archive_format='zip'"))
        with pytest.raises(RuntimeError, match='不能回退'):
            module.downgrade()
    engine.dispose()
