# 素材下载与归档：源码运行

素材下载、手动归档和原转发功能在同一套源码中。直接运行原来的 `app.py` 即可，不要求 Docker，不要求额外创建采集专用数据库或 Redis。数据库连接、Redis、登录和传输加密配置继续使用当前服务器已有的配置。

## 源码启动

以下命令在项目根目录执行，使用启动应用的同一个虚拟环境。切换 Git 分支不会自动安装依赖。

```bash
# 当前项目使用 PostgreSQL；使用 MySQL 时改为 requirements.txt
.venv/bin/python -m pip install -r ruoyi-fastapi-backend/requirements-pg.txt
cd ruoyi-fastapi-backend
```

直接启动 `app.py` 默认加载后端工作目录下的 `.env.dev`。在该文件中增加以下配置，保留原有的数据库、Redis、端口和密钥配置：

```dotenv
ARCHIVE_ENABLED=true
APP_WORKERS=1
APP_RELOAD=false
ARCHIVE_MEDIA_DIR=vf_admin/archive/media
ARCHIVE_STORAGES_FILE=vf_admin/archive/storages.json
```

`ARCHIVE_ENABLED=true` 启动下载、手动归档上传和补采后台任务。`false` 关闭这些任务，保留原有转发功能；它不会修改数据库中的监听规则，也不会删除已保存的文件。规则里的「自动下载图片、视频」决定哪些新消息进入采集队列，「自动转发」单独控制转发。只需转发的部署应关闭规则里的自动下载，避免一直积累待下载记录。

首次升级到此版本，需要在当前业务数据库上执行增量迁移，增加监听规则开关、素材状态和归档任务表。应用启动时的 `create_all` 不会给已有表补列；不要重新导入会重建基础表的完整初始化 SQL。

已经安装媒体下载分支的部署也需再次运行以下迁移，应用 `20261111_02`：为发送记录增加本次素材 ID 列及状态、消息查询索引。该迁移保留原数据，不需要新建数据库或 Redis。

```bash
APP_ENV=dev ../.venv/bin/python -m alembic upgrade head
../.venv/bin/python app.py
```

同时开启采集和自动转发时，如果 Telegram 媒体引用发送失败，发送记录会显示「等待下载后补发」。下载完成后后台使用本地文件补发对应目标，重启后继续处理待补发记录；下载失败或素材被清理时转为失败，可处理素材后手动转发。相册后续到达的素材单独补发，重复监听事件不会重发已保存的成员。后台补发遵守账号监听启停和当前转发规则。

已有 PostgreSQL 后台还需执行仓库中的 `deploy/archive/menu.sql`，增加「素材库」菜单和归档权限；这个脚本不重复创建已有菜单。没有新增菜单时，前端不会显示素材库入口。

PyCharm 的解释器继续使用项目 `.venv/bin/python`，脚本选择 `ruoyi-fastapi-backend/app.py`，**工作目录设为 `ruoyi-fastapi-backend`**。若使用 `.env.prod`，启动参数填 `--env=prod`，迁移命令对应使用 `APP_ENV=prod`。源码运行无需使用仅供容器启动的 `archive_entrypoint.py`。

前端沿用原有开发启动或生产构建方式（`npm run dev` / `npm run build:prod`），后端地址和 API 前缀保持现有配置。`build:archive` 和 `/archive-api` 是下方可选 Compose 方案的配置，源码方式不必切换。

归档存储配置写入 `ARCHIVE_STORAGES_FILE` 指向的文件，可参考 `deploy/archive/storages.example.json`。只保留实际要使用的存储；未配置云端时可以填 `[]` 或暂不创建该文件，仍可采集和预览。夸克的 `cli_path` 填当前服务器安装的 CLI 绝对路径，S3 凭据环境变量可配置在同一份 `.env.dev` 中。配置文件中的环境变量名应与该文件一致。

后台任务开启配置修改后需要重启后端。两台服务器各自沿用原有数据库和 Redis 配置，不新增采集专用的一套。采集服务器开启下载，转发服务器关闭下载。

## 可选：Docker 独立部署

以下是额外提供的隔离部署方案，**源码运行可以跳过本节**。它使用自己的 PostgreSQL、Redis、登录密钥、Telegram session、夸克授权及素材磁盘。

默认入口为 `http://服务器地址:12581`。Compose 项目名为 `forwardbot-archive`，不复用原部署的容器名、网络或数据卷；PostgreSQL 和 Redis 不向宿主机开放端口。此方案的前端使用 `Archive-Token` Cookie，API 前缀为 `/archive-api`。

在这个分支的独立检出目录中执行，勿将配置文件或数据卷指向原转发部署：

```bash
cp deploy/archive/.env.example deploy/archive/.env
cp deploy/archive/storages.example.json deploy/archive/storages.json
```

编辑 `.env`：给数据库、Redis、JWT 分别设置新的随机密钥。可以运行三次 `openssl rand -hex 32` 生成。按需修改 `ARCHIVE_HTTP_PORT`。

编辑 `storages.json`：只保留要使用的存储配置。尚未配置云端时可填 `[]`，先使用采集和本地预览。S3 密钥填写在 `.env` 中，不写入数据库或前端。以上实际配置文件已被 Git 忽略。

```bash
docker compose --env-file deploy/archive/.env -f compose.archive.yml config --quiet
docker compose --env-file deploy/archive/.env -f compose.archive.yml up -d --build
docker compose --env-file deploy/archive/.env -f compose.archive.yml logs -f backend
```

全新数据库由 PostgreSQL 初始化脚本创建管理后台基础表及菜单，后端启动前运行 `alembic upgrade head` 创建 Telegram 和归档表。容器启动入口还会为这套应用生成并持久化独立的传输加密密钥，不读取原部署密钥。不要把基础初始化 SQL 手动导入现有业务数据库，它包含建表重置语句。选择此隔离方案时使用这里的专用 Compose。

管理后台沿用项目基础 SQL 中的初始管理员账号，首次登录后修改密码。新数据库不含原部署的 Telegram 账号和监听规则，需在「TG管理 → 账号管理」重新登录，再同步群列表。

采集部署固定 `APP_WORKERS=1`、`APP_RELOAD=false`，不要对 backend 做多副本扩容。这样同一 Telegram session 只由一个进程持有；该进程中有独立的下载、上传和补采任务，不在新消息回调中下载大文件。

## 夸克配置与扫码登录

使用服务器已有的 `zhangjingwei/kuake_cli` 负责目录和上传，Python 的 `quarkpan-plus==1.0.6` 负责扫码。安装后端 requirements 会安装 Python 依赖；不再需要 Node.js 或 `quark-drive.cjs`。

在服务器执行 `command -v kuake` 确认实际可执行文件路径，并让运行后端的系统用户有执行权限。在 `ARCHIVE_STORAGES_FILE` 中配置：

```json
[
  {
    "id": "quark",
    "name": "夸克素材库",
    "type": "quark",
    "cli_path": "/usr/local/bin/kuake",
    "prefix": ""
  }
]
```

- `cli_path` 改为服务器实际 `kuake` 可执行文件的绝对路径；也可填 `kuake`，此时依赖后端进程的 PATH。
- `prefix: ""` 表示网盘根目录；填 `素材库` 则界面只浏览该目录及其子目录。使用非空前缀前，先在夸克建立该目录。
- 原来的 `root_fid` 改用 `prefix` 路径。旧 `.cjs` 配置会给出迁移提示，不能指向 Go CLI 后继续使用旧参数。
- `id` 与归档记录关联，已有记录时保持不变；不同夸克账号使用不同存储 ID。

重启后端，打开「素材库 → 夸克账号 → 获取登录二维码」，使用夸克 App 扫码确认。验证 `kuake user` 成功后界面显示登录昵称。无需服务器图形桌面，也无需把 Cookie 复制到网页或配置文件。二维码五分钟过期，后端重启后需重新获取尚未完成的二维码；已保存登录仍可使用。

登录 Cookie 使用现有 `JWT_SECRET_KEY` 加密，保存在 `ARCHIVE_MEDIA_DIR/.quark-auth/`，文件权限为 `0600`。备份时同时保留该目录和 JWT 密钥，修改密钥会使旧凭据无法解密。调用 CLI 时通过子进程 `KUAKE_COOKIE` 环境变量传入，不写进命令参数、数据库或 API 响应。应用的登录独立于服务器 CLI 自己保存的登录，不依赖新版本才有的 `auth` 命令。

授权过期后在同一入口重新扫码，再重试失败任务。同一个存储 ID 只允许重新登录原账号，避免将已有归档任务误传到别人的网盘。确需切换账号时添加新的存储 ID。

目录浏览使用 `kuake list` 的完整 JSON 清单，创建目录使用 `kuake create`，上传使用 `kuake upload`。每次检查业务成功字段，上传后再次读取目录核对文件名、数量和大小。远端校验没有逐字节读取云端内容；同一内容指纹子目录中同名且同大小的文件会在重试时复用，请勿手动替换这些文件。

可选 Docker 方案需将对应容器架构的 `kuake` 可执行文件安装到持久卷 `/opt/quark/kuake` 并设置执行权限，`cli_path` 对应填写该路径。扫码凭据随素材卷保存，不再使用旧 Node CLI 的授权卷。

真实扫码、账号限制及大视频上传仍需在目标服务器验收，本地模拟测试不代表云端链路已经通过。

## S3 配置

示例见 `storages.example.json`。目标 bucket 必须预先存在，应用只创建目录前缀，不创建 bucket。

- AWS：填写 `bucket`、`region`。也可省略两个凭据环境变量名，使用容器运行环境提供的 IAM 凭据链。
- S3 兼容服务：额外填写 `endpoint_url`；按服务要求使用 `addressing_style: "path"`。
- `prefix` 限定素材根路径；界面内的 `/` 代表这个前缀，不代表整个 bucket。
- 所需能力：列举指定前缀、读取对象元信息、上传对象及分段上传的相关操作。应用不删除远端对象。

上传使用 SDK 分段传输，完成后核对远端大小和 SHA-256 元信息。同名但内容不同的对象会阻止上传，不直接覆盖；远端已存在同一文件时跳过，支持“远端已成功而本地状态尚未提交”后的恢复。

## 使用方式

1. 在监听规则中选择多个来源群，开启「自动下载图片、视频」。新建规则默认只采集；如果同时开启转发，则还需填写目标群。
2. 新消息的原文先入库，图片和视频进入下载队列；相册共用一条素材记录，保留每项 caption。敏感词拦截规则仍然有效，其他类型附件显示为「非图片或视频」。
3. 在「素材库」查看下载状态、预览素材，按群、原文、下载状态或归档状态筛选。
4. 选中已就绪消息，点击归档，选择存储及已有目录，或点击新建目录。每条消息会生成包含原文、元数据、全部已采集图片视频的子目录。
5. 在消息详情查看每个文件的上传结果。失败后重试只处理未完成文件；可以把同一消息归档到多个存储或不同目录。
6. 使用行内或详情中的「清理本地」，也可勾选多条后点击「清理所选本地素材」。确认后删除本地图片、视频及下载残片，保留文字、来源信息、云端文件和归档记录。未归档消息会明确提醒；下载中或归档排队、上传、自动重试中的消息需等待任务结束后清理。
7. 清理后显示「本地已清理」，不会被补采自动重新下载；需要时点击「重新下载」。源消息已被删除或失去访问权限时可能无法恢复。清理失败保留「清理中 / 待重试」及错误提示，修复磁盘权限后再次清理即可；重启后也可继续清理。

子目录包含日期、来源群 ID、Telegram 消息 ID 和内容指纹。归档提交时固定内容清单；晚到的相册素材会增加版本，旧归档保留，素材列表重新显示待归档。文字及元数据为 `message.txt` 和 `metadata.json`，媒体使用源消息 ID 命名，避免原文件同名覆盖。

首次补采扫描只记录监控起点，不下载启用前的全部历史。随后每分钟根据持久化游标补采停机和断线期间遗漏的消息。恢复任务仍要求源消息存在、Telegram 账号仍有访问权限。

## 磁盘、恢复与权限

- 素材保存在 `ARCHIVE_MEDIA_DIR` 指定的本机目录，默认是后端目录下的 `vf_admin/archive/media`；Docker 方案对应 `forwardbot-archive_media` 卷。该路径不挂到公开静态目录；预览需登录并具有 `tg:archive:list` 权限，由服务端签发绑定单个素材的短期链接。视频支持 Range 请求。
- `tg:archive:write` 控制目录创建、归档提交、失败重试、夸克登录及本地清理。管理员可在角色管理中分配这两个权限。
- 下载写入 `.part` 文件，按已有字节恢复；完整性检查后原子替换正式文件。任务状态、重试次数、租约、上传文件结果都保存在数据库。
- 磁盘剩余空间不足时暂停并自动重试，`ARCHIVE_MIN_FREE_BYTES` 默认预留 256 MiB。应按视频量为素材卷预留容量。
- 原来的七天转发缓存清理只清理 `storage_kind=cache`。采集素材不会因为超过七天或完成上传被自动删除，由用户在素材库手动清理。清理允许取消尚未开始的下载队列，正在下载的文件会被阻止。
- 源码服务正常停止或重启即可，保留 `vf_admin` 和配置的素材目录。使用 Docker 时停止服务不要附带 `-v` 删除数据卷。
- 备份应同时覆盖数据库、素材目录、应用状态、夸克授权以及部署配置。恢复时使用同一份配套备份。

## 验证

开发环境安装后端依赖及 `pytest aiosqlite`，执行：

```bash
cd ruoyi-fastapi-backend
python -m pytest tests/test_telegram_services.py tests/test_media_archive.py tests/test_quark_archive.py -q
cd ../ruoyi-fastapi-frontend
npm install
npm run build:prod
```

测试使用临时 SQLite 数据库与模拟云端，不访问生产数据库或发送 Telegram 消息。部署验收还应包含实际群里的图片、相册、大视频、重启恢复、夸克登录和上传，以及 S3 上传。未提供真实账号或存储凭据时，这些外部链路不能由本地测试替代。
