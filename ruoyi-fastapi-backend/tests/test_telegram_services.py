import asyncio
import importlib.util
import os
import sys
from datetime import UTC, datetime, timedelta
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
from module_telegram.service.telegram_service import (
    TelegramCrudService,
    TelegramForwardService,
    TelegramMediaCleanupService,
    TelegramMessageIngestService,
)


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


def test_media_cleanup_removes_only_expired_local_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def run_case() -> None:
        expired_file = tmp_path / 'tg/1/100/expired.jpg'
        fresh_file = tmp_path / 'tg/1/101/fresh.jpg'
        external_file = tmp_path.parent / 'external.jpg'
        expired_file.parent.mkdir(parents=True)
        fresh_file.parent.mkdir(parents=True)
        expired_file.write_text('old')
        fresh_file.write_text('new')
        external_file.write_text('external')
        now = datetime(2026, 5, 5, 12, 0, 0)
        cleared_media_ids = []
        expired_media_count = 3

        async def fake_get_expired_media(db: object, cutoff_time: datetime) -> list:
            assert cutoff_time == now - timedelta(days=7)
            return [
                SimpleNamespace(media_id=1, local_path='tg/1/100/expired.jpg'),
                SimpleNamespace(media_id=3, local_path='../external.jpg'),
                SimpleNamespace(media_id=4, local_path='tg/1/102/missing.jpg'),
            ]

        async def fake_clear_media_local_paths(db: object, media_ids: list[int]) -> None:
            cleared_media_ids.extend(media_ids)

        class FakeDb:
            async def commit(self) -> None:
                return None

            async def rollback(self) -> None:
                return None

        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_expired_media_with_local_path', fake_get_expired_media)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.clear_media_local_paths', fake_clear_media_local_paths)

        result = await TelegramMediaCleanupService.cleanup_expired_local_files(
            FakeDb(),
            base_dir=tmp_path,
            retention_days=7,
            now=now,
        )

        assert expired_file.exists() is False
        assert fresh_file.exists() is True
        assert external_file.exists() is True
        assert cleared_media_ids == [1, 4]
        assert result.scanned_count == expired_media_count
        assert result.deleted_count == 1
        assert result.missing_count == 1
        assert result.skipped_count == 1

    asyncio.run(run_case())


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
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramStorageService', FakeStorage)
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


def test_ingest_event_records_media_reference_without_downloading(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_case() -> None:
        stored_medias = []
        forwarded = []

        class FakeDb:
            async def flush(self) -> None:
                return None

        class FakeMessage:
            id = 42
            message = 'photo caption'
            text = 'photo caption'
            date = None
            media = object()
            file = SimpleNamespace(mime_type='image/jpeg', name='photo.jpg', size=123)

            async def download_media(self, file: str) -> None:
                raise AssertionError('media should not be downloaded during ingest')

        async def fake_get_enabled_words(db: object) -> list:
            return []

        async def fake_add_message(db: object, data: dict) -> SimpleNamespace:
            return SimpleNamespace(message_id=300, **data)

        async def fake_add_media(db: object, data: dict) -> None:
            stored_medias.append(data)

        async def fake_forward_to_chats(cls: type, db: object, account: object, message: object, target_chats: list, forward_type: str, source_messages: list | None = None) -> None:
            forwarded.append(source_messages)

        async def fake_get_rules(db: object, account_id: int) -> list:
            return [SimpleNamespace(source_chat_pk=2, target_chat_pks='3')]

        async def fake_get_chats(db: object, chat_pks: list[int]) -> list:
            return [SimpleNamespace(chat_pk=3, chat_id='target', chat_title='Target')]

        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_enabled_words', fake_get_enabled_words)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.add_message', fake_add_message)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.add_media', fake_add_media)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_enabled_rules_for_account', fake_get_rules)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_chats_by_pks', fake_get_chats)
        monkeypatch.setattr(TelegramForwardService, 'forward_to_chats', classmethod(fake_forward_to_chats))

        source_message = FakeMessage()
        await TelegramMessageIngestService.ingest_event(
            FakeDb(),
            SimpleNamespace(account_id=1),
            SimpleNamespace(chat_pk=2, chat_id='source', chat_title='Source'),
            SimpleNamespace(message=source_message, media=object()),
        )

        assert stored_medias == [
            {
                'message_id': 300,
                'media_type': 'media',
                'local_path': '',
                'source_telegram_message_id': 42,
                'media_index': 0,
                'file_name': 'photo.jpg',
                'mime_type': 'image/jpeg',
                'file_size': 123,
            }
        ]
        assert forwarded == [[source_message]]

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
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramStorageService', FakeStorage)

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
        db_message_id = 200
        first_message_id = 10
        media_count = 2

        class FakeDb:
            async def flush(self) -> None:
                return None

        async def fake_get_enabled_words(db: object) -> list:
            return []

        async def fake_add_message(db: object, data: dict) -> SimpleNamespace:
            stored_messages.append(data)
            return SimpleNamespace(message_id=db_message_id, **data)

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
        monkeypatch.setattr(TelegramForwardService, 'forward_to_chats', classmethod(fake_forward_to_chats))

        event = SimpleNamespace(
            messages=[
                SimpleNamespace(id=first_message_id, message='caption', text='caption', date=None, media=object(), file=SimpleNamespace(mime_type='image/jpeg')),
                SimpleNamespace(id=11, message='', text='', date=None, media=object(), file=SimpleNamespace(mime_type='image/jpeg')),
            ]
        )
        result = await TelegramMessageIngestService.ingest_album(
            FakeDb(),
            SimpleNamespace(account_id=1),
            SimpleNamespace(chat_pk=2, chat_id='source', chat_title='Source'),
            event,
        )

        assert result.message_id == db_message_id
        assert len(stored_messages) == 1
        assert stored_messages[0]['telegram_message_id'] == first_message_id
        assert stored_messages[0]['message_text'] == 'caption'
        assert len(stored_medias) == media_count
        assert [media['local_path'] for media in stored_medias] == ['', '']
        assert [media['source_telegram_message_id'] for media in stored_medias] == [10, 11]
        assert [media['media_index'] for media in stored_medias] == [0, 1]
        assert len(forward_calls) == 1

    asyncio.run(run_case())


def test_ingest_album_records_large_video_media_even_when_auto_forward_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_case() -> None:
        stored_messages = []
        stored_medias = []
        update_calls = []
        db_message_id = 202
        expected_media_count = 3
        large_video_size = 300 * 1024 * 1024

        class FakeDb:
            async def flush(self) -> None:
                return None

        async def fake_get_enabled_words(db: object) -> list:
            return []

        async def fake_add_message(db: object, data: dict) -> SimpleNamespace:
            stored_messages.append(data)
            return SimpleNamespace(message_id=db_message_id, **data)

        async def fake_add_media(db: object, data: dict) -> None:
            stored_medias.append(data)

        async def fake_get_rules(db: object, account_id: int) -> list:
            return [SimpleNamespace(source_chat_pk=2, target_chat_pks='3')]

        async def fake_get_chats(db: object, chat_pks: list[int]) -> list:
            return [SimpleNamespace(chat_pk=3, chat_id='target', chat_title='Target')]

        async def fake_forward_to_chats(*args: object, **kwargs: object) -> None:
            raise RuntimeError('send failed after ingest')

        async def fake_update_message(db: object, data: dict) -> None:
            update_calls.append(data)

        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_enabled_words', fake_get_enabled_words)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.add_message', fake_add_message)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.add_media', fake_add_media)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_enabled_rules_for_account', fake_get_rules)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_chats_by_pks', fake_get_chats)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.update_message', fake_update_message)
        monkeypatch.setattr(TelegramForwardService, 'forward_to_chats', classmethod(fake_forward_to_chats))

        event = SimpleNamespace(
            messages=[
                SimpleNamespace(id=10, message='caption', text='caption', date=None, media=object(), file=SimpleNamespace(mime_type='image/jpeg', size=123)),
                SimpleNamespace(id=11, message='', text='', date=None, media=object(), file=SimpleNamespace(mime_type='image/jpeg', size=456)),
                SimpleNamespace(
                    id=12,
                    message='',
                    text='',
                    date=None,
                    media=object(),
                    file=SimpleNamespace(mime_type='video/mp4', name='large.mp4', size=large_video_size),
                ),
            ]
        )

        result = await TelegramMessageIngestService.ingest_album(
            FakeDb(),
            SimpleNamespace(account_id=1),
            SimpleNamespace(chat_pk=2, chat_id='source', chat_title='Source'),
            event,
        )

        assert result.message_id == db_message_id
        assert len(stored_messages) == 1
        assert len(stored_medias) == expected_media_count
        assert [media['source_telegram_message_id'] for media in stored_medias] == [10, 11, 12]
        assert stored_medias[2]['mime_type'] == 'video/mp4'
        assert stored_medias[2]['file_size'] == large_video_size
        assert update_calls[-1]['message_id'] == db_message_id
        assert update_calls[-1]['auto_forward_status'] == 'partial_failed'

    asyncio.run(run_case())


def test_ingest_album_uses_album_caption_when_message_text_is_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_case() -> None:
        stored_messages = []
        stored_medias = []

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
            return SimpleNamespace(message_id=201, **data)

        async def fake_add_media(db: object, data: dict) -> None:
            stored_medias.append(data)

        async def fake_get_rules(db: object, account_id: int) -> list:
            return []

        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_enabled_words', fake_get_enabled_words)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.add_message', fake_add_message)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.add_media', fake_add_media)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_enabled_rules_for_account', fake_get_rules)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramStorageService', FakeStorage)

        first_album_message_id = 34073
        album_media_count = 3
        event = SimpleNamespace(
            raw_text='相册正文',
            text='相册正文',
            messages=[
                SimpleNamespace(id=first_album_message_id, message='', text='', date=None, media=object(), file=SimpleNamespace(mime_type='image/jpeg')),
                SimpleNamespace(id=34074, message='', text='', date=None, media=object(), file=SimpleNamespace(mime_type='image/jpeg')),
                SimpleNamespace(id=34075, message='', text='', date=None, media=object(), file=SimpleNamespace(mime_type='image/jpeg')),
            ],
        )
        await TelegramMessageIngestService.ingest_album(
            FakeDb(),
            SimpleNamespace(account_id=1),
            SimpleNamespace(chat_pk=2, chat_id='source', chat_title='Source'),
            event,
        )

        assert stored_messages[0]['telegram_message_id'] == first_album_message_id
        assert stored_messages[0]['message_text'] == '相册正文'
        assert len(stored_medias) == album_media_count

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


def test_manual_forward_reuses_source_media_without_local_file(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_case() -> None:
        sent_files = []
        records = []
        source_media = SimpleNamespace(id=42, media=object())

        class FakeClient:
            async def get_messages(self, source_chat_id: str, ids: list[int]) -> list:
                assert source_chat_id == 'source-chat'
                assert ids == [42]
                return [source_media]

            async def send_file(self, target_chat_id: str, files: list, caption: str) -> SimpleNamespace:
                sent_files.append((target_chat_id, files, caption))
                return SimpleNamespace(id=500)

        async def fake_get_authorized_client(cls: type, account: SimpleNamespace) -> FakeClient:
            return FakeClient()

        async def fake_get_enabled_ad_text(db: object) -> SimpleNamespace:
            return SimpleNamespace(ad_content='ad')

        async def fake_get_enabled_clean_rules(db: object) -> list:
            return []

        async def fake_get_media_by_message_id(db: object, message_id: int) -> list:
            return [
                SimpleNamespace(
                    media_id=1,
                    source_telegram_message_id=42,
                    media_index=0,
                    local_path='',
                )
            ]

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
            message=SimpleNamespace(message_id=10, source_chat_id='source-chat', message_text='caption'),
            target_chats=[SimpleNamespace(chat_pk=2, chat_id='target', chat_title='Target')],
            forward_type='manual',
        )

        assert sent_files == [('target', [source_media], 'caption\n\n\nad')]
        assert records[0]['status'] == 'success'

    asyncio.run(run_case())


def test_manual_forward_falls_back_to_local_path_for_legacy_media(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_case() -> None:
        sent_files = []

        class FakeClient:
            async def send_file(self, target_chat_id: str, file_paths: list[str], caption: str) -> SimpleNamespace:
                sent_files.append((target_chat_id, file_paths, caption))
                return SimpleNamespace(id=501)

        async def fake_get_authorized_client(cls: type, account: SimpleNamespace) -> FakeClient:
            return FakeClient()

        async def fake_get_enabled_ad_text(db: object) -> None:
            return None

        async def fake_get_enabled_clean_rules(db: object) -> list:
            return []

        async def fake_get_media_by_message_id(db: object, message_id: int) -> list:
            return [SimpleNamespace(media_id=1, source_telegram_message_id=None, media_index=0, local_path='tg/1/10/photo.jpg')]

        async def fake_add_forward_record(db: object, data: dict) -> None:
            return None

        monkeypatch.setattr(TelegramClientManager, 'get_authorized_client', classmethod(fake_get_authorized_client))
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_enabled_ad_text', fake_get_enabled_ad_text)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_enabled_clean_rules', fake_get_enabled_clean_rules)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_media_by_message_id', fake_get_media_by_message_id)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.add_forward_record', fake_add_forward_record)
        monkeypatch.setattr('module_telegram.service.telegram_rule_service.TelegramStorageService', lambda: TelegramStorageService(base_dir='/tmp/base'))
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramStorageService', lambda: TelegramStorageService(base_dir='/tmp/base'))

        await TelegramForwardService.forward_to_chats(
            db=object(),
            account=SimpleNamespace(account_id=1),
            message=SimpleNamespace(message_id=10, source_chat_id='source-chat', message_text='caption'),
            target_chats=[SimpleNamespace(chat_pk=2, chat_id='target', chat_title='Target')],
            forward_type='manual',
        )

        assert sent_files == [('target', ['/tmp/base/tg/1/10/photo.jpg'], 'caption')]

    asyncio.run(run_case())


def test_manual_forward_falls_back_to_local_path_when_source_lookup_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_case() -> None:
        sent_files = []

        class FakeClient:
            async def get_messages(self, source_chat_id: str, ids: list[int]) -> list:
                raise RuntimeError('source unavailable')

            async def send_file(self, target_chat_id: str, file_paths: list[str], caption: str) -> SimpleNamespace:
                sent_files.append((target_chat_id, file_paths, caption))
                return SimpleNamespace(id=503)

        async def fake_get_authorized_client(cls: type, account: SimpleNamespace) -> FakeClient:
            return FakeClient()

        async def fake_get_enabled_ad_text(db: object) -> None:
            return None

        async def fake_get_enabled_clean_rules(db: object) -> list:
            return []

        async def fake_get_media_by_message_id(db: object, message_id: int) -> list:
            return [SimpleNamespace(media_id=1, source_telegram_message_id=42, media_index=0, local_path='tg/1/10/photo.jpg')]

        async def fake_add_forward_record(db: object, data: dict) -> None:
            return None

        monkeypatch.setattr(TelegramClientManager, 'get_authorized_client', classmethod(fake_get_authorized_client))
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_enabled_ad_text', fake_get_enabled_ad_text)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_enabled_clean_rules', fake_get_enabled_clean_rules)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_media_by_message_id', fake_get_media_by_message_id)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.add_forward_record', fake_add_forward_record)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramStorageService', lambda: TelegramStorageService(base_dir='/tmp/base'))

        await TelegramForwardService.forward_to_chats(
            db=object(),
            account=SimpleNamespace(account_id=1),
            message=SimpleNamespace(message_id=10, source_chat_id='source-chat', message_text='caption'),
            target_chats=[SimpleNamespace(chat_pk=2, chat_id='target', chat_title='Target')],
            forward_type='manual',
        )

        assert sent_files == [('target', ['/tmp/base/tg/1/10/photo.jpg'], 'caption')]

    asyncio.run(run_case())


def test_manual_forward_records_failure_when_media_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_case() -> None:
        records = []

        class FakeClient:
            async def get_messages(self, source_chat_id: str, ids: list[int]) -> list:
                return []

            async def send_file(self, target_chat_id: str, file_paths: list[str], caption: str) -> SimpleNamespace:
                raise AssertionError('send_file should not be called without source media or local files')

        async def fake_get_authorized_client(cls: type, account: SimpleNamespace) -> FakeClient:
            return FakeClient()

        async def fake_get_enabled_ad_text(db: object) -> None:
            return None

        async def fake_get_enabled_clean_rules(db: object) -> list:
            return []

        async def fake_get_media_by_message_id(db: object, message_id: int) -> list:
            return [SimpleNamespace(media_id=1, source_telegram_message_id=42, media_index=0, local_path='')]

        async def fake_add_forward_record(db: object, data: dict) -> None:
            records.append(data)

        monkeypatch.setattr(TelegramClientManager, 'get_authorized_client', classmethod(fake_get_authorized_client))
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_enabled_ad_text', fake_get_enabled_ad_text)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_enabled_clean_rules', fake_get_enabled_clean_rules)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_media_by_message_id', fake_get_media_by_message_id)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.add_forward_record', fake_add_forward_record)

        results = await TelegramForwardService.forward_to_chats(
            db=object(),
            account=SimpleNamespace(account_id=1),
            message=SimpleNamespace(message_id=10, source_chat_id='source-chat', message_text='caption'),
            target_chats=[SimpleNamespace(chat_pk=2, chat_id='target', chat_title='Target')],
            forward_type='manual',
        )

        assert [result.status for result in results] == ['failed']
        assert results[0].error_message == '媒体文件不可用'
        assert records[0]['status'] == 'failed'
        assert records[0]['error_message'] == '媒体文件不可用'

    asyncio.run(run_case())


def test_manual_forward_does_not_send_partial_album_when_source_lookup_is_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_case() -> None:
        records = []
        source_media_a = SimpleNamespace(id=42, media=object())
        source_media_b = SimpleNamespace(id=43, media=object())

        class FakeClient:
            async def get_messages(self, source_chat_id: str, ids: list[int]) -> list:
                assert ids == [42, 43, 44]
                return [source_media_a, source_media_b]

            async def send_file(self, target_chat_id: str, files: list, caption: str) -> SimpleNamespace:
                raise AssertionError('partial album should not be sent')

        async def fake_get_authorized_client(cls: type, account: SimpleNamespace) -> FakeClient:
            return FakeClient()

        async def fake_get_enabled_ad_text(db: object) -> None:
            return None

        async def fake_get_enabled_clean_rules(db: object) -> list:
            return []

        async def fake_get_media_by_message_id(db: object, message_id: int) -> list:
            return [
                SimpleNamespace(media_id=1, source_telegram_message_id=42, media_index=0, local_path=''),
                SimpleNamespace(media_id=2, source_telegram_message_id=43, media_index=1, local_path=''),
                SimpleNamespace(media_id=3, source_telegram_message_id=44, media_index=2, local_path=''),
            ]

        async def fake_add_forward_record(db: object, data: dict) -> None:
            records.append(data)

        monkeypatch.setattr(TelegramClientManager, 'get_authorized_client', classmethod(fake_get_authorized_client))
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_enabled_ad_text', fake_get_enabled_ad_text)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_enabled_clean_rules', fake_get_enabled_clean_rules)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_media_by_message_id', fake_get_media_by_message_id)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.add_forward_record', fake_add_forward_record)

        results = await TelegramForwardService.forward_to_chats(
            db=object(),
            account=SimpleNamespace(account_id=1),
            message=SimpleNamespace(message_id=10, source_chat_id='source-chat', message_text='caption'),
            target_chats=[SimpleNamespace(chat_pk=2, chat_id='target', chat_title='Target')],
            forward_type='manual',
        )

        assert [result.status for result in results] == ['failed']
        assert results[0].error_message == '媒体文件不可用'
        assert records[0]['status'] == 'failed'

    asyncio.run(run_case())


def test_manual_forward_uses_complete_fallback_when_source_album_lookup_is_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_case() -> None:
        sent_files = []
        source_media_a = SimpleNamespace(id=42, media=object())
        source_media_b = SimpleNamespace(id=43, media=object())

        class FakeClient:
            async def get_messages(self, source_chat_id: str, ids: list[int]) -> list:
                return [source_media_a, source_media_b]

            async def send_file(self, target_chat_id: str, files: list, caption: str) -> SimpleNamespace:
                sent_files.append((target_chat_id, files, caption))
                return SimpleNamespace(id=504)

        class FakeStorage:
            base_dir = Path('/tmp/base')

            async def save_message_media(self, message: object, account_id: int, message_id: int) -> SimpleNamespace:
                return SimpleNamespace(
                    absolute_path=Path(f'/tmp/base/tg/1/10/{message.id}.jpg'),
                    relative_path=f'tg/1/10/{message.id}.jpg',
                )

        async def fake_get_authorized_client(cls: type, account: SimpleNamespace) -> FakeClient:
            return FakeClient()

        async def fake_get_enabled_ad_text(db: object) -> None:
            return None

        async def fake_get_enabled_clean_rules(db: object) -> list:
            return []

        async def fake_get_media_by_message_id(db: object, message_id: int) -> list:
            return [
                SimpleNamespace(media_id=1, source_telegram_message_id=42, media_index=0, local_path=''),
                SimpleNamespace(media_id=2, source_telegram_message_id=43, media_index=1, local_path=''),
                SimpleNamespace(media_id=3, source_telegram_message_id=44, media_index=2, local_path='tg/1/10/44.jpg'),
            ]

        async def fake_update_media_local_path(db: object, media_id: int, local_path: str) -> None:
            return None

        async def fake_add_forward_record(db: object, data: dict) -> None:
            return None

        monkeypatch.setattr(TelegramClientManager, 'get_authorized_client', classmethod(fake_get_authorized_client))
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_enabled_ad_text', fake_get_enabled_ad_text)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_enabled_clean_rules', fake_get_enabled_clean_rules)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_media_by_message_id', fake_get_media_by_message_id)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.update_media_local_path', fake_update_media_local_path)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.add_forward_record', fake_add_forward_record)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramStorageService', FakeStorage)

        results = await TelegramForwardService.forward_to_chats(
            db=object(),
            account=SimpleNamespace(account_id=1),
            message=SimpleNamespace(message_id=10, source_chat_id='source-chat', message_text='caption'),
            target_chats=[SimpleNamespace(chat_pk=2, chat_id='target', chat_title='Target')],
            forward_type='manual',
        )

        assert [result.status for result in results] == ['success']
        assert sent_files == [
            (
                'target',
                [
                    '/tmp/base/tg/1/10/42.jpg',
                    '/tmp/base/tg/1/10/43.jpg',
                    '/tmp/base/tg/1/10/44.jpg',
                ],
                'caption',
            )
        ]

    asyncio.run(run_case())


def test_auto_forward_falls_back_to_download_when_source_media_send_fails(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    async def run_case() -> None:
        sent_files = []
        updated_paths = []
        source_message = SimpleNamespace(id=42, media=object())

        class FakeClient:
            async def send_file(self, target_chat_id: str, files: list, caption: str) -> SimpleNamespace:
                sent_files.append((target_chat_id, files, caption))
                if files == [source_message]:
                    raise RuntimeError('source media expired')
                return SimpleNamespace(id=502)

        class FakeStorage:
            base_dir = tmp_path

            async def save_message_media(self, message: object, account_id: int, message_id: int) -> SimpleNamespace:
                assert message is source_message
                return SimpleNamespace(absolute_path=tmp_path / 'tg/1/10/42.jpg', relative_path='tg/1/10/42.jpg')

        async def fake_get_authorized_client(cls: type, account: SimpleNamespace) -> FakeClient:
            return FakeClient()

        async def fake_get_enabled_ad_text(db: object) -> None:
            return None

        async def fake_get_enabled_clean_rules(db: object) -> list:
            return []

        async def fake_get_media_by_message_id(db: object, message_id: int) -> list:
            return [SimpleNamespace(media_id=1, source_telegram_message_id=42, media_index=0, local_path='')]

        async def fake_update_media_local_path(db: object, media_id: int, local_path: str) -> None:
            updated_paths.append((media_id, local_path))

        async def fake_add_forward_record(db: object, data: dict) -> None:
            return None

        async def fake_update_message(db: object, data: dict) -> None:
            return None

        monkeypatch.setattr(TelegramClientManager, 'get_authorized_client', classmethod(fake_get_authorized_client))
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_enabled_ad_text', fake_get_enabled_ad_text)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_enabled_clean_rules', fake_get_enabled_clean_rules)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_media_by_message_id', fake_get_media_by_message_id)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.update_media_local_path', fake_update_media_local_path)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.add_forward_record', fake_add_forward_record)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.update_message', fake_update_message)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramStorageService', FakeStorage)

        await TelegramForwardService.forward_to_chats(
            db=object(),
            account=SimpleNamespace(account_id=1),
            message=SimpleNamespace(message_id=10, source_chat_id='source-chat', message_text='caption'),
            target_chats=[SimpleNamespace(chat_pk=2, chat_id='target', chat_title='Target')],
            forward_type='auto',
            source_messages=[source_message],
        )

        assert sent_files == [
            ('target', [source_message], 'caption'),
            ('target', [str(tmp_path / 'tg/1/10/42.jpg')], 'caption'),
        ]
        assert updated_paths == [(1, 'tg/1/10/42.jpg')]

    asyncio.run(run_case())


def test_tg_media_source_reference_migration_sets_local_path_default() -> None:
    migration_path = Path(__file__).resolve().parents[1] / 'alembic/versions/20260506_01_add_tg_media_source_reference.py'
    spec = importlib.util.spec_from_file_location('tg_media_source_reference_migration', migration_path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    alter_calls = []

    migration._has_table = lambda table_name: table_name == 'tg_message_media'
    migration._has_column = lambda table_name, column_name: column_name in {'local_path'}
    migration.op.add_column = lambda *args, **kwargs: None
    migration.op.alter_column = lambda *args, **kwargs: alter_calls.append((args, kwargs))

    migration.upgrade()

    assert any(
        args[:2] == ('tg_message_media', 'local_path') and kwargs.get('server_default') == ''
        for args, kwargs in alter_calls
    )


def test_listener_rule_source_chat_pks_migration_uses_cross_database_cast() -> None:
    migration_path = Path(__file__).resolve().parents[1] / 'alembic/versions/20260507_01_add_tg_listener_rule_source_chat_pks.py'
    spec = importlib.util.spec_from_file_location('tg_listener_rule_source_pks_migration', migration_path)
    assert spec and spec.loader
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    execute_calls = []

    migration._has_table = lambda table_name: table_name == 'tg_listener_rule'
    migration._has_column = lambda table_name, column_name: column_name == 'source_chat_pks'
    def capture_execute(statement: object) -> None:
        execute_calls.append(statement)

    migration.op.add_column = lambda *args, **kwargs: None
    migration.op.execute = capture_execute

    migration.upgrade()

    assert execute_calls
    assert all('::text' not in str(statement) for statement in execute_calls)
    assert not any(isinstance(statement, str) for statement in execute_calls)


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


def test_save_rule_normalizes_multiple_source_chats(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_case() -> None:
        stored_payloads = []

        class FakeDb:
            async def commit(self) -> None:
                return None

            async def rollback(self) -> None:
                return None

        async def fake_add_item(db: object, model: type, data: dict) -> SimpleNamespace:
            stored_payloads.append(data)
            return SimpleNamespace(**data)

        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.add_item', fake_add_item)

        result = await TelegramCrudService.save_rule(
            FakeDb(),
            SimpleNamespace(
                model_dump=lambda exclude_unset, exclude_none: {
                    'account_id': 1,
                    'rule_name': 'multi source',
                    'source_chat_pks': '2, 3,2',
                    'target_chat_pks': '8,9',
                    'status': '0',
                }
            ),
        )

        assert result.is_success is True
        expected_source_chat_pk = 2
        assert stored_payloads[0]['source_chat_pk'] == expected_source_chat_pk
        assert stored_payloads[0]['source_chat_pks'] == '2,3'

    asyncio.run(run_case())


def test_save_rule_requires_at_least_one_source_chat() -> None:
    async def run_case() -> None:
        with pytest.raises(Exception) as exc_info:
            await TelegramCrudService.save_rule(
                object(),
                SimpleNamespace(
                    model_dump=lambda exclude_unset, exclude_none: {
                        'account_id': 1,
                        'rule_name': 'missing source',
                        'target_chat_pks': '8',
                        'status': '0',
                    }
                ),
            )
        assert getattr(exc_info.value, 'message', None) == '来源频道不能为空'

    asyncio.run(run_case())


def test_auto_forward_matches_any_source_in_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_case() -> None:
        forward_calls = []

        async def fake_get_rules(db: object, account_id: int) -> list:
            return [SimpleNamespace(source_chat_pk=1, source_chat_pks='1,2', target_chat_pks='8')]

        async def fake_get_chats(db: object, chat_pks: list[int]) -> list:
            return [SimpleNamespace(chat_pk=8, chat_id='target', chat_title='Target')]

        async def fake_forward_to_chats(cls: type, db: object, account: object, message: object, targets: list, forward_type: str, source_messages: list | None = None) -> None:
            forward_calls.append((targets, forward_type, source_messages))

        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_enabled_rules_for_account', fake_get_rules)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_chats_by_pks', fake_get_chats)
        monkeypatch.setattr(TelegramForwardService, 'forward_to_chats', classmethod(fake_forward_to_chats))

        await TelegramMessageIngestService._forward_source_message_by_rules(
            db=object(),
            account=SimpleNamespace(account_id=1),
            source_chat=SimpleNamespace(chat_pk=2),
            db_message=SimpleNamespace(message_id=10),
            source_messages=[SimpleNamespace(id=42)],
        )

        assert len(forward_calls) == 1
        expected_target_chat_pk = 8
        assert forward_calls[0][0][0].chat_pk == expected_target_chat_pk
        assert forward_calls[0][1] == 'auto'

    asyncio.run(run_case())


def test_ingest_event_records_forward_failure_when_auto_forward_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_case() -> None:
        forward_records = []
        update_calls = []

        class FakeDb:
            async def flush(self) -> None:
                return None

        async def fake_get_enabled_words(db: object) -> list:
            return []

        async def fake_add_message(db: object, data: dict) -> SimpleNamespace:
            return SimpleNamespace(message_id=301, **data)

        async def fake_add_media(db: object, data: dict) -> None:
            return None

        async def fake_get_rules(db: object, account_id: int) -> list:
            return [SimpleNamespace(source_chat_pk=2, target_chat_pks='8,9')]

        async def fake_get_chats(db: object, chat_pks: list[int]) -> list:
            return [
                SimpleNamespace(chat_pk=8, chat_id='target-a', chat_title='Target A'),
                SimpleNamespace(chat_pk=9, chat_id='target-b', chat_title='Target B'),
            ]

        async def fake_forward_to_chats(*args: object, **kwargs: object) -> None:
            raise RuntimeError('client init failed')

        async def fake_add_forward_record(db: object, data: dict) -> None:
            forward_records.append(data)

        async def fake_update_message(db: object, data: dict) -> None:
            update_calls.append(data)

        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_enabled_words', fake_get_enabled_words)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.add_message', fake_add_message)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.add_media', fake_add_media)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_enabled_rules_for_account', fake_get_rules)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.get_chats_by_pks', fake_get_chats)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.add_forward_record', fake_add_forward_record)
        monkeypatch.setattr('module_telegram.service.telegram_service.TelegramDao.update_message', fake_update_message)
        monkeypatch.setattr(TelegramForwardService, 'forward_to_chats', classmethod(fake_forward_to_chats))

        await TelegramMessageIngestService.ingest_event(
            FakeDb(),
            SimpleNamespace(account_id=1),
            SimpleNamespace(chat_pk=2, chat_id='source', chat_title='Source'),
            SimpleNamespace(message=SimpleNamespace(id=42, message='text', text='text', date=None, media=None), media=None),
        )

        assert [record['target_chat_id'] for record in forward_records] == ['target-a', 'target-b']
        assert [record['status'] for record in forward_records] == ['failed', 'failed']
        assert all(record['error_message'] == 'client init failed' for record in forward_records)
        assert update_calls[-1]['auto_forward_status'] == 'partial_failed'

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

        assert [builder[0] for _, builder in added_handlers] == ['new']
        assert len(TelegramClientManager._handlers[1]) == 1

    asyncio.run(run_case())


def test_grouped_new_messages_flush_as_complete_album(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_case() -> None:
        ingest_calls = []
        expected_album_count = 3
        large_video_size = 300 * 1024 * 1024

        class FakeDb:
            async def __aenter__(self) -> "FakeDb":
                return self

            async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
                return None

            async def commit(self) -> None:
                return None

            async def rollback(self) -> None:
                return None

        class FakeSessionFactory:
            def __call__(self) -> FakeDb:
                return FakeDb()

        async def fake_ingest_album(db: object, account: object, source_chat: object, event: object) -> None:
            ingest_calls.append((account, source_chat, event))

        monkeypatch.setattr('config.database.AsyncSessionLocal', FakeSessionFactory())
        monkeypatch.setattr(TelegramMessageIngestService, 'ingest_album', staticmethod(fake_ingest_album))
        TelegramClientManager._album_buffers.clear()
        TelegramClientManager._album_flush_tasks.clear()

        account = SimpleNamespace(account_id=1)
        source_chat = SimpleNamespace(chat_pk=2, chat_id='source', chat_title='Source')
        messages = [
            SimpleNamespace(id=12, grouped_id=777, message='', text='', media=object(), file=SimpleNamespace(mime_type='video/mp4', size=large_video_size)),
            SimpleNamespace(id=10, grouped_id=777, message='caption', text='caption', media=object(), file=SimpleNamespace(mime_type='image/jpeg', size=123)),
            SimpleNamespace(id=11, grouped_id=777, message='', text='', media=object(), file=SimpleNamespace(mime_type='image/jpeg', size=456)),
        ]

        for message in messages:
            TelegramClientManager._queue_grouped_message(account, source_chat, SimpleNamespace(message=message))
        await TelegramClientManager._flush_grouped_album((1, 2, 777), account, source_chat)

        assert len(ingest_calls) == 1
        flushed_event = ingest_calls[0][2]
        assert [message.id for message in flushed_event.messages] == [10, 11, 12]
        assert len(flushed_event.messages) == expected_album_count
        assert flushed_event.messages[2].file.mime_type == 'video/mp4'

    asyncio.run(run_case())


def test_start_listener_registers_all_sources_from_multi_source_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run_case() -> None:
        added_builders = []

        class FakeClient:
            def add_event_handler(self, handler: object, builder: object) -> None:
                added_builders.append(builder)

        async def fake_get_authorized_client(cls: type, account: SimpleNamespace) -> FakeClient:
            return FakeClient()

        async def fake_get_rules(db: object, account_id: int) -> list:
            return [
                SimpleNamespace(source_chat_pk=1, source_chat_pks='1,2'),
                SimpleNamespace(source_chat_pk=3, source_chat_pks=None),
            ]

        async def fake_get_chats(db: object, chat_pks: list[int]) -> list:
            assert chat_pks == [1, 2, 3]
            return [
                SimpleNamespace(chat_pk=1, chat_id='source-a', can_listen='Y', status='0'),
                SimpleNamespace(chat_pk=2, chat_id='source-b', can_listen='Y', status='0'),
                SimpleNamespace(chat_pk=3, chat_id='source-c', can_listen='Y', status='0'),
            ]

        monkeypatch.setattr(TelegramClientManager, 'get_authorized_client', classmethod(fake_get_authorized_client))
        monkeypatch.setattr('module_telegram.service.telegram_client_service.TelegramDao.get_enabled_rules_for_account', fake_get_rules)
        monkeypatch.setattr('module_telegram.service.telegram_client_service.TelegramDao.get_chats_by_pks', fake_get_chats)
        monkeypatch.setattr(
            'module_telegram.service.telegram_client_service.events',
            SimpleNamespace(NewMessage=lambda chats: ('new', chats), Album=lambda chats: ('album', chats)),
        )
        TelegramClientManager._handlers.clear()

        await TelegramClientManager.start_listener(object(), SimpleNamespace(account_id=1))

        assert added_builders == [
            ('new', 'source-a'),
            ('new', 'source-b'),
            ('new', 'source-c'),
        ]

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
