from config.database import AsyncSessionLocal
from module_telegram.service.telegram_service import TelegramMediaCleanupService
from utils.log_util import logger


async def cleanup_expired_local_files() -> None:
    """
    清理超过7天的Telegram媒体本地文件。
    """
    async with AsyncSessionLocal() as db:
        result = await TelegramMediaCleanupService.cleanup_expired_local_files(db)
    logger.info(
        'Telegram媒体本地文件清理完成，扫描{}条，删除{}个，缺失{}个，跳过{}个',
        result.scanned_count,
        result.deleted_count,
        result.missing_count,
        result.skipped_count,
    )
