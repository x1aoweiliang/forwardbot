import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from module_telegram.entity.do.telegram_do import TgAccount
from module_telegram.entity.vo.telegram_vo import TgChatSendMessageModel
from module_telegram.service.telegram_client_service import TelegramClientManager
from module_telegram.service.telegram_rule_service import (
    AdTextPolicy,
    ContentCleanPolicy,
    ForwardDispatcher,
    SensitiveWordMatcher,
    TelegramStorageService,
)
from module_telegram.service.telegram_service import TelegramCrudService, TelegramForwardService, TelegramMessageIngestService


def test_sensitive_word_matcher_returns_first_enabled_hit() -> None:
    matcher = SensitiveWordMatcher(['spam', '诈骗', '忽略'])

    result = matcher.match('这是一条包含诈骗内容的消息')

    assert result.is_blocked is True
    assert result.hit_word == '诈骗'


def test_sensitive_word_matcher_ignores_empty_words() -> None:
    matcher = SensitiveWordMatcher(['', '   ', 'spam'])

    result = matcher.match('normal message')

    assert result.is_blocked is False
    assert result.hit_word is None


def test_sensitive_word_matcher_respects_per_word_match_case() -> None:
    matcher = SensitiveWordMatcher(
        [
            SimpleNamespace(word='Spam', match_case='Y'),
            SimpleNamespace(word='诈骗', match_case='N'),
        ]
    )

    assert matcher.match('contains spam').is_blocked is False
    assert matcher.match('contains Spam').hit_word == 'Spam'
    assert matcher.match('这是诈骗内容').hit_word == '诈骗'


def test_account_detail_sanitizer_hides_api_hash_and_session_path() -> None:
    account = TgAccount(
        account_name='test',
        phone='+10000000000',
        api_id=1,
        api_hash='real-api-hash',
        session_path='vf_admin/telegram_sessions/account_1',
    )

    sanitized = TelegramCrudService.sanitize_account_result(account)

    assert sanitized.api_hash == '********'
    assert sanitized.session_path is None


def test_ad_text_policy_allows_only_one_enabled_ad() -> None:
    enabled = SimpleNamespace(ad_id=1, enabled='1')
    disabled = SimpleNamespace(ad_id=2, enabled='0')

    assert AdTextPolicy.validate_single_enabled([enabled, disabled]) is True

    with pytest.raises(ValueError, match='只能启用一个广告词'):
        AdTextPolicy.validate_single_enabled([enabled, SimpleNamespace(ad_id=3, enabled='1')])


def test_ad_text_policy_separates_ad_with_two_blank_lines() -> None:
    assert AdTextPolicy.append_ad_text('hello', 'ad') == 'hello\n\n\nad'


def test_content_clean_policy_removes_configured_text_before_send() -> None:
    rules = [
        SimpleNamespace(
            match_text='关注大事件频道➡️ @bx666 投稿：@tx188',
            replacement='',
            match_case='Y',
        )
    ]

    result = ContentCleanPolicy.apply('原始消息\n关注大事件频道➡️ @bx666 投稿：@tx188', rules)

    assert result == '原始消息'


def test_forward_dispatcher_records_failure_without_blocking_other_targets() -> None:
    async def run_case() -> None:
        sent_targets = []

        class FakeClient:
            async def send_message(self, target_chat_id: str, text: str, link_preview: bool = False) -> SimpleNamespace:
                if target_chat_id == 'bad':
                    raise RuntimeError('no write permission')
                sent_targets.append((target_chat_id, text, link_preview))
                return SimpleNamespace(id=99)

        dispatcher = ForwardDispatcher(FakeClient())

        results = await dispatcher.dispatch_text(
            message_id=10,
            target_chat_ids=['good-a', 'bad', 'good-b'],
            text='hello',
            ad_text='ad',
            forward_type='auto',
        )

        assert [result.target_chat_id for result in results] == ['good-a', 'bad', 'good-b']
        assert [result.status for result in results] == ['success', 'failed', 'success']
        assert results[1].error_message == 'no write permission'
        assert sent_targets == [('good-a', 'hello\n\n\nad', False), ('good-b', 'hello\n\n\nad', False)]

    asyncio.run(run_case())


def test_ingest_event_blocks_sensitive_message_before_forward(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_case() -> None:
        forward_calls = []
        stored_messages = []

        class FakeDb:
            async def flush(self) -> None:
                return None

        class FakeStorage:
            async def save_message_media(self, message: object, account_id: int, message_id: int) -> None:
                return None

            async def save_event_media(self, event: object, account_id: int, message_id: int) -> None:
                return None

        async def fake_get_enabled_words(db: object) -> list:
            return [SimpleNamespace(word='spam', match_case='N')]

        async def fake_add_message(db: object, data: dict) -> SimpleNamespace:
            stored_messages.append(data)
            return SimpleNamespace(message_id=100, **data)

        async def fake_forward_to_chats(*args: object, **kwargs: object) -> None:
            forward_calls.append((args, kwargs))

        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_enabled_words', fake_get_enabled_words)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.add_message', fake_add_message)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramStorageService', lambda: FakeStorage())
        monkeypatch.setattr(TelegramForwardService, 'forward_to_chats', classmethod(fake_forward_to_chats))

        event = SimpleNamespace(message=SimpleNamespace(id=1, message='contains spam', text='contains spam', date=None), media=None)
        result = await TelegramMessageIngestService.ingest_event(
            FakeDb(),
            SimpleNamespace(account_id=1),
            SimpleNamespace(chat_pk=2, chat_id='source', chat_title='Source'),
            event,
        )

        assert result.auto_forward_status == 'blocked'
        assert stored_messages[0]['is_sensitive'] == 'Y'
        assert stored_messages[0]['sensitive_word'] == 'spam'
        assert forward_calls == []

    asyncio.run(run_case())


def test_ingest_event_normalizes_aware_sent_at(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_case() -> None:
        stored_messages = []

        class FakeDb:
            async def flush(self) -> None:
                return None

        class FakeStorage:
            async def save_message_media(self, message: object, account_id: int, message_id: int) -> None:
                return None

            async def save_event_media(self, event: object, account_id: int, message_id: int) -> None:
                return None

        async def fake_get_enabled_words(db: object) -> list:
            return []

        async def fake_add_message(db: object, data: dict) -> SimpleNamespace:
            stored_messages.append(data)
            return SimpleNamespace(message_id=101, **data)

        async def fake_get_rules(db: object, account_id: int) -> list:
            return []

        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_enabled_words', fake_get_enabled_words)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.add_message', fake_add_message)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_enabled_rules_for_account', fake_get_rules)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramStorageService', lambda: FakeStorage())

        event = SimpleNamespace(
            message=SimpleNamespace(id=2, message='normal', text='normal', date=datetime(2026, 5, 2, 10, 0, tzinfo=UTC)),
            media=None,
        )
        await TelegramMessageIngestService.ingest_event(
            FakeDb(),
            SimpleNamespace(account_id=1),
            SimpleNamespace(chat_pk=2, chat_id='source', chat_title='Source'),
            event,
        )

        assert stored_messages[0]['sent_at'].tzinfo is None

    asyncio.run(run_case())


def test_ingest_album_stores_multiple_media_under_one_message(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_case() -> None:
        stored_messages = []
        stored_medias = []
        forward_calls = []

        class FakeDb:
            async def flush(self) -> None:
                return None

        class FakeStorage:
            async def save_message_media(self, message: object, account_id: int, message_id: int) -> SimpleNamespace:
                return SimpleNamespace(absolute_path=Path('/tmp/photo.jpg'), relative_path=f'tg/1/{message_id}/{message.id}.jpg')

        async def fake_get_enabled_words(db: object) -> list:
            return []

        async def fake_add_message(db: object, data: dict) -> SimpleNamespace:
            stored_messages.append(data)
            return SimpleNamespace(message_id=200, **data)

        async def fake_add_media(db: object, data: dict) -> None:
            stored_medias.append(data)

        async def fake_get_rules(db: object, account_id: int) -> list:
            return [SimpleNamespace(source_chat_pk=2, target_chat_pks='3')]

        async def fake_get_chats(db: object, chat_pks: list[int]) -> list:
            return [SimpleNamespace(chat_pk=3, chat_id='target', chat_title='Target')]

        async def fake_forward_to_chats(*args: object, **kwargs: object) -> None:
            forward_calls.append((args, kwargs))

        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_enabled_words', fake_get_enabled_words)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.add_message', fake_add_message)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.add_media', fake_add_media)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_enabled_rules_for_account', fake_get_rules)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_chats_by_pks', fake_get_chats)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramStorageService', lambda: FakeStorage())
        monkeypatch.setattr(TelegramForwardService, 'forward_to_chats', classmethod(fake_forward_to_chats))

        event = SimpleNamespace(
            messages=[
                SimpleNamespace(id=10, message='caption', text='caption', date=None, media=object(), file=SimpleNamespace(mime_type='image/jpeg')),
                SimpleNamespace(id=11, message='', text='', date=None, media=object(), file=SimpleNamespace(mime_type='image/jpeg')),
            ]
        )
        result = await TelegramMessageIngestService.ingest_album(
            FakeDb(),
            SimpleNamespace(account_id=1),
            SimpleNamespace(chat_pk=2, chat_id='source', chat_title='Source'),
            event,
        )

        assert result.message_id == 200
        assert len(stored_messages) == 1
        assert stored_messages[0]['telegram_message_id'] == 10
        assert stored_messages[0]['message_text'] == 'caption'
        assert len(stored_medias) == 2
        assert [media['local_path'] for media in stored_medias] == ['tg/1/200/10.jpg', 'tg/1/200/11.jpg']
        assert len(forward_calls) == 1

    asyncio.run(run_case())


def test_manual_forward_does_not_update_auto_forward_status(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_case() -> None:
        update_calls = []
        records = []

        class FakeClient:
            async def send_message(self, target_chat_id: str, text: str, link_preview: bool = False) -> SimpleNamespace:
                return SimpleNamespace(id=101)

        async def fake_get_authorized_client(cls: type, account: SimpleNamespace) -> FakeClient:
            return FakeClient()

        async def fake_get_enabled_ad_text(db: object) -> None:
            return None

        async def fake_get_enabled_clean_rules(db: object) -> list:
            return []

        async def fake_get_media_by_message_id(db: object, message_id: int) -> list:
            return []

        async def fake_add_forward_record(db: object, data: dict) -> None:
            records.append(data)

        async def fake_update_message(db: object, data: dict) -> None:
            update_calls.append(data)

        monkeypatch.setattr(TelegramClientManager, 'get_authorized_client', classmethod(fake_get_authorized_client))
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_enabled_ad_text', fake_get_enabled_ad_text)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_enabled_clean_rules', fake_get_enabled_clean_rules)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_media_by_message_id', fake_get_media_by_message_id)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.add_forward_record', fake_add_forward_record)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.update_message', fake_update_message)

        await TelegramForwardService.forward_to_chats(
            db=object(),
            account=SimpleNamespace(account_id=1),
            message=SimpleNamespace(message_id=10, message_text='hello'),
            target_chats=[SimpleNamespace(chat_pk=2, chat_id='target', chat_title='Target')],
            forward_type='manual',
        )

        assert len(records) == 1
        assert records[0]['create_time'] is not None
        assert update_calls == []

    asyncio.run(run_case())


def test_forward_service_cleans_content_before_appending_ad(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_case() -> None:
        sent_messages = []
        records = []

        class FakeClient:
            async def send_message(self, target_chat_id: str, text: str, link_preview: bool = False) -> SimpleNamespace:
                sent_messages.append((target_chat_id, text, link_preview))
                return SimpleNamespace(id=102)

        async def fake_get_authorized_client(cls: type, account: SimpleNamespace) -> FakeClient:
            return FakeClient()

        async def fake_get_enabled_ad_text(db: object) -> SimpleNamespace:
            return SimpleNamespace(ad_content='自己的广告')

        async def fake_get_enabled_clean_rules(db: object) -> list:
            return [
                SimpleNamespace(
                    match_text='关注大事件频道➡️ @bx666 投稿：@tx188',
                    replacement='',
                    match_case='Y',
                )
            ]

        async def fake_get_media_by_message_id(db: object, message_id: int) -> list:
            return []

        async def fake_add_forward_record(db: object, data: dict) -> None:
            records.append(data)

        monkeypatch.setattr(TelegramClientManager, 'get_authorized_client', classmethod(fake_get_authorized_client))
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_enabled_ad_text', fake_get_enabled_ad_text)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_enabled_clean_rules', fake_get_enabled_clean_rules)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_media_by_message_id', fake_get_media_by_message_id)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.add_forward_record', fake_add_forward_record)

        await TelegramForwardService.forward_to_chats(
            db=object(),
            account=SimpleNamespace(account_id=1),
            message=SimpleNamespace(message_id=11, message_text='原始消息\n关注大事件频道➡️ @bx666 投稿：@tx188'),
            target_chats=[SimpleNamespace(chat_pk=3, chat_id='target', chat_title='Target')],
            forward_type='manual',
        )

        assert sent_messages == [('target', '原始消息\n\n\n自己的广告', False)]
        assert records[0]['status'] == 'success'
        assert records[0]['create_time'] is not None

    asyncio.run(run_case())


def test_start_listener_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_case() -> None:
        added_handlers = []

        class FakeClient:
            def add_event_handler(self, handler: object, builder: object) -> None:
                added_handlers.append((handler, builder))

        async def fake_get_authorized_client(cls: type, account: SimpleNamespace) -> FakeClient:
            return FakeClient()

        async def fake_get_rules(db: object, account_id: int) -> list:
            return [SimpleNamespace(source_chat_pk=1)]

        async def fake_get_chats(db: object, chat_pks: list[int]) -> list:
            return [SimpleNamespace(chat_pk=1, chat_id='source', can_listen='Y', status='0')]

        monkeypatch.setattr(TelegramClientManager, 'get_authorized_client', classmethod(fake_get_authorized_client))
        monkeypatch.setattr('module_telegram.service.telegram_client_service.TelegramDao.get_enabled_rules_for_account', fake_get_rules)
        monkeypatch.setattr('module_telegram.service.telegram_client_service.TelegramDao.get_chats_by_pks', fake_get_chats)
        monkeypatch.setattr(
            'module_telegram.service.telegram_client_service.events',
            SimpleNamespace(NewMessage=lambda chats: ('new', chats), Album=lambda chats: ('album', chats)),
        )
        TelegramClientManager._handlers.clear()

        account = SimpleNamespace(account_id=1)
        await TelegramClientManager.start_listener(object(), account)
        await TelegramClientManager.start_listener(object(), account)

        assert [builder[0] for _, builder in added_handlers] == ['new', 'album']
        assert len(TelegramClientManager._handlers[1]) == 2

    asyncio.run(run_case())


def test_send_chat_message_uses_saved_dialog_and_records_result(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_case() -> None:
        account_id = 7
        records = []

        class FakeDb:
            async def commit(self) -> None:
                return None

            async def rollback(self) -> None:
                return None

        async def fake_get_chat_by_pk(db: object, chat_pk: int) -> SimpleNamespace:
            return SimpleNamespace(
                chat_pk=chat_pk,
                account_id=account_id,
                chat_id='-100123456',
                chat_title='Target',
                username='target_channel',
                can_send='Y',
                status='0',
            )

        async def fake_get_detail(db: object, model: type, pk_name: str, pk_value: int) -> SimpleNamespace:
            return SimpleNamespace(account_id=pk_value)

        async def fake_send_message_to_chat(
            cls: type,
            account: SimpleNamespace,
            chat_id: str,
            text: str,
            username: str | None = None,
        ) -> dict:
            assert account.account_id == account_id
            assert chat_id == '-100123456'
            assert username == 'target_channel'
            assert text == 'hello'
            return {'success': True, 'telegram_message_id': 88}

        async def fake_add_forward_record(db: object, data: dict) -> None:
            records.append(data)

        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_chat_by_pk', fake_get_chat_by_pk)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_detail', fake_get_detail)
        monkeypatch.setattr(TelegramClientManager, 'send_message_to_chat', classmethod(fake_send_message_to_chat))
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.add_forward_record', fake_add_forward_record)

        result = await TelegramCrudService.send_chat_message(FakeDb(), TgChatSendMessageModel(chat_pk=3, text='hello'))

        assert result.message == '发送成功'
        assert records[0] == {
            'message_id': None,
            'account_id': account_id,
            'target_chat_pk': 3,
            'target_chat_id': '-100123456',
            'target_chat_title': 'Target',
            'forward_type': 'dialog',
            'status': 'success',
            'sent_telegram_message_id': 88,
            'error_message': None,
            'create_time': records[0]['create_time'],
        }
        assert records[0]['create_time'] is not None

    asyncio.run(run_case())


def test_storage_service_builds_relative_media_path(tmp_path: Path) -> None:
    storage = TelegramStorageService(base_dir=tmp_path)

    path = storage.build_media_path(account_id=1, message_id=20, filename='photo.jpg')

    assert path.relative_path == 'tg/1/20/photo.jpg'
    assert path.absolute_path == tmp_path / 'tg' / '1' / '20' / 'photo.jpg'
