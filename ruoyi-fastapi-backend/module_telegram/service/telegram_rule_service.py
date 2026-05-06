import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SensitiveMatchResult:
    is_blocked: bool
    hit_word: str | None = None


@dataclass(frozen=True)
class SensitiveWordRule:
    word: str
    match_case: bool


class SensitiveWordMatcher:
    """
    简单包含匹配的敏感词检测器。
    """

    def __init__(self, words: list[Any], match_case: bool = True) -> None:
        self.rules = []
        for item in words:
            word = getattr(item, 'word', item)
            if not word or not str(word).strip():
                continue
            item_match_case = getattr(item, 'match_case', 'Y' if match_case else 'N') == 'Y'
            self.rules.append(SensitiveWordRule(word=str(word).strip(), match_case=item_match_case))

    def match(self, text: str | None) -> SensitiveMatchResult:
        if not text or not self.rules:
            return SensitiveMatchResult(is_blocked=False)
        for rule in self.rules:
            source_text = text if rule.match_case else text.lower()
            target_word = rule.word if rule.match_case else rule.word.lower()
            if target_word in source_text:
                return SensitiveMatchResult(is_blocked=True, hit_word=rule.word)
        return SensitiveMatchResult(is_blocked=False)


class AdTextPolicy:
    """
    广告词策略：同一时间只能启用一个。
    """

    @staticmethod
    def validate_single_enabled(records: list[Any]) -> bool:
        enabled_count = sum(1 for record in records if getattr(record, 'enabled', None) == '1')
        if enabled_count > 1:
            raise ValueError('只能启用一个广告词')
        return True

    @staticmethod
    def append_ad_text(text: str | None, ad_text: str | None) -> str:
        base_text = text or ''
        if not ad_text:
            return base_text
        if not base_text:
            return ad_text
        return f'{base_text}\n\n\n{ad_text}'


class ContentCleanPolicy:
    """
    发送前按启用规则清理固定文案。
    """

    @staticmethod
    def apply(text: str | None, rules: list[Any]) -> str | None:
        if text is None or not rules:
            return text
        cleaned_text = text
        for rule in rules:
            match_text = getattr(rule, 'match_text', None)
            if not match_text:
                continue
            replacement = getattr(rule, 'replacement', None) or ''
            if getattr(rule, 'match_case', 'Y') == 'Y':
                cleaned_text = cleaned_text.replace(match_text, replacement)
            else:
                cleaned_text = ContentCleanPolicy._replace_ignore_case(cleaned_text, match_text, replacement)
        return cleaned_text.strip()

    @staticmethod
    def _replace_ignore_case(text: str, match_text: str, replacement: str) -> str:
        return re.sub(re.escape(match_text), replacement, text, flags=re.IGNORECASE)


@dataclass(frozen=True)
class StoragePath:
    absolute_path: Path
    relative_path: str


class TelegramStorageService:
    """
    Telegram媒体本地存储路径服务。
    """

    def __init__(self, base_dir: str | Path = 'vf_admin/upload_path') -> None:
        self.base_dir = Path(base_dir)

    def build_media_path(self, account_id: int, message_id: int, filename: str) -> StoragePath:
        safe_filename = Path(filename).name or 'telegram_media'
        relative_path = Path('tg') / str(account_id) / str(message_id) / safe_filename
        return StoragePath(absolute_path=self.base_dir / relative_path, relative_path=relative_path.as_posix())

    async def save_message_media(self, message: Any, account_id: int, message_id: int, filename: str | None = None) -> StoragePath | None:
        if not getattr(message, 'media', None):
            return None
        media_name = filename or f'{getattr(message, "id", message_id)}'
        path = self.build_media_path(account_id, message_id, media_name)
        path.absolute_path.parent.mkdir(parents=True, exist_ok=True)
        downloaded_path = await message.download_media(file=str(path.absolute_path))
        if downloaded_path and Path(downloaded_path) != path.absolute_path:
            downloaded = Path(downloaded_path)
            return StoragePath(absolute_path=downloaded, relative_path=downloaded.relative_to(self.base_dir).as_posix())
        return path

    async def save_event_media(self, event: Any, account_id: int, message_id: int, filename: str | None = None) -> StoragePath | None:
        return await self.save_message_media(event.message, account_id, message_id, filename)


@dataclass(frozen=True)
class ForwardDispatchResult:
    message_id: int
    target_chat_id: str
    forward_type: str
    status: str
    sent_telegram_message_id: int | None = None
    error_message: str | None = None


class ForwardDispatcher:
    """
    将已经保存的消息复制发送到目标频道，逐目标隔离失败。
    """

    def __init__(self, client: Any) -> None:
        self.client = client

    @staticmethod
    def _normalize_target_chat(target_chat_id: str) -> int | str:
        target_ref = str(target_chat_id).strip()
        if target_ref.lstrip('-').isdigit():
            return int(target_ref)
        return target_ref

    async def dispatch_text(
        self,
        message_id: int,
        target_chat_ids: list[str],
        text: str | None,
        ad_text: str | None,
        forward_type: str,
    ) -> list[ForwardDispatchResult]:
        content = AdTextPolicy.append_ad_text(text, ad_text)
        results = []
        for target_chat_id in target_chat_ids:
            try:
                sent_message = await self.client.send_message(self._normalize_target_chat(target_chat_id), content, link_preview=False)
                results.append(
                    ForwardDispatchResult(
                        message_id=message_id,
                        target_chat_id=target_chat_id,
                        forward_type=forward_type,
                        status='success',
                        sent_telegram_message_id=getattr(sent_message, 'id', None),
                    )
                )
            except Exception as exc:  # noqa: PERF203
                results.append(
                    ForwardDispatchResult(
                        message_id=message_id,
                        target_chat_id=target_chat_id,
                        forward_type=forward_type,
                        status='failed',
                        error_message=str(exc),
                    )
                )
        return results

    async def dispatch_files(
        self,
        message_id: int,
        target_chat_ids: list[str],
        files: list[Any],
        text: str | None,
        ad_text: str | None,
        forward_type: str,
    ) -> list[ForwardDispatchResult]:
        caption = AdTextPolicy.append_ad_text(text, ad_text)
        results = []
        for target_chat_id in target_chat_ids:
            try:
                sent_message = await self.client.send_file(self._normalize_target_chat(target_chat_id), files, caption=caption)
                if isinstance(sent_message, list):
                    sent_message_id = getattr(sent_message[0], 'id', None) if sent_message else None
                else:
                    sent_message_id = getattr(sent_message, 'id', None)
                results.append(
                    ForwardDispatchResult(
                        message_id=message_id,
                        target_chat_id=target_chat_id,
                        forward_type=forward_type,
                        status='success',
                        sent_telegram_message_id=sent_message_id,
                    )
                )
            except Exception as exc:  # noqa: PERF203
                results.append(
                    ForwardDispatchResult(
                        message_id=message_id,
                        target_chat_id=target_chat_id,
                        forward_type=forward_type,
                        status='failed',
                        error_message=str(exc),
                    )
                )
        return results
