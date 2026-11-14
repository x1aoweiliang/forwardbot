import re
from typing import Literal

from pydantic import Field, field_validator

from module_telegram.entity.vo.telegram_vo import TelegramBaseModel

MAX_ARCHIVE_NAME_BYTES = 180


class ArchiveLibraryQuery(TelegramBaseModel):
    page_num: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    account_id: int | None = None
    source_chat_pk: int | None = None
    keyword: str | None = Field(default=None, max_length=200)
    state: Literal['ready', 'pending', 'failed', 'archived', 'unarchived', 'blocked', 'deleted', 'deleting'] | None = (
        None
    )


class ArchiveCleanupModel(TelegramBaseModel):
    message_ids: list[int] = Field(min_length=1, max_length=100)


class ArchiveSubmitModel(TelegramBaseModel):
    message_ids: list[int] = Field(min_length=1, max_length=100)
    storage_id: str = Field(min_length=1, max_length=64)
    target_path: str = Field(default='', max_length=700)
    archive_name: str = Field(min_length=1, max_length=200)

    @field_validator('archive_name')
    @classmethod
    def validate_archive_name(cls, value: str) -> str:
        stem = value.strip()
        if stem.lower().endswith('.zip'):
            stem = stem[:-4].rstrip()
        if (
            not stem or stem in {'.', '..'} or stem.endswith('.')
            or re.search(r'[<>:"/\\|?*\x00-\x1f\x7f]', stem)
            or len(stem.encode('utf-8')) > MAX_ARCHIVE_NAME_BYTES
        ):
            raise ValueError('请输入有效的 ZIP 名称，不包含路径或特殊字符，名称不超过 180 字节')
        return stem + '.zip'


class ArchiveFolderModel(TelegramBaseModel):
    storage_id: str
    parent_path: str = ''
    name: str = Field(min_length=1, max_length=200)
