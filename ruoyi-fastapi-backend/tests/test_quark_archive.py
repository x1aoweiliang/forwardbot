# ruff: noqa: PLR2004
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from config.env import ArchiveConfig
from module_telegram.service import archive_quark_auth as auth
from module_telegram.service.archive_storage import (
    ArchiveStorageError,
    QuarkArchiveStorage,
    StorageSpec,
    load_quark_credentials,
    quark_credentials_path,
    save_quark_credentials,
)


@pytest.fixture
def quark(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> StorageSpec:
    monkeypatch.setattr(ArchiveConfig, 'archive_media_dir', str(tmp_path / 'private'))
    monkeypatch.setattr(ArchiveConfig, 'archive_enabled', True)
    spec = StorageSpec(id='quark', name='Quark', type='quark', cli_path='/usr/local/bin/kuake')
    monkeypatch.setattr(auth, 'load_storages', lambda: [spec])
    return spec


def test_cli_uses_json_envelope_and_cookie_environment(monkeypatch: pytest.MonkeyPatch, quark: StorageSpec) -> None:
    cookie = '__pus=secret; __puus=private'
    storage = QuarkArchiveStorage(quark, cookie)
    monkeypatch.setattr('module_telegram.service.archive_storage.shutil.which', lambda _: '/usr/local/bin/kuake')
    response = {'success': True, 'data': {'uid': '123', 'nickname': '测试账号'}}

    def run(argv: list[str], **kwargs: Any) -> Any:
        assert argv == ['/usr/local/bin/kuake', 'user']
        assert cookie not in repr(argv)
        assert kwargs['env']['KUAKE_COOKIE'] == cookie
        assert Path(kwargs['cwd']).is_dir()
        assert kwargs.get('shell') is not True
        return SimpleNamespace(stdout=json.dumps(response), returncode=0)

    monkeypatch.setattr('module_telegram.service.archive_storage.subprocess.run', run)
    assert storage.account() == {'uid': '123', 'nickname': '测试账号'}
    response.update(success=False, message=cookie)
    with pytest.raises(ArchiveStorageError) as error:
        storage.account()
    assert cookie not in str(error.value)


def test_quark_creates_nested_directories_and_verifies_upload(
    monkeypatch: pytest.MonkeyPatch, quark: StorageSpec, tmp_path: Path
) -> None:
    storage = QuarkArchiveStorage(quark.model_copy(update={'prefix': '素材'}), 'cookie')
    directories = {'/': [{'dir': True, 'file_name': '素材'}], '/素材': []}
    calls = []
    source = tmp_path / 'photo.jpg'
    source.write_bytes(b'abc')

    def run(args: list[str]) -> dict:
        calls.append(args)
        if args[0] == 'list':
            return {'list': directories[args[1]]}
        if args[0] == 'create':
            directories[args[2]].append({'dir': True, 'file_name': args[1]})
            directories[args[2] + '/' + args[1]] = []
        if args[0] == 'upload':
            assert args == ['upload', str(source), '/素材/旅行/photo.jpg', '--max_upload_parallel', '2']
            directories['/素材/旅行'] = [{'dir': False, 'file_name': 'photo.jpg', 'fid': '123', 'size': 3}]
        return {}

    monkeypatch.setattr(storage, '_run', run)
    assert storage.ensure_dir('旅行') == '旅行'
    assert storage.ensure_dir('旅行') == '旅行'
    assert sum(call[0] == 'create' for call in calls) == 1
    assert storage.upload(source, '旅行', 'photo.jpg', 'hash') == '123'
    assert storage.upload(source, '旅行', 'photo.jpg', 'hash') == '123'
    assert sum(call[0] == 'upload' for call in calls) == 1
    source.write_bytes(b'conflict')
    with pytest.raises(ArchiveStorageError):
        storage.upload(source, '旅行', 'photo.jpg', 'hash')


class FakeLogin:
    def __init__(self) -> None:
        self.ready = False
        self.cookies = httpx.Cookies()
        self.cookies.set('__pus', 'pus-secret', domain='.quark.cn')
        self.cookies.set('__puus', 'puus-secret', domain='.quark.cn')
        self.cookies.set('unrelated', 'do-not-save', domain='unrelated.com')
        self.client = httpx.Client(transport=httpx.MockTransport(self.exchange), cookies=self.cookies)

    def exchange(self, request: httpx.Request) -> httpx.Response:
        assert request.url.params['st'] == 'secret-ticket'
        return httpx.Response(200, json={'success': True})

    def get_qr_code(self) -> tuple[str, str]:
        return 'qr-token', 'https://example.test/qr'

    def check_login_status(self, token: str) -> dict | None:
        assert token == 'qr-token'
        return {'data': {'members': {'service_ticket': 'secret-ticket'}}} if self.ready else None


def test_qr_lifecycle_is_private_owned_expiring_and_account_bound(
    monkeypatch: pytest.MonkeyPatch, quark: StorageSpec
) -> None:
    login = FakeLogin()
    monkeypatch.setattr(auth, '_new_login', lambda: login)
    monkeypatch.setattr(QuarkArchiveStorage, 'account', lambda _: {'uid': '123', 'nickname': '素材账号'})
    service = auth.QuarkLoginService()
    assert service.status('quark') == {'loggedIn': False}
    result = service.start('quark', 10)
    session_id = result['sessionId']
    assert result['image'].startswith('data:image/png;base64,')
    assert 'qr-token' not in repr(result)
    with pytest.raises(ArchiveStorageError):
        service.poll('quark', session_id, 11)
    service.cancel('quark', session_id, 11)
    assert session_id in service.sessions
    assert service.poll('quark', session_id, 10) == {'status': 'waiting'}
    login.ready = True
    service.sessions[session_id].last_poll = 0
    result = service.poll('quark', session_id, 10)
    assert result == {'status': 'authorized', 'nickname': '素材账号'}
    assert login.client.is_closed
    assert service.poll('quark', session_id, 10) == result
    path = quark_credentials_path('quark')
    assert path.stat().st_mode & 0o777 == 0o600
    assert b'pus-secret' not in path.read_bytes()
    assert b'secret-ticket' not in path.read_bytes()
    saved = load_quark_credentials('quark')
    assert 'unrelated' not in saved['cookie']
    assert saved['uid'] == '123'
    assert service.status('quark') == {'loggedIn': True, 'nickname': '素材账号'}
    login = FakeLogin()
    new = service.start('quark', 10)['sessionId']
    service.sessions[new].expires = 0
    assert service.poll('quark', new, 10) == {'status': 'expired'}
    assert login.client.is_closed
    login = FakeLogin()
    login.ready = True
    new = service.start('quark', 10)['sessionId']
    monkeypatch.setattr(QuarkArchiveStorage, 'account', lambda _: {'uid': '456', 'nickname': '其他账号'})
    with pytest.raises(ArchiveStorageError, match='原夸克账号'):
        service.poll('quark', new, 10)
    assert load_quark_credentials('quark') == saved
    assert login.client.is_closed
    service.close()


def test_failed_cookie_validation_does_not_replace_saved_credentials(
    monkeypatch: pytest.MonkeyPatch, quark: StorageSpec
) -> None:
    previous = {'cookie': 'old', 'uid': '123', 'nickname': '旧账号'}
    save_quark_credentials('quark', previous)
    login = FakeLogin()
    login.ready = True
    monkeypatch.setattr(auth, '_new_login', lambda: login)

    def fail(_self: Any) -> None:
        raise ArchiveStorageError('验证失败')

    monkeypatch.setattr(QuarkArchiveStorage, 'account', fail)
    service = auth.QuarkLoginService()
    session = service.start('quark', 10)['sessionId']
    with pytest.raises(ArchiveStorageError):
        service.poll('quark', session, 10)
    assert load_quark_credentials('quark') == previous
    assert login.client.is_closed


def test_sdk_can_initialize_without_writing_auth_files(
    quark: StorageSpec, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv('QUARK_CONFIG_DIR', str(tmp_path / 'sdk'))
    login = auth._new_login()
    assert login.logger.disabled
    login.client.close()
    assert list((tmp_path / 'sdk').iterdir()) == []
