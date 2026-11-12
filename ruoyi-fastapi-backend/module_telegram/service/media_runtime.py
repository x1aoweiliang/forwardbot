"""Ownership of actual media I/O in the single-process Telegram runtime.

Database leases survive restarts; these guards last until the real operation
exits, including blocking SDK/CLI calls after coroutine cancellation.
"""

import asyncio
from contextlib import contextmanager
from typing import Any

from exceptions.exception import ServiceException


class MediaRuntime:
    def __init__(self) -> None:
        self.active: dict[tuple[str, int], int] = {}
        self.deleting: set[int] = set()

    def busy(self, message_id: int) -> bool:
        return message_id in self.active.values() or message_id in self.deleting

    @contextmanager
    def operation(self, kind: str, job_id: int, message_id: int) -> Any:
        key = (kind, job_id)
        if key in self.active or message_id in self.deleting:
            raise ServiceException(message='素材任务仍在执行或清理中')
        self.active[key] = message_id
        try:
            yield
        finally:
            self.active.pop(key)

    @contextmanager
    def cleanup(self, message_ids: list[int]) -> Any:
        ids = set(message_ids)
        if any(self.busy(message_id) for message_id in ids):
            raise ServiceException(message='素材正在发送、下载或归档，暂不能清理')
        self.deleting.update(ids)
        try:
            yield
        finally:
            self.deleting.difference_update(ids)


media_runtime = MediaRuntime()


async def run_blocking(function: Any, *args: Any) -> Any:
    """Do not release ownership while a non-cancellable SDK call is still alive."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Repeated stop requests must not detach the underlying thread either.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:  # noqa: PERF203 - cancellation can arrive repeatedly while draining
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()
        raise
