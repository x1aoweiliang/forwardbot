"""Build deterministic ZIP64 media packages using bounded memory."""
import errno
import hashlib
import shutil
from pathlib import Path
from typing import Any
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from config.env import ArchiveConfig
from module_telegram.service.archive_storage import ArchiveStorageError


class DiskSpaceError(Exception):
    pass


def build_media_zip(destination: Path, files: list[dict[str, Any]]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    required = sum(file['file_size'] for file in files) + len(files) * 1024 + 65536
    if shutil.disk_usage(destination.parent).free < required + ArchiveConfig.archive_min_free_bytes:
        raise DiskSpaceError
    try:
        # Fixed timestamps and stored entries make rebuilding after a restart byte-identical.
        # Media is already encoded; avoid spending CPU recompressing large videos.
        with ZipFile(destination, 'w', compression=ZIP_STORED, allowZip64=True) as archive:
            for file in files:
                source = Path(file['source'])
                if not source.is_file() or source.stat().st_size != file['file_size']:
                    raise ArchiveStorageError('本地素材缺失或大小发生变化，打包已停止')
                info = ZipInfo(file['file_name'], date_time=(1980, 1, 1, 0, 0, 0))
                info.file_size = file['file_size']
                info.compress_type = ZIP_STORED
                digest, size = hashlib.sha256(), 0
                with source.open('rb') as input_file, archive.open(info, 'w', force_zip64=True) as output:
                    for chunk in iter(lambda: input_file.read(1024 * 1024), b''):
                        output.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                if size != file['file_size'] or digest.hexdigest() != file['sha256']:
                    raise ArchiveStorageError('本地素材校验未通过，打包已停止')
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            raise DiskSpaceError from exc
        raise
