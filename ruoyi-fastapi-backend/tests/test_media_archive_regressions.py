# ruff: noqa: PLR2004, PLC0415, ASYNC240
import asyncio
import importlib.util
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import MetaData, Table, create_engine, inspect, select

sys.path.insert(0, str(Path(__file__).parents[1]))

from test_media_archive import database, ingest, ready_media, source_message

from config.env import ArchiveConfig, TransportCryptoConfig
from module_telegram.entity.do.telegram_do import (
    TgAccount,
    TgChat,
    TgForwardRecord,
    TgListenerRule,
    TgMessage,
    TgMessageMedia,
)
from module_telegram.service.archive_service import TelegramArchiveService
from module_telegram.service.archive_worker import TelegramArchiveWorker
from module_telegram.service.telegram_client_service import TelegramClientManager


class ForwardClient:
    def __init__(self, reject_sources: set[int] | None = None) -> None:
        self.reject_sources = reject_sources or set()
        self.calls: list[tuple[int, tuple[int, ...], bool]] = []
        self.download_ids: list[int] = []

    async def send_file(self, target: int, files: list[Any], caption: str) -> Any:
        local = isinstance(files[0], str)
        ids = tuple(int(Path(item).stem) if local else item.id for item in files)
        self.calls.append((target, ids, local))
        if not local and target in self.reject_sources:
            raise RuntimeError('source media expired')
        if local:
            assert all(Path(item).read_bytes() == b'abcdef' for item in files)
        return SimpleNamespace(id=500 + len(self.calls))

    async def forward_messages(self, target: int, ids: list[int], **kwargs: Any) -> Any:
        assert kwargs['drop_author'] is True
        self.calls.append((target, tuple(ids), False))
        return SimpleNamespace(id=500 + len(self.calls))

    async def get_messages(self, entity: Any, ids: int) -> Any:
        self.download_ids.append(ids)
        return source_message(ids)

    def iter_download(self, media: Any, offset: int) -> Any:
        class Stream:
            def __aiter__(self) -> Any:
                return self

            async def __anext__(self) -> bytes:
                if getattr(self, 'sent', False):
                    raise StopAsyncIteration
                self.sent = True
                return b'abcdef'[offset:]

            async def close(self) -> None:
                pass

        return Stream()


async def forward_rule(db: Any, collect: bool = True, mode: str = 'copy_clean') -> None:
    rule = await db.get(TgListenerRule, 1)
    rule.forward_enabled, rule.download_enabled = 'Y', 'Y' if collect else 'N'
    rule.target_chat_pks, rule.forward_mode = '2,3', mode
    for pk in (2, 3):
        db.add(
            TgChat(
                chat_pk=pk,
                account_id=1,
                chat_id=f'-100{pk}',
                chat_title=f'Target {pk}',
                chat_type='channel',
                can_send='Y',
            )
        )
    await db.commit()


def use_client(monkeypatch: pytest.MonkeyPatch, client: Any) -> None:
    async def get_client(*args: Any) -> Any:
        return client

    monkeypatch.setattr(TelegramClientManager, 'get_authorized_client', get_client)


@pytest.mark.parametrize('collect', [False, True])
@pytest.mark.parametrize('mode', ['copy_clean', 'native_hidden'])
def test_late_album_forwards_only_new_members_to_each_destination(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, collect: bool, mode: str
) -> None:
    client = ForwardClient()
    use_client(monkeypatch, client)
    monkeypatch.setattr(ArchiveConfig, 'archive_enabled', collect)

    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions, sessions() as db:
            await forward_rule(db, collect, mode)
            # Overlapping rules must not duplicate successful/pending destinations.
            db.add(
                TgListenerRule(
                    account_id=1,
                    source_chat_pk=1,
                    source_chat_pks='1',
                    target_chat_pks='2',
                    rule_name='Overlap',
                    forward_mode=mode,
                )
            )
            await db.commit()
            first, late = source_message(10, grouped_id=777), source_message(11, grouped_id=777)
            message_id = await ingest(db, [first])
            await ingest(db, [first])
            await ingest(db, [late])
            await ingest(db, [first, late])
            assert client.calls == [
                (-1002, (10,), False),
                (-1003, (10,), False),
                (-1002, (11,), False),
                (-1003, (11,), False),
            ]
            records = (await db.scalars(select(TgForwardRecord).order_by(TgForwardRecord.record_id))).all()
            assert [json.loads(record.source_message_ids) for record in records] == [[10], [10], [11], [11]]
            assert all(record.status == 'success' for record in records)
            assert (await db.get(TgMessage, message_id)).auto_forward_status == 'success'

    asyncio.run(run())


def test_pending_forward_survives_restart_and_uses_only_failed_targets_and_original_subset(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client = ForwardClient({-1003})
    use_client(monkeypatch, client)
    monkeypatch.setattr(ArchiveConfig, 'archive_enabled', True)

    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions:
            async with sessions() as db:
                await forward_rule(db)
                message_id = await ingest(db, [source_message(10, grouped_id=777)])
                await ingest(db, [source_message(11, grouped_id=777)])
                assert (await db.get(TgMessage, message_id)).auto_forward_status == 'pending'
            await TelegramArchiveWorker().forward_waiting()
            assert len(client.calls) == 4
            worker = TelegramArchiveWorker()
            while job := await worker.claim('download'):
                await worker.download(job)
            # New worker and sessions, with no in-memory delivery state.
            await TelegramArchiveWorker().forward_waiting()
            await TelegramArchiveWorker().forward_waiting()
            assert client.calls[4:] == [(-1003, (10,), True), (-1003, (11,), True)]
            assert client.download_ids == [10, 11]  # retry does not re-fetch source references
            async with sessions() as db:
                records = (await db.scalars(select(TgForwardRecord))).all()
                assert len(records) == 4
                assert all(record.status == 'success' for record in records)
                assert (await db.get(TgMessage, message_id)).auto_forward_status == 'success'

    asyncio.run(run())


@pytest.mark.parametrize(
    'action', ['cleanup', 'failed', 'blocked', 'rule_disabled', 'target_disabled', 'listener_stopped']
)
def test_pending_forward_obeys_cleanup_and_current_controls(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, action: str
) -> None:
    client = ForwardClient({-1003})
    use_client(monkeypatch, client)
    monkeypatch.setattr(ArchiveConfig, 'archive_enabled', True)

    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions:
            async with sessions() as db:
                await forward_rule(db)
                message_id = await ingest(db, [source_message()])
                await ready_media(db, message_id)
                if action == 'cleanup':
                    await TelegramArchiveService.cleanup_local(db, [message_id])
                elif action == 'failed':
                    (await db.scalar(select(TgMessageMedia))).download_status = 'failed'
                elif action == 'blocked':
                    message = await db.get(TgMessage, message_id)
                    message.is_sensitive, message.auto_forward_status = 'Y', 'blocked'
                elif action == 'rule_disabled':
                    (await db.get(TgListenerRule, 1)).forward_enabled = 'N'
                elif action == 'target_disabled':
                    (await db.get(TgChat, 3)).status = '1'
                else:
                    monkeypatch.setattr(TelegramClientManager, '_handlers', {})
                await db.commit()
            await TelegramArchiveWorker().forward_waiting()
            assert len(client.calls) == 2
            async with sessions() as db:
                pending = await db.scalar(select(TgForwardRecord).where(TgForwardRecord.target_chat_pk == 3))
                assert pending.status == ('waiting_download' if action == 'listener_stopped' else 'failed')
                message = await db.get(TgMessage, message_id)
                assert message.auto_forward_status == (
                    'blocked'
                    if action == 'blocked'
                    else 'pending'
                    if action == 'listener_stopped'
                    else 'partial_failed'
                )

    asyncio.run(run())


@pytest.mark.parametrize('initial_cursor', [None, 0])
def test_catchup_continues_after_bad_group_and_recovers_its_cursor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, initial_cursor: int | None
) -> None:
    visited = []
    inaccessible = True

    class Client:
        async def get_messages(self, entity: int, **kwargs: Any) -> list[Any]:
            visited.append(entity)
            if inaccessible and entity == -1001:
                raise RuntimeError('source inaccessible')
            message = source_message(20)
            message.date = datetime.now() - timedelta(minutes=1)
            return [message]

        async def iter_messages(self, entity: int, **kwargs: Any) -> Any:
            for message in await self.get_messages(entity):
                if message.id > kwargs['min_id']:
                    yield message

    use_client(monkeypatch, Client())

    async def run() -> None:
        nonlocal inaccessible
        async with database(monkeypatch, tmp_path) as sessions:
            async with sessions() as db:
                (await db.get(TgChat, 1)).collection_cursor = initial_cursor
                db.add(
                    TgChat(
                        chat_pk=2,
                        account_id=1,
                        chat_id='-1002',
                        chat_title='Healthy',
                        chat_type='group',
                        can_listen='Y',
                        collection_cursor=initial_cursor,
                    )
                )
                (await db.get(TgListenerRule, 1)).source_chat_pks = '1,2'
                await db.commit()
            await TelegramArchiveWorker().catch_up()
            async with sessions() as db:
                assert (await db.get(TgChat, 1)).collection_cursor == initial_cursor
                assert (await db.get(TgChat, 2)).collection_cursor == 20
            assert visited == [-1001, -1002]
            inaccessible = False
            await TelegramArchiveWorker().catch_up()
            async with sessions() as db:
                assert (await db.get(TgChat, 1)).collection_cursor == 20

    asyncio.run(run())


def test_catchup_continues_after_failed_account(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def get_client(account: Any) -> Any:
        if account.account_id == 1:
            raise RuntimeError('account unavailable')
        return ForwardClient()

    async def latest(self: Any, entity: Any, limit: int) -> list[Any]:
        return [source_message(30)]

    monkeypatch.setattr(TelegramClientManager, 'get_authorized_client', get_client)
    monkeypatch.setattr(ForwardClient, 'get_messages', latest)

    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions:
            monkeypatch.setattr(TelegramClientManager, '_handlers', {1: [('handler', None)], 2: [('handler', None)]})
            async with sessions() as db:
                db.add(
                    TgAccount(
                        account_id=2,
                        account_name='Second',
                        phone='second',
                        api_id=2,
                        api_hash='test',
                        session_status='authorized',
                    )
                )
                db.add(
                    TgChat(
                        chat_pk=2,
                        account_id=2,
                        chat_id='-1002',
                        chat_title='Healthy',
                        chat_type='group',
                        can_listen='Y',
                    )
                )
                db.add(
                    TgListenerRule(
                        account_id=2,
                        source_chat_pk=2,
                        source_chat_pks='2',
                        target_chat_pks='',
                        rule_name='Collect',
                        forward_enabled='N',
                        download_enabled='Y',
                    )
                )
                await db.commit()
            await TelegramArchiveWorker().catch_up()
            async with sessions() as db:
                assert (await db.get(TgChat, 1)).collection_cursor is None
                assert (await db.get(TgChat, 2)).collection_cursor == 30

    asyncio.run(run())


@pytest.mark.parametrize('mode,required_paths', [('required', ''), ('optional', '/telegram')])
def test_signed_media_bypasses_only_body_encryption_and_still_requires_ticket(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str, required_paths: str
) -> None:
    import httpx
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    from config.get_db import get_db
    from exceptions.exception import ServiceException
    from middlewares.transport_crypto_middleware import TransportCryptoMiddleware
    from module_telegram.controller.archive_controller import archive_controller

    monkeypatch.setattr(TransportCryptoConfig, 'transport_crypto_enabled', True)
    monkeypatch.setattr(TransportCryptoConfig, 'transport_crypto_mode', mode)
    monkeypatch.setattr(TransportCryptoConfig, 'transport_crypto_enabled_paths', '')
    monkeypatch.setattr(TransportCryptoConfig, 'transport_crypto_required_paths', required_paths)
    monkeypatch.setattr(TransportCryptoConfig, 'transport_crypto_exclude_paths', '')

    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions:
            async with sessions() as db:
                message_id = await ingest(db, [source_message()])
                await ready_media(db, message_id)
                media_id = await db.scalar(select(TgMessageMedia.media_id))
            app = FastAPI()
            app.include_router(archive_controller)
            app.add_middleware(TransportCryptoMiddleware)

            async def dependency() -> Any:
                async with sessions() as db:
                    yield db

            async def denied(request: Any, exc: Any) -> JSONResponse:
                return JSONResponse({'message': exc.message}, status_code=403)

            app.dependency_overrides[get_db] = dependency
            app.add_exception_handler(ServiceException, denied)
            ticket = TelegramArchiveService.preview_ticket(media_id)
            path = f'/telegram/archive/media/{media_id}/content'
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
                response = await client.get(path, params={'ticket': ticket}, headers={'Range': 'bytes=1-3'})
                assert response.status_code == 206
                assert response.content == b'bcd'
                assert (await client.head(path, params={'ticket': ticket})).status_code == 200
                assert (await client.get(path)).status_code == 422
                assert (await client.get(path, params={'ticket': ticket + 'invalid'})).status_code == 403
                assert (
                    await client.get(path.replace(f'/{media_id}/', f'/{media_id + 1}/'), params={'ticket': ticket})
                ).status_code == 403
                assert (await client.post(path, params={'ticket': ticket})).status_code == 400
                assert (await client.get(path + '/extra')).status_code == 400
                assert (await client.get(path.replace('/content', '/ticket'))).status_code == 400
                assert (await client.get('/telegram/archive/library')).status_code == 400

    asyncio.run(run())


def test_delivery_migration_preserves_existing_records_and_roundtrips(tmp_path: Path) -> None:
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    engine = create_engine(f'sqlite:///{tmp_path / "migration.db"}')
    versions = Path(__file__).parents[1] / 'alembic' / 'versions'
    modules = []
    for filename in (
        '20260430_01_create_telegram_forwardbot_tables.py',
        '20261111_01_add_media_archive.py',
        '20261111_02_add_forward_source_ids.py',
    ):
        path = versions / filename
        spec = importlib.util.spec_from_file_location(path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        modules.append(module)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        for module in modules[:2]:
            module.upgrade()
        old = Table('tg_forward_record', MetaData(), autoload_with=conn)
        conn.execute(
            old.insert().values(
                record_id=1, account_id=1, target_chat_id='-1002', forward_type='auto', status='success'
            )
        )
        migration = modules[-1]
        migration.upgrade()
        migration.upgrade()
        current = Table('tg_forward_record', MetaData(), autoload_with=conn)
        assert conn.execute(select(current.c.status, current.c.source_message_ids)).one() == ('success', None)
        assert 'ix_tg_forward_record_status' in {
            index['name'] for index in inspect(conn).get_indexes('tg_forward_record')
        }
        migration.downgrade()
        assert 'source_message_ids' not in {column['name'] for column in inspect(conn).get_columns('tg_forward_record')}
        assert conn.scalar(select(old.c.status)) == 'success'
        migration.upgrade()
    engine.dispose()
