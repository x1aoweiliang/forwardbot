"""Cloud archive adapters. Configuration is local; credentials never enter API responses."""

import base64
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import ClientError
from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, Field, ValidationError

from config.env import ArchiveConfig, JwtConfig


class ArchiveStorageError(Exception):
    pass


MAX_PATH_LENGTH = 700
MAX_SEGMENT_LENGTH = 200
FIRST_PRINTABLE_CHARACTER = 32


def normalize_path(value: str) -> str:
    value = value.strip('/')
    parts = value.split('/') if value else []
    if len(value) > MAX_PATH_LENGTH or any(
        part in {'', '.', '..'}
        or len(part) > MAX_SEGMENT_LENGTH
        or '\\' in part
        or any(ord(char) < FIRST_PRINTABLE_CHARACTER for char in part)
        for part in parts
    ):
        raise ArchiveStorageError('目录路径不合法')
    return '/'.join(parts)


def child_path(parent: str, name: str) -> str:
    if '/' in name or not name or name.strip() != name:
        raise ArchiveStorageError('请输入单个文件夹名称')
    return normalize_path('/'.join(filter(None, [normalize_path(parent), name])))


class StorageSpec(BaseModel):
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    name: str
    type: Literal['s3', 'quark']
    bucket: str = ''
    prefix: str = ''
    endpoint_url: str | None = None
    region: str = 'us-east-1'
    access_key_env: str = ''
    secret_key_env: str = ''
    session_token_env: str = ''
    addressing_style: Literal['auto', 'path', 'virtual'] = 'auto'
    cli_path: str = 'kuake'


def load_storages() -> list[StorageSpec]:
    path = Path(ArchiveConfig.archive_storages_file)
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(raw, list) or any(not isinstance(item, dict) for item in raw):
            raise ValueError('expected a list of storage objects')
        if any(
            item.get('type') == 'quark'
            and (item.get('root_fid', '0') != '0' or str(item.get('cli_path', '')).endswith('.cjs'))
            for item in raw
        ):
            raise ArchiveStorageError('请将夸克 cli_path 改为 kuake 可执行文件，并用 prefix 路径替代 root_fid')
        specs = [StorageSpec.model_validate(item) for item in raw]
        if len({item.id for item in specs}) != len(specs):
            raise ValueError('duplicate storage id')
        for item in specs:
            normalize_path(item.prefix)
            if item.type == 's3' and not item.bucket:
                raise ValueError('missing bucket')
        return specs
    except (ValueError, TypeError, ValidationError) as exc:
        raise ArchiveStorageError('存储配置格式错误，请检查服务器配置文件') from exc


def get_storage(storage_id: str) -> 'S3ArchiveStorage | QuarkArchiveStorage':
    spec = next((item for item in load_storages() if item.id == storage_id), None)
    if not spec:
        raise ArchiveStorageError('存储目标未配置或已移除')
    return S3ArchiveStorage(spec) if spec.type == 's3' else QuarkArchiveStorage(spec)


class S3ArchiveStorage:
    def __init__(self, spec: StorageSpec) -> None:
        self.spec = spec
        credentials = {}
        for field, env_name in (
            ('aws_access_key_id', spec.access_key_env),
            ('aws_secret_access_key', spec.secret_key_env),
            ('aws_session_token', spec.session_token_env),
        ):
            if env_name:
                value = os.environ.get(env_name)
                if not value:
                    raise ArchiveStorageError('S3 凭据环境变量未配置')
                credentials[field] = value
        self.client = boto3.client(
            's3',
            endpoint_url=spec.endpoint_url,
            region_name=spec.region,
            config=Config(
                connect_timeout=15,
                read_timeout=120,
                retries={'mode': 'standard', 'total_max_attempts': 3},
                s3={'addressing_style': spec.addressing_style},
            ),
            **credentials,
        )

    def _key(self, path: str) -> str:
        return '/'.join(filter(None, [normalize_path(self.spec.prefix), normalize_path(path)]))

    def list_dirs(self, path: str) -> list[dict[str, str]]:
        path = normalize_path(path)
        prefix = self._key(path)
        prefix = prefix + '/' if prefix else ''
        result = []
        for page in self.client.get_paginator('list_objects_v2').paginate(
            Bucket=self.spec.bucket, Prefix=prefix, Delimiter='/'
        ):
            for folder in page.get('CommonPrefixes', []):
                name = folder['Prefix'][len(prefix) :].rstrip('/')
                result.append({'name': name, 'path': child_path(path, name)})
        return sorted(result, key=lambda item: item['name'])

    def ensure_dir(self, path: str) -> str:
        path = normalize_path(path)
        key = self._key(path)
        if key:
            self.client.put_object(Bucket=self.spec.bucket, Key=key + '/', Body=b'')
        return path

    def upload(self, source: Path, directory: str, filename: str, sha256: str) -> str:
        key = self._key(child_path(directory, filename))
        try:
            previous = self.client.head_object(Bucket=self.spec.bucket, Key=key)
        except ClientError as exc:
            if str(exc.response.get('Error', {}).get('Code')) not in {'404', 'NoSuchKey', 'NotFound'}:
                raise
        else:
            if (
                previous['ContentLength'] == source.stat().st_size
                and previous.get('Metadata', {}).get('sha256') == sha256
            ):
                return key
            raise ArchiveStorageError('远端存在不同内容的同名文件，请选择其他目录')
        self.client.upload_file(
            str(source),
            self.spec.bucket,
            key,
            ExtraArgs={'Metadata': {'sha256': sha256}},
            Config=TransferConfig(
                multipart_threshold=16 * 1024 * 1024, multipart_chunksize=16 * 1024 * 1024, max_concurrency=2
            ),
        )
        uploaded = self.client.head_object(Bucket=self.spec.bucket, Key=key)
        if uploaded['ContentLength'] != source.stat().st_size or uploaded.get('Metadata', {}).get('sha256') != sha256:
            raise ArchiveStorageError('S3 上传后的文件校验未通过')
        return key


def quark_credentials_path(storage_id: str) -> Path:
    if not storage_id or any(
        char not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for char in storage_id
    ):
        raise ArchiveStorageError('存储标识不合法')
    return Path(ArchiveConfig.archive_media_dir).resolve() / '.quark-auth' / f'{storage_id}.json'


def _credential_cipher() -> Fernet:
    key = hashlib.sha256(('quark-archive:' + JwtConfig.jwt_secret_key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def load_quark_credentials(storage_id: str) -> dict[str, str]:
    path = quark_credentials_path(storage_id)
    if not path.is_file():
        return {}
    try:
        return json.loads(_credential_cipher().decrypt(path.read_bytes()))
    except (OSError, ValueError, InvalidToken) as exc:
        raise ArchiveStorageError('夸克登录凭据无法读取，请检查文件权限及 JWT 密钥是否变更') from exc


def save_quark_credentials(storage_id: str, credentials: dict[str, str]) -> None:
    path = quark_credentials_path(storage_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as output:
        temporary = Path(output.name)
        try:
            os.chmod(temporary, 0o600)
            output.write(_credential_cipher().encrypt(json.dumps(credentials).encode()))
            output.flush()
            os.fsync(output.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


class QuarkArchiveStorage:
    """zhangjingwei/kuake_cli JSON protocol, with per-storage browser QR credentials."""

    def __init__(self, spec: StorageSpec, cookie: str | None = None) -> None:
        self.spec = spec
        # Snapshot credentials for this operation; re-login cannot switch its account.
        self.cookie = cookie if cookie is not None else load_quark_credentials(spec.id).get('cookie', '')

    def _run(self, args: list[str]) -> dict[str, Any]:
        executable = shutil.which(self.spec.cli_path)
        if not executable:
            raise ArchiveStorageError('kuake_cli 不可执行，请将 cli_path 配置为 kuake 的绝对路径')
        if not self.cookie:
            raise ArchiveStorageError('请先在素材库的「夸克账号」中扫码登录')
        env = dict(os.environ)
        env['KUAKE_COOKIE'] = self.cookie
        try:
            # A fresh cwd prevents kuake from loading an unrelated project's .env.
            with tempfile.TemporaryDirectory(prefix='quark-cli-') as cwd:
                output = subprocess.run(
                    [str(Path(executable).resolve()), *args],
                    cwd=cwd,
                    env=env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True,
                    timeout=14400 if args[0] == 'upload' else 120,
                    check=False,
                )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ArchiveStorageError('夸克命令启动失败或超时，请检查 CLI 和网络') from exc
        try:
            result = json.loads(output.stdout)
        except ValueError as exc:
            raise ArchiveStorageError('kuake_cli 返回格式不兼容，请检查安装版本') from exc
        if not isinstance(result, dict) or output.returncode or result.get('success') is not True:
            # The CLI may include a Cookie or signed URL in its raw error output.
            raise ArchiveStorageError('夸克操作失败，请检查登录状态、目录和网盘空间后重试')
        data = result.get('data')
        if not isinstance(data, dict):
            raise ArchiveStorageError('kuake_cli 未返回有效的数据')
        return data

    def account(self) -> dict[str, str]:
        data = self._run(['user'])
        uid = data.get('uid')
        if not uid:
            raise ArchiveStorageError('夸克未返回账号 ID，请检查 kuake_cli 版本及登录状态')
        return {'uid': str(uid), 'nickname': str(data.get('nickname') or '夸克账号')}

    def _remote_path(self, path: str) -> str:
        return '/' + '/'.join(filter(None, [normalize_path(self.spec.prefix), normalize_path(path)]))

    def _list(self, remote_path: str) -> list[dict[str, Any]]:
        files = self._run(['list', remote_path]).get('list')
        if not isinstance(files, list) or any(not isinstance(file, dict) for file in files):
            raise ArchiveStorageError('夸克目录查询未返回完整清单')
        return files

    def list_dirs(self, path: str) -> list[dict[str, str]]:
        path = normalize_path(path)
        return sorted(
            [
                {'name': item['file_name'], 'path': child_path(path, item['file_name'])}
                for item in self._list(self._remote_path(path))
                if item.get('dir') is True
            ],
            key=lambda item: item['name'],
        )

    def ensure_dir(self, path: str) -> str:
        remote = ''
        for name in PurePosixPath(self._remote_path(path)).parts[1:]:
            matches = [item for item in self._list(remote or '/') if item.get('file_name') == name]
            if len(matches) > 1 or (matches and matches[0].get('dir') is not True):
                raise ArchiveStorageError('夸克存在同名目录或文件冲突，请选择其他目录')
            if not matches:
                self._run(['create', name, remote or '/'])
            remote += '/' + name
        return normalize_path(path)

    def upload(self, source: Path, directory: str, filename: str, sha256: str) -> str:
        destination = self._remote_path(child_path(directory, filename))
        remote_dir = self._remote_path(directory)
        files = [item for item in self._list(remote_dir) if item.get('file_name') == filename]
        if files:
            if (
                len(files) == 1
                and files[0].get('dir') is False
                and files[0].get('size') == source.stat().st_size
                and files[0].get('fid')
            ):
                return str(files[0]['fid'])
            raise ArchiveStorageError('夸克存在同名文件冲突，请选择其他目录')
        self._run(['upload', str(source.resolve()), destination, '--max_upload_parallel', '2'])
        uploaded = [item for item in self._list(remote_dir) if item.get('file_name') == filename]
        if (
            len(uploaded) != 1
            or uploaded[0].get('dir') is not False
            or uploaded[0].get('size') != source.stat().st_size
            or not uploaded[0].get('fid')
        ):
            raise ArchiveStorageError('夸克上传后的文件大小或数量校验未通过')
        return str(uploaded[0]['fid'])
