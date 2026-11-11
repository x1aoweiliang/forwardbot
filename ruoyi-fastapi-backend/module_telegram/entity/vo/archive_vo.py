from typing import Literal

from pydantic import Field

from module_telegram.entity.vo.telegram_vo import TelegramBaseModel


class ArchiveLibraryQuery(TelegramBaseModel):
    page_num: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    account_id: int | None = None
    source_chat_pk: int | None = None
    keyword: str | None = Field(default=None, max_length=200)
    state: Literal['ready', 'pending', 'failed', 'archived', 'unarchived', 'blocked'] | None = None


class ArchiveSubmitModel(TelegramBaseModel):
    message_ids: list[int] = Field(min_length=1, max_length=100)
    storage_id: str = Field(min_length=1, max_length=64)
    target_path: str = Field(default='', max_length=700)


class ArchiveFolderModel(TelegramBaseModel):
    storage_id: str
    parent_path: str = ''
    name: str = Field(min_length=1, max_length=200)
