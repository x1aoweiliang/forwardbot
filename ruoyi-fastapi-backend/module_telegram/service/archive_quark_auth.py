"""Short-lived QR sessions. Only encrypted cookies are persisted, never login tickets."""

import base64
import io
import logging
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any

from config.env import ArchiveConfig
from module_telegram.service.archive_storage import (
    ArchiveStorageError,
    QuarkArchiveStorage,
    StorageSpec,
    load_quark_credentials,
    load_storages,
    save_quark_credentials,
)

LOGIN_TTL = 300
MAX_LOGIN_SESSIONS = 16
MIN_POLL_INTERVAL = 2


class _HideQuarkRequests(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        # httpx logs complete URLs, including the service ticket query string.
        return 'quark.cn' not in record.getMessage()


logging.getLogger('httpx').addFilter(_HideQuarkRequests())


def _new_login() -> Any:
    # Optional until the user requests login; forwarding-only startup stays usable.
    try:
        from quark_client.auth.api_login import APILogin  # noqa: PLC0415
    except ImportError as exc:
        raise ArchiveStorageError('请在后端虚拟环境安装 quarkpan-plus==1.0.6 后重试') from exc
    login = APILogin(timeout=LOGIN_TTL)
    login.logger = logging.getLogger('archive_quark_qr')
    login.logger.disabled = True
    return login


@dataclass
class QRSession:
    owner: int
    storage_id: str
    token: str
    login: Any
    expires: float
    result: dict[str, Any] | None = None
    last_poll: float = 0


class QuarkLoginService:
    def __init__(self) -> None:
        self.sessions: dict[str, QRSession] = {}
        # All entrypoints run in a thread. Serialize poll/exchange/save so a ticket
        # is never consumed twice, and two logins cannot overwrite account binding.
        self.lock = threading.RLock()

    @staticmethod
    def _spec(storage_id: str) -> StorageSpec:
        if not ArchiveConfig.archive_enabled:
            raise ArchiveStorageError('请先开启 ARCHIVE_ENABLED 并以单进程运行采集服务')
        spec = next((item for item in load_storages() if item.id == storage_id and item.type == 'quark'), None)
        if not spec:
            raise ArchiveStorageError('夸克存储目标未配置')
        return spec

    def _remove(self, session_id: str) -> None:
        session = self.sessions.pop(session_id)
        if session.login:
            session.login.client.close()

    def _prune(self) -> None:
        for session_id, session in list(self.sessions.items()):
            if session.expires <= time.monotonic():
                self._remove(session_id)

    def status(self, storage_id: str) -> dict[str, Any]:
        with self.lock:
            self._prune()
            spec = self._spec(storage_id)
            credentials = load_quark_credentials(storage_id)
            if not credentials:
                return {'loggedIn': False}
            try:
                account = QuarkArchiveStorage(spec, credentials['cookie']).account()
            except ArchiveStorageError as exc:
                return {'loggedIn': False, 'message': str(exc)}
            if account['uid'] != credentials['uid']:
                raise ArchiveStorageError('夸克账号与存储绑定不一致，请检查登录凭据')
            return {'loggedIn': True, 'nickname': account['nickname']}

    def start(self, storage_id: str, owner: int) -> dict[str, Any]:
        import qrcode  # noqa: PLC0415

        with self.lock:
            self._spec(storage_id)
            self._prune()
            for session_id, session in list(self.sessions.items()):
                if (session.owner, session.storage_id) == (owner, storage_id):
                    self._remove(session_id)
            if len(self.sessions) >= MAX_LOGIN_SESSIONS:
                raise ArchiveStorageError('扫码会话较多，请稍后再试')
            login = _new_login()
            try:
                token, url = login.get_qr_code()
                image = io.BytesIO()
                qrcode.make(url).save(image, format='PNG')
            except Exception as exc:
                login.client.close()
                raise ArchiveStorageError('获取夸克二维码失败，请检查服务器网络后重试') from exc
            session_id = secrets.token_urlsafe(32)
            self.sessions[session_id] = QRSession(owner, storage_id, token, login, time.monotonic() + LOGIN_TTL)
            return {
                'sessionId': session_id,
                'expiresIn': LOGIN_TTL,
                'image': 'data:image/png;base64,' + base64.b64encode(image.getvalue()).decode(),
            }

    def poll(self, storage_id: str, session_id: str, owner: int) -> dict[str, Any]:
        with self.lock:
            spec = self._spec(storage_id)
            self._prune()
            session = self.sessions.get(session_id)
            if session is None:
                return {'status': 'expired'}
            if (session.owner, session.storage_id) != (owner, storage_id):
                raise ArchiveStorageError('扫码会话不属于当前用户或存储')
            if session.result:
                return session.result
            if time.monotonic() - session.last_poll < MIN_POLL_INTERVAL:
                return {'status': 'waiting'}
            session.last_poll = time.monotonic()
            try:
                result = session.login.check_login_status(session.token)
                if result is None:
                    return {'status': 'waiting'}
                ticket = result['data']['members']['service_ticket']
                response = session.login.client.get(
                    'https://pan.quark.cn/account/info', params={'st': ticket, 'lw': 'scan'}
                )
                response.raise_for_status()
                cookies = {
                    cookie.name: cookie.value
                    for cookie in session.login.client.cookies.jar
                    if cookie.domain.lstrip('.') == 'quark.cn' or cookie.domain.endswith('.quark.cn')
                }
                if not cookies.get('__pus') or not cookies.get('__puus'):
                    raise ArchiveStorageError('夸克没有返回完整登录凭据，请重新扫码')
                cookie = '; '.join(f'{key}={value}' for key, value in cookies.items())
                account = QuarkArchiveStorage(spec, cookie).account()
                previous = load_quark_credentials(storage_id)
                if previous and previous['uid'] != account['uid']:
                    raise ArchiveStorageError('请使用原夸克账号重新登录；其他账号请配置新的存储 ID，避免混淆归档记录')
                save_quark_credentials(storage_id, {'cookie': cookie, **account})
                session.result = {'status': 'authorized', 'nickname': account['nickname']}
            except Exception as exc:
                self._remove(session_id)
                if isinstance(exc, ArchiveStorageError):
                    raise
                raise ArchiveStorageError('夸克登录验证失败，请检查服务器网络后重新扫码') from exc
            session.login.client.close()
            session.login = None
            session.token = ''
            return session.result

    def cancel(self, storage_id: str, session_id: str, owner: int) -> None:
        with self.lock:
            session = self.sessions.get(session_id)
            if session and (session.owner, session.storage_id) == (owner, storage_id):
                self._remove(session_id)

    def close(self) -> None:
        with self.lock:
            for session_id in list(self.sessions):
                self._remove(session_id)


quark_login_service = QuarkLoginService()
