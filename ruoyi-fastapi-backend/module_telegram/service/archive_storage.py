"""Cloud archive adapters. Configuration is local; credentials never enter API responses."""

import json
import os
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import ClientError
from pydantic import BaseModel, Field, ValidationError

from config.env import ArchiveConfig


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
    cli_path: str = '/opt/quark/scripts/quark-drive.cjs'
    root_fid: str = '0'


def load_storages() -> list[StorageSpec]:
    path = Path(ArchiveConfig.archive_storages_file)
    if not path.is_file():
        return []
    try:
        specs = [StorageSpec.model_validate(item) for item in json.loads(path.read_text(encoding='utf-8'))]
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


class QuarkArchiveStorage:
    """Official CLI NDJSON protocol; never invoke a shell or auto-login in a background task."""

    def __init__(self, spec: StorageSpec) -> None:
        self.spec = spec

    def _run(self, args: list[str], cwd: str) -> list[dict[str, Any]]:
        if not Path(self.spec.cli_path).is_file():
            raise ArchiveStorageError('夸克 CLI 尚未安装，请按部署文档配置并授权')
        try:
            output = subprocess.run(
                ['node', self.spec.cli_path, *args],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=14400 if args[0] == 'upload' else 120,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ArchiveStorageError('夸克命令启动失败或超时，请检查 CLI 和网络') from exc
        try:
            events = [json.loads(line) for line in output.stdout.splitlines() if line.strip()]
        except ValueError as exc:
            raise ArchiveStorageError('夸克 CLI 返回格式不兼容') from exc
        results = [event for event in events if event.get('type') == 'result']
        if not results or output.returncode or results[-1].get('code') != 0:
            message = str(results[-1].get('msg', '夸克操作失败'))[:400] if results else '夸克未返回操作结果'
            raise ArchiveStorageError(message)
        return events

    def _browse(self, fid: str) -> list[dict[str, Any]]:
        with tempfile.TemporaryDirectory(prefix='quark-browse-') as directory:
            events = self._run(['browse', '--parent-fid', fid, '--all'], directory)
            artifact = next((event for event in events if event.get('type') == 'artifact'), None)
            if not artifact or not artifact.get('data', {}).get('file_path'):
                raise ArchiveStorageError('夸克目录查询未返回完整清单')
            path = Path(artifact['data']['file_path']).resolve()
            if not path.is_relative_to(Path(directory).resolve()):
                raise ArchiveStorageError('夸克目录清单路径不合法')
            return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]

    @staticmethod
    def _is_dir(item: dict[str, Any]) -> bool:
        return item.get('file_type') == '0' or item.get('category') == 0

    def _resolve(self, path: str, create: bool = False) -> str:
        fid = self.spec.root_fid
        for name in PurePosixPath(normalize_path(path)).parts:
            matches = [item for item in self._browse(fid) if item.get('filename') == name and self._is_dir(item)]
            if len(matches) > 1:
                raise ArchiveStorageError('存在多个同名目录，请先在夸克中整理目录名称')
            if matches:
                fid = str(matches[0]['fid'])
            elif create:
                with tempfile.TemporaryDirectory(prefix='quark-mkdir-') as directory:
                    events = self._run(['create-folder', '--dir-path', name, '--parent-fid', fid], directory)
                result = next(event for event in reversed(events) if event.get('type') == 'result')
                fid = result.get('data', {}).get('fid')
                if not fid:
                    raise ArchiveStorageError('夸克创建目录未返回目录 ID')
            else:
                raise ArchiveStorageError('目标目录不存在，请刷新目录或新建目录')
        return fid

    def list_dirs(self, path: str) -> list[dict[str, str]]:
        path = normalize_path(path)
        return sorted(
            [
                {'name': item['filename'], 'path': child_path(path, item['filename'])}
                for item in self._browse(self._resolve(path))
                if self._is_dir(item)
            ],
            key=lambda item: item['name'],
        )

    def ensure_dir(self, path: str) -> str:
        self._resolve(path, create=True)
        return normalize_path(path)

    def upload(self, source: Path, directory: str, filename: str, sha256: str) -> str:
        fid = self._resolve(directory)
        files = [item for item in self._browse(fid) if item.get('filename') == filename and not self._is_dir(item)]
        if files:
            if len(files) == 1 and files[0].get('size') == source.stat().st_size:
                return str(files[0]['fid'])
            raise ArchiveStorageError('夸克存在同名文件冲突，请选择其他目录')
        if source.name != filename:
            raise ArchiveStorageError('归档文件名称不一致')
        with tempfile.TemporaryDirectory(prefix='quark-upload-') as workdir:
            events = self._run(['upload', '--file', str(source.resolve()), '--parent-fid', fid], workdir)
        result = next(event for event in reversed(events) if event.get('type') == 'result')
        data = result.get('data', {})
        if data.get('failedCount', 0) or data.get('successCount') != 1:
            raise ArchiveStorageError('夸克未确认文件上传成功')
        uploaded = [item for item in self._browse(fid) if item.get('filename') == filename and not self._is_dir(item)]
        if len(uploaded) != 1 or uploaded[0].get('size') != source.stat().st_size:
            raise ArchiveStorageError('夸克上传后的文件大小或数量校验未通过')
        return str(uploaded[0]['fid'])
