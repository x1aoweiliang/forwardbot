import asyncio
from typing import Annotated

from fastapi import Query, Request, Response
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from common.aspect.db_seesion import DBSessionDependency
from common.aspect.interface_auth import UserInterfaceAuthDependency
from common.aspect.pre_auth import CurrentUserDependency, PreAuthDependency
from common.router import APIRouterPro
from exceptions.exception import ServiceException
from module_admin.entity.vo.user_vo import CurrentUserModel
from module_telegram.entity.vo.archive_vo import (
    ArchiveCleanupModel,
    ArchiveFolderModel,
    ArchiveLibraryQuery,
    ArchiveSubmitModel,
)
from module_telegram.service.archive_quark_auth import quark_login_service
from module_telegram.service.archive_service import TelegramArchiveService
from module_telegram.service.archive_storage import (
    ArchiveStorageError,
    child_path,
    get_storage,
    load_storages,
    normalize_path,
)
from utils.response_util import ResponseUtil

archive_controller = APIRouterPro(
    prefix='/telegram/archive',
    order_num=31,
    tags=['素材采集归档'],
    dependencies=[
        PreAuthDependency(
            exclude_routes=[
                {'path': '/telegram/archive/media/{media_id}/content', 'methods': ['GET', 'HEAD']},
            ]
        )
    ],
)
ReadPermission = UserInterfaceAuthDependency('tg:archive:list')
WritePermission = UserInterfaceAuthDependency('tg:archive:write')


@archive_controller.get('/library', dependencies=[ReadPermission])
async def library(
    request: Request,
    query: Annotated[ArchiveLibraryQuery, Query()],
    db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    return ResponseUtil.success(dict_content=await TelegramArchiveService.list_library(db, query))


@archive_controller.get('/message/{message_id}', dependencies=[ReadPermission])
async def detail(request: Request, message_id: int, db: Annotated[AsyncSession, DBSessionDependency()]) -> Response:
    return ResponseUtil.success(data=await TelegramArchiveService.detail(db, message_id))


@archive_controller.post('/message/{message_id}/retry', dependencies=[WritePermission])
async def retry_download(
    request: Request, message_id: int, db: Annotated[AsyncSession, DBSessionDependency()]
) -> Response:
    count = await TelegramArchiveService.retry_download(db, message_id)
    return ResponseUtil.success(msg=f'已重新安排 {count} 个下载任务')


@archive_controller.get('/storages', dependencies=[ReadPermission])
async def storages(request: Request) -> Response:
    try:
        return ResponseUtil.success(
            data=[{'id': item.id, 'name': item.name, 'type': item.type} for item in load_storages()]
        )
    except ArchiveStorageError as exc:
        raise ServiceException(message=str(exc)) from exc


@archive_controller.post('/local/cleanup', dependencies=[WritePermission])
async def cleanup_local(
    request: Request, item: ArchiveCleanupModel, db: Annotated[AsyncSession, DBSessionDependency()]
) -> Response:
    data = await TelegramArchiveService.cleanup_local(db, item.message_ids)
    return ResponseUtil.success(data=data, msg='部分素材清理失败，可再次清理' if data['errors'] else '本地素材已清理')


@archive_controller.get('/quark/{storage_id}/status', dependencies=[WritePermission])
async def quark_status(request: Request, storage_id: str) -> Response:
    try:
        return ResponseUtil.success(data=await asyncio.to_thread(quark_login_service.status, storage_id))
    except ArchiveStorageError as exc:
        raise ServiceException(message=str(exc)) from exc


@archive_controller.post('/quark/{storage_id}/login', dependencies=[WritePermission])
async def quark_login(
    request: Request, storage_id: str, user: Annotated[CurrentUserModel, CurrentUserDependency()]
) -> Response:
    try:
        return ResponseUtil.success(
            data=await asyncio.to_thread(quark_login_service.start, storage_id, user.user.user_id)
        )
    except ArchiveStorageError as exc:
        raise ServiceException(message=str(exc)) from exc


@archive_controller.get('/quark/{storage_id}/login/{session_id}', dependencies=[WritePermission])
async def quark_poll(
    request: Request, storage_id: str, session_id: str, user: Annotated[CurrentUserModel, CurrentUserDependency()]
) -> Response:
    try:
        return ResponseUtil.success(
            data=await asyncio.to_thread(quark_login_service.poll, storage_id, session_id, user.user.user_id)
        )
    except ArchiveStorageError as exc:
        raise ServiceException(message=str(exc)) from exc


@archive_controller.delete('/quark/{storage_id}/login/{session_id}', dependencies=[WritePermission])
async def quark_cancel(
    request: Request, storage_id: str, session_id: str, user: Annotated[CurrentUserModel, CurrentUserDependency()]
) -> Response:
    await asyncio.to_thread(quark_login_service.cancel, storage_id, session_id, user.user.user_id)
    return ResponseUtil.success()


@archive_controller.get('/folders', dependencies=[ReadPermission])
async def folders(request: Request, storage_id: str, path: str = '') -> Response:
    try:
        normalized = normalize_path(path)
        storage = await asyncio.to_thread(get_storage, storage_id)
        return ResponseUtil.success(
            data={'path': normalized, 'folders': await asyncio.to_thread(storage.list_dirs, normalized)}
        )
    except Exception as exc:
        message = str(exc) if isinstance(exc, ArchiveStorageError) else f'目录读取失败（{type(exc).__name__}）'
        raise ServiceException(message=message) from exc


@archive_controller.post('/folders', dependencies=[WritePermission])
async def create_folder(request: Request, item: ArchiveFolderModel) -> Response:
    try:
        path = child_path(item.parent_path, item.name)
        storage = await asyncio.to_thread(get_storage, item.storage_id)
        await asyncio.to_thread(storage.ensure_dir, path)
        return ResponseUtil.success(data={'path': path}, msg='目录已准备好')
    except Exception as exc:
        message = str(exc) if isinstance(exc, ArchiveStorageError) else f'创建目录失败（{type(exc).__name__}）'
        raise ServiceException(message=message) from exc


@archive_controller.post('/tasks', dependencies=[WritePermission])
async def submit(
    request: Request,
    item: ArchiveSubmitModel,
    db: Annotated[AsyncSession, DBSessionDependency()],
    user: Annotated[CurrentUserModel, CurrentUserDependency()],
) -> Response:
    try:
        ids = await TelegramArchiveService.submit(db, item, user.user.user_name)
        return ResponseUtil.success(data={'taskIds': ids}, msg='已提交归档任务，可在素材详情中查看进度')
    except ArchiveStorageError as exc:
        raise ServiceException(message=str(exc)) from exc


@archive_controller.post('/tasks/{task_id}/retry', dependencies=[WritePermission])
async def retry_archive(request: Request, task_id: int, db: Annotated[AsyncSession, DBSessionDependency()]) -> Response:
    await TelegramArchiveService.retry_archive(db, task_id)
    return ResponseUtil.success(msg='归档任务已重新排队')


@archive_controller.get('/media/{media_id}/ticket', dependencies=[ReadPermission])
async def preview_ticket(
    request: Request, media_id: int, db: Annotated[AsyncSession, DBSessionDependency()]
) -> Response:
    await TelegramArchiveService.media_file(db, media_id)
    return ResponseUtil.success(data={'ticket': TelegramArchiveService.preview_ticket(media_id)})


@archive_controller.get('/media/{media_id}/content')
@archive_controller.head('/media/{media_id}/content')
async def media_content(
    request: Request,
    media_id: int,
    ticket: str,
    db: Annotated[AsyncSession, DBSessionDependency()],
) -> Response:
    TelegramArchiveService.verify_ticket(media_id, ticket)
    path, mime = await TelegramArchiveService.media_file(db, media_id)
    return FileResponse(
        path,
        media_type=mime,
        headers={
            'Cache-Control': 'private, no-store',
            'X-Content-Type-Options': 'nosniff',
            'Content-Security-Policy': "default-src 'none'; sandbox",
        },
    )
