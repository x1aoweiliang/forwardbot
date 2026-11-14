# ruff: noqa: PLC0415, PLR2004
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
from module_telegram.entity.do.telegram_do import TgMessage, TgMessageMedia
from module_telegram.entity.vo.archive_vo import ArchiveLibraryQuery, ArchiveSubmitModel, MediaRenameModel
from module_telegram.service.archive_service import TelegramArchiveService, private_path
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


def test_custom_name_preserves_original_text_and_album_names(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions, sessions() as db:
            first = source_message(10, '很长的原消息内容', 777)
            second = source_message(11, '单项说明', 777)
            for source in [first, second]:
                source.photo = None
                source.document = SimpleNamespace(id=source.id * 100)
                source.file.mime_type = 'video/mp4'
            message_id = await ingest(db, [first, second])
            await ready_media(db, message_id)
            original = (await db.get(TgMessage, message_id)).message_text
            db.sync_session.expire_on_commit = True
            await TelegramArchiveService.rename_media(db, message_id, MediaRenameModel(media_name=' 第五集.mp4 '))
            result = await TelegramArchiveService.detail(db, message_id)
            assert result['messageText'] == original and result['mediaRevision'] == 2
            assert [media['fileName'] for media in result['medias']] == ['第五集_01.mp4', '第五集_02.mp4']
            await ingest(db, [first, second])
            library = await TelegramArchiveService.list_library(db, ArchiveLibraryQuery(keyword='第五集'))
            assert library['total'] == 1 and library['rows'][0]['mediaName'] == '第五集.mp4'
            request = ArchiveSubmitModel(message_ids=[message_id], storage_id='test', archive_name='series')
            ids = await TelegramArchiveService.submit(db, request, 'test')
            with pytest.raises(ServiceException):
                await TelegramArchiveService.rename_media(db, message_id, MediaRenameModel(media_name='第六集'))
            await db.rollback()
            names = list(await db.scalars(select(TgArchiveItem.file_name).where(TgArchiveItem.media_id.is_not(None))))
            assert names == ['第五集_01.mp4', '第五集_02.mp4']
            await db.execute(update(TgArchiveTask).where(TgArchiveTask.task_id == ids[0]).values(status='completed'))
            await db.commit()
            await TelegramArchiveService.rename_media(db, message_id, MediaRenameModel(media_name=''))
            detail = await TelegramArchiveService.detail(db, message_id)
            assert detail['mediaName'] is None and detail['messageText'] == original
            assert detail['medias'][0]['fileName'] == '很长的原消息内容_01.mp4'
            assert (await TelegramArchiveService.list_library(db, ArchiveLibraryQuery(state='archived')))['total'] == 0

    asyncio.run(run())


@pytest.mark.parametrize('name', ['../第一集', '第一/集', 'a\nb', '.', '剧' * 47])
def test_custom_name_rejects_invalid_filename(name: str) -> None:
    with pytest.raises(ValidationError):
        MediaRenameModel(media_name=name)


def test_retained_zip_cleanup_recovers_and_only_removes_packed_sources(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    class Storage:
        def ensure_dir(self, path: str) -> str:
            return path

        def upload(self, source: Path, directory: str, filename: str, checksum: str) -> str:
            return filename

    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions, sessions() as db:
            monkeypatch.setattr('module_telegram.service.archive_worker.get_storage', lambda _: Storage())
            message_id = await ingest(db, [source_message(10, '第一集', 777)])
            history_id = await ingest(db, [source_message(90, '历史素材')])
            for item in [message_id, history_id]:
                await ready_media(db, item)
            ids = await TelegramArchiveService.submit(db, ArchiveSubmitModel(
                message_ids=[message_id], storage_id='test', archive_name='series',
            ), 'test')
            worker = TelegramArchiveWorker()

            async def stopped_before_cleanup() -> None:
                pass

            monkeypatch.setattr(worker, 'cleanup_archives', stopped_before_cleanup)
            await worker.archive(await worker.claim('archive'))
            db.expire_all()
            task = await db.get(TgArchiveTask, ids[0])
            assert task.status == 'completed' and task.source_cleanup == 'pending'
            package = private_path(task.local_path)
            assert package.is_file()
            # A later album member is not part of this ZIP and must remain intact.
            await ingest(db, [source_message(11, '晚到第二集', 777)])
            await ready_media(db, message_id)
            await TelegramArchiveWorker().cleanup_archives()
            db.expire_all()
            states = dict((await db.execute(select(TgMessageMedia.source_telegram_message_id, TgMessageMedia.download_status))).all())
            assert states == {10: 'deleted', 11: 'ready', 90: 'ready'}
            assert (await db.get(TgArchiveTask, ids[0])).source_cleanup == 'completed'
            assert package.is_file()

    asyncio.run(run())


def test_auto_cleanup_waits_for_other_archives_and_preserves_sources_without_zip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    class Storage:
        def ensure_dir(self, path: str) -> str:
            return path

        def upload(self, source: Path, directory: str, filename: str, checksum: str) -> str:
            return filename

    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions, sessions() as db:
            monkeypatch.setattr('module_telegram.service.archive_worker.get_storage', lambda _: Storage())
            message_id = await ingest(db, [source_message()])
            await ready_media(db, message_id)
            request = ArchiveSubmitModel(message_ids=[message_id], storage_id='test', archive_name='first')
            first = (await TelegramArchiveService.submit(db, request, 'test'))[0]
            second = (await TelegramArchiveService.submit(db, ArchiveSubmitModel(
                message_ids=[message_id], storage_id='test', archive_name='second',
            ), 'test'))[0]
            worker = TelegramArchiveWorker()
            await worker.archive(await worker.claim('archive'))
            db.expire_all()
            assert (await db.scalar(select(TgMessageMedia))).download_status == 'ready'
            assert (await db.get(TgArchiveTask, first)).source_cleanup == 'pending'
            # Stop before the second archive's cleanup to exercise its persisted recovery.
            async def no_cleanup() -> None:
                pass

            monkeypatch.setattr(worker, 'cleanup_archives', no_cleanup)
            await worker.archive(await worker.claim('archive'))
            db.expire_all()
            task = await db.get(TgArchiveTask, second)
            private_path(task.local_path).unlink()
            await TelegramArchiveWorker().cleanup_archives()
            db.expire_all()
            assert (await db.scalar(select(TgMessageMedia))).download_status == 'ready'
            assert 'ZIP' in (await db.get(TgArchiveTask, second)).cleanup_error
            # The first, intact ZIP is sufficient once no other task needs originals.
            await db.execute(update(TgArchiveTask).where(TgArchiveTask.task_id == first).values(next_retry_at=None))
            await db.commit()
            await TelegramArchiveWorker().cleanup_archives()
            db.expire_all()
            assert (await db.scalar(select(TgMessageMedia))).download_status == 'deleted'
            assert (await db.get(TgArchiveTask, first)).source_cleanup == 'completed'

    asyncio.run(run())


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
            task = await db.get(TgArchiveTask, task_id)
            assert task.status == 'completed' and task.source_cleanup == 'none'
            assert (await db.scalar(select(TgMessageMedia))).download_status == 'ready'

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
        spec = importlib.util.spec_from_file_location('retained_zip_migration', versions / '20261114_02_media_names_retained_zip.py')
        retained = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(retained)
        retained.upgrade()
        retained.upgrade()
        assert conn.execute(text('SELECT source_cleanup, local_path FROM tg_archive_task')).one() == ('none', '')
        assert 'media_name' in {column['name'] for column in inspect(conn).get_columns('tg_message')}
        conn.execute(text("UPDATE tg_archive_task SET local_path='zips/1/old.zip'"))
        with pytest.raises(RuntimeError, match='不能回退'):
            retained.downgrade()
        conn.execute(text("UPDATE tg_archive_task SET local_path=''"))
        retained.downgrade()
        module.downgrade()
        assert 'tg_archive_message' not in inspect(conn).get_table_names()
        module.upgrade()
        conn.execute(text("UPDATE tg_archive_task SET archive_format='zip'"))
        with pytest.raises(RuntimeError, match='不能回退'):
            module.downgrade()
    engine.dispose()
