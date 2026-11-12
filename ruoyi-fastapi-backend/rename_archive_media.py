"""Offline migration: rename collected media without downloading it again.

Stop the backend first. Dry run by default; --apply requires a backup journal.
"""
# ruff: noqa: PLC0415, ASYNC240
# This offline maintenance command intentionally performs filesystem work synchronously.
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def migrate(db: AsyncSession, *, apply: bool = False, backup: Path | None = None) -> dict:  # noqa: PLR0912
    from sqlalchemy import select

    from module_telegram.entity.do.telegram_do import TgMessage, TgMessageMedia
    from module_telegram.service.archive_service import file_sha256, message_media_filenames, private_path

    messages = list(await db.scalars(select(TgMessage).where(TgMessage.collected == 'Y')))
    medias = list(await db.scalars(select(TgMessageMedia).where(TgMessageMedia.storage_kind == 'archive')))
    grouped = {}
    for media in medias:
        grouped.setdefault(media.message_id, []).append(media)
    changes = []
    unchanged = 0
    for message in messages:
        files = grouped.get(message.message_id, [])
        names = message_media_filenames(message, files)
        for media in files:
            if media.download_status != 'ready':
                continue
            relative = f'tg/{message.account_id}/{message.message_id}/{names[media.media_id]}'
            if media.local_path == relative:
                unchanged += 1
                continue
            if not media.local_path:
                raise RuntimeError(f'素材 {media.media_id} 缺少本地路径')
            source, target = private_path(media.local_path), private_path(relative)
            if source.parent != target.parent:
                raise RuntimeError(f'素材 {media.media_id} 不在所属消息目录')
            # A previous interrupted run may have renamed the file before its DB commit.
            current = source if source.is_file() else target
            if not current.is_file() or current.stat().st_size != media.file_size:
                raise RuntimeError(f'素材 {media.media_id} 文件缺失或大小不匹配')
            if source != current and file_sha256(current) != media.sha256:
                raise RuntimeError(f'素材 {media.media_id} 目标文件内容不匹配')
            if source.is_file() and target.exists():
                raise RuntimeError(f'素材 {media.media_id} 目标文件已存在，未覆盖')
            changes.append((media, source, target, relative))
    journal = [
        {'media_id': media.media_id, 'old_path': media.local_path, 'new_path': relative,
         'size': media.file_size, 'sha256': media.sha256}
        for media, _, _, relative in changes
    ]
    if apply:
        if backup is None:
            raise ValueError('执行重命名必须指定 --backup')
        backup.parent.mkdir(parents=True, exist_ok=True)
        with backup.open('x', encoding='utf-8') as output:
            output.write(json.dumps(journal, ensure_ascii=False, indent=2))
        backup.chmod(0o600)
        moved = []
        try:
            for media, source, target, relative in changes:
                if source.exists():
                    source.rename(target)
                    moved.append((source, target))
                media.local_path = relative
            await db.commit()
        except BaseException:
            await db.rollback()
            for source, target in reversed(moved):
                target.rename(source)
            raise
    return {'renamed' if apply else 'planned': len(changes), 'unchanged': unchanged}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', default='prod')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--backup', type=Path)
    options = parser.parse_args()
    if options.apply and not options.backup:
        parser.error('--apply 需要 --backup 指定新的迁移日志文件')
    # config.env parses app.py arguments; keep migration flags out of that parser.
    sys.argv = ['app.py', f'--env={options.env}']
    from config.database import AsyncSessionLocal

    async def run() -> None:
        async with AsyncSessionLocal() as db:
            result = await migrate(db, apply=options.apply, backup=options.backup)
            print(json.dumps(result, ensure_ascii=False))

    asyncio.run(run())


if __name__ == '__main__':
    main()
