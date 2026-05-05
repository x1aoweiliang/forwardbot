from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from common.vo import PageModel
from module_telegram.entity.do.telegram_do import (
    TgAccount,
    TgAdText,
    TgChat,
    TgContentCleanRule,
    TgForwardRecord,
    TgListenerRule,
    TgMessage,
    TgMessageMedia,
    TgSensitiveWord,
)
from utils.page_util import PageUtil


class TelegramDao:
    """
    Telegram模块通用数据库操作层
    """

    @classmethod
    async def get_detail(cls, db: AsyncSession, model: type, pk_name: str, pk_value: int) -> Any | None:
        return (await db.execute(select(model).where(getattr(model, pk_name) == pk_value))).scalars().first()

    @classmethod
    async def list_items(
        cls, db: AsyncSession, model: type, query_object: Any, filters: list[Any], order_by: Any, is_page: bool
    ) -> PageModel | list[dict[str, Any]]:
        query = select(model).where(*filters).order_by(order_by)
        return await PageUtil.paginate(db, query, query_object.page_num, query_object.page_size, is_page)

    @classmethod
    async def add_item(cls, db: AsyncSession, model: type, data: dict[str, Any]) -> Any:
        db_model = model(**data)
        db.add(db_model)
        await db.flush()
        return db_model

    @classmethod
    async def update_item(cls, db: AsyncSession, model: type, data: dict[str, Any]) -> None:
        await db.execute(update(model), [data])

    @classmethod
    async def delete_items(cls, db: AsyncSession, model: type, pk_column: Any, ids: list[int]) -> None:
        await db.execute(delete(model).where(pk_column.in_(ids)))

    @classmethod
    async def get_enabled_words(cls, db: AsyncSession) -> list[TgSensitiveWord]:
        return (
            (await db.execute(select(TgSensitiveWord).where(TgSensitiveWord.status == '0').order_by(TgSensitiveWord.word_id)))
            .scalars()
            .all()
        )

    @classmethod
    async def get_enabled_ad_text(cls, db: AsyncSession) -> TgAdText | None:
        return (await db.execute(select(TgAdText).where(TgAdText.enabled == '1').order_by(TgAdText.ad_id))).scalars().first()

    @classmethod
    async def get_enabled_clean_rules(cls, db: AsyncSession) -> list[TgContentCleanRule]:
        return (
            (await db.execute(select(TgContentCleanRule).where(TgContentCleanRule.status == '0').order_by(TgContentCleanRule.clean_id)))
            .scalars()
            .all()
        )

    @classmethod
    async def disable_other_ad_texts(cls, db: AsyncSession, keep_ad_id: int | None = None) -> None:
        statement = update(TgAdText).where(TgAdText.enabled == '1')
        if keep_ad_id:
            statement = statement.where(TgAdText.ad_id != keep_ad_id)
        await db.execute(statement.values(enabled='0'))

    @classmethod
    async def get_enabled_accounts(cls, db: AsyncSession) -> list[TgAccount]:
        return (await db.execute(select(TgAccount).where(TgAccount.status == '0'))).scalars().all()

    @classmethod
    async def get_enabled_rules_for_account(cls, db: AsyncSession, account_id: int) -> list[TgListenerRule]:
        return (
            (
                await db.execute(
                    select(TgListenerRule).where(TgListenerRule.account_id == account_id, TgListenerRule.status == '0')
                )
            )
            .scalars()
            .all()
        )

    @classmethod
    async def get_chats_by_pks(cls, db: AsyncSession, chat_pks: list[int]) -> list[TgChat]:
        if not chat_pks:
            return []
        return (await db.execute(select(TgChat).where(TgChat.chat_pk.in_(chat_pks)))).scalars().all()

    @classmethod
    async def get_chat_by_pk(cls, db: AsyncSession, chat_pk: int) -> TgChat | None:
        return await cls.get_detail(db, TgChat, 'chat_pk', chat_pk)

    @classmethod
    async def get_chat_by_account_and_chat_id(cls, db: AsyncSession, account_id: int, chat_id: str) -> TgChat | None:
        return (
            (await db.execute(select(TgChat).where(TgChat.account_id == account_id, TgChat.chat_id == chat_id)))
            .scalars()
            .first()
        )

    @classmethod
    async def add_message(cls, db: AsyncSession, data: dict[str, Any]) -> TgMessage:
        return await cls.add_item(db, TgMessage, data)

    @classmethod
    async def update_message(cls, db: AsyncSession, data: dict[str, Any]) -> None:
        await cls.update_item(db, TgMessage, data)

    @classmethod
    async def add_media(cls, db: AsyncSession, data: dict[str, Any]) -> TgMessageMedia:
        return await cls.add_item(db, TgMessageMedia, data)

    @classmethod
    async def get_media_by_message_id(cls, db: AsyncSession, message_id: int) -> list[TgMessageMedia]:
        return (
            (await db.execute(select(TgMessageMedia).where(TgMessageMedia.message_id == message_id)))
            .scalars()
            .all()
        )

    @classmethod
    async def add_forward_record(cls, db: AsyncSession, data: dict[str, Any]) -> TgForwardRecord:
        return await cls.add_item(db, TgForwardRecord, data)
