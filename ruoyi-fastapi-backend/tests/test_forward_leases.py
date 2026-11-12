# ruff: noqa: PLR2004, PLC0415, ASYNC240, PLR0915
# Keep the renewal/restart/stale-completion lifecycle in a single regression case.
import asyncio
import importlib.util
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import MetaData, Table, create_engine, inspect, select
from sqlalchemy.ext.asyncio import create_async_engine

sys.path.insert(0, str(Path(__file__).parents[1]))

import test_media_archive as fixtures
from test_media_archive import database, ready_media, source_message
from test_media_archive import ingest_and_dispatch as ingest
from test_media_archive_regressions import ForwardClient, forward_rule, use_client

from config.env import ArchiveConfig
from exceptions.exception import ServiceException
from module_telegram.entity.do.telegram_do import TgForwardRecord, TgMessage, TgMessageMedia
from module_telegram.service.archive_service import TelegramArchiveService
from module_telegram.service.archive_worker import TelegramArchiveWorker, row_values
from module_telegram.service.telegram_rule_service import ForwardDispatchResult
from module_telegram.service.telegram_service import TelegramMediaCleanupService


def cached_source(message_id: int = 10, mime: str = 'application/pdf', grouped_id: int | None = None) -> Any:
    source = source_message(message_id, grouped_id=grouped_id)
    source.photo = None
    source.file.mime_type = mime

    async def download_media(file: str) -> str:
        Path(file).write_bytes(b'abcdef')
        return file

    source.download_media = download_media
    return source


class SlowClient(ForwardClient):
    def __init__(self) -> None:
        super().__init__({-1003})
        self.sending, self.release = asyncio.Event(), asyncio.Event()

    async def send_file(self, target: int, files: list[Any], caption: str) -> Any:
        if isinstance(files[0], str):
            self.sending.set()
            await self.release.wait()
        return await super().send_file(target, files, caption)


@pytest.mark.parametrize('collect', [False, True])
@pytest.mark.parametrize('mime', ['application/pdf', 'audio/mpeg'])
def test_unsupported_collection_types_keep_forwarding_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    collect: bool,
    mime: str,
) -> None:
    client = ForwardClient({-1003})
    use_client(monkeypatch, client)
    monkeypatch.setattr(ArchiveConfig, 'archive_enabled', True)
    monkeypatch.chdir(tmp_path)

    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions, sessions() as db:
            await forward_rule(db, collect)
            await ingest(db, [cached_source(mime=mime)])
            record = await db.scalar(select(TgForwardRecord).where(TgForwardRecord.target_chat_pk == 3))
            media = await db.scalar(select(TgMessageMedia))
            assert record.status == 'success'
            assert media.storage_kind == 'cache' and media.local_path
            assert media.download_status == ('skipped' if collect else 'disabled')
            assert client.calls[-1] == (-1003, (10,), True)

    asyncio.run(run())


def test_mixed_album_keeps_cached_files_until_pending_send_finishes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(ArchiveConfig, 'archive_enabled', True)
    monkeypatch.chdir(tmp_path)

    async def run() -> None:
        client = SlowClient()
        use_client(monkeypatch, client)
        async with database(monkeypatch, tmp_path) as sessions:
            async with sessions() as db:
                await forward_rule(db)
                message_id = await ingest(db, [source_message(10, grouped_id=777), cached_source(11, grouped_id=777)])
                cached = await db.scalar(select(TgMessageMedia).where(TgMessageMedia.storage_kind == 'cache'))
                cached.create_time = datetime.now() - timedelta(days=30)
                await db.commit()
                result = await TelegramMediaCleanupService.cleanup_expired_local_files(db)
                assert result.skipped_count == 1 and result.deleted_count == 0
            worker = TelegramArchiveWorker()
            await worker.download(await worker.claim('download'))
            sending = asyncio.create_task(worker.deliver_pending())
            try:
                await asyncio.wait_for(client.sending.wait(), 2)
                async with sessions() as db:
                    result = await TelegramMediaCleanupService.cleanup_expired_local_files(db)
                    assert result.skipped_count == 1 and result.deleted_count == 0
            finally:
                client.release.set()
                await asyncio.wait_for(sending, 2)
            async with sessions() as db:
                record = await db.scalar(select(TgForwardRecord).where(TgForwardRecord.target_chat_pk == 3))
                assert record.status == 'success'
                assert (await db.get(TgMessage, message_id)).auto_forward_status == 'success'
                result = await TelegramMediaCleanupService.cleanup_expired_local_files(db)
                assert result.deleted_count == 1
            assert client.calls[-1] == (-1003, (10, 11), True)

    asyncio.run(run())


def test_forward_heartbeat_restart_and_stale_owner_are_fenced(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(ArchiveConfig, 'archive_enabled', True)
    monkeypatch.setattr(ArchiveConfig, 'archive_lease_seconds', 3)

    async def run() -> None:
        client = SlowClient()
        use_client(monkeypatch, client)
        async with database(monkeypatch, tmp_path) as sessions:
            async with sessions() as db:
                await forward_rule(db)
                message_id = await ingest(db, [source_message()])
                await ready_media(db, message_id)
            worker = TelegramArchiveWorker()
            sending = asyncio.create_task(worker.deliver_pending())
            try:
                await asyncio.wait_for(client.sending.wait(), 2)
                async with sessions() as db:
                    record = await db.scalar(select(TgForwardRecord).where(TgForwardRecord.status == 'sending'))
                    old_job = row_values(record)
                # Another worker must not dispatch a live lease.
                await TelegramArchiveWorker().deliver_pending()
                assert len(client.calls) == 2
                await asyncio.sleep(1.2)
                async with sessions() as db:
                    record = await db.get(TgForwardRecord, old_job['record_id'])
                    assert record.lease_until > old_job['lease_until']
            finally:
                sending.cancel()
                await asyncio.gather(sending, return_exceptions=True)
            async with sessions() as db:
                record = await db.get(TgForwardRecord, old_job['record_id'])
                assert record.status == 'sending'
                record.lease_until = datetime.now() - timedelta(seconds=1)
                await db.commit()
            assert not await worker._update('forward', old_job, lease_until=datetime.now() + timedelta(minutes=1))
            client.sending.clear()
            restarted = asyncio.create_task(TelegramArchiveWorker().deliver_pending())
            try:
                await asyncio.wait_for(client.sending.wait(), 2)
                assert not await worker._update('forward', old_job, status='failed')
                await worker._complete_delivery(
                    old_job,
                    ForwardDispatchResult(message_id, '-1003', 'auto', 'success', 123),
                    {},
                )
                async with sessions() as db:
                    record = await db.get(TgForwardRecord, old_job['record_id'])
                    assert record.status == 'sending'
                    assert record.lease_token != old_job['lease_token']
                    assert record.sent_telegram_message_id is None
            finally:
                client.release.set()
                await asyncio.wait_for(restarted, 2)
            async with sessions() as db:
                record = await db.get(TgForwardRecord, old_job['record_id'])
                assert record.status == 'success'
                assert record.lease_token is None and record.lease_until is None
            assert client.calls[2:] == [(-1003, (10,), True)]

    asyncio.run(run())


@pytest.mark.parametrize('failure', ['stolen', 'database_error'])
def test_forward_stops_if_heartbeat_cannot_renew(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure: str,
) -> None:
    monkeypatch.setattr(ArchiveConfig, 'archive_enabled', True)
    monkeypatch.setattr(ArchiveConfig, 'archive_lease_seconds', 3)

    async def run() -> None:
        client = SlowClient()
        use_client(monkeypatch, client)
        async with database(monkeypatch, tmp_path) as sessions:
            async with sessions() as db:
                await forward_rule(db)
                message_id = await ingest(db, [source_message()])
                await ready_media(db, message_id)
            worker = TelegramArchiveWorker()
            sending = asyncio.create_task(worker.deliver_pending())
            try:
                await asyncio.wait_for(client.sending.wait(), 2)
                async with sessions() as db:
                    record = await db.scalar(select(TgForwardRecord).where(TgForwardRecord.status == 'sending'))
                    record_id = record.record_id
                    if failure == 'stolen':
                        record.lease_token = 'new-owner'
                        await db.commit()
                    else:

                        async def unavailable(*args: Any, **kwargs: Any) -> bool:
                            raise RuntimeError('database unavailable')

                        monkeypatch.setattr(worker, '_update', unavailable)
                await asyncio.wait_for(sending, 2)
                assert client.calls == [(-1002, (10,), False), (-1003, (10,), False)]
                async with sessions() as db:
                    record = await db.get(TgForwardRecord, record_id)
                    assert record.status == 'sending' and record.sent_telegram_message_id is None
                    if failure == 'stolen':
                        assert record.lease_token == 'new-owner'
            finally:
                sending.cancel()
                await asyncio.gather(sending, return_exceptions=True)

    asyncio.run(run())


@pytest.fixture
def temporary_pg() -> Any:
    initdb, pg_ctl = shutil.which('initdb'), shutil.which('pg_ctl')
    if not initdb or not pg_ctl:
        pytest.skip('PostgreSQL binaries required for row-lock regression')
    with tempfile.TemporaryDirectory(prefix='forwardbot-test-pg-') as directory:
        root = Path(directory)
        data, socket = root / 'data', root / 'socket'
        socket.mkdir()
        subprocess.run(
            [initdb, '-D', str(data), '-A', 'trust', '--no-locale', '-E', 'UTF8', '-U', 'audit'],
            check=True,
            capture_output=True,
        )
        options = f"-k '{socket}' -p 55439 -c listen_addresses= -c fsync=off"
        subprocess.run(
            [pg_ctl, '-D', str(data), '-l', str(root / 'postgres.log'), '-o', options, '-w', 'start'],
            check=True,
            capture_output=True,
        )
        try:
            yield f'postgresql+asyncpg://audit@/postgres?host={socket}&port=55439'
        finally:
            subprocess.run([pg_ctl, '-D', str(data), '-m', 'immediate', '-w', 'stop'], check=True, capture_output=True)


def test_slow_forward_releases_database_locks_but_protects_local_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    temporary_pg: str,
) -> None:
    monkeypatch.setattr(fixtures, 'create_async_engine', lambda _: create_async_engine(temporary_pg))
    monkeypatch.setattr(ArchiveConfig, 'archive_enabled', True)

    async def run() -> None:
        client = SlowClient()
        use_client(monkeypatch, client)
        async with database(monkeypatch, tmp_path) as sessions:
            async with sessions() as db:
                await forward_rule(db)
                first = await ingest(db, [source_message(10, grouped_id=777)])
                await ready_media(db, first)
            worker = TelegramArchiveWorker()
            sending = asyncio.create_task(worker.deliver_pending())
            try:
                await asyncio.wait_for(client.sending.wait(), 2)
                assert sessions.kw['bind'].sync_engine.pool.checkedout() == 0
                async with sessions() as db:
                    with pytest.raises(ServiceException) as error:
                        await asyncio.wait_for(TelegramArchiveService.cleanup_local(db, [first]), 2)
                    assert '暂不能清理' in error.value.message
                async with sessions() as db:
                    # Ingest a late member and a duplicate while the first is sending.
                    await asyncio.wait_for(
                        ingest(db, [source_message(10, grouped_id=777), source_message(11, grouped_id=777)]), 2
                    )
                    other = await ingest(db, [source_message(20)])
                for message_id in (first, other):
                    job = await asyncio.wait_for(worker.claim('download'), 2)
                    assert job['message_id'] == message_id
                    await asyncio.wait_for(worker.download(job), 2)
                assert not sending.done()
            finally:
                client.release.set()
                await asyncio.wait_for(sending, 2)
            await worker.deliver_pending()
            async with sessions() as db:
                records = (await db.scalars(select(TgForwardRecord))).all()
                assert len(records) == 6 and all(record.status == 'success' for record in records)
                result = await TelegramArchiveService.cleanup_local(db, [first, other])
                assert result['freedBytes'] == 18 and not result['errors']
            assert client.calls.count((-1003, (10,), True)) == 1

    asyncio.run(run())


def test_forward_lease_migration_repairs_only_empty_skipped_archive_paths(tmp_path: Path) -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = create_engine(f'sqlite:///{tmp_path / "migration.db"}')
    versions = Path(__file__).parents[1] / 'alembic' / 'versions'
    modules = []
    for filename in (
        '20260430_01_create_telegram_forwardbot_tables.py',
        '20261111_01_add_media_archive.py',
        '20261111_02_add_forward_source_ids.py',
        '20261111_03_add_forward_lease.py',
    ):
        path = versions / filename
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        for module in modules[:-1]:
            module.upgrade()
        media = Table('tg_message_media', MetaData(), autoload_with=conn)
        records = Table('tg_forward_record', MetaData(), autoload_with=conn)
        conn.execute(
            records.insert().values(
                account_id=1, target_chat_id='-1003', forward_type='auto', status='waiting_download'
            )
        )
        for status, path in [('skipped', ''), ('skipped', 'existing.pdf'), ('ready', '10.jpg')]:
            conn.execute(
                media.insert().values(
                    message_id=1, media_type='document', storage_kind='archive', download_status=status, local_path=path
                )
            )
        migration = modules[-1]
        migration.upgrade()
        migration.upgrade()
        assert conn.execute(select(media.c.storage_kind, media.c.local_path).order_by(media.c.media_id)).all() == [
            ('cache', ''),
            ('archive', 'existing.pdf'),
            ('archive', '10.jpg'),
        ]
        current = Table('tg_forward_record', MetaData(), autoload_with=conn)
        assert conn.execute(select(current.c.status, current.c.lease_token, current.c.lease_until)).one() == (
            'waiting_download',
            None,
            None,
        )
        conn.execute(current.update().values(status='sending', lease_token='interrupted', lease_until=datetime.now()))
        migration.downgrade()
        assert conn.scalar(select(records.c.status)) == 'waiting_download'
        assert 'lease_token' not in {column['name'] for column in inspect(conn).get_columns('tg_forward_record')}
        migration.upgrade()
    engine.dispose()
