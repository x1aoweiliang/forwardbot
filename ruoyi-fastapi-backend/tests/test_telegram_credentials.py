import asyncio
from pathlib import Path

import pytest
from sqlalchemy import func, select
from test_media_archive import database

from config.env import TelegramConfig, TelegramSettings
from exceptions.exception import ServiceException
from module_telegram.entity.do.telegram_do import TgAccount
from module_telegram.entity.vo.telegram_vo import TgAccountModel, TgAccountPageQueryModel
from module_telegram.service.telegram_client_service import TelegramClientManager
from module_telegram.service.telegram_service import TelegramCrudService


def test_account_crud_uses_config_and_never_returns_credentials(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    configured_id = 123456
    monkeypatch.setattr(TelegramConfig, 'telegram_api_id', configured_id)
    monkeypatch.setattr(TelegramConfig, 'telegram_api_hash', 'configured-hash')

    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions, sessions() as db:
            # Both the new form and a cached old form use the server configuration.
            for name, extra in [('new-form', {}), ('old-form', {'apiId': 9, 'apiHash': 'browser-hash'})]:
                result = await TelegramCrudService.add_account(
                    db, TgAccountModel(accountName=name, phone='+10000000000', **extra)
                )
                assert result.is_success
                account = await db.scalar(select(TgAccount).where(TgAccount.account_name == name))
                assert (account.api_id, account.api_hash) == (configured_id, 'configured-hash')
                result = await TelegramCrudService.edit_account(
                    db, TgAccountModel(accountId=account.account_id, remark='edited', apiId=9, apiHash='browser-hash')
                )
                assert result.is_success
                await db.refresh(account)
                assert account.remark == 'edited'
                assert (account.api_id, account.api_hash) == (configured_id, 'configured-hash')
                detail = (await TelegramCrudService.account_detail(db, account.account_id)).model_dump(by_alias=True)
                assert 'apiId' not in detail and 'apiHash' not in detail
            for paginated in (True, False):
                result = await TelegramCrudService.list_accounts(db, TgAccountPageQueryModel(), is_page=paginated)
                rows = result.rows if paginated else result
                assert rows
                assert all('apiId' not in row and 'apiHash' not in row for row in rows)

    asyncio.run(run())


@pytest.mark.parametrize('api_id,api_hash', [(None, None), (123, ''), (None, 'hash')])
def test_missing_config_rejects_account_before_writing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, api_id: int | None, api_hash: str | None
) -> None:
    monkeypatch.setattr(TelegramConfig, 'telegram_api_id', api_id)
    monkeypatch.setattr(TelegramConfig, 'telegram_api_hash', api_hash)

    async def run() -> None:
        async with database(monkeypatch, tmp_path) as sessions, sessions() as db:
            before = await db.scalar(select(func.count()).select_from(TgAccount))
            with pytest.raises(ServiceException) as error:
                await TelegramCrudService.add_account(db, TgAccountModel(accountName='new', phone='+10000000000'))
            assert 'TELEGRAM_API_ID' in error.value.message
            assert await db.scalar(select(func.count()).select_from(TgAccount)) == before
            monkeypatch.setattr(TelegramClientManager, '_clients', {})
            # Existing DB credentials must not silently bypass incomplete config.
            with pytest.raises(ServiceException) as error:
                await TelegramClientManager.get_client(await db.get(TgAccount, 1))
            assert 'TELEGRAM_API_ID' in error.value.message

    asyncio.run(run())


def test_telegram_settings_read_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    configured_id = 654321
    monkeypatch.setenv('TELEGRAM_API_ID', str(configured_id))
    monkeypatch.setenv('TELEGRAM_API_HASH', 'environment-hash')
    config = TelegramSettings()
    assert config.telegram_api_id == configured_id
    assert config.telegram_api_hash == 'environment-hash'
