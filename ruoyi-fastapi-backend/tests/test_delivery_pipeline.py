# ruff: noqa: PLR2004, PLC0415, F811
"""Observable contracts of the unified outbox and actual media-I/O lifecycle."""

import asyncio
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import test_media_archive as fixtures
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine
from test_forward_leases import SlowClient, temporary_pg  # noqa: F401 - pytest fixture
from test_media_archive import database, ingest, ready_media, source_message
from test_media_archive_regressions import ForwardClient, forward_rule, use_client

from config.env import ArchiveConfig
from exceptions.exception import ServiceException
from module_telegram.entity.do.archive_do import TgArchiveTask
from module_telegram.entity.do.telegram_do import (
    TgAdText,
    TgChat,
    TgContentCleanRule,
    TgForwardRecord,
    TgListenerRule,
    TgMessage,
    TgMessageMedia,
)
from module_telegram.entity.vo.archive_vo import ArchiveSubmitModel
from module_telegram.entity.vo.telegram_vo import TgManualForwardModel
from module_telegram.service.archive_service import TelegramArchiveService
from module_telegram.service.archive_worker import TelegramArchiveWorker
from module_telegram.service.media_runtime import media_runtime
from module_telegram.service.telegram_client_service import TelegramClientManager
from module_telegram.service.telegram_service import TelegramCrudService


@pytest.mark.parametrize(
    'scenario',
    [
        'references',
        'local',
        'legacy',
        'missing',
        'partial',
        'partial_local',
        'cache',
        'native',
        'native_failure',
        'native_missing_ids',
    ],
)
def test_manual_delivery_preserves_media_and_mode_contracts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scenario: str,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ArchiveConfig, 'archive_enabled', True)

    class Client(ForwardClient):
        async def get_messages(self, entity: Any, ids: Any) -> Any:
            if scenario.startswith('native'):
                pytest.fail('native forwarding must not download or reload references')
            if scenario in {'local', 'missing', 'legacy'}:
                raise RuntimeError('source unavailable')
            return await super().get_messages(entity, ids[:1] if scenario.startswith('partial') else ids)

        async def forward_messages(self, target: int, ids: list[int], **kwargs: Any) -> Any:
            if scenario == 'native_failure':
                raise RuntimeError('target rejected')
            return await super().forward_messages(target, ids, **kwargs)

    client = Client({-1003} if scenario == 'cache' else set())
    use_client(monkeypatch, client)

    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions:
            async with sessions() as db:
                await forward_rule(db, collect=scenario != 'cache')
                (await db.get(TgListenerRule, 1)).forward_enabled = 'N'
                await db.commit()
                message_id = await ingest(db, [source_message(10, grouped_id=777), source_message(11, grouped_id=777)])
                if scenario in {'local', 'partial_local', 'legacy'}:
                    await ready_media(db, message_id)
                if scenario in {'legacy', 'native_missing_ids'}:
                    for media in await db.scalars(select(TgMessageMedia)):
                        media.source_telegram_message_id = None
                    await db.commit()
                result = await TelegramCrudService.manual_forward(
                    db,
                    TgManualForwardModel(
                        message_id=message_id,
                        target_chat_pks=[3],
                        forward_mode='native_hidden' if scenario.startswith('native') else 'copy_clean',
                    ),
                )
                assert '队列' in result.message and not client.calls
            # Manual tasks do not depend on the account's listener being started.
            monkeypatch.setattr(TelegramClientManager, '_handlers', {})
            await TelegramArchiveWorker().deliver_pending()
            async with sessions() as db:
                record = await db.scalar(select(TgForwardRecord))
                expected = (
                    'failed'
                    if scenario in {'missing', 'partial', 'native_failure', 'native_missing_ids'}
                    else 'success'
                )
                # Undownloaded collection files wait for their worker.
                if scenario in {'missing', 'partial'}:
                    expected = 'waiting_download'
                assert record.status == expected
                assert (await db.get(TgMessage, message_id)).auto_forward_status == 'disabled'
                if scenario in {'missing', 'partial', 'native_missing_ids'}:
                    assert not client.calls
                elif expected == 'success':
                    assert client.calls[-1][1] == (10, 11)
                    assert client.calls[-1][2] == (scenario in {'local', 'legacy', 'partial_local', 'cache'})

    asyncio.run(run())


def test_cleaning_and_target_ads_use_same_worker_for_text_and_media(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen = []

    class Client(ForwardClient):
        async def send_file(self, target: int, files: list[Any], caption: str) -> Any:
            seen.append((target, caption))
            return await super().send_file(target, files, caption)

        async def send_message(self, target: int, text: str, **kwargs: Any) -> Any:
            seen.append((target, text))
            return source_message(999)

    use_client(monkeypatch, Client())

    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions, sessions() as db:
            await forward_rule(db)
            db.add(TgAdText(ad_id=1, ad_name='default', ad_content='DEFAULT', enabled='1'))
            db.add(TgAdText(ad_id=2, ad_name='target', ad_content='TARGET', enabled='0'))
            db.add(TgContentCleanRule(clean_name='remove', match_text='REMOVE', replacement='', status='0'))
            (await db.get(TgChat, 3)).ad_text_id = 2
            await db.commit()
            await ingest(db, [source_message(10, 'helloREMOVE')])
            text_message = source_message(20, 'helloREMOVE')
            text_message.media = None
            await ingest(db, [text_message])
        await TelegramArchiveWorker().deliver_pending()
        assert seen == [(-1002, 'hello\n\n\nDEFAULT'), (-1003, 'hello\n\n\nTARGET')] * 2

    asyncio.run(run())


@pytest.mark.parametrize('trigger', ['auto', 'manual'])
def test_ingest_and_manual_enqueue_release_connections_before_slow_send(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    temporary_pg: str,
    trigger: str,
) -> None:
    monkeypatch.setattr(fixtures, 'create_async_engine', lambda _: create_async_engine(temporary_pg))
    monkeypatch.setattr(ArchiveConfig, 'archive_enabled', True)

    async def run() -> None:
        client = SlowClient()
        use_client(monkeypatch, client)
        async with database(monkeypatch, tmp_path) as sessions:
            async with sessions() as db:
                await forward_rule(db)
                if trigger == 'manual':
                    (await db.get(TgListenerRule, 1)).forward_enabled = 'N'
                    await db.commit()
                first = await ingest(db, [source_message(10, grouped_id=777)])
                await ready_media(db, first)
                if trigger == 'manual':
                    await TelegramCrudService.manual_forward(
                        db,
                        TgManualForwardModel(
                            message_id=first,
                            target_chat_pks=[3],
                            forward_mode='copy_clean',
                        ),
                    )
                assert not client.calls
            worker = TelegramArchiveWorker()
            sending = asyncio.create_task(worker.deliver_pending())
            try:
                await asyncio.wait_for(client.sending.wait(), 2)
                assert sessions.kw['bind'].sync_engine.pool.checkedout() == 0
                async with sessions() as db:
                    # Network send is slow, but both real-time ingest paths commit promptly.
                    await asyncio.wait_for(ingest(db, [source_message(11, grouped_id=777)]), 1)
                    other = await asyncio.wait_for(ingest(db, [source_message(20)]), 1)
                for expected in (first, other):
                    job = await asyncio.wait_for(worker.claim('download'), 1)
                    assert job['message_id'] == expected
                    await worker.download(job)
                async with sessions() as db:
                    with pytest.raises(ServiceException):
                        await asyncio.wait_for(TelegramArchiveService.cleanup_local(db, [first]), 1)
            finally:
                client.release.set()
                await asyncio.wait_for(sending, 3)
            await worker.deliver_pending()
            async with sessions() as db:
                assert all(row.status == 'success' for row in await db.scalars(select(TgForwardRecord)))
                assert (await TelegramArchiveService.cleanup_local(db, [first, other]))['freedBytes'] == 18

    asyncio.run(run())


def test_outbox_migration_preserves_recovery_phase_and_stops_pending_on_rollback(tmp_path: Path) -> None:
    import importlib.util

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import MetaData, Table, create_engine, inspect

    engine = create_engine(f'sqlite:///{tmp_path / "migration.db"}')
    versions = Path(__file__).parents[1] / 'alembic' / 'versions'
    modules = []
    for name in (
        '20260430_01_create_telegram_forwardbot_tables.py',
        '20261111_01_add_media_archive.py',
        '20261111_02_add_forward_source_ids.py',
        '20261111_03_add_forward_lease.py',
        '20261112_01_unify_delivery_jobs.py',
    ):
        spec = importlib.util.spec_from_file_location(name, versions / name)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        for module in modules[:-1]:
            module.upgrade()
        old = Table('tg_forward_record', MetaData(), autoload_with=conn)
        for status in ('success', 'waiting_download', 'sending'):
            conn.execute(old.insert().values(account_id=1, target_chat_id='-1003', forward_type='auto', status=status))
        migration = modules[-1]
        migration.upgrade()
        migration.upgrade()
        current = Table('tg_forward_record', MetaData(), autoload_with=conn)
        assert conn.execute(select(current.c.status, current.c.delivery_stage).order_by(current.c.record_id)).all() == [
            ('success', 'source'),
            ('waiting_download', 'local'),
            ('sending', 'local'),
        ]
        conn.execute(
            current.insert().values(
                account_id=1,
                target_chat_id='-1003',
                forward_type='manual',
                forward_mode='native_hidden',
                status='queued',
            )
        )
        migration.downgrade()
        assert list(conn.scalars(select(old.c.status).order_by(old.c.record_id))) == [
            'success',
            'failed',
            'failed',
            'failed',
        ]
        assert 'delivery_stage' not in {column['name'] for column in inspect(conn).get_columns('tg_forward_record')}
        migration.upgrade()
    engine.dispose()


def test_outbox_rolls_back_with_message_and_deduplicates_before_send(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from types import SimpleNamespace

    from module_telegram.entity.do.telegram_do import TgAccount
    from module_telegram.service.telegram_service import TelegramMessageIngestService

    client = ForwardClient()
    use_client(monkeypatch, client)

    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions:
            async with sessions() as db:
                await forward_rule(db)
                account, chat = await db.get(TgAccount, 1), await db.get(TgChat, 1)
                await TelegramMessageIngestService.ingest_album(
                    db, account, chat, SimpleNamespace(messages=[source_message()])
                )
                await db.rollback()
                assert await db.scalar(select(TgMessage)) is None
                assert await db.scalar(select(TgForwardRecord)) is None
                await ingest(db, [source_message()])
                await ingest(db, [source_message()])
                assert len((await db.scalars(select(TgForwardRecord))).all()) == 2
                assert not client.calls
            await TelegramArchiveWorker().deliver_pending()
            assert len(client.calls) == 2

    asyncio.run(run())


def test_disabling_collection_keeps_persistent_delivery_running(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(ArchiveConfig, 'archive_enabled', False)
    client = ForwardClient()
    use_client(monkeypatch, client)

    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions:
            async with sessions() as db:
                await forward_rule(db, collect=False)
                await ingest(db, [source_message()])
            worker = TelegramArchiveWorker()
            worker.start()
            try:
                assert len(worker.tasks) == 1

                async def delivered() -> None:
                    while True:
                        async with sessions() as db:
                            states = list(await db.scalars(select(TgForwardRecord.status)))
                        if states == ['success', 'success']:
                            return
                        await asyncio.sleep(0.01)

                await asyncio.wait_for(delivered(), 2)
                assert client.calls == [(-1002, (10,), False), (-1003, (10,), False)]
            finally:
                await worker.stop()

    asyncio.run(run())


def test_cancelled_upload_keeps_ownership_until_real_operation_exits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(ArchiveConfig, 'archive_lease_seconds', 1)

    async def run() -> None:
        release = threading.Event()
        started = asyncio.Event()
        loop = asyncio.get_running_loop()
        uploads = []

        class Storage:
            def ensure_dir(self, path: str) -> str:
                return path

            def upload(self, source: Path, directory: str, name: str, checksum: str) -> str:
                uploads.append(name)
                loop.call_soon_threadsafe(started.set)
                assert release.wait(10)
                return name

        monkeypatch.setattr('module_telegram.service.archive_worker.get_storage', lambda _: Storage())
        async with database(monkeypatch, tmp_path) as sessions:
            async with sessions() as db:
                message_id = await ingest(db, [source_message()])
                await ready_media(db, message_id)
                await TelegramArchiveService.submit(
                    db, ArchiveSubmitModel(message_ids=[message_id], storage_id='test'), 'test'
                )
            worker = TelegramArchiveWorker()
            job = await worker.claim('archive')
            failed = asyncio.Event()

            async def unavailable(*args: Any, **kwargs: Any) -> bool:
                failed.set()
                raise RuntimeError('database unavailable')

            monkeypatch.setattr(worker, '_update', unavailable)
            sending = asyncio.create_task(
                worker._run_with_heartbeat('archive', job, asyncio.create_task(worker.archive(job)))
            )
            try:
                await asyncio.wait_for(started.wait(), 2)
                await asyncio.wait_for(failed.wait(), 2)
                sending.cancel()  # A second cancellation must not detach the thread.
                await asyncio.sleep(0)
                assert not sending.done() and media_runtime.busy(message_id)
                assert await TelegramArchiveWorker().claim('archive') is None
                async with sessions() as db:
                    with pytest.raises(ServiceException):
                        await TelegramArchiveService.cleanup_local(db, [message_id])
                assert uploads == ['10.jpg']
            finally:
                release.set()
                await asyncio.gather(sending, return_exceptions=True)
            assert not media_runtime.busy(message_id)
            async with sessions() as db:
                task = await db.get(TgArchiveTask, job['task_id'])
                assert task.status == 'uploading'
                task.lease_until = datetime.now() - timedelta(seconds=1)
                await db.commit()
            restarted = TelegramArchiveWorker()
            await restarted.archive(await restarted.claim('archive'))
            async with sessions() as db:
                assert (await db.get(TgArchiveTask, job['task_id'])).status == 'completed'

    asyncio.run(run())
